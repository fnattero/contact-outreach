from collections.abc import Callable
from typing import Any, cast

from django.http import HttpRequest, HttpResponseBase
from django.urls import path
from django.views.decorators.csrf import csrf_protect

from apps.api.admin import (
    UnlockLoginView,
    UserActivationLinkView,
    UserListView,
    UserRoleView,
    UserStatusView,
)
from apps.api.auth import (
    ActivateView,
    CsrfView,
    LoginView,
    LogoutView,
    ReauthenticateView,
    SessionView,
)
from apps.api.catalogs import CatalogDownloadView, CatalogListView
from apps.api.contacts import (
    ContactDetailView,
    ContactEmailCreateView,
    ContactEmailValidateView,
    ContactListView,
    ContactPreferredEmailView,
    ContactRestrictionView,
    RestrictionRevokeView,
)
from apps.api.dashboard import DashboardSummaryView
from apps.api.operations import (
    AuditEventListView,
    BackgroundJobDetailView,
    BackgroundJobListView,
)
from apps.api.workspace import BusinessProfileView
from apps.health.views import degraded, liveness, readiness


def _csrf_protected_api_view(
    view: Callable[[HttpRequest], HttpResponseBase],
) -> Callable[[HttpRequest], HttpResponseBase]:
    """Protect DRF's csrf-exempt APIView wrapper with Django CSRF middleware."""
    protected = csrf_protect(view)
    cast(Any, protected).csrf_exempt = False
    return protected


urlpatterns = [
    path("auth/csrf/", CsrfView.as_view(), name="api-auth-csrf"),
    path("auth/login/", _csrf_protected_api_view(LoginView.as_view()), name="api-auth-login"),
    path(
        "auth/activate/",
        _csrf_protected_api_view(ActivateView.as_view()),
        name="api-auth-activate",
    ),
    path("auth/session/", SessionView.as_view(), name="api-auth-session"),
    path("auth/logout/", LogoutView.as_view(), name="api-auth-logout"),
    path("auth/reauthenticate/", ReauthenticateView.as_view(), name="api-auth-reauthenticate"),
    path("users/", UserListView.as_view(), name="api-users"),
    path("users/unlock-login/", UnlockLoginView.as_view(), name="api-users-unlock-login"),
    path("users/<int:user_id>/role/", UserRoleView.as_view(), name="api-user-role"),
    path("users/<int:user_id>/status/", UserStatusView.as_view(), name="api-user-status"),
    path(
        "users/<int:user_id>/activation-link/",
        UserActivationLinkView.as_view(),
        name="api-user-activation-link",
    ),
    path("workspace/profile/", BusinessProfileView.as_view(), name="api-workspace-profile"),
    path("dashboard/summary/", DashboardSummaryView.as_view(), name="api-dashboard-summary"),
    path("audit-events/", AuditEventListView.as_view(), name="api-audit-events"),
    path("background-jobs/", BackgroundJobListView.as_view(), name="api-background-jobs"),
    path(
        "background-jobs/<uuid:job_id>/",
        BackgroundJobDetailView.as_view(),
        name="api-background-job-detail",
    ),
    path("catalogs/", CatalogListView.as_view(), name="api-catalogs"),
    path(
        "catalogs/<uuid:catalog_id>/download/",
        CatalogDownloadView.as_view(),
        name="api-catalog-download",
    ),
    path("contacts/", ContactListView.as_view(), name="api-contacts"),
    path("contacts/<uuid:contact_id>/", ContactDetailView.as_view(), name="api-contact-detail"),
    path(
        "contacts/<uuid:contact_id>/emails/",
        ContactEmailCreateView.as_view(),
        name="api-contact-emails",
    ),
    path(
        "contacts/<uuid:contact_id>/emails/<uuid:email_id>/preferred/",
        ContactPreferredEmailView.as_view(),
        name="api-contact-email-preferred",
    ),
    path(
        "contacts/<uuid:contact_id>/emails/<uuid:email_id>/validate/",
        ContactEmailValidateView.as_view(),
        name="api-contact-email-validate",
    ),
    path(
        "contacts/<uuid:contact_id>/restrictions/",
        ContactRestrictionView.as_view(),
        name="api-contact-restrictions",
    ),
    path(
        "contacts/<uuid:contact_id>/restrictions/<uuid:restriction_id>/revoke/",
        RestrictionRevokeView.as_view(),
        name="api-contact-restriction-revoke",
    ),
    path("health/live/", liveness, name="api-health-live"),
    path("health/ready/", readiness, name="api-health-ready"),
    path("health/degraded/", degraded, name="api-health-degraded"),
]
