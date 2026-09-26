from __future__ import annotations

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.accounts.models import Membership


class LoginSerializer(serializers.Serializer[dict[str, object]]):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class ActivationSerializer(serializers.Serializer[dict[str, object]]):
    token = serializers.CharField(max_length=128, write_only=True, trim_whitespace=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    password_confirmation = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        password = str(attrs["password"])
        confirmation = str(attrs["password_confirmation"])
        if password != confirmation:
            raise serializers.ValidationError(
                {"password_confirmation": "Las contraseñas no coinciden."}
            )
        validate_password(password)
        return attrs


class ReauthenticateSerializer(serializers.Serializer[dict[str, object]]):
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class UserSessionSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    role = serializers.ChoiceField(choices=Membership.Role.choices)
    workspace_id = serializers.UUIDField()
    workspace_name = serializers.CharField()
    capabilities = serializers.ListField(child=serializers.CharField())
    session_expires_at = serializers.DateTimeField()
    reauthentication_active = serializers.BooleanField()


def session_data(
    *,
    user: User,
    session_expires_at: object,
    reauthentication_active: bool,
) -> dict[str, object]:
    membership = user.membership
    from apps.accounts.permissions import VENDEDOR_CAPABILITIES, Capability

    capabilities = [
        str(capability)
        for capability in Capability
        if membership.role == Membership.Role.ADMIN or capability in VENDEDOR_CAPABILITIES
    ]
    return {
        "id": user.pk,
        "username": user.get_username(),
        "email": user.email,
        "role": membership.role,
        "workspace_id": membership.workspace_id,
        "workspace_name": membership.workspace.name,
        "capabilities": capabilities,
        "session_expires_at": session_expires_at,
        "reauthentication_active": reauthentication_active,
    }
