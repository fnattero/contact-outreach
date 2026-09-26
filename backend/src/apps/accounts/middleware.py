from __future__ import annotations

from collections.abc import Callable

from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponseBase
from django.shortcuts import redirect

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
