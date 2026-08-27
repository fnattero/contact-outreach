from __future__ import annotations

from apps.integrations.domains import registrable_domain_from_hostname


def test_registrable_domain_strips_trailing_dot_and_casefolds() -> None:
    assert registrable_domain_from_hostname("Servicios.Business.COM.") == "business.com"


def test_registrable_domain_of_empty_hostname_is_empty() -> None:
    assert registrable_domain_from_hostname("") == ""
    assert registrable_domain_from_hostname(".") == ""
