"""Copy validation and grounded-evidence assembly for prospects.

This module no longer drafts outreach copy. The LLM-backed `analyze_prospect` path — which
scored relevance and wrote a subject and body in one call — was removed; initial campaign
messages are fixed and human-approved. See `docs/ASSUMPTIONS.md` (A-059) for the scoring
capability that was lost with it and the plan to rebuild it without drafting.

What remains is used by the current flow:

- `validate_operator_message` guards copy a human edited, via `apps.campaigns.review`.
- `build_analysis_facts` assembles grounded, literal facts from a prospect and its website
  snapshot. It calls no provider and is kept as the starting point for standalone relevance
  scoring.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from apps.integrations.contracts import AnalysisFact, ValidationProviderError
from apps.prospects.models import Prospect, WebsiteSnapshot

CTA = "¿Qué día conviene que pase el vendedor?"
OPERATOR_MIN_WORDS = 60
MAX_MESSAGE_WORDS = 130
WORD_RE = re.compile(r"\b[\wÁÉÍÓÚÜÑáéíóúüñ]+\b", flags=re.UNICODE)
HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
FORBIDDEN_COPY = (
    "descuento",
    "oferta imperdible",
    "garantizamos",
    "vimos su sitio",
    "visitamos su web",
    "seguro necesitan",
    "stock permanente",
    "entrega inmediata",
)
UNSUPPORTED_ASSERTION_RE = re.compile(
    r"\b(?:sabemos que|confirmamos que|nos consta que|"
    r"ustedes? (?:compran|usan|necesitan|tienen)|"
    r"su empresa (?:compra|usa|necesita|tiene))\b",
    flags=re.IGNORECASE,
)


def _safe_value(value: object, *, limit: int = 20_000) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def build_analysis_facts(prospect: Prospect, snapshot: WebsiteSnapshot) -> tuple[AnalysisFact, ...]:
    """Collect literal, attributable facts about a prospect. Calls no provider."""

    candidates = (
        ("prospect.name", prospect.name),
        ("prospect.category", prospect.category),
        ("prospect.address", prospect.address),
        ("prospect.neighborhood", prospect.neighborhood),
        ("prospect.website", prospect.website),
        ("prospect.phone", prospect.phone),
    )
    facts = [
        AnalysisFact(fact_id=fact_id, value=_safe_value(value))
        for fact_id, value in candidates
        if value
    ]
    for index, page in enumerate(snapshot.pages[:4], start=1):
        excerpt = page.get("excerpt") if isinstance(page, dict) else None
        if excerpt:
            facts.append(AnalysisFact(fact_id=f"web.page.{index}", value=_safe_value(excerpt)))
    return tuple(facts)


def signature_block(profile: dict[str, Any]) -> str:
    signature = _safe_value(profile.get("signature", ""), limit=500)
    company = _safe_value(profile.get("company_name", ""), limit=200)
    address = _safe_value(profile.get("address", ""), limit=300)
    return f"{signature}\n{company} · {address}"


def _contains_emoji(value: str) -> bool:
    return any(unicodedata.category(character) in {"So", "Sk"} for character in value)


def validate_operator_message(
    *,
    subject: str,
    body_text: str,
    profile: dict[str, Any],
    minimum_words: int = OPERATOR_MIN_WORDS,
) -> tuple[str, str]:
    """Validate operator-edited copy before it can be persisted or approved."""

    normalized_subject = re.sub(r"\s+", " ", subject).strip()
    if (
        not normalized_subject
        or "\n" in subject
        or len(normalized_subject) > 255
        or normalized_subject.casefold().startswith("publicidad")
    ):
        raise ValidationProviderError("El asunto no es válido.")
    # Browsers submit textarea line endings as CRLF. Persist one canonical form so
    # an unchanged visible signature is not rejected solely because of transport syntax.
    body = body_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body or len(body) > 4000 or HTML_RE.search(body) or "\x00" in body:
        raise ValidationProviderError("El mensaje final debe ser texto plano válido.")
    if not body.endswith(signature_block(profile)):
        raise ValidationProviderError(
            "El mensaje debe conservar la firma e identidad configuradas."
        )
    normalized = body.casefold()
    if any(phrase in normalized for phrase in FORBIDDEN_COPY):
        raise ValidationProviderError("El mensaje contiene una afirmación o recurso prohibido.")
    if UNSUPPORTED_ASSERTION_RE.search(normalized) or "%" in normalized:
        raise ValidationProviderError("El mensaje afirma un hecho del prospecto no permitido.")
    if _contains_emoji(f"{normalized_subject} {body}"):
        raise ValidationProviderError("El mensaje final no puede contener emojis.")
    if body.count(CTA) != 1 or body.count("?") != 1:
        raise ValidationProviderError(
            "El mensaje final debe incluir únicamente el CTA obligatorio."
        )
    word_count = len(WORD_RE.findall(body))
    if word_count < minimum_words or word_count > MAX_MESSAGE_WORDS:
        raise ValidationProviderError(
            f"El mensaje final debe tener entre {minimum_words} y {MAX_MESSAGE_WORDS} palabras; "
            f"tiene {word_count}."
        )
    return normalized_subject, body
