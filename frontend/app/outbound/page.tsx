"use client";

import { Input, Select, Table } from "antd";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge, type SemanticLevel } from "@/components/design-system";
import { getCampaigns, getOutboundMessages, problemMessage, type DashboardCampaign, type OutboundMessage, type Problem } from "@/lib/api";

const stateOptions = [
  { value: "PREPARED", label: "Preparado" },
  { value: "REVIEW_READY", label: "Listo para revisar" },
  { value: "QUEUED", label: "En cola" },
  { value: "SENDING", label: "Enviando" },
  { value: "RECONCILING", label: "Reconciliando" },
  { value: "SENT", label: "Enviado" },
  { value: "DRY_RUN_COMPLETED", label: "Simulado" },
  { value: "INELIGIBLE", label: "No elegible" },
  { value: "SEND_FAILED", label: "Falló" },
  { value: "CANCELLED", label: "Cancelado" },
];

function statusLevel(state: string): SemanticLevel {
  if (state === "SENT") return "success";
  if (["SEND_FAILED", "INELIGIBLE"].includes(state)) return "danger";
  if (state === "DRY_RUN_COMPLETED" || state === "CANCELLED") return "inactive";
  return "info";
}

function eventTime(message: OutboundMessage): string {
  return message.sent_at || message.simulated_at || message.created_at;
}

function plainError(error: string | null | undefined): string {
  if (!error) return "No se pudo completar el envío.";
  const normalized = error.toLowerCase();
  if (normalized.includes("gmail") || normalized.includes("connection")) return "Gmail no está listo para este envío.";
  if (normalized.includes("kill") || normalized.includes("send_mode") || normalized.includes("live")) return "La configuración de seguridad no autorizó el envío.";
  if (normalized.includes("restriction") || normalized.includes("unsubscribe") || normalized.includes("bounce")) return "El contacto o su dirección no están habilitados para recibir este envío.";
  if (normalized.includes("mime") || normalized.includes("attachment")) return "El mensaje o sus adjuntos no pudieron reconciliarse.";
  return "El envío falló y necesita revisión.";
}

function FailureDetails({ message }: { message: OutboundMessage }) {
  return <div className="outbound-failure"><p>{plainError(message.error)}</p>{message.error ? <details><summary>Detalles técnicos</summary><code>{message.error}</code></details> : null}<p className="outbound-failure__retry-note">No hay reintento directo disponible desde este registro.</p></div>;
}

export default function OutboundPage() {
  const [messages, setMessages] = useState<OutboundMessage[]>([]);
  const [campaigns, setCampaigns] = useState<DashboardCampaign[]>([]);
  const [state, setState] = useState("");
  const [campaign, setCampaign] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getOutboundMessages({ state: state || undefined, campaign: campaign || undefined, date_from: dateFrom || undefined, date_to: dateTo || undefined }), getCampaigns()])
      .then(([outbound, campaignPage]) => { if (!cancelled) { setMessages(outbound.data); setCampaigns(campaignPage.data); setError(null); } })
      .catch((problem) => { if (!cancelled) setError(problem); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [state, campaign, dateFrom, dateTo]);

  const campaignNames = useMemo(() => Object.fromEntries(campaigns.map((item) => [item.id, item.name])), [campaigns]);
  const selectedStateLabel = stateOptions.find((option) => option.value === state)?.label;

  if (loading) return <LoadingState layout="list" label="Cargando registro de envíos" />;
  if (error && !messages.length) return <ErrorState failed="No se pudo cargar el registro de envíos" instruction={problemMessage(error as Problem)} onRetry={() => window.location.reload()} />;

  return <>
    <PageHeader title="Envíos" description="Registro de cada entrega y su reconciliación." filters={<div className="outbound-filters"><Select className="outbound-filter-control" allowClear aria-label="Filtrar por estado" placeholder="Todos los estados" value={state || undefined} onChange={(value) => setState(value || "")} options={stateOptions} /><Select className="outbound-filter-control" allowClear aria-label="Filtrar por campaña" placeholder="Todas las campañas" value={campaign || undefined} onChange={(value) => setCampaign(value || "")} options={campaigns.map((item) => ({ value: item.id, label: item.name }))} /><label>Desde <Input aria-label="Fecha desde" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label><label>Hasta <Input aria-label="Fecha hasta" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label></div>} />
    {error ? <div className="dashboard-inline-error" role="alert">{problemMessage(error as Problem)}</div> : null}
    {messages.length ? <section className="outbound-log" aria-label="Registro de envíos"><Table<OutboundMessage> rowKey="id" dataSource={messages} pagination={false} scroll={{ x: 920 }} expandable={{ rowExpandable: (message) => message.state === "SEND_FAILED", expandedRowRender: (message) => <FailureDetails message={message} /> }} rowClassName={(message) => message.state === "DRY_RUN_COMPLETED" ? "outbound-row--simulated" : ""} columns={[{ title: "Fecha", dataIndex: "created_at", render: (_value: string, message) => <time className="data-text" dateTime={eventTime(message)}>{new Date(eventTime(message)).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" })}</time> }, { title: "Destinatario", dataIndex: "recipient", render: (recipient: string) => <span className="data-text">{recipient}</span> }, { title: "Campaña", dataIndex: "campaign_id", render: (campaignId: string | null) => campaignId ? campaignNames[campaignId] || "Campaña vinculada" : "Respuesta directa" }, { title: "Estado", dataIndex: "state_label", render: (label: string, message) => <span className="outbound-status"><StatusBadge label={label} level={statusLevel(message.state)} />{message.state === "DRY_RUN_COMPLETED" ? <span className="outbound-simulation-label">Simulado</span> : null}</span> }, { title: "Error o reconciliación", dataIndex: "error", render: (errorValue: string | null | undefined, message) => message.state === "SEND_FAILED" ? <span className="outbound-error-summary">{plainError(errorValue)}</span> : <span className="outbound-reconciliation">{message.state === "SENT" ? "Entregado" : message.state === "DRY_RUN_COMPLETED" ? "Sin envío real" : "Pendiente de reconciliar"}</span> }, { title: "", key: "detail", render: (_value: unknown, message) => <Link href={`/outbound/${message.id}`}>Ver detalle</Link> }]} /></section> : <EmptyState headline="Todavía no hay envíos" explanation={selectedStateLabel ? `No hay registros con estado ${selectedStateLabel.toLowerCase()}.` : "Los envíos aparecerán acá cuando una campaña o una respuesta los genere."} actionLabel="Volver al resumen" actionHref="/dashboard" />}
  </>;
}
