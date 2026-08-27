from __future__ import annotations

import re
import unicodedata

_NON_TOKEN = re.compile(r"[^a-z0-9]+")
_TAXONOMY_CODE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def normalize_search_text(value: str) -> str:
    """Return accent-insensitive lowercase tokens separated by one space."""

    decomposed = unicodedata.normalize("NFKD", value)
    ascii_like = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()
    return " ".join(part for part in _NON_TOKEN.split(ascii_like) if part)


def padded_name_search(value: str) -> str:
    normalized = normalize_search_text(value)
    return f" {normalized} " if normalized else ""


def normalize_taxonomy_code(value: str) -> str:
    normalized = value.strip().casefold()
    return normalized if _TAXONOMY_CODE.fullmatch(normalized) else ""


def taxonomy_codes_search(codes: tuple[str, ...] | list[str]) -> str:
    normalized = sorted(
        {candidate for raw_code in codes if (candidate := normalize_taxonomy_code(raw_code))}
    )
    return f"|{'|'.join(normalized)}|" if normalized else ""
