from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, cast

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Membership


class MembershipSessionMiddleware:
    """Invalidate a session reliably after a role or account-status change."""

    session_key = "accounts_membership_version"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if not request.user.is_authenticated:
            return self.get_response(request)
        try:
            membership = request.user.membership
        except Membership.DoesNotExist:
            logout(request)
            return redirect("login")
        # Resolve the authorization scope once per authenticated request.  Views
        # may still use the typed helpers in ``accounts.permissions``; these
        # attributes make the resolved scope available to middleware and
        # templates without re-deriving it from attribution fields.
        request.membership = membership  # type: ignore[attr-defined]
        request.workspace = membership.workspace  # type: ignore[attr-defined]
        current = str(membership.session_version)
        stored = request.session.get(self.session_key)
        if stored is None:
            request.session[self.session_key] = current
        elif stored != current:
            logout(request)
            return redirect("login")
        return self.get_response(request)


class MFARequiredMiddleware:
    """Require confirmed, per-session OTP for admins and opted-in sellers."""

    allowed_names: ClassVar[set[str]] = {
        "login",
        "logout",
        "account-activate",
        "mfa-enroll",
        "mfa-verify",
        "health",
        "health-live",
    }

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if not getattr(settings, "MFA_ENFORCEMENT_ENABLED", True):
            return self.get_response(request)
        if not request.user.is_authenticated:
            return self.get_response(request)
        try:
            name = resolve(request.path_info).url_name
        except Resolver404:
            name = None
        if name in self.allowed_names:
            return self.get_response(request)

        user = request.user
        confirmed = TOTPDevice.objects.filter(user=user, confirmed=True).exists()
        membership = getattr(user, "membership", None)
        if membership is not None and membership.role == Membership.Role.ADMIN and not confirmed:
            return cast(HttpResponse, redirect("mfa-enroll"))
        verified_method = getattr(user, "is_verified", None)
        verified = bool(verified_method()) if callable(verified_method) else False
        if confirmed and not verified:
            return cast(HttpResponse, redirect("mfa-verify"))
        return self.get_response(request)
