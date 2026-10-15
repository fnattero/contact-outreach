"""Credential encryption (SECURITY.md section 5): confidentiality, integrity and key separation."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import override_settings

from apps.core.crypto import SECRET_CIPHERTEXT_VERSION, decrypt_secret, encrypt_secret

SECRET = "sk-live-1234567890-very-secret"


@pytest.fixture(autouse=True)
def _field_key() -> None:
    with override_settings(FIELD_ENCRYPTION_KEY="k" * 48):
        yield


def test_a_secret_round_trips_and_the_ciphertext_hides_it() -> None:
    ciphertext = encrypt_secret(SECRET, purpose="llm-api-key")

    assert ciphertext.startswith(f"{SECRET_CIPHERTEXT_VERSION}:")
    assert SECRET not in ciphertext
    assert "sk-live" not in ciphertext
    assert decrypt_secret(ciphertext, purpose="llm-api-key") == SECRET


def test_unicode_secrets_survive() -> None:
    ciphertext = encrypt_secret("clave-ñandú-✓", purpose="llm-api-key")

    assert decrypt_secret(ciphertext, purpose="llm-api-key") == "clave-ñandú-✓"


def test_the_same_secret_never_produces_the_same_ciphertext_twice() -> None:
    first = encrypt_secret(SECRET, purpose="llm-api-key")
    second = encrypt_secret(SECRET, purpose="llm-api-key")

    assert first != second


def test_a_ciphertext_only_opens_for_the_purpose_it_was_made_for() -> None:
    ciphertext = encrypt_secret(SECRET, purpose="llm-api-key")

    with pytest.raises(ValidationError):
        decrypt_secret(ciphertext, purpose="google-client-secret")


def test_a_different_root_key_cannot_open_the_ciphertext() -> None:
    ciphertext = encrypt_secret(SECRET, purpose="llm-api-key")

    with override_settings(FIELD_ENCRYPTION_KEY="z" * 48), pytest.raises(ValidationError):
        decrypt_secret(ciphertext, purpose="llm-api-key")


def test_tampering_with_the_ciphertext_is_detected() -> None:
    ciphertext = encrypt_secret(SECRET, purpose="llm-api-key")
    version, _, token = ciphertext.partition(":")
    flipped = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")

    with pytest.raises(ValidationError, match="descifrar"):
        decrypt_secret(f"{version}:{flipped}", purpose="llm-api-key")


@pytest.mark.parametrize(
    "ciphertext",
    ["", "v1", "v1:", ":token", "v2:abc", "plaintext-secret", "v1:not-base64!!!", "v1:ñ"],
)
def test_malformed_ciphertexts_are_refused_without_echoing_them(ciphertext: str) -> None:
    with pytest.raises(ValidationError) as caught:
        decrypt_secret(ciphertext, purpose="llm-api-key")

    assert ciphertext not in " ".join(caught.value.messages) or ciphertext == ""


def test_an_empty_secret_is_never_stored() -> None:
    with pytest.raises(ValidationError, match="vacía"):
        encrypt_secret("", purpose="llm-api-key")


@pytest.mark.parametrize("purpose", ["", "propósito"])
def test_a_missing_or_non_ascii_purpose_is_refused(purpose: str) -> None:
    with pytest.raises(ValueError, match="propósito"):
        encrypt_secret(SECRET, purpose=purpose)


@pytest.mark.parametrize("key", ["", None])
def test_nothing_is_encrypted_or_decrypted_without_the_root_key(key: str | None) -> None:
    ciphertext = encrypt_secret(SECRET, purpose="llm-api-key")

    with override_settings(FIELD_ENCRYPTION_KEY=key):
        with pytest.raises(ImproperlyConfigured, match="FIELD_ENCRYPTION_KEY"):
            encrypt_secret(SECRET, purpose="llm-api-key")
        with pytest.raises(ImproperlyConfigured, match="FIELD_ENCRYPTION_KEY"):
            decrypt_secret(ciphertext, purpose="llm-api-key")
