from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from apps.accounts.models import Membership
from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.observability import redact_text
from apps.audit.services import record_event
from apps.configuration.models import IntegrationConfiguration
from apps.core.crypto import decrypt_secret, encrypt_secret

LLM_KEY_PURPOSE = "integration.llm.api-key"
GMAIL_CLIENT_SECRET_PURPOSE = "integration.gmail.oauth-client-secret"


def _local_http_host(hostname: str, *, allow_service_name: bool) -> bool:
    normalized = hostname.casefold().rstrip(".")
    if normalized in {"localhost", "host.docker.internal"} or normalized.endswith(
        (".localhost", ".test")
    ):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return allow_service_name and "." not in normalized
    return bool(
        (address.is_loopback or address.is_private)
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def validate_integration_base_url(
    value: str,
    *,
    label: str,
    official_hosts: frozenset[str] | None = None,
    allow_http_service_name: bool = False,
) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValidationError(
            f"La URL de {label} no puede contener credenciales, query ni fragmento."
        )
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValidationError(f"La URL de {label} debe ser HTTP o HTTPS válida.")
    if official_hosts is not None:
        if (
            parsed.scheme != "https"
            or hostname not in official_hosts
            or parsed.path not in {"", "/"}
        ):
            raise ValidationError(f"Usá únicamente la URL HTTPS oficial de {label}.")
    elif hostname in {"metadata", "metadata.google.internal"}:
        raise ValidationError(f"La URL de {label} apunta a un host reservado.")
    elif parsed.scheme != "https" and not _local_http_host(
        hostname, allow_service_name=allow_http_service_name
    ):
        raise ValidationError(
            f"La URL de {label} debe usar HTTPS salvo para un servicio local o privado."
        )
    return normalized


@dataclass(frozen=True, slots=True)
class RuntimeIntegrationConfiguration:
    extractor_provider: str
    overture_min_confidence: Decimal
    website_fetcher: str
    llm_provider: str
    llm_model: str
    ollama_base_url: str
    openai_compatible_base_url: str
    llm_credential_source: str
    llm_credential_configured: bool
    gmail_provider: str
    gmail_oauth_client_id: str
    gmail_credential_source: str
    gmail_credential_configured: bool
    revision: int

    def llm_base_url(self, provider_name: str | None = None) -> str:
        selected = provider_name or self.llm_provider
        if selected == IntegrationConfiguration.LLMProvider.OLLAMA:
            return self.ollama_base_url
        if selected == IntegrationConfiguration.LLMProvider.OPENAI_COMPATIBLE:
            return self.openai_compatible_base_url
        return ""


def _configuration(
    owner_id: int | None = None, *, for_update: bool = False
) -> IntegrationConfiguration | None:
    queryset = IntegrationConfiguration.objects.all()
    if for_update:
        queryset = queryset.select_for_update()
    if owner_id is None:
        return None
    workspace_id = (
        Membership.objects.filter(user_id=owner_id).values_list("workspace_id", flat=True).first()
    )
    if workspace_id is None:
        return None
    return queryset.filter(workspace_id=workspace_id).first()


def configured_integration_owner_id() -> int | None:
    value = (
        IntegrationConfiguration.objects.order_by("created_at")
        .values_list("owner_id", flat=True)
        .first()
    )
    return int(value) if value is not None else None


def _secret_status(
    configuration: IntegrationConfiguration | None,
    *,
    source_field: str,
    ciphertext_field: str,
    environment_value: str,
) -> tuple[str, bool]:
    if configuration is None:
        source: str = IntegrationConfiguration.SecretSource.ENVIRONMENT
        return source, bool(environment_value)
    source = str(getattr(configuration, source_field))
    if source == IntegrationConfiguration.SecretSource.ENCRYPTED:
        return source, bool(getattr(configuration, ciphertext_field))
    if source == IntegrationConfiguration.SecretSource.ENVIRONMENT:
        return source, bool(environment_value)
    return IntegrationConfiguration.SecretSource.NONE, False


def runtime_integration_configuration(
    owner_id: int | None = None,
) -> RuntimeIntegrationConfiguration:
    configuration = _configuration(owner_id)
    llm_source, llm_configured = _secret_status(
        configuration,
        source_field="llm_api_key_source",
        ciphertext_field="llm_api_key_encrypted",
        environment_value=settings.LLM_API_KEY,
    )
    gmail_source, gmail_configured = _secret_status(
        configuration,
        source_field="gmail_oauth_client_secret_source",
        ciphertext_field="gmail_oauth_client_secret_encrypted",
        environment_value=settings.GMAIL_OAUTH_CLIENT_SECRET,
    )
    if configuration is None:
        return RuntimeIntegrationConfiguration(
            extractor_provider=IntegrationConfiguration.ExtractorProvider.FAKE,
            overture_min_confidence=Decimal("0.750"),
            website_fetcher=settings.WEBSITE_FETCHER,
            llm_provider=settings.LLM_PROVIDER,
            llm_model=settings.LLM_MODEL,
            ollama_base_url=settings.OLLAMA_BASE_URL,
            openai_compatible_base_url=settings.OPENAI_COMPATIBLE_BASE_URL,
            llm_credential_source=llm_source,
            llm_credential_configured=llm_configured,
            gmail_provider=settings.GMAIL_PROVIDER,
            gmail_oauth_client_id=settings.GMAIL_OAUTH_CLIENT_ID,
            gmail_credential_source=gmail_source,
            gmail_credential_configured=gmail_configured,
            revision=0,
        )
    return RuntimeIntegrationConfiguration(
        extractor_provider=configuration.extractor_provider,
        overture_min_confidence=configuration.overture_min_confidence,
        website_fetcher=configuration.website_fetcher,
        llm_provider=configuration.llm_provider,
        llm_model=configuration.llm_model,
        ollama_base_url=configuration.ollama_base_url,
        openai_compatible_base_url=configuration.openai_compatible_base_url,
        llm_credential_source=llm_source,
        llm_credential_configured=llm_configured,
        gmail_provider=configuration.gmail_provider,
        gmail_oauth_client_id=configuration.gmail_oauth_client_id,
        gmail_credential_source=gmail_source,
        gmail_credential_configured=gmail_configured,
        revision=configuration.revision,
    )


def _resolve_secret(
    owner_id: int | None,
    *,
    source_field: str,
    ciphertext_field: str,
    environment_value: str,
    purpose: str,
) -> str:
    configuration = _configuration(owner_id)
    if configuration is None:
        return environment_value.strip()
    source = getattr(configuration, source_field)
    if source == IntegrationConfiguration.SecretSource.NONE:
        return ""
    if source == IntegrationConfiguration.SecretSource.ENVIRONMENT:
        return environment_value.strip()
    ciphertext = str(getattr(configuration, ciphertext_field))
    if not ciphertext:
        raise ImproperlyConfigured("La credencial cifrada configurada está ausente.")
    return decrypt_secret(ciphertext, purpose=purpose)


def get_llm_api_key(owner_id: int | None = None) -> str:
    return _resolve_secret(
        owner_id,
        source_field="llm_api_key_source",
        ciphertext_field="llm_api_key_encrypted",
        environment_value=settings.LLM_API_KEY,
        purpose=LLM_KEY_PURPOSE,
    )


def get_gmail_oauth_client_secret(owner_id: int | None = None) -> str:
    return _resolve_secret(
        owner_id,
        source_field="gmail_oauth_client_secret_source",
        ciphertext_field="gmail_oauth_client_secret_encrypted",
        environment_value=settings.GMAIL_OAUTH_CLIENT_SECRET,
        purpose=GMAIL_CLIENT_SECRET_PURPOSE,
    )


def integration_configuration_initial(owner_id: int) -> dict[str, object]:
    runtime = runtime_integration_configuration(owner_id)
    return {
        "extractor_provider": runtime.extractor_provider,
        "overture_min_confidence": runtime.overture_min_confidence,
        "website_fetcher": runtime.website_fetcher,
        "llm_provider": runtime.llm_provider,
        "llm_model": runtime.llm_model,
        "ollama_base_url": runtime.ollama_base_url,
        "openai_compatible_base_url": runtime.openai_compatible_base_url,
        "gmail_provider": runtime.gmail_provider,
        "gmail_oauth_client_id": runtime.gmail_oauth_client_id,
    }


def _safe_snapshot(runtime: RuntimeIntegrationConfiguration) -> dict[str, object]:
    return {
        "extractor_provider": runtime.extractor_provider,
        "overture_min_confidence": str(runtime.overture_min_confidence),
        "website_fetcher": runtime.website_fetcher,
        "llm_provider": runtime.llm_provider,
        "llm_model": runtime.llm_model,
        "ollama_base_url": runtime.ollama_base_url,
        "openai_compatible_base_url": runtime.openai_compatible_base_url,
        "llm_credential": "configured" if runtime.llm_credential_configured else "missing",
        "gmail_provider": runtime.gmail_provider,
        "gmail_client_id_configured": bool(runtime.gmail_oauth_client_id),
        "gmail_credential": "configured" if runtime.gmail_credential_configured else "missing",
        "revision": runtime.revision,
    }


@sensitive_variables("value", "ciphertext")
def _set_secret(
    configuration: IntegrationConfiguration,
    *,
    value: str,
    remove: bool,
    source_field: str,
    ciphertext_field: str,
    purpose: str,
) -> None:
    if value and remove:
        raise ValidationError("No se puede reemplazar y eliminar la misma credencial.")
    if value:
        try:
            ciphertext = encrypt_secret(value.strip(), purpose=purpose)
        except ImproperlyConfigured as exc:
            raise ValidationError(str(exc)) from exc
        setattr(configuration, ciphertext_field, ciphertext)
        setattr(configuration, source_field, IntegrationConfiguration.SecretSource.ENCRYPTED)
    elif remove:
        setattr(configuration, ciphertext_field, "")
        setattr(configuration, source_field, IntegrationConfiguration.SecretSource.NONE)


def _candidate_secret_configured(
    configuration: IntegrationConfiguration,
    *,
    source_field: str,
    ciphertext_field: str,
    environment_value: str,
) -> bool:
    source = getattr(configuration, source_field)
    if source == IntegrationConfiguration.SecretSource.ENCRYPTED:
        return bool(getattr(configuration, ciphertext_field))
    if source == IntegrationConfiguration.SecretSource.ENVIRONMENT:
        return bool(environment_value)
    return False


@transaction.atomic
@sensitive_variables("values")
def save_integration_configuration(
    *, owner: User, values: dict[str, Any]
) -> IntegrationConfiguration:
    membership = require_user_capability(owner, Capability.MANAGE_INTEGRATIONS)
    current_runtime = runtime_integration_configuration(owner.pk)
    configuration = _configuration(owner.pk, for_update=True)
    if configuration is None:
        configuration = IntegrationConfiguration(
            owner=owner,
            workspace=membership.workspace,
            **integration_configuration_initial(owner.pk),
        )

    from apps.mailbox.models import GmailConnection

    existing_connection = (
        GmailConnection.objects.select_for_update().filter(workspace=membership.workspace).first()
    )
    gmail_credentials_change = bool(
        values.get("gmail_provider") != current_runtime.gmail_provider
        or values.get("gmail_oauth_client_id", "").strip() != current_runtime.gmail_oauth_client_id
        or values.get("gmail_oauth_client_secret")
        or values.get("remove_gmail_oauth_client_secret")
    )
    if (
        gmail_credentials_change
        and existing_connection is not None
        and existing_connection.refresh_token_encrypted
    ):
        raise ValidationError(
            "Desconectá Gmail antes de cambiar sus credenciales de autorización con Google."
        )

    editable_fields = (
        "extractor_provider",
        "overture_min_confidence",
        "website_fetcher",
        "llm_provider",
        "llm_model",
        "gmail_provider",
        "gmail_oauth_client_id",
    )
    for field in editable_fields:
        setattr(configuration, field, values[field])
    configuration.ollama_base_url = validate_integration_base_url(
        values["ollama_base_url"],
        label="Ollama",
        allow_http_service_name=True,
    )
    openai_base_url = values["openai_compatible_base_url"]
    configuration.openai_compatible_base_url = (
        validate_integration_base_url(openai_base_url, label="OpenAI compatible")
        if openai_base_url
        else ""
    )
    _set_secret(
        configuration,
        value=values.get("llm_api_key", ""),
        remove=bool(values.get("remove_llm_api_key")),
        source_field="llm_api_key_source",
        ciphertext_field="llm_api_key_encrypted",
        purpose=LLM_KEY_PURPOSE,
    )
    _set_secret(
        configuration,
        value=values.get("gmail_oauth_client_secret", ""),
        remove=bool(values.get("remove_gmail_oauth_client_secret")),
        source_field="gmail_oauth_client_secret_source",
        ciphertext_field="gmail_oauth_client_secret_encrypted",
        purpose=GMAIL_CLIENT_SECRET_PURPOSE,
    )

    if configuration.llm_provider == IntegrationConfiguration.LLMProvider.OPENAI_COMPATIBLE:
        configured = _candidate_secret_configured(
            configuration,
            source_field="llm_api_key_source",
            ciphertext_field="llm_api_key_encrypted",
            environment_value=settings.LLM_API_KEY,
        )
        if not configured:
            raise ValidationError(
                "El proveedor compatible con OpenAI requiere una clave de acceso."
            )
    if configuration.gmail_provider == IntegrationConfiguration.GmailProvider.API:
        configured = _candidate_secret_configured(
            configuration,
            source_field="gmail_oauth_client_secret_source",
            ciphertext_field="gmail_oauth_client_secret_encrypted",
            environment_value=settings.GMAIL_OAUTH_CLIENT_SECRET,
        )
        if not configuration.gmail_oauth_client_id.strip() or not configured:
            raise ValidationError(
                "Google Gmail requiere el identificador y el secreto de cliente "
                "para la autorización con Google."
            )

    configuration.revision = current_runtime.revision + 1
    configuration.full_clean()
    configuration.save()
    updated_runtime = runtime_integration_configuration(owner.pk)
    record_event(
        action="integration_configuration.saved",
        entity=configuration,
        actor=owner,
        before=_safe_snapshot(current_runtime),
        after=_safe_snapshot(updated_runtime),
    )
    return configuration


@sensitive_variables("secret")
def redact_provider_error(error: object, *, owner_id: int | None = None) -> str:
    message = " ".join(str(error).split()) or error.__class__.__name__
    getters: tuple[Callable[[int | None], str], ...] = (
        get_llm_api_key,
        get_gmail_oauth_client_secret,
    )
    for getter in getters:
        try:
            secret = getter(owner_id)
        except Exception:
            continue
        if secret:
            message = message.replace(secret, "[REDACTED]")
    message = re.sub(
        r"(?i)\b(?:refresh|access)\s+token\s+"
        r"(revocado|revocada|revoked|vencido|vencida|expired)\b",
        r"credencial \1",
        message,
    )
    return redact_text(message)[:500]


def validate_encrypted_integration_credentials() -> list[str]:
    failures: list[str] = []
    checks = (
        ("llm_api_key_source", "llm_api_key_encrypted", LLM_KEY_PURPOSE, "LLM"),
        (
            "gmail_oauth_client_secret_source",
            "gmail_oauth_client_secret_encrypted",
            GMAIL_CLIENT_SECRET_PURPOSE,
            "Google OAuth",
        ),
    )
    for configuration in IntegrationConfiguration.objects.all():
        for source_field, ciphertext_field, purpose, label in checks:
            if (
                getattr(configuration, source_field)
                != IntegrationConfiguration.SecretSource.ENCRYPTED
            ):
                continue
            try:
                decrypt_secret(getattr(configuration, ciphertext_field), purpose=purpose)
            except Exception:
                failures.append(f"credencial {label} de {configuration.pk} no descifrable")
    return failures
