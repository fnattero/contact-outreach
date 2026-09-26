"use client";

import { Card, DatePicker, Flex, Input, Select, Table } from "antd";
import { useEffect, useMemo, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/design-system/states";
import { getAuditEvents, problemMessage, type AuditEvent, type Problem } from "@/lib/api";

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
};
const entityLabels: Record<string, string> = { User: "Usuario", Campaign: "Campaña", Catalog: "Catálogo", OutboundMessage: "Mensaje", Workspace: "Espacio de trabajo" };

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

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para ver auditoría." }} />;
  if (loading) return <LoadingState layout="list" />;
  if (error) return <ErrorState failed="No se pudo cargar la auditoría" instruction={problemMessage(error as Problem)} onRetry={() => { setLoading(true); setError(null); void getAuditEvents().then((response) => setEvents(response.data)).catch(setError).finally(() => setLoading(false)); }} />;

  return <Flex vertical gap="large">
    <PageHeader title="Auditoría" description="Registro append-only de cambios y acciones sensibles." filters={<div className="audit-filters"><Input placeholder="Filtrar por actor" aria-label="Filtrar por actor" value={actorFilter} onChange={(event) => setActorFilter(event.target.value)} /><Select allowClear placeholder="Acción" aria-label="Filtrar por acción" value={actionFilter} onChange={setActionFilter} options={actions.map((action) => ({ value: action, label: actionLabel(action) }))} /><Select allowClear placeholder="Recurso" aria-label="Filtrar por recurso" value={targetFilter} onChange={setTargetFilter} options={targets.map((target) => ({ value: target, label: entityLabel(target) }))} /><DatePicker.RangePicker aria-label="Filtrar por fecha" onChange={(value) => setDateRange(value ? [value[0]!.startOf("day").valueOf(), value[1]!.endOf("day").valueOf()] : null)} /></div>} />
    {filtered.length ? <Card><Table<AuditEvent> rowKey="id" dataSource={filtered} pagination={{ pageSize: 20, responsive: true }} columns={[{ title: "Actor", dataIndex: "actor", render: (value: string | null) => value ?? "Sistema" }, { title: "Acción", dataIndex: "action", render: (value: string) => actionLabel(value) }, { title: "Objetivo", dataIndex: "entity_type", render: (value: string) => entityLabel(value) }, { title: "Fecha y hora", dataIndex: "created_at", render: (value: string) => <time className="data-text" dateTime={value}>{dateFormatter.format(new Date(value))}</time> }]} expandable={{ expandedRowRender: (event) => <details className="integration-technical" open><summary>Detalles técnicos</summary><dl><dt>Acción interna</dt><dd>{event.action}</dd><dt>Tipo de objetivo</dt><dd>{event.entity_type}</dd><dt>ID de objetivo</dt><dd>{event.entity_id}</dd><dt>Correlación</dt><dd>{event.correlation_id}</dd><dt>Payload</dt><dd>La API actual no devuelve el payload.</dd></dl></details> }} /></Card> : <EmptyState headline="No hay eventos para estos filtros" explanation="Probá con otro actor, acción, recurso o rango de fechas." actionLabel="Limpiar filtros" onAction={() => { setActorFilter(""); setActionFilter(undefined); setTargetFilter(undefined); setDateRange(null); }} />}
  </Flex>;
}
