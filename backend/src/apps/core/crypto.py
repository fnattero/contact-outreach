from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.views.decorators.debug import sensitive_variables

SECRET_CIPHERTEXT_VERSION = "v1"


@sensitive_variables()
def _fernet(*, purpose: str) -> Fernet:
    root_secret = settings.FIELD_ENCRYPTION_KEY
    if not root_secret:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY es obligatoria para guardar credenciales cifradas."
        )
    if not purpose or not purpose.isascii():
        raise ValueError("El propósito de cifrado no es válido.")
    key = hmac.new(
        root_secret.encode("utf-8"),
        f"contact-outreach:{SECRET_CIPHERTEXT_VERSION}:{purpose}".encode(),
        hashlib.sha256,
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key))


@sensitive_variables()
def encrypt_secret(value: str, *, purpose: str) -> str:
    if not value:
        raise ValidationError("La credencial no puede estar vacía.")
    token = _fernet(purpose=purpose).encrypt(value.encode("utf-8")).decode("ascii")
    return f"{SECRET_CIPHERTEXT_VERSION}:{token}"


@sensitive_variables()
def decrypt_secret(ciphertext: str, *, purpose: str) -> str:
    version, separator, token = ciphertext.partition(":")
    if separator != ":" or version != SECRET_CIPHERTEXT_VERSION or not token:
        raise ValidationError("La credencial cifrada no tiene un formato válido.")
    try:
        return _fernet(purpose=purpose).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise ValidationError("No se pudo descifrar la credencial.") from exc
