from __future__ import annotations

from typing import cast

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import ActivationToken, Membership
from apps.accounts.services import (
    LastActiveAdminError,
    change_membership_role,
    create_managed_user,
    issue_activation_token,
    set_user_active,
    unlock_login,
)
from apps.api.permissions import ManageUsersPermission, authenticated_user
from apps.api.schema import SchemaAPIView


class ManagedUserCreateSerializer(serializers.Serializer[dict[str, object]]):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=Membership.Role.choices)


class UserRoleSerializer(serializers.Serializer[dict[str, object]]):
    role = serializers.ChoiceField(choices=Membership.Role.choices)


class UserStatusSerializer(serializers.Serializer[dict[str, object]]):
    is_active = serializers.BooleanField()


class LoginUnlockSerializer(serializers.Serializer[dict[str, object]]):
    username = serializers.CharField(max_length=150, required=False, allow_blank=True)
    client_ip = serializers.IPAddressField(required=False, allow_blank=True)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        if not attrs.get("username") and not attrs.get("client_ip"):
            raise serializers.ValidationError("Indicá un usuario o una dirección IP.")
        return attrs


class ManagedUserSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    role = serializers.ChoiceField(choices=Membership.Role.choices)
    is_active = serializers.BooleanField()
    date_joined = serializers.DateTimeField()


def _user_data(membership: Membership) -> dict[str, object]:
    user = membership.user
    return {
        "id": user.pk,
        "username": user.get_username(),
        "email": user.email,
        "role": membership.role,
        "is_active": user.is_active,
        "date_joined": user.date_joined,
    }


def _memberships_for(actor: User) -> QuerySet[Membership]:
    return Membership.objects.filter(workspace=actor.membership.workspace).select_related("user")


def _membership_for(actor: User, user_id: int) -> Membership:
    try:
        return _memberships_for(actor).get(user_id=user_id)
    except Membership.DoesNotExist as exc:
        raise NotFound from exc


def _validation_error(exc: ValidationError) -> serializers.ValidationError:
    if hasattr(exc, "message_dict"):
        return serializers.ValidationError(exc.message_dict)
    return serializers.ValidationError(str(exc))


class UserListView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageUsersPermission)

    def get(self, request: Request) -> Response:
        data = [
            _user_data(membership) for membership in _memberships_for(authenticated_user(request))
        ]
        return Response({"data": [ManagedUserSerializer(item).data for item in data]})

    def post(self, request: Request) -> Response:
        serializer = ManagedUserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user, issued = create_managed_user(
                username=cast(str, serializer.validated_data["username"]),
                email=cast(str, serializer.validated_data["email"]),
                role=cast(str, serializer.validated_data["role"]),
                actor=authenticated_user(request),
            )
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        configured_base = str(getattr(settings, "PUBLIC_BASE_URL", "")).rstrip("/")
        activation_path = "/activate"
        activation_url = f"{configured_base}{activation_path}#token={issued.raw_token}"
        return Response(
            {
                "data": {
                    "user": ManagedUserSerializer(_user_data(user.membership)).data,
                    "activation_url": activation_url,
                    "expires_at": issued.token.expires_at,
                }
            },
            status=status.HTTP_201_CREATED,
        )


class UserRoleView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageUsersPermission)

    def patch(self, request: Request, user_id: int) -> Response:
        serializer = UserRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            membership = change_membership_role(
                membership=_membership_for(authenticated_user(request), user_id),
                role=cast(str, serializer.validated_data["role"]),
                actor=authenticated_user(request),
            )
        except LastActiveAdminError as exc:
            raise _validation_error(exc) from exc
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        return Response({"data": ManagedUserSerializer(_user_data(membership)).data})


class UserStatusView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageUsersPermission)

    def patch(self, request: Request, user_id: int) -> Response:
        serializer = UserStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = _membership_for(authenticated_user(request), user_id)
        try:
            set_user_active(
                membership=membership,
                active=cast(bool, serializer.validated_data["is_active"]),
                actor=authenticated_user(request),
            )
        except LastActiveAdminError as exc:
            raise _validation_error(exc) from exc
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        membership.refresh_from_db(fields=("role",))
        return Response({"data": ManagedUserSerializer(_user_data(membership)).data})


class UserActivationLinkView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageUsersPermission)

    def post(self, request: Request, user_id: int) -> Response:
        actor = authenticated_user(request)
        membership = _membership_for(actor, user_id)
        issued = issue_activation_token(
            user=membership.user,
            actor=actor,
            kind=ActivationToken.Kind.RESET,
        )
        configured_base = str(getattr(settings, "PUBLIC_BASE_URL", "")).rstrip("/")
        return Response(
            {
                "data": {
                    "activation_url": f"{configured_base}/activate#token={issued.raw_token}",
                    "expires_at": issued.token.expires_at,
                }
            }
        )


class UnlockLoginView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageUsersPermission)

    def post(self, request: Request) -> Response:
        serializer = LoginUnlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        count = unlock_login(
            username=cast(str, serializer.validated_data.get("username", "")),
            client_ip=cast(str, serializer.validated_data.get("client_ip", "")),
            actor=authenticated_user(request),
        )
        return Response({"data": {"cleared": count}})
