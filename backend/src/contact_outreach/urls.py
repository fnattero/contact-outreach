from __future__ import annotations

from django.contrib.auth.views import LogoutView
from django.urls import path

from apps.accounts.views import (
    ThrottledLoginView,
    account_security,
    activate_account,
    mfa_enroll,
    mfa_verify,
    user_list,
    user_reset_link,
    user_role,
    user_status,
    user_unlock,
)
from apps.audit.views import audit_log, job_list, retry_job
from apps.automation.views import (
    automatic_reply_prompt_save,
    automation_mode,
    automation_settings,
    follow_up_topic_save,
    global_context_approve,
    global_context_create,
    knowledge_approve,
    knowledge_create,
    knowledge_search_preview,
)
from apps.campaigns.views import (
    campaign_action,
    campaign_approve,
    campaign_create,
    campaign_detail,
    campaign_list,
    campaign_start_approved,
    campaign_zone_map,
    regenerate_outdated_campaign_analyses,
    regenerate_prospect_message,
)
from apps.catalogs.views import catalog_download, catalog_list
from apps.compliance.views import suppression_list
from apps.configuration.views import (
    business_profile,
    categories,
    delete_item,
    integrations,
    message_templates,
    prompts,
    toggle_item,
)
from apps.contacts.views import (
    attention_list,
    contact_create,
    contact_detail,
    contact_email_add,
    contact_email_preferred,
    contact_email_restrict,
    contact_email_validate,
    contact_follow_up_topic_approve,
    contact_list,
    contact_no_contact_toggle,
    contact_plan_save,
    contact_plan_snooze,
    contact_plan_state,
    contact_restrict,
    contact_restriction_revoke,
    human_task_close,
    scheduled_contact_authorize,
    scheduled_contact_draft_edit,
)
from apps.dashboard.views import (
    dashboard,
    outbound_approve,
    outbound_detail,
    outbound_edit,
    outbound_export,
    outbound_list,
    prospect_export,
    prospect_list,
)
from apps.health.views import degraded, liveness, readiness
from apps.mailbox.views import (
    fake_inbound,
    gmail_connect,
    gmail_disconnect,
    gmail_oauth_callback,
    gmail_settings,
    gmail_test,
    manual_reply,
    response_export,
    response_list,
    response_thread,
)
from apps.overture.views import overture_datasets, overture_sync

urlpatterns = [
    path("login/", ThrottledLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("activar/<str:token>/", activate_account, name="account-activate"),
    path("seguridad/", account_security, name="account-security"),
    path("seguridad/verificacion/", mfa_verify, name="mfa-verify"),
    path("seguridad/activar-verificacion/", mfa_enroll, name="mfa-enroll"),
    path("usuarios/", user_list, name="account-users"),
    path("usuarios/desbloquear/", user_unlock, name="account-user-unlock"),
    path("usuarios/<int:user_id>/rol/", user_role, name="account-user-role"),
    path("usuarios/<int:user_id>/estado/", user_status, name="account-user-status"),
    path("usuarios/<int:user_id>/nuevo-enlace/", user_reset_link, name="account-user-reset"),
    path("", dashboard, name="dashboard"),
    path("contactos/", contact_list, name="contacts"),
    path("contactos/nuevo/", contact_create, name="contact-create"),
    path(
        "contactos/<uuid:contact_id>/no-contactar/toggle/",
        contact_no_contact_toggle,
        name="contact-no-contact-toggle",
    ),
    path("contactos/<uuid:contact_id>/", contact_detail, name="contact-detail"),
    path(
        "contactos/<uuid:contact_id>/seguimiento/<uuid:topic_id>/aprobar/",
        contact_follow_up_topic_approve,
        name="contact-follow-up-topic-approve",
    ),
    path(
        "contactos/<uuid:contact_id>/proximo-contacto/guardar/",
        contact_plan_save,
        name="contact-plan-save",
    ),
    path(
        "contactos/<uuid:contact_id>/seguimiento/<uuid:plan_id>/estado/<str:state>/",
        contact_plan_state,
        name="contact-plan-state",
    ),
    path(
        "contactos/<uuid:contact_id>/proximo-contacto/posponer/",
        contact_plan_snooze,
        name="contact-plan-snooze",
    ),
    path(
        "contactos/<uuid:contact_id>/proximo-contacto/<uuid:attempt_id>/borrador/",
        scheduled_contact_draft_edit,
        name="scheduled-contact-draft-edit",
    ),
    path(
        "contactos/<uuid:contact_id>/proximo-contacto/<uuid:attempt_id>/autorizar/",
        scheduled_contact_authorize,
        name="scheduled-contact-authorize",
    ),
    path(
        "contactos/<uuid:contact_id>/emails/agregar/", contact_email_add, name="contact-email-add"
    ),
    path(
        "contactos/<uuid:contact_id>/emails/<uuid:email_address_id>/preferido/",
        contact_email_preferred,
        name="contact-email-preferred",
    ),
    path(
        "contactos/<uuid:contact_id>/emails/<uuid:email_address_id>/validar/",
        contact_email_validate,
        name="contact-email-validate",
    ),
    path(
        "contactos/<uuid:contact_id>/no-contactar/",
        contact_restrict,
        name="contact-restrict",
    ),
    path(
        "contactos/<uuid:contact_id>/emails/<uuid:email_address_id>/no-usar/",
        contact_email_restrict,
        name="contact-email-restrict",
    ),
    path(
        "contactos/<uuid:contact_id>/restricciones/<uuid:restriction_id>/habilitar/",
        contact_restriction_revoke,
        name="contact-restriction-revoke",
    ),
    path(
        "contactos/<uuid:contact_id>/tareas/<uuid:task_id>/cerrar/",
        human_task_close,
        name="human-task-close",
    ),
    path("necesita-atencion/", attention_list, name="attention"),
    path("prospectos/", prospect_list, name="prospects"),
    path("prospectos/exportar.csv", prospect_export, name="prospects-export"),
    path("envios/", outbound_list, name="outbound-messages"),
    path("envios/exportar.csv", outbound_export, name="outbound-export"),
    path("envios/<uuid:message_id>/", outbound_detail, name="outbound-detail"),
    path("envios/<uuid:message_id>/editar/", outbound_edit, name="outbound-edit"),
    path("envios/<uuid:message_id>/aprobar/", outbound_approve, name="outbound-approve"),
    path("perfil/", business_profile, name="business-profile"),
    path("mensajes-fijos/", message_templates, name="message-templates"),
    path("prompts/", prompts, name="prompts"),
    path("respuesta-automatica/", automation_settings, name="automation-settings"),
    path(
        "respuesta-automatica/instrucciones/guardar/",
        automatic_reply_prompt_save,
        name="automatic-reply-prompt-save",
    ),
    path("respuesta-automatica/modo/", automation_mode, name="automation-mode"),
    path(
        "respuesta-automatica/temas/guardar/",
        follow_up_topic_save,
        name="follow-up-topic-save",
    ),
    path(
        "respuesta-automatica/contexto-general/nuevo/",
        global_context_create,
        name="global-context-create",
    ),
    path(
        "respuesta-automatica/contexto-general/<uuid:revision_id>/aprobar/",
        global_context_approve,
        name="global-context-approve",
    ),
    path("respuesta-automatica/informacion/nueva/", knowledge_create, name="knowledge-create"),
    path(
        "respuesta-automatica/informacion/probar-busqueda/",
        knowledge_search_preview,
        name="knowledge-search-preview",
    ),
    path(
        "respuesta-automatica/informacion/<uuid:revision_id>/aprobar/",
        knowledge_approve,
        name="knowledge-approve",
    ),
    path("integraciones/", integrations, name="integrations"),
    path("integraciones/overture/", overture_datasets, name="overture-datasets"),
    path("integraciones/overture/sincronizar/", overture_sync, name="overture-sync"),
    path("rubros/", categories, name="categories"),
    path("configuracion/<str:kind>/<uuid:item_id>/toggle/", toggle_item, name="config-toggle"),
    path("configuracion/<str:kind>/<uuid:item_id>/delete/", delete_item, name="config-delete"),
    path("catalogos/", catalog_list, name="catalogs"),
    path("catalogos/<uuid:catalog_id>/descargar/", catalog_download, name="catalog-download"),
    path("campanas/", campaign_list, name="campaigns"),
    path("campanas/nueva/", campaign_create, name="campaign-create"),
    path(
        "campanas/nueva/provincias/<uuid:province_id>/mapa/",
        campaign_zone_map,
        name="campaign-zone-map",
    ),
    path("campanas/<uuid:campaign_id>/", campaign_detail, name="campaign-detail"),
    path(
        "campanas/<uuid:campaign_id>/aprobar/",
        campaign_approve,
        name="campaign-approve",
    ),
    path(
        "campanas/<uuid:campaign_id>/iniciar-aprobados/",
        campaign_start_approved,
        name="campaign-start-approved",
    ),
    path(
        "campanas/<uuid:campaign_id>/prospectos/<uuid:prospect_id>/regenerar/",
        regenerate_prospect_message,
        name="prospect-regenerate",
    ),
    path(
        "campanas/<uuid:campaign_id>/reanalizar-contrato-ia/",
        regenerate_outdated_campaign_analyses,
        name="campaign-regenerate-outdated-analyses",
    ),
    path("campanas/<uuid:campaign_id>/<str:action>/", campaign_action, name="campaign-action"),
    path("supresiones/", suppression_list, name="suppressions"),
    path("auditoria/", audit_log, name="audit-log"),
    path("jobs/", job_list, name="jobs"),
    path("jobs/<uuid:job_id>/reintentar/", retry_job, name="job-retry"),
    path("gmail/", gmail_settings, name="gmail-settings"),
    path("gmail/conectar/", gmail_connect, name="gmail-connect"),
    path("gmail/oauth/callback/", gmail_oauth_callback, name="gmail-oauth-callback"),
    path("gmail/probar/", gmail_test, name="gmail-test"),
    path("gmail/desconectar/", gmail_disconnect, name="gmail-disconnect"),
    path("gmail/fake/respuesta/", fake_inbound, name="gmail-fake-inbound"),
    path("respuestas/", response_list, name="responses"),
    path("respuestas/exportar.csv", response_export, name="responses-export"),
    path("respuestas/<uuid:inbound_id>/", response_thread, name="response-thread"),
    path(
        "respuestas/<uuid:inbound_id>/enviar/",
        manual_reply,
        name="manual-reply",
    ),
    path("health/", liveness, name="health"),
    path("health/live/", liveness, name="health-live"),
    path("health/ready/", readiness, name="health-ready"),
    path("health/degraded/", degraded, name="health-degraded"),
]

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
