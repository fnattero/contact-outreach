from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.api.permissions import ManageIntegrationsPermission, authenticated_user
from apps.api.schema import SchemaAPIView
from apps.configuration.integrations import runtime_integration_configuration
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
            "connection_status": connection.status if connection else "NOT_CONNECTED",
            "email": connection.email if connection else None,
        },
        "revision": runtime.revision,
    }


class IntegrationStatusView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def get(self, request: Request) -> Response:
        return Response({"data": _status_data(request)})
