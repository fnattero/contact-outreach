from __future__ import annotations

from apps.automation.candidates import (
    MAX_CANDIDATES,
    extract_literal_candidates,
    extract_mailto_literals,
)
from apps.automation.models import EmailCandidate


def test_literal_candidates_are_normalized_deduplicated_and_region_labelled() -> None:
    text = (
        "Mandalo a Compras@ejemplo.com.\n"
        "También a compras@ejemplo.com\n"
        "Saludos,\n"
        "Persona <persona@firma.com>\n"
        "> El mensaje anterior decía viejo@citado.com"
    )

    candidates = extract_literal_candidates(text)

    assert [(item.normalized, item.region) for item in candidates] == [
        ("compras@ejemplo.com", EmailCandidate.Region.NEW_CONTENT),
        ("persona@firma.com", EmailCandidate.Region.SIGNATURE),
        ("viejo@citado.com", EmailCandidate.Region.QUOTED),
    ]


def test_literal_candidates_support_idn_and_mailto_without_reconstructing_obfuscation() -> None:
    candidates = extract_literal_candidates(
        "Escribí a ventas@mañana.com; no a nombre arroba ejemplo punto com.",
        mailto_literals=(("info@ejemplo.com", EmailCandidate.Region.NEW_CONTENT),),
    )

    assert [item.normalized for item in candidates] == [
        "ventas@xn--maana-pta.com",
        "info@ejemplo.com",
    ]
    assert candidates[1].source == EmailCandidate.Source.MAILTO


def test_literal_candidate_extraction_is_bounded_to_ten() -> None:
    text = " ".join(f"persona{index}@example.com" for index in range(20))

    candidates = extract_literal_candidates(text)

    assert len(candidates) == MAX_CANDIDATES
    assert candidates[-1].normalized == "persona9@example.com"


def test_mailto_literals_are_extracted_from_html_and_labelled_by_region() -> None:
    html = (
        '<p>Enviá la propuesta a <a href="mailto:Compras%40ejemplo.com?subject=Propuesta">'
        "esta dirección</a>.</p>"
        '<p>Saludos,</p><a href="mailto:persona@firma.com">Persona</a>'
        '<blockquote><a href="mailto:viejo@citado.com">correo anterior</a></blockquote>'
        '<script><a href="mailto:no@ejemplo.com">ignorado</a></script>'
    )

    assert extract_mailto_literals(html) == (
        ("Compras@ejemplo.com", EmailCandidate.Region.NEW_CONTENT),
        ("persona@firma.com", EmailCandidate.Region.SIGNATURE),
        ("viejo@citado.com", EmailCandidate.Region.QUOTED),
    )


def test_mailto_extraction_ignores_non_mailto_links_and_malformed_html() -> None:
    html = '<a href="https://example.com/a@b.com">web</a><a href="mailto:ventas@example.com">'

    assert extract_mailto_literals(html) == (
        ("ventas@example.com", EmailCandidate.Region.NEW_CONTENT),
    )
