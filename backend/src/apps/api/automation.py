from __future__ import annotations

from typing import cast

from django.core.exceptions import PermissionDenied, ValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied as ApiPermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.api.auth import _reauthentication_active
from apps.api.errors import raise_domain_error
from apps.api.permissions import (
    ManageAutomationPermission,
    ManageIntegrationsPermission,
    authenticated_user,
)
from apps.api.schema import SchemaAPIView
from apps.automation.models import ReplyAutomationConfiguration
from apps.automation.services import set_live_mode, set_non_live_mode
from apps.configuration.send_mode import (
    disable_live_sending,
    enable_live_sending,
    send_mode_state,
)
from apps.configuration.services import runtime_prompt_configuration, save_automatic_reply_prompt


class AutomationModeSerializer(serializers.Serializer[dict[str, object]]):
    mode = serializers.ChoiceField(choices=ReplyAutomationConfiguration.Mode.choices)


class AutomationConfigurationSerializer(serializers.Serializer[dict[str, object]]):
    mode = serializers.CharField()
    mode_label = serializers.CharField()
    policy_version = serializers.CharField()
    live_enabled_at = serializers.DateTimeField(allow_null=True)
    live_enabled_by = serializers.CharField(allow_null=True)


LIVE_CONFIRMATION_WORD = "CONFIRMAR"


class LiveConfirmationSerializer(serializers.Serializer[dict[str, object]]):
    confirmation = serializers.CharField(max_length=40)

    def validate_confirmation(self, value: str) -> str:
        if value != LIVE_CONFIRMATION_WORD:
            raise serializers.ValidationError(f"Escribí {LIVE_CONFIRMATION_WORD} para confirmar.")
        return value


class WritingInstructionsSerializer(serializers.Serializer[dict[str, object]]):
    automatic_reply_prompt = serializers.CharField(max_length=4000)


def _configuration_data(configuration: ReplyAutomationConfiguration) -> dict[str, object]:
    return {
        "mode": configuration.mode,
        "mode_label": configuration.get_mode_display(),
        "policy_version": configuration.policy_version,
        "live_enabled_at": configuration.live_enabled_at,
        "live_enabled_by": (
            configuration.live_enabled_by.get_username()
            if configuration.live_enabled_by_id and configuration.live_enabled_by
            else None
        ),
    }


class AutomationConfigurationView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageAutomationPermission)

    def get(self, request: Request) -> Response:
        workspace = authenticated_user(request).membership.workspace
        # A read must not write: a workspace without a row reports the defaults (SHADOW) and the
        # row is created by the first mode change.
        configuration = ReplyAutomationConfiguration.objects.filter(
            workspace=workspace
        ).first() or ReplyAutomationConfiguration(workspace=workspace)
        return Response(
            {"data": AutomationConfigurationSerializer(_configuration_data(configuration)).data}
        )

    def patch(self, request: Request) -> Response:
        serializer = AutomationModeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mode = cast(str, serializer.validated_data["mode"])
        if mode == ReplyAutomationConfiguration.Mode.LIVE:
            raise serializers.ValidationError(
                {"mode": "Usá la acción de activación para habilitar LIVE."}
            )
        try:
            configuration = set_non_live_mode(
                workspace=authenticated_user(request).membership.workspace,
                actor=authenticated_user(request),
                mode=mode,
            )
        except (PermissionDenied, ValidationError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(
            {"data": AutomationConfigurationSerializer(_configuration_data(configuration)).data}
        )


class AutomationLiveActionView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageAutomationPermission)

    def post(self, request: Request, action: str) -> Response:
        actor = authenticated_user(request)
        if action not in {"enable-live", "disable-live"}:
            raise serializers.ValidationError({"action": "La acción no existe."})
        workspace = actor.membership.workspace
        try:
            if action == "enable-live":
                if not _reauthentication_active(request):
                    raise ApiPermissionDenied(
                        "Volvé a ingresar tu contraseña antes de activar respuestas."
                    )
                LiveConfirmationSerializer(data=request.data).is_valid(raise_exception=True)
                configuration = set_live_mode(
                    workspace=workspace,
                    actor=actor,
                    reauthenticated=True,
                )
            else:
                configuration = set_non_live_mode(
                    workspace=workspace,
                    actor=actor,
                    mode=ReplyAutomationConfiguration.Mode.SHADOW,
                )
        except (PermissionDenied, ValidationError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(
            {"data": AutomationConfigurationSerializer(_configuration_data(configuration)).data},
            status=status.HTTP_200_OK,
        )


class WritingInstructionsView(SchemaAPIView):
    # The approved automatic-reply prompt is administrative configuration: reading it needs the
    # same capability as editing it. Without this, any authenticated seller could read it.
    permission_classes = (IsAuthenticated, ManageAutomationPermission)

    def get(self, request: Request) -> Response:
        runtime = runtime_prompt_configuration(authenticated_user(request).pk)
        return Response({"data": {"automatic_reply_prompt": runtime.automatic_reply_prompt}})

    def patch(self, request: Request) -> Response:
        serializer = WritingInstructionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            saved = save_automatic_reply_prompt(
                owner=authenticated_user(request),
                automatic_reply_prompt=cast(
                    str, serializer.validated_data["automatic_reply_prompt"]
                ),
            )
        except (PermissionDenied, ValidationError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": {"automatic_reply_prompt": saved.automatic_reply_prompt}})


def _send_mode_data() -> dict[str, object]:
    state = send_mode_state()
    return {
        "server_allows_live": state.server_allows_live,
        "app_enabled": state.app_enabled,
        "effective_live": state.effective_live,
        "enabled_by": state.enabled_by,
    }


class SendModeView(SchemaAPIView):
    """Whether real email may leave the app. Reading it is part of operating the app."""

    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def get(self, request: Request) -> Response:
        return Response({"data": _send_mode_data()})


class SendModeActionView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def post(self, request: Request, action: str) -> Response:
        actor = authenticated_user(request)
        if action not in {"enable-live", "disable-live"}:
            raise serializers.ValidationError({"action": "La acción no existe."})
        try:
            if action == "enable-live":
                if not _reauthentication_active(request):
                    raise ApiPermissionDenied(
                        "Volvé a ingresar tu contraseña antes de activar los envíos reales."
                    )
                LiveConfirmationSerializer(data=request.data).is_valid(raise_exception=True)
                enable_live_sending(actor=actor, reauthenticated=True)
            else:
                disable_live_sending(actor=actor)
        except (PermissionDenied, ValidationError) as exc:
            raise_domain_error(exc)
        return Response({"data": _send_mode_data()}, status=status.HTTP_200_OK)
