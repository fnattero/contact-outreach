from __future__ import annotations

from django.contrib.auth.views import LogoutView
from django.urls import path

from apps.accounts.views import ThrottledLoginView
from apps.audit.views import audit_log
from apps.campaigns.views import (
    campaign_action,
    campaign_create,
    campaign_detail,
    campaign_list,
    regenerate_prospect_message,
)
from apps.catalogs.views import catalog_download, catalog_list
from apps.compliance.views import suppression_list
from apps.configuration.views import (
    business_profile,
    categories,
    delete_item,
    toggle_item,
    zones,
)
from apps.dashboard.views import dashboard
from apps.health.views import liveness, readiness
from apps.mailbox.views import (
    gmail_connect,
    gmail_disconnect,
    gmail_oauth_callback,
    gmail_settings,
    gmail_test,
)

urlpatterns = [
    path("login/", ThrottledLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("", dashboard, name="dashboard"),
    path("perfil/", business_profile, name="business-profile"),
    path("rubros/", categories, name="categories"),
    path("zonas/", zones, name="zones"),
    path("configuracion/<str:kind>/<uuid:item_id>/toggle/", toggle_item, name="config-toggle"),
    path("configuracion/<str:kind>/<uuid:item_id>/delete/", delete_item, name="config-delete"),
    path("catalogos/", catalog_list, name="catalogs"),
    path("catalogos/<uuid:catalog_id>/descargar/", catalog_download, name="catalog-download"),
    path("campanas/", campaign_list, name="campaigns"),
    path("campanas/nueva/", campaign_create, name="campaign-create"),
    path("campanas/<uuid:campaign_id>/", campaign_detail, name="campaign-detail"),
    path(
        "campanas/<uuid:campaign_id>/prospectos/<uuid:prospect_id>/regenerar/",
        regenerate_prospect_message,
        name="prospect-regenerate",
    ),
    path("campanas/<uuid:campaign_id>/<str:action>/", campaign_action, name="campaign-action"),
    path("supresiones/", suppression_list, name="suppressions"),
    path("auditoria/", audit_log, name="audit-log"),
    path("gmail/", gmail_settings, name="gmail-settings"),
    path("gmail/conectar/", gmail_connect, name="gmail-connect"),
    path("gmail/oauth/callback/", gmail_oauth_callback, name="gmail-oauth-callback"),
    path("gmail/probar/", gmail_test, name="gmail-test"),
    path("gmail/desconectar/", gmail_disconnect, name="gmail-disconnect"),
    path("health/", liveness, name="health"),
    path("health/live/", liveness, name="health-live"),
    path("health/ready/", readiness, name="health-ready"),
]
