from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from django.db import transaction
from email_validator import EmailNotValidError, validate_email

from apps.automation.models import EmailCandidate
from apps.mailbox.models import InboundMessage

MAX_CANDIDATES = 10
MAX_HTML_CHARS = 200_000
_EMAIL_LITERAL = re.compile(
    r"(?<![\w@])"
    r"[\w.!#$%&'*+/=?^`{|}~-]{1,64}"
    r"@"
    r"(?:[^\s<>()[\],;:\\]+\.)+[^\s<>()[\],;:\\]{2,63}"
    r"(?![\w@])",
    re.UNICODE,
)
_QUOTE_MARKER = re.compile(
    r"^(?:>+\s?|On .+ wrote:\s*$|El .+ escribi[oó]:\s*$|De:\s|From:\s)",
    re.IGNORECASE,
)
_SIGNATURE_MARKER = re.compile(
    r"^(?:--\s*$|saludos[,.!]?\s*$|atentamente[,.!]?\s*$|cordialmente[,.!]?\s*$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class InboundRegions:
    new_content: str
    signature: str
    quoted: str
    new_end: int
    signature_end: int

    def region_at(self, offset: int) -> str:
        if offset < self.new_end:
            return EmailCandidate.Region.NEW_CONTENT
        if offset < self.signature_end:
            return EmailCandidate.Region.SIGNATURE
        return EmailCandidate.Region.QUOTED


@dataclass(frozen=True, slots=True)
class LiteralCandidate:
    original: str
    normalized: str
    region: str
    source: str
    start_offset: int | None
    end_offset: int | None


class _MailtoLiteralParser(HTMLParser):
    """Collect literal mailto targets and enough text to classify their region."""

    _BLOCK_TAGS = frozenset({"blockquote", "br", "div", "li", "ol", "p", "pre", "ul"})
    _BLOCKED_TAGS = frozenset({"form", "iframe", "object", "script", "style", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.targets: list[tuple[str, int]] = []
        self._text_length = 0
        self._blocked_depth = 0

    def _append_text(self, value: str) -> None:
        self.text.append(value)
        self._text_length += len(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized in self._BLOCKED_TAGS:
            self._blocked_depth += 1
            return
        if self._blocked_depth:
            return
        if normalized == "blockquote":
            self._append_text("\n> ")
        elif normalized in self._BLOCK_TAGS:
            self._append_text("\n")
        if normalized != "a" or len(self.targets) >= MAX_CANDIDATES:
            return
        href = next((value for key, value in attrs if key.casefold() == "href"), None)
        if href is None or not href.casefold().startswith("mailto:"):
            return
        parsed = urlsplit(href)
        # A mailto URI can explicitly contain more than one comma-separated
        # mailbox. They are still literal values; no obfuscated address is rebuilt.
        for literal in unquote(parsed.path).split(","):
            value = literal.strip()
            if value:
                self.targets.append((value, self._text_length))
            if len(self.targets) >= MAX_CANDIDATES:
                break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self._BLOCKED_TAGS:
            self._blocked_depth = max(0, self._blocked_depth - 1)
            return
        if not self._blocked_depth and normalized in self._BLOCK_TAGS:
            self._append_text("\n")

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self._append_text(data)


def extract_mailto_literals(body_html: str) -> tuple[tuple[str, str], ...]:
    """Return literal mailto targets labelled by authored/signature/quoted region."""

    parser = _MailtoLiteralParser()
    try:
        parser.feed(body_html[:MAX_HTML_CHARS])
        parser.close()
    except (AssertionError, ValueError):
        # Malformed email HTML must never interrupt mailbox synchronization.
        return ()
    # Keep a link occurring at the current end of an otherwise-authored body in
    # NEW_CONTENT; region_at intentionally treats exact section boundaries as
    # belonging to the following section.
    regions = split_inbound_regions("".join(parser.text) + "\n ")
    return tuple((literal, regions.region_at(offset)) for literal, offset in parser.targets)


def split_inbound_regions(text: str) -> InboundRegions:
    """Conservatively split authored content, signature and quoted history."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    signature_start: int | None = None
    quote_start: int | None = None
    offset = 0
    for line in lines:
        content = line.rstrip("\n")
        if quote_start is None and _QUOTE_MARKER.match(content):
            quote_start = offset
            break
        if signature_start is None and _SIGNATURE_MARKER.match(content.strip()):
            signature_start = offset
        offset += len(line)
    if quote_start is None:
        quote_start = len(normalized)
    if signature_start is None or signature_start > quote_start:
        signature_start = quote_start
    return InboundRegions(
        new_content=normalized[:signature_start].strip(),
        signature=normalized[signature_start:quote_start].strip(),
        quoted=normalized[quote_start:].strip(),
        new_end=signature_start,
        signature_end=quote_start,
    )


def _normalize_literal(value: str) -> str | None:
    candidate = value.strip().strip(".,;:!?()[]{}<>\"'")
    try:
        result = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        return None
    domain = result.ascii_domain.casefold() if result.ascii_domain else result.domain.casefold()
    return f"{result.local_part.casefold()}@{domain}"


def extract_literal_candidates(
    text: str,
    *,
    mailto_literals: tuple[tuple[str, str], ...] = (),
) -> tuple[LiteralCandidate, ...]:
    """Extract only literal addresses; never reconstruct obfuscated forms."""

    regions = split_inbound_regions(text)
    values: list[LiteralCandidate] = []
    seen: set[tuple[str, str]] = set()
    for match in _EMAIL_LITERAL.finditer(text):
        normalized = _normalize_literal(match.group(0))
        if normalized is None:
            continue
        region = regions.region_at(match.start())
        key = (normalized, region)
        if key in seen:
            continue
        seen.add(key)
        values.append(
            LiteralCandidate(
                original=match.group(0),
                normalized=normalized,
                region=region,
                source=EmailCandidate.Source.TEXT,
                start_offset=match.start(),
                end_offset=match.end(),
            )
        )
        if len(values) == MAX_CANDIDATES:
            return tuple(values)
    for literal, region in mailto_literals:
        if region not in EmailCandidate.Region.values:
            continue
        normalized = _normalize_literal(literal)
        if normalized is None or (normalized, region) in seen:
            continue
        seen.add((normalized, region))
        values.append(
            LiteralCandidate(
                original=literal,
                normalized=normalized,
                region=region,
                source=EmailCandidate.Source.MAILTO,
                start_offset=None,
                end_offset=None,
            )
        )
        if len(values) == MAX_CANDIDATES:
            break
    return tuple(values)


@transaction.atomic
def persist_email_candidates(
    inbound: InboundMessage,
    *,
    mailto_literals: tuple[tuple[str, str], ...] = (),
) -> tuple[EmailCandidate, ...]:
    values = extract_literal_candidates(inbound.body_text, mailto_literals=mailto_literals)
    persisted_ids = list(
        EmailCandidate.objects.filter(inbound=inbound)
        .order_by("created_at")
        .values_list("pk", flat=True)[:MAX_CANDIDATES]
    )
    for value in values:
        if len(persisted_ids) >= MAX_CANDIDATES:
            break
        evidence = "|".join(
            (
                str(inbound.pk),
                value.original,
                value.normalized,
                value.region,
                value.source,
                str(value.start_offset),
                str(value.end_offset),
            )
        )
        candidate, _ = EmailCandidate.objects.get_or_create(
            inbound=inbound,
            normalized_email=value.normalized,
            region=value.region,
            defaults={
                "original_literal": value.original,
                "source": value.source,
                "start_offset": value.start_offset,
                "end_offset": value.end_offset,
                "syntax_state": EmailCandidate.CheckState.VALID,
                "evidence_hash": sha256(evidence.encode()).hexdigest(),
            },
        )
        if candidate.pk not in persisted_ids:
            persisted_ids.append(candidate.pk)
    by_id = {
        candidate.pk: candidate for candidate in EmailCandidate.objects.filter(pk__in=persisted_ids)
    }
    return tuple(by_id[candidate_id] for candidate_id in persisted_ids)
