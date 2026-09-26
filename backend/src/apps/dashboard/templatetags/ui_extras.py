from __future__ import annotations

from typing import Any

from django import template

register = template.Library()


SPANISH_LABELS = {
    # Proveedores y modos técnicos que se guardan como identificadores internos.
    "fake": "Simulado (sin red)",
    "fake-deterministic": "Simulado determinístico",
    "overture": "Overture Maps Places",
    "http": "HTTP seguro",
    "ollama": "Ollama",
    "openai-compatible": "Compatible con OpenAI",
    "api": "API de Google Gmail",
    "fixture": "Datos de ejemplo",
    "WEBSITE_MAILTO": "Enlace de correo del sitio web",
    "WEBSITE_VISIBLE": "Correo visible en el sitio web",
    "website_mailto": "Enlace de correo del sitio web",
    "website_visible_text": "Correo visible en el sitio web",
    "Email encontrado": "Correo encontrado",
    "Sin email": "Sin correo",
    "Email": "Correo",
    "dry-run": "Simulación",
    "live": "En vivo",
    # Colas y estados mostrados fuera de campos con choices.
    "maintenance": "Mantenimiento",
    "extraction": "Extracción",
    "enrichment": "Enriquecimiento",
    "analysis": "Análisis",
    "delivery": "Entrega",
    "mailbox": "Buzón",
    "orchestration": "Orquestación",
    "PENDING": "Pendiente",
    "RUNNING": "En curso",
    "SUCCEEDED": "Completado",
    "RETRY_WAIT": "Esperando reintento",
    "FAILED": "Falló",
    "CANCELLED": "Cancelado",
    # Tareas persistidas.
    "mailbox.deliver_message": "Entregar correo",
    "mailbox.sync_gmail_connection": "Sincronizar Gmail",
    "campaigns.advance_extraction_run": "Continuar extracción de campaña",
    "prospects.process_pipeline": "Procesar prospecto",
    "overture.sync_snapshot": "Sincronizar datos de Overture",
    "overture.discover_releases": "Comprobar versiones de Overture",
    # Entidades persistidas.
    "Campaign": "Campaña",
    "Catalog": "Catálogo",
    "GmailConnection": "Conexión de Gmail",
    "OutboundMessage": "Correo saliente",
    "OvertureRelease": "Versión de Overture",
    "Prospect": "Prospecto",
    "SearchCategory": "Rubro",
    "SearchRun": "Ejecución de extracción",
    "SearchZone": "Zona",
    "SuppressionEntry": "Supresión",
    "searchcategory": "rubro",
    "searchzone": "zona",
    # Acciones de auditoría.
    "campaign.checked": "Campaña verificada",
    "campaign.created": "Campaña creada",
    "campaign.discovery_finished": "Descubrimiento finalizado",
    "campaign.legacy_boundaries_refreshed": "Límites anteriores actualizados",
    "campaign.outdated_analyses_regeneration_requested": "Reanálisis solicitado",
    "campaign.transitioned": "Estado de campaña actualizado",
    "catalog.created": "Catálogo creado",
    "extraction.run_created": "Ejecución de extracción creada",
    "extraction.run_failed": "Ejecución de extracción fallida",
    "extraction.run_succeeded": "Ejecución de extracción completada",
    "gmail.connected": "Gmail conectado",
    "gmail.disconnected": "Gmail desconectado",
    "gmail.manual_reply_authorized": "Respuesta manual autorizada",
    "gmail.reply_imported": "Respuesta de Gmail importada",
    "gmail.sync_baseline_initialized": "Punto inicial de Gmail registrado",
    "gmail.sync_failed": "Sincronización de Gmail fallida",
    "gmail.synced": "Gmail sincronizado",
    "gmail.test_sent": "Prueba de Gmail enviada",
    "integration_configuration.saved": "Integraciones guardadas",
    "message.approved_for_delivery": "Correo aprobado para entrega",
    "message.draft_edited": "Borrador de correo editado",
    "message.dry_run_completed": "Simulación de correo completada",
    "message.retry_requested": "Reintento de correo solicitado",
    "message.review_ready": "Correo listo para revisar",
    "message.sent": "Correo enviado",
    "overture.sync_queued": "Sincronización de Overture encolada",
    "prospect.analysis_failed": "Análisis de prospecto fallido",
    "prospect.analysis_retry_deferred": "Reintento de análisis programado",
    "prospect.analyzed": "Prospecto analizado",
    "prospect.email_found": "Correo del prospecto encontrado",
    "prospect.enriched": "Prospecto enriquecido",
    "prospect.regeneration_requested": "Regeneración de prospecto solicitada",
    "prospect.website_snapshotted": "Lectura del sitio guardada",
    "suppression.created": "Supresión creada",
    "suppression.upgraded": "Supresión actualizada",
    "searchcategory.archived": "Rubro archivado",
    "searchcategory.deleted": "Rubro eliminado",
    "searchcategory.saved": "Rubro guardado",
    "searchcategory.toggled": "Estado del rubro actualizado",
    "searchzone.archived": "Zona archivada",
    "searchzone.deleted": "Zona eliminada",
    "searchzone.saved": "Zona guardada",
    "searchzone.toggled": "Estado de la zona actualizado",
    # Nombres de campos externos que aparecen en la trazabilidad.
    "names": "Nombres",
    "addresses": "Direcciones",
    "websites": "Sitios web",
    "emails": "Correos electrónicos",
    "phones": "Teléfonos",
    "taxonomy": "Taxonomía",
    "basic_category": "Categoría básica",
    "confidence": "Confianza",
}


@register.filter(name="etiqueta_es")
def spanish_label(value: Any) -> str:
    """Return a Spanish UI label without changing the persisted identifier."""

    if value is None:
        return ""
    text = str(value)
    return SPANISH_LABELS.get(text, text)
