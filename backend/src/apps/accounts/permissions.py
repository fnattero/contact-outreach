from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseBase

from apps.accounts.models import Membership, Workspace


class Capability(StrEnum):
    VIEW_SUMMARY = "view_summary"
    VIEW_CAMPAIGNS = "view_campaigns"
    VIEW_SENT_MESSAGES = "view_sent_messages"
    VIEW_CONTACTS = "view_contacts"
    MANAGE_USERS = "manage_users"
    MANAGE_CAMPAIGNS = "manage_campaigns"
    APPROVE_CAMPAIGNS = "approve_campaigns"
    SEND_REPLIES = "send_replies"
    MANAGE_CONTACTS = "manage_contacts"
    MANAGE_KNOWLEDGE = "manage_knowledge"
    MANAGE_AUTOMATION = "manage_automation"
    MANAGE_CONFIGURATION = "manage_configuration"
    MANAGE_INTEGRATIONS = "manage_integrations"
    DOWNLOAD_PDFS = "download_pdfs"
    EXPORT_DATA = "export_data"
    VIEW_JOBS = "view_jobs"
    VIEW_AUDIT = "view_audit"


VENDEDOR_CAPABILITIES = frozenset(
    {
        Capability.VIEW_SUMMARY,
        Capability.VIEW_CAMPAIGNS,
        Capability.VIEW_SENT_MESSAGES,
        Capability.VIEW_CONTACTS,
    }
)


def membership_for(user: User) -> Membership | None:
    if not user.is_active:
        return None
    try:
        return user.membership
    except Membership.DoesNotExist:
        return None


def has_capability(user: User, capability: Capability) -> bool:
    membership = membership_for(user)
    if membership is None:
        return False
    if membership.role == Membership.Role.ADMIN:
        return True
    return capability in VENDEDOR_CAPABILITIES


def require_user_capability(
    user: User,
    capability: Capability,
    *,
    workspace_id: object | None = None,
) -> Membership:
    """Enforce a capability at a domain-service boundary.

    Views use :func:`require_capability`, while services call this helper before
    locking or mutating durable rows.  Creator/uploader fields deliberately do
    not participate in this decision.
    """

    membership = membership_for(user)
    if membership is None or not has_capability(user, capability):
        raise PermissionDenied
    if workspace_id is not None and str(membership.workspace_id) != str(workspace_id):
        raise PermissionDenied
    return membership


def workspace_for_user(user: User, capability: Capability) -> Workspace:
    """Return the caller's active workspace after checking a capability."""

    membership = require_user_capability(user, capability)
    return membership.workspace


P = ParamSpec("P")
R = TypeVar("R", bound=HttpResponseBase)


def require_capability(
    capability: Capability,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(view: Callable[P, R]) -> Callable[P, R]:
        @login_required
        @wraps(view)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            request = cast(HttpRequest, args[0])
            user = cast(User, request.user)
            if not has_capability(user, capability):
                raise PermissionDenied
            return view(*args, **kwargs)

        return cast(Callable[P, R], wrapped)

    return decorator


admin_required = require_capability(Capability.MANAGE_USERS)


def capabilities_context(request: HttpRequest) -> dict[str, Any]:
    request_user = getattr(request, "user", None)
    if request_user is None or not request_user.is_authenticated:
        return {"current_membership": None, "can_administer": False}
    membership = membership_for(request_user)
    return {
        "current_membership": membership,
        "can_administer": membership is not None and membership.role == Membership.Role.ADMIN,
    }
