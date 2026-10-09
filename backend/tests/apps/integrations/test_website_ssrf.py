"""SSRF, redirect, pinning and transport hardening of the website fetcher (SECURITY.md 9)."""

from __future__ import annotations

import io
from typing import Any

import dns.exception
import dns.resolver
import pytest

from apps.integrations import website
from apps.integrations.contracts import WebsiteErrorKind, WebsiteRequest, WebsiteResult
from apps.integrations.website import (
    HttpWebsiteFetcher,
    PinnedHTTPResponse,
    SocketDNSResolver,
    StandardPinnedHTTPTransport,
    WebsiteFetchError,
)

PUBLIC_IP = "93.184.216.34"


class Resolver:
    """Answers from a table and records every lookup, so tests can prove nothing resolved twice."""

    def __init__(self, table: dict[str, tuple[str, ...]] | None = None) -> None:
        self.table = table or {}
        self.lookups: list[str] = []

    def resolve(self, hostname: str, *, timeout_seconds: float) -> tuple[str, ...]:
        del timeout_seconds
        self.lookups.append(hostname)
        return self.table.get(hostname, (PUBLIC_IP,))


class Transport:
    """Serves queued responses and records what the fetcher was allowed to contact."""

    def __init__(self, *responses: PinnedHTTPResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, **kwargs: Any) -> PinnedHTTPResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _html(body: str = "<main>Reparaciones</main>") -> PinnedHTTPResponse:
    return PinnedHTTPResponse(200, {"content-type": "text/html; charset=utf-8"}, body.encode())


def _redirect(location: str | None, status: int = 302) -> PinnedHTTPResponse:
    headers = {"location": location} if location is not None else {}
    return PinnedHTTPResponse(status, headers, b"")


def _fetch(
    url: str, *responses: PinnedHTTPResponse, resolver: Resolver | None = None
) -> tuple[WebsiteResult, Transport, Resolver]:
    resolver = resolver or Resolver()
    transport = Transport(*responses)
    fetcher = HttpWebsiteFetcher(resolver=resolver, transport=transport)
    result = fetcher.fetch(WebsiteRequest(url=url, correlation_id="ssrf-test"))
    return result, transport, resolver


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://public.example/",
        "gopher://public.example/",
        "javascript:alert(1)",
        "data:text/html,<script>1</script>",
        "//public.example/",
        "public.example",
        "",
    ],
)
def test_non_http_schemes_are_rejected_before_any_lookup_or_request(url: str) -> None:
    result, transport, resolver = _fetch(url)

    assert result.pages == ()
    assert result.error_kind is WebsiteErrorKind.REJECTED
    assert transport.calls == []
    assert resolver.lookups == []


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@public.example/",
        "https://user@public.example/",
        "https://:secret@public.example/",
        "https://public.example/#fragment",
        "https:///path-without-host",
        "https://public.example:notaport/",
        "https://public.example:99999/",
        "https://[::1/",
    ],
)
def test_credentials_fragments_and_malformed_authorities_are_rejected(url: str) -> None:
    result, transport, _ = _fetch(url)

    assert result.error_kind is WebsiteErrorKind.REJECTED
    assert transport.calls == []


@pytest.mark.parametrize("port", [21, 22, 25, 3306, 5432, 6379, 8000, 8080, 9000, 9200])
def test_only_the_standard_web_ports_are_allowed(port: int) -> None:
    result, transport, _ = _fetch(f"http://public.example:{port}/")

    assert result.error_kind is WebsiteErrorKind.REJECTED
    assert "puerto" in (result.error or "").lower()
    assert transport.calls == []


@pytest.mark.parametrize("url", ["http://public.example:80/", "https://public.example:443/"])
def test_explicit_standard_ports_are_accepted_and_normalised(url: str) -> None:
    result, transport, _ = _fetch(url, _html())

    assert result.error is None
    assert transport.calls[0]["port"] in {80, 443}
    assert ":80/" not in transport.calls[0]["url"]
    assert ":443/" not in transport.calls[0]["url"]


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "localhost.",
        "api.localhost",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "service.internal",
        "printer.local",
        "router.home",
        "nas.lan",
        "metadata.evil.example",
        "169.254.169.254",
    ],
)
def test_internal_and_metadata_hostnames_are_blocked_without_resolving(host: str) -> None:
    result, transport, resolver = _fetch(f"http://{host}/latest/meta-data/")

    assert result.error_kind is WebsiteErrorKind.REJECTED
    assert "SSRF" in (result.error or "")
    assert transport.calls == []
    assert resolver.lookups == []


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.0.1",
        "192.168.255.255",
        "169.254.1.1",
        "100.64.0.1",
        "100.127.255.254",
        "0.0.0.0",
        "224.0.0.1",
        "239.255.255.255",
        "240.0.0.1",
        "255.255.255.255",
        "192.0.2.1",
        "[::1]",
        "[::]",
        "[fe80::1]",
        "[fc00::1]",
        "[fd12:3456::1]",
        "[ff02::1]",
        "[::ffff:127.0.0.1]",
        "[::ffff:10.0.0.1]",
        "[::ffff:169.254.169.254]",
    ],
)
def test_literal_non_public_addresses_are_rejected(address: str) -> None:
    result, transport, resolver = _fetch(f"http://{address}/admin")

    assert result.error_kind is WebsiteErrorKind.REJECTED
    assert transport.calls == []
    assert resolver.lookups == []


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "[2606:4700:4700::1111]"])
def test_literal_public_addresses_are_allowed_and_pinned_without_a_lookup(address: str) -> None:
    result, transport, resolver = _fetch(f"https://{address}/", _html())

    assert result.error is None
    assert resolver.lookups == []
    assert transport.calls[0]["resolved_ip"] == address.strip("[]")


@pytest.mark.parametrize(
    "answers",
    [
        ("93.184.216.34", "127.0.0.1"),
        ("93.184.216.34", "10.0.0.5"),
        ("2606:4700:4700::1111", "::1"),
        ("93.184.216.34", "::ffff:192.168.1.1"),
        ("not-an-ip",),
        (),
    ],
)
def test_one_bad_or_missing_dns_answer_rejects_the_whole_host(answers: tuple[str, ...]) -> None:
    resolver = Resolver({"mixed.example": answers})

    result, transport, _ = _fetch("https://mixed.example/", resolver=resolver)

    assert result.error_kind is WebsiteErrorKind.REJECTED
    assert transport.calls == []


def test_the_validated_address_is_pinned_and_dns_is_not_asked_again() -> None:
    resolver = Resolver({"shop.example": ("93.184.216.34", "93.184.216.35")})

    result, transport, resolver = _fetch("https://shop.example/", _html(), resolver=resolver)

    assert result.error is None
    assert resolver.lookups == ["shop.example"]
    call = transport.calls[0]
    assert call["resolved_ip"] == "93.184.216.34"
    assert call["hostname"] == "shop.example"
    assert call["max_bytes"] == website.MAX_PAGE_BYTES


def test_each_redirect_hop_is_resolved_and_validated_again() -> None:
    resolver = Resolver({"a.example": ("93.184.216.34",), "b.example": ("93.184.216.35",)})

    result, transport, resolver = _fetch(
        "https://a.example/",
        _redirect("https://b.example/landing"),
        _html(),
        resolver=resolver,
    )

    assert result.error is None
    assert resolver.lookups == ["a.example", "b.example"]
    assert [call["resolved_ip"] for call in transport.calls] == ["93.184.216.34", "93.184.216.35"]
    assert result.pages[0].requested_url == "https://a.example/"
    assert result.pages[0].final_url == "https://b.example/landing"


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/admin",
        "file:///etc/passwd",
        "ftp://public.example/",
        "https://user:pass@public.example/",
        "https://public.example:6379/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "//169.254.169.254/",
    ],
)
def test_a_redirect_into_a_forbidden_target_is_refused_after_the_first_hop(location: str) -> None:
    result, transport, _ = _fetch("https://public.example/", _redirect(location))

    assert result.pages == ()
    assert result.error_kind is WebsiteErrorKind.REJECTED
    assert len(transport.calls) == 1


def test_a_redirect_to_a_hostname_that_resolves_privately_is_refused() -> None:
    resolver = Resolver({"public.example": (PUBLIC_IP,), "rebind.example": ("10.1.2.3",)})

    result, transport, _ = _fetch(
        "https://public.example/", _redirect("https://rebind.example/"), resolver=resolver
    )

    assert result.error_kind is WebsiteErrorKind.REJECTED
    assert len(transport.calls) == 1


def test_a_relative_redirect_stays_on_the_validated_host() -> None:
    result, transport, resolver = _fetch(
        "https://public.example/old", _redirect("/new?x=1", status=301), _html()
    )

    assert result.error is None
    assert transport.calls[1]["url"] == "https://public.example/new?x=1"
    assert resolver.lookups == ["public.example", "public.example"]


def test_a_redirect_loop_stops_after_three_redirects() -> None:
    result, transport, _ = _fetch(
        "https://public.example/",
        *[_redirect("/again") for _ in range(10)],
    )

    assert result.pages == ()
    assert result.error_kind is WebsiteErrorKind.FETCH_ERROR
    assert "redirecciones" in (result.error or "")
    assert len(transport.calls) == website.MAX_REDIRECTS + 1


def test_a_redirect_without_a_destination_is_an_error() -> None:
    result, _, _ = _fetch("https://public.example/", _redirect(None))

    assert result.error_kind is WebsiteErrorKind.FETCH_ERROR
    assert "destino" in (result.error or "")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_every_redirect_status_is_followed_through_validation(status: int) -> None:
    result, transport, _ = _fetch(
        "https://public.example/", _redirect("http://10.0.0.9/", status=status)
    )

    assert result.error_kind is WebsiteErrorKind.REJECTED
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "content_type",
    [
        "application/pdf",
        "application/octet-stream",
        "application/json",
        "application/javascript",
        "image/svg+xml",
        "image/png",
        "text/css",
        "",
    ],
)
def test_binary_and_active_content_types_are_not_read(content_type: str) -> None:
    result, _, _ = _fetch(
        "https://public.example/",
        PinnedHTTPResponse(200, {"content-type": content_type}, b"<script>alert(1)</script>"),
    )

    assert result.pages == ()
    assert result.error_kind is WebsiteErrorKind.FETCH_ERROR
    assert "tipo de contenido" in (result.error or "")


@pytest.mark.parametrize("status", [100, 400, 401, 403, 404, 410, 429])
def test_non_success_statuses_yield_no_page(status: int) -> None:
    result, transport, _ = _fetch(
        "https://public.example/",
        PinnedHTTPResponse(status, {"content-type": "text/html"}, b"<main>nope</main>"),
    )

    assert result.pages == ()
    assert result.error_kind is WebsiteErrorKind.FETCH_ERROR
    assert len(transport.calls) == 1


def test_text_plain_pages_are_accepted_and_hashed() -> None:
    result, _, _ = _fetch(
        "https://public.example/",
        PinnedHTTPResponse(200, {"content-type": "text/plain; charset=latin-1"}, b"Caf\xe9  shop"),
    )

    assert result.pages[0].text == "Café shop"
    assert len(result.pages[0].content_hash) == 64


def test_an_unknown_charset_falls_back_to_utf8_instead_of_crashing() -> None:
    result, _, _ = _fetch(
        "https://public.example/",
        PinnedHTTPResponse(200, {"content-type": "text/html; charset=nonsense"}, b"<p>hola</p>"),
    )

    assert result.pages[0].text == "hola"


def test_links_to_other_sites_and_non_web_schemes_are_never_followed() -> None:
    home = """
    <a href="https://other-site.example/servicios">Servicios</a>
    <a href="javascript:alert(1)">Contacto</a>
    <a href="mailto:ventas@public.example">Contacto</a>
    <a href="ftp://public.example/servicios">Servicios</a>
    <a href="http://127.0.0.1/servicios">Servicios</a>
    <a href="/servicios">Servicios</a>
    """
    result, transport, _ = _fetch("https://public.example/", _html(home), _html("<main>ok</main>"))

    assert [call["url"] for call in transport.calls] == [
        "https://public.example/",
        "https://public.example/servicios",
    ]
    assert len(result.pages) == 2


def test_a_followed_page_that_turns_out_to_be_private_is_skipped_not_fatal() -> None:
    home = '<a href="https://shop.public.com/servicios">Servicios</a>'
    resolver = Resolver({"public.com": (PUBLIC_IP,), "shop.public.com": ("10.0.0.7",)})

    result, transport, _ = _fetch("https://public.com/", _html(home), resolver=resolver)

    assert len(result.pages) == 1
    assert result.error_kind is WebsiteErrorKind.FETCH_ERROR
    assert len(transport.calls) == 1


def test_a_failing_extra_page_is_reported_but_keeps_the_home_page() -> None:
    home = '<a href="/servicios">Servicios</a>'
    result, _, _ = _fetch(
        "https://public.example/",
        _html(home),
        PinnedHTTPResponse(404, {"content-type": "text/html"}, b""),
    )

    assert len(result.pages) == 1
    assert result.error_kind is WebsiteErrorKind.FETCH_ERROR


def test_an_unreachable_site_is_reported_as_a_fetch_error() -> None:
    class Broken:
        def get(self, **kwargs: Any) -> PinnedHTTPResponse:
            raise OSError("connection refused")

    fetcher = HttpWebsiteFetcher(resolver=Resolver(), transport=Broken())

    result = fetcher.fetch(WebsiteRequest(url="https://public.example/", correlation_id="x"))

    assert result.pages == ()
    assert result.error_kind is WebsiteErrorKind.FETCH_ERROR
    assert "connection refused" in (result.error or "")


def test_the_total_time_budget_is_enforced_before_any_request() -> None:
    transport = Transport()
    fetcher = HttpWebsiteFetcher(resolver=Resolver(), transport=transport)

    result = fetcher.fetch(
        WebsiteRequest(url="https://public.example/", correlation_id="x", timeout_seconds=-1.0)
    )

    assert result.pages == ()
    assert "presupuesto" in (result.error or "")
    assert transport.calls == []


def test_the_budget_expiring_during_dns_stops_before_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(website.time, "monotonic", lambda: next(clock))
    transport = Transport()
    fetcher = HttpWebsiteFetcher(resolver=Resolver(), transport=transport)

    result = fetcher.fetch(WebsiteRequest(url="https://public.example/", correlation_id="x"))

    assert "presupuesto" in (result.error or "")
    assert transport.calls == []


def test_configured_limits_can_only_be_tightened_never_loosened() -> None:
    fetcher = HttpWebsiteFetcher(
        resolver=Resolver(),
        transport=Transport(),
        max_pages=99,
        max_redirects=99,
        max_page_bytes=10**9,
    )

    assert fetcher.max_pages == website.MAX_PAGES
    assert fetcher.max_redirects == website.MAX_REDIRECTS
    assert fetcher.max_page_bytes == website.MAX_PAGE_BYTES


def test_total_text_is_capped_across_all_pages() -> None:
    big = "palabra " * 4000
    home = f'<main>{big}</main><a href="/servicios">Servicios</a><a href="/productos">Productos</a>'
    page = f"<main>{big}</main>"
    result, _, _ = _fetch("https://public.example/", _html(home), _html(page), _html(page))

    assert sum(len(item.text) for item in result.pages) <= website.MAX_TOTAL_TEXT
    assert all(len(item.text) <= website.MAX_PAGE_TEXT for item in result.pages)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("::ffff:8.8.8.8", True),
        ("100.63.255.255", True),
        ("100.128.0.1", True),
        ("127.0.0.1", False),
        ("100.64.0.0", False),
        ("::ffff:127.0.0.1", False),
        ("not an address", False),
        ("", False),
    ],
)
def test_public_address_classifier(value: str, expected: bool) -> None:
    assert website._is_public_address(value) is expected


class _FakeAnswers:
    def __init__(self, addresses: list[str]) -> None:
        self._addresses = addresses

    def addresses(self) -> list[str]:
        return self._addresses


class _FakeDNS:
    def __init__(self, outcome: Exception | list[str]) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, float]] = []

    def resolve_name(self, hostname: str, *, family: int, lifetime: float) -> _FakeAnswers:
        del family
        self.calls.append((hostname, lifetime))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return _FakeAnswers(self.outcome)


def test_dns_answers_are_deduplicated_and_sorted() -> None:
    dns_client = _FakeDNS(["93.184.216.35", "93.184.216.34", "93.184.216.35"])
    resolver = SocketDNSResolver(dns_client)  # type: ignore[arg-type]

    assert resolver.resolve("shop.example", timeout_seconds=3.0) == (
        "93.184.216.34",
        "93.184.216.35",
    )
    assert dns_client.calls == [("shop.example", 3.0)]


def test_dns_lifetime_never_reaches_zero() -> None:
    dns_client = _FakeDNS(["93.184.216.34"])

    SocketDNSResolver(dns_client).resolve("shop.example", timeout_seconds=0)  # type: ignore[arg-type]

    assert dns_client.calls[0][1] > 0


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (dns.exception.Timeout(), "tiempo"),
        (dns.resolver.NXDOMAIN(), "A/AAAA"),
        (dns.resolver.NoAnswer(), "A/AAAA"),
        (dns.exception.DNSException("boom"), "temporalmente"),
    ],
)
def test_dns_failures_become_plain_os_errors(error: Exception, fragment: str) -> None:
    resolver = SocketDNSResolver(_FakeDNS(error))  # type: ignore[arg-type]

    with pytest.raises(OSError, match=fragment):
        resolver.resolve("shop.example", timeout_seconds=1.0)


def test_the_default_resolver_is_built_lazily_without_touching_the_network() -> None:
    assert isinstance(SocketDNSResolver().resolver, dns.resolver.Resolver)


class _FakeSocket:
    def __init__(self, raw_response: bytes) -> None:
        self.raw_response = raw_response
        self.sent = bytearray()
        self.timeouts: list[float] = []
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def makefile(self, *args: Any, **kwargs: Any) -> io.BytesIO:
        return io.BytesIO(self.raw_response)

    def close(self) -> None:
        self.closed = True


def _raw(body: bytes = b"<p>ok</p>", *, headers: str = "", status: str = "200 OK") -> bytes:
    head = f"HTTP/1.1 {status}\r\nContent-Type: text/html\r\n{headers}\r\n"
    return head.encode() + body


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replaces the socket layer so the real transport runs with no network at all."""

    state: dict[str, Any] = {"connections": [], "wrapped": []}

    def create_connection(address: tuple[str, int], timeout: float) -> _FakeSocket:
        sock = _FakeSocket(state["response"])
        state["connections"].append((address, timeout, sock))
        return sock

    class _Context:
        def wrap_socket(self, sock: _FakeSocket, *, server_hostname: str) -> _FakeSocket:
            state["wrapped"].append(server_hostname)
            return sock

    monkeypatch.setattr(website.socket, "create_connection", create_connection)
    monkeypatch.setattr(website.ssl, "create_default_context", lambda: _Context())
    state["response"] = _raw()
    return state


def _get(
    *, url: str = "https://shop.example/a?b=1", hostname: str = "shop.example", port: int = 443
) -> PinnedHTTPResponse:
    return StandardPinnedHTTPTransport().get(
        url=url,
        hostname=hostname,
        resolved_ip="93.184.216.34",
        port=port,
        timeout_seconds=30.0,
        max_bytes=1000,
    )


def test_the_real_transport_connects_to_the_pinned_ip_and_verifies_the_hostname(
    wire: dict[str, Any],
) -> None:
    response = _get()

    (address, timeout, sock), *_ = wire["connections"]
    assert address == ("93.184.216.34", 443)
    assert timeout == 5.0
    assert wire["wrapped"] == ["shop.example"]
    sent = bytes(sock.sent).decode()
    assert sent.startswith("GET /a?b=1 HTTP/1.1\r\n")
    assert "Host: shop.example\r\n" in sent
    assert "Connection: close" in sent
    assert response.status_code == 200
    assert response.body == b"<p>ok</p>"
    assert response.headers["content-type"] == "text/html"


def test_the_real_transport_does_not_wrap_plain_http_and_keeps_a_custom_port_in_host(
    wire: dict[str, Any],
) -> None:
    _get(url="http://shop.example:80/", hostname="shop.example", port=80)
    assert wire["wrapped"] == []

    _get(url="http://shop.example:8080/", hostname="shop.example", port=8080)
    sent = bytes(wire["connections"][1][2].sent).decode()
    assert "Host: shop.example:8080\r\n" in sent


def test_the_real_transport_closes_its_connection_even_on_failure(wire: dict[str, Any]) -> None:
    wire["response"] = b"not http at all"

    with pytest.raises(Exception):  # noqa: B017 - any protocol error must still clean up
        _get()

    assert wire["connections"][0][2].closed


def test_a_declared_content_length_over_the_limit_is_refused_before_reading(
    wire: dict[str, Any],
) -> None:
    wire["response"] = _raw(b"x", headers="Content-Length: 5000\r\n")

    with pytest.raises(WebsiteFetchError, match="2 MiB"):
        _get()


def test_a_body_that_lies_about_its_length_is_still_cut_off(wire: dict[str, Any]) -> None:
    wire["response"] = _raw(b"x" * 5000)

    with pytest.raises(WebsiteFetchError, match="2 MiB"):
        _get()


def test_a_body_exactly_at_the_limit_is_accepted(wire: dict[str, Any]) -> None:
    wire["response"] = _raw(b"x" * 1000)

    assert len(_get().body) == 1000
