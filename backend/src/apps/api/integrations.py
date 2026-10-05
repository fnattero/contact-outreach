from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied as ApiPermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.api.auth import _reauthentication_active
from apps.api.errors import raise_domain_error
from apps.api.permissions import ManageIntegrationsPermission, authenticated_user
from apps.api.schema import SchemaAPIView
from apps.configuration.integrations import (
    integration_configuration_initial,
    runtime_integration_configuration,
    save_integration_configuration,
)
from apps.configuration.models import IntegrationConfiguration
from apps.mailbox.models import GmailConnection


def _status_data(request: Request) -> dict[str, object]:
    user = authenticated_user(request)
    runtime = runtime_integration_configuration(user.pk)
    connection = GmailConnection.objects.filter(workspace=user.membership.workspace).first()
    return {
        "extractor": {
            "provider": runtime.extractor_provider,
            "overture_min_confidence": str(runtime.overture_min_confidence),
        },
        "website_fetcher": {"provider": runtime.website_fetcher},
        "llm": {
            "provider": runtime.llm_provider,
            "model": runtime.llm_model,
            "credential_source": runtime.llm_credential_source,
            "configured": runtime.llm_credential_configured,
        },
        "embeddings": {
            "provider": runtime.embedding_provider,
            "model": runtime.embedding_model,
            "dimensions": runtime.embedding_dimensions,
        },
        "gmail": {
            "provider": runtime.gmail_provider,
            "oauth_client_id_configured": bool(runtime.gmail_oauth_client_id),
            "credential_source": runtime.gmail_credential_source,
            "credential_configured": runtime.gmail_credential_configured,
            "connection_status": (
                connection.status if connection else GmailConnection.Status.DISCONNECTED
            ),
            "email": connection.email if connection else None,
        },
        "revision": runtime.revision,
    }


class IntegrationStatusView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def get(self, request: Request) -> Response:
        return Response({"data": _status_data(request)})


class IntegrationConfigurationInputSerializer(serializers.Serializer[dict[str, Any]]):
    """Editable integration settings. Every field is optional: an omitted field is unchanged.

    The base URLs are plain strings on purpose: ``validate_integration_base_url`` in the service
    is the single authority on what counts as a safe URL, so it is not duplicated here. The two
    credential fields are write-only and are never echoed back.
    """

    extractor_provider = serializers.ChoiceField(
        choices=IntegrationConfiguration.ExtractorProvider.choices
    )
    overture_min_confidence = serializers.DecimalField(
        max_digits=4, decimal_places=3, min_value=Decimal(0), max_value=Decimal(1)
    )
    website_fetcher = serializers.ChoiceField(
        choices=IntegrationConfiguration.WebsiteFetcher.choices
    )
    llm_provider = serializers.ChoiceField(choices=IntegrationConfiguration.LLMProvider.choices)
    llm_model = serializers.CharField(max_length=120)
    ollama_base_url = serializers.CharField(max_length=500)
    openai_compatible_base_url = serializers.CharField(max_length=500, allow_blank=True)
    llm_api_key = serializers.CharField(max_length=4096, allow_blank=True, write_only=True)
    remove_llm_api_key = serializers.BooleanField()
    embedding_provider = serializers.ChoiceField(
        choices=IntegrationConfiguration.EmbeddingProvider.choices
    )
    embedding_model = serializers.CharField(max_length=120)
    embedding_dimensions = serializers.IntegerField(min_value=64, max_value=3072)
    gmail_provider = serializers.ChoiceField(choices=IntegrationConfiguration.GmailProvider.choices)
    gmail_oauth_client_id = serializers.CharField(max_length=500, allow_blank=True)
    gmail_oauth_client_secret = serializers.CharField(
        max_length=4096, allow_blank=True, write_only=True
    )
    remove_gmail_oauth_client_secret = serializers.BooleanField()


def _configuration_data(request: Request) -> dict[str, object]:
    runtime = runtime_integration_configuration(authenticated_user(request).pk)
    return {
        "extractor_provider": runtime.extractor_provider,
        "overture_min_confidence": str(runtime.overture_min_confidence),
        "website_fetcher": runtime.website_fetcher,
        "llm_provider": runtime.llm_provider,
        "llm_model": runtime.llm_model,
        "ollama_base_url": runtime.ollama_base_url,
        "openai_compatible_base_url": runtime.openai_compatible_base_url,
        "embedding_provider": runtime.embedding_provider,
        "embedding_model": runtime.embedding_model,
        "embedding_dimensions": runtime.embedding_dimensions,
        "gmail_provider": runtime.gmail_provider,
        "gmail_oauth_client_id": runtime.gmail_oauth_client_id,
        # Credentials are reported only as state; their values can never be read back.
        "llm_credential": {
            "configured": runtime.llm_credential_configured,
            "source": runtime.llm_credential_source,
        },
        "gmail_credential": {
            "configured": runtime.gmail_credential_configured,
            "source": runtime.gmail_credential_source,
        },
        "revision": runtime.revision,
    }


class IntegrationConfigurationView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def get(self, request: Request) -> Response:
        return Response({"data": _configuration_data(request)})

    def patch(self, request: Request) -> Response:
        # Changing providers or credentials redirects outbound traffic and secrets, so a stolen
        # session must not be enough: the password has to have been re-entered recently.
        if not _reauthentication_active(request):
            raise ApiPermissionDenied(
                "Volvé a ingresar tu contraseña antes de cambiar integraciones."
            )
        serializer = IntegrationConfigurationInputSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = authenticated_user(request)
        # The service reads an omitted key as a change, so start from the current settings.
        values: dict[str, Any] = dict(integration_configuration_initial(user.pk))
        values.update(serializer.validated_data)
        try:
            save_integration_configuration(owner=user, values=values)
        except (ValidationError, PermissionDenied) as exc:
            raise_domain_error(exc)
        return Response({"data": _configuration_data(request)})
