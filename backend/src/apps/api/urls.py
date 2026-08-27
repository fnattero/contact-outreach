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
from apps.api.advanced import (
    AttentionView,
    HumanTaskActionView,
    OvertureStatusView,
    OvertureSyncView,
)
from apps.api.auth import (
    ActivateView,
    CsrfView,
    LoginView,
    LogoutView,
    ReauthenticateView,
    SessionView,
)
from apps.api.automation import (
    AutomationConfigurationView,
    AutomationLiveActionView,
    WritingInstructionsView,
)
from apps.api.campaigns import (
    CampaignActionView,
    CampaignCoverageView,
    CampaignDetailView,
    CampaignEnrollmentListView,
    CampaignListView,
    CampaignMessageListView,
    CampaignProspectListView,
    ProspectExportView,
)
from apps.api.catalogs import CatalogDownloadView, CatalogListView
from apps.api.configuration import (
    MessageTemplateRevisionView,
    PromptConfigurationView,
    SearchCategoryListView,
    SearchCategoryRulesView,
    SearchZoneGeometryView,
    SearchZoneListView,
)
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
from apps.api.gmail import (
    GmailConnectionView,
    GmailDisconnectView,
    GmailOAuthCallbackView,
    GmailOAuthStartView,
    GmailTestView,
)
from apps.api.health import DegradedHealthView
from apps.api.integrations import IntegrationStatusView
from apps.api.knowledge import (
    KnowledgeContextApproveView,
    KnowledgeContextRevisionView,
    KnowledgeFactApproveView,
    KnowledgeFactListView,
    KnowledgeSearchPreviewView,
)
from apps.api.mailbox import (
    InboundManualReplyView,
    InboundMessageExportView,
    InboundMessageListView,
    InboundMessageThreadView,
    OutboundMessageAuthorizeView,
    OutboundMessageDetailView,
    OutboundMessageDraftView,
    OutboundMessageExportView,
    OutboundMessageListView,
)
from apps.api.operations import (
    AuditEventListView,
    BackgroundJobDetailView,
    BackgroundJobListView,
    BackgroundJobRetryView,
)
from apps.api.scheduled import (
    ContactPlanStateView,
    ContactPlanView,
    FollowUpTopicDetailView,
    FollowUpTopicListView,
    ScheduledAttemptActionView,
)
from apps.api.workspace import BusinessProfileView
from apps.health.views import liveness, readiness


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
    path(
        "message-template-revisions/",
        MessageTemplateRevisionView.as_view(),
        name="api-message-template-revisions",
    ),
    path("prompts/", PromptConfigurationView.as_view(), name="api-prompts"),
    path(
        "automation/configuration/",
        AutomationConfigurationView.as_view(),
        name="api-automation-configuration",
    ),
    path(
        "automation/writing-instructions/",
        WritingInstructionsView.as_view(),
        name="api-automation-writing-instructions",
    ),
    path(
        "automation/actions/<str:action>/",
        AutomationLiveActionView.as_view(),
        name="api-automation-action",
    ),
    path("knowledge/facts/", KnowledgeFactListView.as_view(), name="api-knowledge-facts"),
    path(
        "knowledge/fact-revisions/<uuid:revision_id>/approve/",
        KnowledgeFactApproveView.as_view(),
        name="api-knowledge-fact-approve",
    ),
    path(
        "knowledge/global-context-revisions/",
        KnowledgeContextRevisionView.as_view(),
        name="api-knowledge-context-revisions",
    ),
    path(
        "knowledge/global-context-revisions/<uuid:revision_id>/approve/",
        KnowledgeContextApproveView.as_view(),
        name="api-knowledge-context-approve",
    ),
    path(
        "knowledge/search-preview/",
        KnowledgeSearchPreviewView.as_view(),
        name="api-knowledge-search-preview",
    ),
    path("follow-up-topics/", FollowUpTopicListView.as_view(), name="api-follow-up-topics"),
    path(
        "follow-up-topics/<uuid:topic_id>/",
        FollowUpTopicDetailView.as_view(),
        name="api-follow-up-topic-detail",
    ),
    path("attention/", AttentionView.as_view(), name="api-attention"),
    path(
        "human-tasks/<uuid:task_id>/<str:action>/",
        HumanTaskActionView.as_view(),
        name="api-human-task-action",
    ),
    path("overture/status/", OvertureStatusView.as_view(), name="api-overture-status"),
    path("overture/sync/", OvertureSyncView.as_view(), name="api-overture-sync"),
    path("integrations/status/", IntegrationStatusView.as_view(), name="api-integrations-status"),
    path(
        "integrations/gmail/connection/",
        GmailConnectionView.as_view(),
        name="api-gmail-connection",
    ),
    path(
        "integrations/gmail/oauth/start/",
        GmailOAuthStartView.as_view(),
        name="api-gmail-oauth-start",
    ),
    path(
        "integrations/gmail/oauth/callback/",
        GmailOAuthCallbackView.as_view(),
        name="api-gmail-oauth-callback",
    ),
    path("integrations/gmail/test/", GmailTestView.as_view(), name="api-gmail-test"),
    path(
        "integrations/gmail/disconnect/",
        GmailDisconnectView.as_view(),
        name="api-gmail-disconnect",
    ),
    path("search-categories/", SearchCategoryListView.as_view(), name="api-search-categories"),
    path(
        "search-categories/<uuid:category_id>/rules/",
        SearchCategoryRulesView.as_view(),
        name="api-search-category-rules",
    ),
    path("search-zones/", SearchZoneListView.as_view(), name="api-search-zones"),
    path(
        "search-zones/<uuid:zone_id>/geometry/",
        SearchZoneGeometryView.as_view(),
        name="api-search-zone-geometry",
    ),
    path("dashboard/summary/", DashboardSummaryView.as_view(), name="api-dashboard-summary"),
    path("campaigns/", CampaignListView.as_view(), name="api-campaigns"),
    path("prospects/export.csv", ProspectExportView.as_view(), name="api-prospect-export"),
    path(
        "campaigns/<uuid:campaign_id>/",
        CampaignDetailView.as_view(),
        name="api-campaign-detail",
    ),
    path(
        "campaigns/<uuid:campaign_id>/coverage-map/",
        CampaignCoverageView.as_view(),
        name="api-campaign-coverage",
    ),
    path(
        "campaigns/<uuid:campaign_id>/enrollments/",
        CampaignEnrollmentListView.as_view(),
        name="api-campaign-enrollments",
    ),
    path(
        "campaigns/<uuid:campaign_id>/messages/",
        CampaignMessageListView.as_view(),
        name="api-campaign-messages",
    ),
    path(
        "campaigns/<uuid:campaign_id>/prospects/",
        CampaignProspectListView.as_view(),
        name="api-campaign-prospects",
    ),
    path(
        "campaigns/<uuid:campaign_id>/actions/<str:action>/",
        CampaignActionView.as_view(),
        name="api-campaign-action",
    ),
    path("audit-events/", AuditEventListView.as_view(), name="api-audit-events"),
    path("background-jobs/", BackgroundJobListView.as_view(), name="api-background-jobs"),
    path(
        "background-jobs/<uuid:job_id>/",
        BackgroundJobDetailView.as_view(),
        name="api-background-job-detail",
    ),
    path(
        "background-jobs/<uuid:job_id>/retry/",
        BackgroundJobRetryView.as_view(),
        name="api-background-job-retry",
    ),
    path("inbound-messages/", InboundMessageListView.as_view(), name="api-inbound-messages"),
    path(
        "inbound-messages/export.csv",
        InboundMessageExportView.as_view(),
        name="api-inbound-message-export",
    ),
    path(
        "inbound-messages/<uuid:inbound_id>/thread/",
        InboundMessageThreadView.as_view(),
        name="api-inbound-message-thread",
    ),
    path(
        "inbound-messages/<uuid:inbound_id>/manual-reply/",
        InboundManualReplyView.as_view(),
        name="api-inbound-message-manual-reply",
    ),
    path("outbound-messages/", OutboundMessageListView.as_view(), name="api-outbound-messages"),
    path(
        "outbound-messages/export.csv",
        OutboundMessageExportView.as_view(),
        name="api-outbound-message-export",
    ),
    path(
        "outbound-messages/<uuid:message_id>/",
        OutboundMessageDetailView.as_view(),
        name="api-outbound-message-detail",
    ),
    path(
        "outbound-messages/<uuid:message_id>/draft/",
        OutboundMessageDraftView.as_view(),
        name="api-outbound-message-draft",
    ),
    path(
        "outbound-messages/<uuid:message_id>/authorize/",
        OutboundMessageAuthorizeView.as_view(),
        name="api-outbound-message-authorize",
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
        "contacts/<uuid:contact_id>/communication-plans/",
        ContactPlanView.as_view(),
        name="api-contact-communication-plans",
    ),
    path(
        "contacts/<uuid:contact_id>/communication-plans/<uuid:plan_id>/<str:action>/",
        ContactPlanStateView.as_view(),
        name="api-contact-communication-plan-action",
    ),
    path(
        "contacts/<uuid:contact_id>/scheduled-attempts/<uuid:attempt_id>/draft/",
        ScheduledAttemptActionView.as_view(),
        name="api-scheduled-attempt-draft",
    ),
    path(
        "contacts/<uuid:contact_id>/scheduled-attempts/<uuid:attempt_id>/authorize/",
        ScheduledAttemptActionView.as_view(),
        name="api-scheduled-attempt-authorize",
    ),
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
    path("health/degraded/", DegradedHealthView.as_view(), name="api-health-degraded"),
]
