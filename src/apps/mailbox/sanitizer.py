from __future__ import annotations

import html
import re
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset(
    {"blockquote", "br", "code", "div", "em", "li", "ol", "p", "pre", "span", "strong", "ul"}
)
BLOCKED_WITH_CONTENT = frozenset({"form", "iframe", "object", "script", "style", "svg"})
BLOCK_TAGS = frozenset({"blockquote", "br", "div", "li", "ol", "p", "pre", "ul"})
MAX_BODY_CHARS = 200_000


class _EmailHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.text: list[str] = []
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in BLOCKED_WITH_CONTENT:
            self._blocked_depth += 1
            return
        if self._blocked_depth:
            return
        if normalized in ALLOWED_TAGS:
            self.output.append(f"<{normalized}>")
        if normalized in BLOCK_TAGS:
            self.text.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in BLOCKED_WITH_CONTENT:
            self._blocked_depth = max(0, self._blocked_depth - 1)
            return
        if self._blocked_depth:
            return
        if normalized in ALLOWED_TAGS and normalized != "br":
            self.output.append(f"</{normalized}>")
        if normalized in BLOCK_TAGS:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._blocked_depth:
            return
        bounded = data[:MAX_BODY_CHARS]
        self.output.append(html.escape(bounded, quote=False))
        self.text.append(bounded)


def _readable_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()[:MAX_BODY_CHARS]


def sanitize_email_bodies(*, body_text: str, body_html: str) -> tuple[str, str]:
    sanitizer = _EmailHTMLSanitizer()
    sanitizer.feed(body_html[:MAX_BODY_CHARS])
    sanitizer.close()
    sanitized_html = "".join(sanitizer.output)[:MAX_BODY_CHARS]
    readable = _readable_text(body_text)
    if not readable:
        readable = _readable_text("".join(sanitizer.text))
    return readable, sanitized_html
