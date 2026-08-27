from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import membership_for
from apps.accounts.services import (
    ActivationError,
    activate_with_token,
    canonical_client_ip,
    clear_login_pair,
    login_throttle_status,
    record_login_failure,
)
from apps.api.serializers import (
    ActivationSerializer,
    LoginSerializer,
    ReauthenticateSerializer,
    UserSessionSerializer,
    session_data,
)

LOGIN_ERROR = "No se pudo iniciar sesión con esos datos. Intentá nuevamente."
REAUTHENTICATION_LIFETIME = timedelta(minutes=10)
REAUTHENTICATED_AT_KEY = "api_reauthenticated_at"


def _client_ip(request: Request) -> str:
    return canonical_client_ip(cast(dict[str, object], request.META))


def _session_expiry(request: Request) -> datetime:
    expiry = request.session.get_expiry_date()
    return expiry


def _reauthentication_active(request: Request) -> bool:
    value = request.session.get(REAUTHENTICATED_AT_KEY)
    if not isinstance(value, str):
        return False
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return False
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone=timezone.get_current_timezone())
    return timestamp + REAUTHENTICATION_LIFETIME > timezone.now()


class CsrfView(APIView):
    authentication_classes: tuple[Any, ...] = ()
    permission_classes = (AllowAny,)

    def get(self, request: Request) -> Response:
        return Response({"data": {"csrf_token": get_token(request)}})


class LoginView(APIView):
    authentication_classes: tuple[Any, ...] = ()
    permission_classes = (AllowAny,)

    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        username = cast(str, serializer.validated_data["username"])
        client_ip = _client_ip(request)
        throttle = login_throttle_status(username=username, client_ip=client_ip)
        if throttle.locked:
            response = Response(
                {
                    "type": "about:blank",
                    "title": "Demasiadas solicitudes",
                    "status": 429,
                    "code": "login_temporarily_locked",
                    "detail": "No se pudo iniciar sesión con esos datos. Intentá nuevamente.",
                    "correlation_id": getattr(request, "correlation_id", ""),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
                content_type="application/problem+json",
            )
            response["Retry-After"] = str(throttle.retry_after)
            return response

        user = authenticate(
            request,
            username=username,
            password=serializer.validated_data["password"],
        )
        if not isinstance(user, User) or membership_for(user) is None:
            record_login_failure(username=username, client_ip=client_ip)
            return Response(
                {
                    "type": "about:blank",
                    "title": "Autenticación fallida",
                    "status": 401,
                    "code": "invalid_credentials",
                    "detail": LOGIN_ERROR,
                    "correlation_id": getattr(request, "correlation_id", ""),
                },
                status=status.HTTP_401_UNAUTHORIZED,
                content_type="application/problem+json",
            )

        clear_login_pair(username=username, client_ip=client_ip)
        login(request, user)
        request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        request.session["accounts_membership_version"] = str(user.membership.session_version)
        payload = session_data(
            user=user,
            session_expires_at=_session_expiry(request),
            reauthentication_active=False,
        )
        return Response({"data": UserSessionSerializer(payload).data})


class ActivateView(APIView):
    authentication_classes: tuple[Any, ...] = ()
    permission_classes = (AllowAny,)

    def post(self, request: Request) -> Response:
        serializer = ActivationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = activate_with_token(
                raw_token=cast(str, serializer.validated_data["token"]),
                password=cast(str, serializer.validated_data["password"]),
            )
        except ActivationError as exc:
            raise serializers.ValidationError({"token": str(exc.message)}) from exc
        login(request, user)
        request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        request.session["accounts_membership_version"] = str(user.membership.session_version)
        payload = session_data(
            user=user,
            session_expires_at=_session_expiry(request),
            reauthentication_active=False,
        )
        return Response({"data": UserSessionSerializer(payload).data}, status=status.HTTP_200_OK)


class SessionView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request: Request) -> Response:
        user = cast(User, request.user)
        payload = session_data(
            user=user,
            session_expires_at=_session_expiry(request),
            reauthentication_active=_reauthentication_active(request),
        )
        return Response({"data": UserSessionSerializer(payload).data})


class LogoutView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request: Request) -> Response:
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReauthenticateView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request: Request) -> Response:
        serializer = ReauthenticateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = cast(User, request.user)
        password = cast(str, serializer.validated_data["password"])
        if authenticate(request, username=user.get_username(), password=password) is None:
            return Response(
                {
                    "type": "about:blank",
                    "title": "Reautenticación fallida",
                    "status": 401,
                    "code": "invalid_reauthentication",
                    "detail": "La contraseña no es válida.",
                    "correlation_id": getattr(request, "correlation_id", ""),
                },
                status=status.HTTP_401_UNAUTHORIZED,
                content_type="application/problem+json",
            )
        request.session[REAUTHENTICATED_AT_KEY] = timezone.now().isoformat()
        return Response({"data": {"reauthentication_active": True}})
