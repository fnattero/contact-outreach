"use client";

import { Card, DatePicker, Flex, Input, Select, Table } from "antd";
import { useEffect, useMemo, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/design-system/states";
import { can, getAuditEvents, problemMessage, type AuditEvent, type Problem } from "@/lib/api";

const dateFormatter = new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeStyle: "short", timeZone: "America/Argentina/Buenos_Aires" });
const actionLabels: Record<string, string> = {
  "accounts.role_changed": "Cambió un rol",
  "accounts.user_status_changed": "Cambió el estado de un usuario",
  "catalog.created": "Creó un catálogo",
  "campaign.approved": "Aprobó una campaña",
  "campaign.awaiting_approval": "Envió una campaña a aprobación",
  "automation.live_enabled": "Activó el envío real",
  "automation.mode_changed": "Cambió el modo de automatización",
  "gmail.connected": "Conectó Gmail",
  "gmail.disconnected": "Desconectó Gmail",
  "message.approved_for_delivery": "Aprobó un mensaje",
  "message.sent": "Envió un mensaje",
  "campaign.checked": "Campaña verificada",
  "campaign.created": "Campaña creada",
  "campaign.discovery_finished": "Descubrimiento finalizado",
  "campaign.legacy_boundaries_refreshed": "Límites anteriores actualizados",
  "campaign.outdated_analyses_regeneration_requested": "Reanálisis solicitado",
  "campaign.transitioned": "Estado de campaña actualizado",
  "contact.renamed": "Contacto renombrado",
  "extraction.run_created": "Ejecución de extracción creada",
  "extraction.run_failed": "Ejecución de extracción fallida",
  "extraction.run_succeeded": "Ejecución de extracción completada",
  "gmail.manual_reply_authorized": "Respuesta manual autorizada",
  "gmail.reply_imported": "Respuesta de Gmail importada",
  "gmail.sync_baseline_initialized": "Punto inicial de Gmail registrado",
  "gmail.sync_failed": "Sincronización de Gmail fallida",
  "gmail.synced": "Gmail sincronizado",
  "gmail.test_sent": "Prueba de Gmail enviada",
  "integration_configuration.saved": "Integraciones guardadas",
  "message.draft_edited": "Borrador de correo editado",
  "message.dry_run_completed": "Simulación de correo completada",
  "message.retry_requested": "Reintento de correo solicitado",
  "message.review_ready": "Correo listo para revisar",
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
};
const entityLabels: Record<string, string> = { User: "Usuario", Campaign: "Campaña", Catalog: "Catálogo", OutboundMessage: "Mensaje", Workspace: "Espacio de trabajo", "GmailConnection": "Conexión de Gmail", "OvertureRelease": "Versión de Overture", "Prospect": "Prospecto", "SearchCategory": "Rubro", "SearchRun": "Ejecución de extracción", "SearchZone": "Zona", "SuppressionEntry": "Supresión" };

function actionLabel(action: string): string { return actionLabels[action] ?? "Registró una acción"; }
function entityLabel(entity: string): string { return entityLabels[entity] ?? "Recurso"; }

export default function AuditPage() {
  const { session } = useAuth();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [actorFilter, setActorFilter] = useState("");
  const [actionFilter, setActionFilter] = useState<string | undefined>();
  const [targetFilter, setTargetFilter] = useState<string | undefined>();
  const [dateRange, setDateRange] = useState<[number, number] | null>(null);

  useEffect(() => { void getAuditEvents().then((response) => setEvents(response.data)).catch(setError).finally(() => setLoading(false)); }, []);
  const actions = useMemo(() => [...new Set(events.map((event) => event.action))], [events]);
  const targets = useMemo(() => [...new Set(events.map((event) => event.entity_type))], [events]);
  const filtered = useMemo(() => events.filter((event) => {
    const date = new Date(event.created_at).getTime();
    return (!actorFilter || (event.actor ?? "Sistema").toLowerCase().includes(actorFilter.toLowerCase())) && (!actionFilter || event.action === actionFilter) && (!targetFilter || event.entity_type === targetFilter) && (!dateRange || (date >= dateRange[0] && date <= dateRange[1]));
  }), [events, actorFilter, actionFilter, targetFilter, dateRange]);

  if (!can(session, "view_audit")) return <AuthError error={{ detail: "No tenés permisos para ver auditoría." }} />;
  if (loading) return <LoadingState layout="list" />;
  if (error) return <ErrorState failed="No se pudo cargar la auditoría" instruction={problemMessage(error as Problem)} onRetry={() => { setLoading(true); setError(null); void getAuditEvents().then((response) => setEvents(response.data)).catch(setError).finally(() => setLoading(false)); }} />;

  return <Flex vertical gap="large">
    <PageHeader title="Auditoría" description="Quién hizo cada cambio o acción importante, y cuándo. No se puede borrar ni modificar." filters={<div className="audit-filters"><Input placeholder="Filtrar por actor" aria-label="Filtrar por actor" value={actorFilter} onChange={(event) => setActorFilter(event.target.value)} /><Select allowClear placeholder="Acción" aria-label="Filtrar por acción" value={actionFilter} onChange={setActionFilter} options={actions.map((action) => ({ value: action, label: actionLabel(action) }))} /><Select allowClear placeholder="Recurso" aria-label="Filtrar por recurso" value={targetFilter} onChange={setTargetFilter} options={targets.map((target) => ({ value: target, label: entityLabel(target) }))} /><DatePicker.RangePicker aria-label="Filtrar por fecha" onChange={(value) => setDateRange(value ? [value[0]!.startOf("day").valueOf(), value[1]!.endOf("day").valueOf()] : null)} /></div>} />
    {filtered.length ? <Card><Table<AuditEvent> rowKey="id" dataSource={filtered} pagination={{ pageSize: 20, responsive: true }} columns={[{ title: "Actor", dataIndex: "actor", render: (value: string | null) => value ?? "Sistema" }, { title: "Acción", dataIndex: "action", render: (value: string) => actionLabel(value) }, { title: "Objetivo", dataIndex: "entity_type", render: (value: string) => entityLabel(value) }, { title: "Fecha y hora", dataIndex: "created_at", render: (value: string) => <time className="data-text" dateTime={value}>{dateFormatter.format(new Date(value))}</time> }]} expandable={{ expandedRowRender: (event) => <details className="integration-technical" open><summary>Detalles técnicos</summary><dl><dt>Acción interna</dt><dd>{event.action}</dd><dt>Tipo de objetivo</dt><dd>{event.entity_type}</dd><dt>ID de objetivo</dt><dd>{event.entity_id}</dd><dt>Correlación</dt><dd>{event.correlation_id}</dd><dt>Payload</dt><dd>La API actual no devuelve el payload.</dd></dl></details> }} /></Card> : <EmptyState headline="No hay eventos para estos filtros" explanation="Probá con otro actor, acción, recurso o rango de fechas." actionLabel="Limpiar filtros" onAction={() => { setActorFilter(""); setActionFilter(undefined); setTargetFilter(undefined); setDateRange(null); }} />}
  </Flex>;
}
