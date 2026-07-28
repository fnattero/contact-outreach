from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import SplitResult, unquote, urljoin, urlsplit, urlunsplit

import dns.exception
import dns.resolver

from apps.integrations.contracts import (
    WebsiteEmailCandidate,
    WebsiteErrorKind,
    WebsitePage,
    WebsiteRequest,
    WebsiteResult,
)
from apps.integrations.domains import registrable_domain_from_hostname

MAX_PAGES = 4
MAX_REDIRECTS = 3
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_PAGE_TEXT = 20_000
MAX_TOTAL_TEXT = 50_000
MAX_EMAIL_CANDIDATE_LENGTH = 320
MAX_EMAIL_CANDIDATES_PER_PAGE = 20
MAX_EMAIL_CANDIDATES_TOTAL = 40
ALLOWED_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "169.254.169.254",
    }
)
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")
RELEVANT_TERMS = (
    "servicio",
    "servicios",
    "producto",
    "productos",
    "nosotros",
    "empresa",
    "reparacion",
    "reparaciones",
    "mantenimiento",
    "contacto",
)
VISIBLE_EMAIL_RE = re.compile(
    r"(?<![\w@])"
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+",
    flags=re.IGNORECASE,
)


class DNSResolver(Protocol):
    def resolve(self, hostname: str, *, timeout_seconds: float) -> tuple[str, ...]: ...


class SocketDNSResolver:
    """Bounded A/AAAA resolver; the historical name remains API-compatible."""

    def __init__(self, resolver: dns.resolver.Resolver | None = None) -> None:
        self.resolver = resolver or dns.resolver.Resolver()

    def resolve(self, hostname: str, *, timeout_seconds: float) -> tuple[str, ...]:
        try:
            answers = self.resolver.resolve_name(
                hostname,
                family=socket.AF_UNSPEC,
                lifetime=max(0.001, timeout_seconds),
            )
        except dns.exception.Timeout as exc:
            raise OSError("La resolución DNS agotó el tiempo disponible.") from exc
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer) as exc:
            raise OSError("El hostname no tiene direcciones A/AAAA utilizables.") from exc
        except dns.exception.DNSException as exc:
            raise OSError("La resolución DNS falló temporalmente.") from exc
        return tuple(sorted({str(address) for address in answers.addresses()}))


@dataclass(frozen=True, slots=True)
class PinnedHTTPResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


class PinnedHTTPTransport(Protocol):
    def get(
        self,
        *,
        url: str,
        hostname: str,
        resolved_ip: str,
        port: int,
        timeout_seconds: float,
        max_bytes: int,
    ) -> PinnedHTTPResponse: ...


class StandardPinnedHTTPTransport:
    """HTTP transport that connects to the validated IP without resolving again."""

    def get(
        self,
        *,
        url: str,
        hostname: str,
        resolved_ip: str,
        port: int,
        timeout_seconds: float,
        max_bytes: int,
    ) -> PinnedHTTPResponse:
        parsed = urlsplit(url)
        raw_socket = socket.create_connection(
            (resolved_ip, port), timeout=min(5.0, timeout_seconds)
        )
        raw_socket.settimeout(min(10.0, timeout_seconds))
        connection = http.client.HTTPConnection(resolved_ip, port, timeout=timeout_seconds)
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            connection.sock = context.wrap_socket(raw_socket, server_hostname=hostname)
        else:
            connection.sock = raw_socket
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        host_header = hostname
        if port != (443 if parsed.scheme == "https" else 80):
            host_header = f"{hostname}:{port}"
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Host": host_header,
                    "User-Agent": "ContactOutreachWebsiteFetcher/1.0",
                    "Accept": "text/html,text/plain;q=0.9",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            content_length = response.getheader("Content-Length")
            if content_length is not None and int(content_length) > max_bytes:
                raise WebsiteFetchError("La respuesta excede el límite de 2 MiB.")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise WebsiteFetchError("La respuesta excede el límite de 2 MiB.")
            headers = {key.casefold(): value for key, value in response.getheaders()}
            return PinnedHTTPResponse(response.status, headers, body)
        finally:
            connection.close()


class WebsiteFetchError(RuntimeError):
    pass


class UnsafeWebsiteURLError(WebsiteFetchError):
    pass


class RetryableWebsiteFetchError(WebsiteFetchError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    candidate = mapped or address
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    return not (
        candidate.is_private
        or candidate.is_loopback
        or candidate.is_link_local
        or candidate.is_multicast
        or candidate.is_reserved
        or candidate.is_unspecified
        or (isinstance(candidate, ipaddress.IPv4Address) and candidate in cgnat)
    )


def _canonical_url(url: str, resolver: DNSResolver, *, timeout_seconds: float) -> ValidatedURL:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeWebsiteURLError("La URL no tiene una sintaxis segura.") from exc
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeWebsiteURLError("Sólo se permiten URLs HTTP o HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeWebsiteURLError("La URL no puede incluir credenciales.")
    if parsed.fragment:
        raise UnsafeWebsiteURLError("La URL no puede incluir fragmentos.")
    if not parsed.hostname:
        raise UnsafeWebsiteURLError("La URL necesita un hostname.")
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise UnsafeWebsiteURLError("El hostname no es válido.") from exc
    if (
        hostname in BLOCKED_HOSTS
        or hostname.endswith(BLOCKED_SUFFIXES)
        or "metadata" in hostname.split(".")
    ):
        raise UnsafeWebsiteURLError("El hostname está bloqueado por la política SSRF.")
    expected_port = 443 if parsed.scheme == "https" else 80
    effective_port = port or expected_port
    if effective_port not in {80, 443}:
        raise UnsafeWebsiteURLError("El puerto no está permitido.")
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        addresses = resolver.resolve(hostname, timeout_seconds=timeout_seconds)
    else:
        addresses = (str(literal),)
    if not addresses or any(not _is_public_address(value) for value in addresses):
        raise UnsafeWebsiteURLError("El hostname resuelve a una dirección no pública.")
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = display_host if effective_port == expected_port else f"{display_host}:{effective_port}"
    canonical = urlunsplit(SplitResult(parsed.scheme, netloc, parsed.path or "/", parsed.query, ""))
    return ValidatedURL(canonical, hostname, effective_port, addresses)


class _UsefulHTMLParser(HTMLParser):
    _ignored_tags = frozenset(
        {"script", "style", "nav", "header", "footer", "form", "svg", "iframe", "noscript"}
    )
    _contact_ignored_tags = frozenset({"script", "style", "svg", "iframe", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._contact_ignored_depth = 0
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self.text_parts: list[str] = []
        self.contact_text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.mailto_values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        href = dict(attrs).get("href") if normalized == "a" else None
        if not self._contact_ignored_depth and href and href.casefold().startswith("mailto:"):
            self.mailto_values.extend(_mailto_values(href))
        if normalized in self._contact_ignored_tags:
            self._contact_ignored_depth += 1
        if normalized in self._ignored_tags:
            self._ignored_depth += 1
            return
        if self._ignored_depth == 0 and normalized == "a":
            self._anchor_href = href
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self._contact_ignored_tags and self._contact_ignored_depth:
            self._contact_ignored_depth -= 1
        if normalized in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth == 0 and normalized == "a" and self._anchor_href:
            self.links.append((self._anchor_href, " ".join(self._anchor_text)))
            self._anchor_href = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        cleaned = re.sub(r"\s+", " ", data).strip()
        if cleaned and not self._contact_ignored_depth:
            self.contact_text_parts.append(cleaned)
        if self._ignored_depth:
            return
        if cleaned:
            self.text_parts.append(cleaned)
            if self._anchor_href:
                self._anchor_text.append(cleaned)


def _decode_body(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    charset = match.group(1) if match else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _registrable_domain(hostname: str) -> str:
    return registrable_domain_from_hostname(hostname)


def _bounded_email_value(value: str) -> str | None:
    candidate = value.strip().strip("<>()[]{}.,;:\"'")
    if (
        not candidate
        or len(candidate) > MAX_EMAIL_CANDIDATE_LENGTH
        or "@" not in candidate
        or any(character.isspace() or ord(character) < 32 for character in candidate)
    ):
        return None
    return candidate


def _mailto_values(href: str) -> tuple[str, ...]:
    try:
        path = unquote(urlsplit(href).path)
    except ValueError:
        return ()
    values: list[str] = []
    for raw_value in re.split(r"[,;]", path):
        candidate = _bounded_email_value(raw_value)
        if candidate is not None:
            values.append(candidate)
        if len(values) >= MAX_EMAIL_CANDIDATES_PER_PAGE:
            break
    return tuple(values)


def _email_candidates(
    *,
    text: str,
    mailto_values: list[str],
    page_url: str,
    page_content_hash: str,
) -> tuple[WebsiteEmailCandidate, ...]:
    candidates: list[WebsiteEmailCandidate] = []
    seen: set[str] = set()

    def add(value: str, source: str) -> None:
        candidate = _bounded_email_value(value)
        if candidate is None:
            return
        key = candidate.casefold()
        if key in seen or len(candidates) >= MAX_EMAIL_CANDIDATES_PER_PAGE:
            return
        seen.add(key)
        candidates.append(
            WebsiteEmailCandidate(
                value=candidate,
                source=source,
                page_url=page_url,
                page_content_hash=page_content_hash,
                order=len(candidates),
            )
        )

    for value in mailto_values:
        add(value, "mailto")
    for match in VISIBLE_EMAIL_RE.finditer(text):
        add(match.group(0), "visible_text")
    return tuple(candidates)


class HttpWebsiteFetcher:
    def __init__(
        self,
        *,
        resolver: DNSResolver | None = None,
        transport: PinnedHTTPTransport | None = None,
        max_pages: int = MAX_PAGES,
        max_redirects: int = MAX_REDIRECTS,
        max_page_bytes: int = MAX_PAGE_BYTES,
    ) -> None:
        self.resolver = resolver or SocketDNSResolver()
        self.transport = transport or StandardPinnedHTTPTransport()
        self.max_pages = min(max_pages, MAX_PAGES)
        self.max_redirects = min(max_redirects, MAX_REDIRECTS)
        self.max_page_bytes = min(max_page_bytes, MAX_PAGE_BYTES)

    def _fetch_one(self, url: str, *, deadline: float) -> tuple[WebsitePage, list[tuple[str, str]]]:
        requested_url = url
        current_url = url
        for redirect_count in range(self.max_redirects + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WebsiteFetchError("Se agotó el presupuesto total de 30 segundos.")
            validated = _canonical_url(
                current_url,
                self.resolver,
                timeout_seconds=remaining,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WebsiteFetchError("Se agotó el presupuesto total de 30 segundos.")
            response = self.transport.get(
                url=validated.url,
                hostname=validated.hostname,
                resolved_ip=validated.addresses[0],
                port=validated.port,
                timeout_seconds=min(10.0, remaining),
                max_bytes=self.max_page_bytes,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise WebsiteFetchError("La redirección no informó destino.")
                if redirect_count >= self.max_redirects:
                    raise WebsiteFetchError("Se excedió el máximo de tres redirecciones.")
                current_url = urljoin(validated.url, location)
                continue
            if response.status_code >= 500:
                raise RetryableWebsiteFetchError("El sitio respondió con un error transitorio.")
            if response.status_code < 200 or response.status_code >= 300:
                raise WebsiteFetchError("El sitio respondió con un estado HTTP no utilizable.")
            media_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
            if media_type not in ALLOWED_CONTENT_TYPES:
                raise WebsiteFetchError("El tipo de contenido no es HTML ni texto.")
            decoded = _decode_body(response.body, response.headers.get("content-type", ""))
            links: list[tuple[str, str]] = []
            mailto_values: list[str] = []
            if media_type == "text/plain":
                text = re.sub(r"\s+", " ", decoded).strip()
                contact_text = text
            else:
                parser = _UsefulHTMLParser()
                parser.feed(decoded)
                text = " ".join(parser.text_parts)
                contact_text = " ".join(parser.contact_text_parts)
                links = parser.links
                mailto_values = parser.mailto_values
            text = re.sub(r"\s+", " ", text).strip()[:MAX_PAGE_TEXT]
            content_hash = hashlib.sha256(response.body).hexdigest()
            page = WebsitePage(
                requested_url=requested_url,
                final_url=validated.url,
                status_code=response.status_code,
                text=text,
                content_type=media_type,
                content_hash=content_hash,
                byte_count=len(response.body),
                email_candidates=_email_candidates(
                    text=contact_text,
                    mailto_values=mailto_values,
                    page_url=validated.url,
                    page_content_hash=content_hash,
                ),
            )
            return page, links
        raise WebsiteFetchError("No se pudo resolver la cadena de redirecciones.")

    def _fetch_with_retry(
        self, url: str, *, deadline: float
    ) -> tuple[WebsitePage, list[tuple[str, str]]]:
        for attempt in range(2):
            try:
                return self._fetch_one(url, deadline=deadline)
            except (OSError, RetryableWebsiteFetchError):
                if attempt == 1:
                    raise
        raise WebsiteFetchError("El sitio agotó el reintento permitido.")

    def fetch(self, request: WebsiteRequest) -> WebsiteResult:
        deadline = time.monotonic() + min(request.timeout_seconds, 30.0)
        try:
            home, links = self._fetch_with_retry(request.url, deadline=deadline)
        except UnsafeWebsiteURLError as exc:
            return WebsiteResult(
                pages=(),
                error=str(exc)[:500],
                error_kind=WebsiteErrorKind.REJECTED,
            )
        except (OSError, ValueError, WebsiteFetchError) as exc:
            return WebsiteResult(
                pages=(),
                error=str(exc)[:500],
                error_kind=WebsiteErrorKind.FETCH_ERROR,
            )
        pages = [home]
        home_host = (urlsplit(home.final_url).hostname or "").casefold()
        root_domain = _registrable_domain(home_host)
        ranked: list[tuple[int, str]] = []
        seen = {home.final_url}
        for href, anchor_text in links:
            candidate = urljoin(home.final_url, href)
            parsed = urlsplit(candidate)
            candidate_host = (parsed.hostname or "").casefold()
            if (
                parsed.scheme not in {"http", "https"}
                or _registrable_domain(candidate_host) != root_domain
            ):
                continue
            haystack = f"{parsed.path} {anchor_text}".casefold()
            score = sum(1 for term in RELEVANT_TERMS if term in haystack)
            if score and candidate not in seen:
                seen.add(candidate)
                ranked.append((score, candidate))
        error: str | None = None
        for _, candidate in sorted(ranked, key=lambda item: (-item[0], item[1])):
            if len(pages) >= self.max_pages:
                break
            try:
                page, _ = self._fetch_with_retry(candidate, deadline=deadline)
            except (OSError, ValueError, WebsiteFetchError) as exc:
                error = str(exc)[:500]
                continue
            pages.append(page)
        remaining = MAX_TOTAL_TEXT
        remaining_email_candidates = MAX_EMAIL_CANDIDATES_TOTAL
        bounded_pages: list[WebsitePage] = []
        for page in pages:
            bounded_text = page.text[:remaining]
            remaining -= len(bounded_text)
            bounded_candidates = page.email_candidates[:remaining_email_candidates]
            remaining_email_candidates -= len(bounded_candidates)
            bounded_pages.append(
                WebsitePage(
                    requested_url=page.requested_url,
                    final_url=page.final_url,
                    status_code=page.status_code,
                    text=bounded_text,
                    content_type=page.content_type,
                    content_hash=page.content_hash,
                    byte_count=page.byte_count,
                    email_candidates=bounded_candidates,
                )
            )
            if remaining <= 0:
                break
        return WebsiteResult(
            pages=tuple(bounded_pages),
            error=error,
            error_kind=WebsiteErrorKind.FETCH_ERROR if error else None,
        )
