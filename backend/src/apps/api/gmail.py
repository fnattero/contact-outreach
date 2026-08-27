from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from typing import cast

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import ManageIntegrationsPermission, authenticated_user
from apps.integrations.contracts import ProviderError
from apps.mailbox.models import GmailConnection
from apps.mailbox.services import (
    authorization_url,
    connect_gmail,
    disconnect_gmail,
    oauth_material,
    oauth_redirect_uri,
    test_gmail_connection,
)

OAUTH_STATE_SESSION_KEY = "api_gmail_oauth_state"
OAUTH_VERIFIER_SESSION_KEY = "api_gmail_oauth_verifier"
OAUTH_STARTED_AT_SESSION_KEY = "api_gmail_oauth_started_at"
OAUTH_STATE_LIFETIME = timedelta(minutes=10)


def _connection_data(connection: GmailConnection | None) -> dict[str, object]:
    if connection is None:
        return {
            "connected": False,
            "status": GmailConnection.Status.DISCONNECTED,
            "email": None,
            "scopes": [],
            "last_tested_at": None,
            "error": None,
        }
    return {
        "connected": connection.status == GmailConnection.Status.CONNECTED,
        "status": connection.status,
        "email": connection.email or None,
        "scopes": connection.scopes,
        "last_tested_at": connection.last_tested_at.isoformat()
        if connection.last_tested_at
        else None,
        "error": connection.error or None,
    }


def _redirect_uri(request: Request) -> str:
    return oauth_redirect_uri(request.build_absolute_uri(reverse("api-gmail-oauth-callback")))


class GmailConnectionView(APIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def get(self, request: Request) -> Response:
        connection = GmailConnection.objects.filter(
            workspace=authenticated_user(request).membership.workspace
        ).first()
        return Response({"data": _connection_data(connection)})


class GmailOAuthStartView(APIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def post(self, request: Request) -> Response:
        owner = authenticated_user(request)
        state, verifier, challenge = oauth_material()
        request.session[OAUTH_STATE_SESSION_KEY] = state
        request.session[OAUTH_VERIFIER_SESSION_KEY] = verifier
        request.session[OAUTH_STARTED_AT_SESSION_KEY] = timezone.now().isoformat()
        try:
            url = authorization_url(
                owner=owner,
                state=state,
                verifier=verifier,
                challenge=challenge,
                redirect_uri=_redirect_uri(request),
            )
        except (ValidationError, ProviderError, ValueError) as exc:
            request.session.pop(OAUTH_STATE_SESSION_KEY, None)
            request.session.pop(OAUTH_VERIFIER_SESSION_KEY, None)
            request.session.pop(OAUTH_STARTED_AT_SESSION_KEY, None)
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": {"authorization_url": url}})


class GmailOAuthCallbackView(APIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def get(self, request: Request) -> HttpResponseRedirect:
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        expected_state = request.session.pop(OAUTH_STATE_SESSION_KEY, "")
        verifier = request.session.pop(OAUTH_VERIFIER_SESSION_KEY, "")
        started_at_value = request.session.pop(OAUTH_STARTED_AT_SESSION_KEY, "")
        started_at: datetime | None = None
        if isinstance(started_at_value, str):
            try:
                started_at = datetime.fromisoformat(started_at_value)
            except ValueError:
                started_at = None
        if started_at is not None and timezone.is_naive(started_at):
            started_at = timezone.make_aware(started_at, timezone=timezone.get_current_timezone())
        state_is_fresh = (
            started_at is not None and started_at + OAUTH_STATE_LIFETIME > timezone.now()
        )
        if (
            not state
            or not hmac.compare_digest(state, str(expected_state))
            or not code
            or not verifier
            or not state_is_fresh
        ):
            return HttpResponseRedirect("/settings/integrations?gmail=oauth_failed")
        try:
            connect_gmail(
                owner=cast(User, request.user),
                code=code,
                verifier=str(verifier),
                redirect_uri=_redirect_uri(request),
            )
        except (ValidationError, ProviderError):
            return HttpResponseRedirect("/settings/integrations?gmail=oauth_failed")
        return HttpResponseRedirect("/settings/integrations?gmail=connected")


class GmailTestView(APIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def post(self, request: Request) -> Response:
        owner = cast(User, request.user)
        try:
            connection = test_gmail_connection(owner=owner)
        except (GmailConnection.DoesNotExist, ValidationError, ProviderError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(
            {"data": {"status": "tested", "email": connection.email}},
            status=status.HTTP_200_OK,
        )


class GmailDisconnectView(APIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def post(self, request: Request) -> Response:
        try:
            connection = disconnect_gmail(owner=authenticated_user(request))
        except (GmailConnection.DoesNotExist, ValidationError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _connection_data(connection)})
