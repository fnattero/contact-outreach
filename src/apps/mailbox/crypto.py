from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError


def _fernet() -> Fernet:
    secret = settings.FIELD_ENCRYPTION_KEY
    if not secret:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY es obligatoria para guardar OAuth.")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_token(token: str) -> str:
    if not token:
        raise ValidationError("Google no devolvió la credencial necesaria para renovar el acceso.")
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise ValidationError("No se pudo descifrar la credencial de Gmail.") from exc
