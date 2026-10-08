"use client";

import { ArrowRightOutlined, ExclamationCircleOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { Select, Table } from "antd";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  displayValueMap,
  type SemanticLevel,
} from "@/components/design-system";
import { useAuth } from "@/components/auth-provider";
import { automationState } from "@/app/automation/automation-labels";
import { buildProblems } from "./dashboard-helpers";
import { SendModeControl } from "./send-mode-control";
import { can,
  getAttention,
  getAutomationConfiguration,
  getDashboardSummary,
  getInboundMessages,
  problemMessage,
  type AttentionTask,
  type AutomationConfiguration,
  type DashboardCampaign,
  type DashboardSummary,
  type InboundMessage,
  type Problem,
} from "@/lib/api";

type AttentionItem =
  | { kind: "task"; id: string; title: string; contact: string; contactId: string; reason: string; href: string; openedAt: string }
  | { kind: "response"; id: string; title: string; contact: string; contactId: string | null; reason: string; href: string; openedAt: string };

const activeCampaignStates = new Set(["DRAFT", "DISCOVERING", "AWAITING_APPROVAL", "RUNNING", "PAUSED"]);

function percentage(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function duration(value: number | null): string {
  if (value === null) return "—";
  const minutes = Math.round(value / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours} h ${remainder} min` : `${hours} h`;
}

function campaignLevel(state: string): SemanticLevel {
  if (state === "RUNNING") return "success";
  if (state === "PAUSED" || state === "STOPPED_ERROR") return "warning";
  if (state === "CANCELLED" || state === "COMPLETED") return "inactive";
  return "info";
}

function nextCampaignAction(state: string, isAdmin: boolean): string {
  if (!isAdmin) return "Consultar detalle";
  switch (state) {
    case "DRAFT": return "Iniciar búsqueda";
    case "DISCOVERING": return "Esperar resultados";
    case "AWAITING_APPROVAL": return "Aprobar campaña";
    case "RUNNING": return "Pausar campaña";
    case "PAUSED": return "Reanudar campaña";
    default: return "Sin acciones disponibles";
  }
}

function buildAttentionItems(tasks: AttentionTask[], messages: InboundMessage[]): AttentionItem[] {
  const taskItems: AttentionItem[] = tasks.map((task) => ({
    kind: "task",
    id: task.id,
    title: task.title,
    contact: task.contact_name,
    contactId: task.contact_id,
    reason: task.reason || task.summary || task.next_step || "Revisión humana pendiente.",
    href: "/attention",
    openedAt: task.opened_at,
  }));
  const responseItems: AttentionItem[] = messages.filter((message) => !message.is_read).map((message) => ({
    kind: "response",
    id: message.id,
    title: message.subject || "Respuesta sin asunto",
    contact: message.sender,
    contactId: null,
    reason: message.classification_label || "Respuesta nueva",
    href: `/responses/${message.id}`,
    openedAt: message.external_at,
  }));
  return [...taskItems, ...responseItems].sort((left, right) => right.openedAt.localeCompare(left.openedAt));
}

export default function DashboardPage() {
  const { session } = useAuth();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [tasks, setTasks] = useState<AttentionTask[]>([]);
  const [messages, setMessages] = useState<InboundMessage[]>([]);
  const [campaignId, setCampaignId] = useState<string>();
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [automation, setAutomation] = useState<AutomationConfiguration | null>(null);
  const [reload, setReload] = useState(0);
  const canSeeAutomation = can(session, "manage_automation");

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getDashboardSummary(campaignId), getAttention(), getInboundMessages()])
      .then(([nextSummary, nextTasks, nextMessages]) => {
        if (cancelled) return;
        setSummary(nextSummary);
        setTasks(nextTasks);
        setMessages(nextMessages.data);
        setError(null);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [campaignId, reload]);

  useEffect(() => {
    if (!canSeeAutomation) return;
    // An extra the summary does not depend on: if it fails the card simply keeps its older wording.
    void getAutomationConfiguration().then(setAutomation).catch(() => undefined);
  }, [canSeeAutomation]);

  const attentionItems = useMemo(() => buildAttentionItems(tasks, messages), [tasks, messages]);

  if (loading && !summary) return <LoadingState layout="detail" label="Cargando el resumen" />;
  if (error && !summary) {
    return <ErrorState failed="No se pudo cargar el resumen" instruction={problemMessage(error as Problem) || "Revisá la conexión y volvé a intentarlo."} onRetry={() => window.location.reload()} />;
  }
  if (!summary) return null;

  const isSimulation = !summary.safety.send_effective_live;
  const canChangeSend = can(session, "manage_integrations");
  const isAdmin = can(session, "manage_campaigns");
  const activeCampaigns = summary.campaigns.filter((campaign) => activeCampaignStates.has(campaign.state));
  const problems = buildProblems(summary);
  const automationNow = automation ? automationState(automation.mode) : null;
  const campaignOptions = summary.campaigns.map((campaign) => ({ label: campaign.name, value: campaign.id }));

  return (
    <>
      <PageHeader
        title="Resumen"
        description="Qué cambió, qué necesita atención y qué es seguro hacer ahora."
        filters={<Select allowClear aria-label="Filtrar por campaña" placeholder="Todas las campañas" value={campaignId} onChange={setCampaignId} options={campaignOptions} style={{ minWidth: 220 }} />}
      />

      {error ? <div className="dashboard-inline-error" role="alert">{problemMessage(error as Problem)}</div> : null}

      {problems.length ? (
        <section className="problems" aria-labelledby="problems-heading">
          <h2 className="type-title" id="problems-heading">Para resolver</h2>
          <ul className="problems__list">
            {problems.map((problem) => (
              <li className={`problems__item problems__item--${problem.level}`} key={problem.id}>
                <ExclamationCircleOutlined aria-hidden />
                <div className="problems__copy"><strong>{problem.title}</strong><span>{problem.detail}</span></div>
                <Link className="problems__action" href={problem.href}>{problem.action} <ArrowRightOutlined aria-hidden /></Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className={`safety-band${isSimulation ? " safety-band--simulation" : ""}`} aria-labelledby="safety-heading">
        <div className="safety-band__heading">
          <SafetyCertificateOutlined aria-hidden />
          <div><p className="type-micro">Primero, seguridad</p><h2 className="type-title" id="safety-heading">¿Es seguro operar ahora?</h2></div>
        </div>
        <div className="safety-band__items">
          <div className="safety-band__item"><span className="safety-band__label">Modo de envío</span><span className="safety-band__status"><StatusBadge value={summary.safety.send_effective_live ? "live" : "dry-run"} /></span><span className="safety-band__explanation">{summary.safety.send_effective_live ? displayValueMap.live.explanation : displayValueMap["dry-run"].explanation}{canChangeSend ? <> <SendModeControl safety={summary.safety} onChanged={() => setReload((current) => current + 1)} /></> : null}</span></div>
          <div className="safety-band__item"><span className="safety-band__label">Bloqueo del servidor</span><span className="safety-band__status"><StatusBadge label={summary.safety.send_kill_switch ? "Envíos detenidos" : "Envíos permitidos"} level={summary.safety.send_kill_switch ? "warning" : "success"} /></span><span className="safety-band__explanation">{summary.safety.send_kill_switch ? "El servidor tiene los envíos bloqueados: no sale ningún correo real." : "El servidor permite enviar si las demás condiciones se cumplen."}</span></div>
          <div className="safety-band__item"><span className="safety-band__label">Respuestas automáticas</span><span className="safety-band__status">{automationNow ? <StatusBadge label={automationNow.title} level={automationNow.level} /> : <StatusBadge label={summary.safety.auto_reply_kill_switch ? "Detenidas" : "Habilitadas"} level={summary.safety.auto_reply_kill_switch ? "inactive" : isSimulation ? "info" : "warning"} />}</span><span className="safety-band__explanation">{summary.safety.auto_reply_kill_switch ? "Bloqueadas desde el servidor: no se prepara ni se envía ninguna respuesta." : automationNow ? automationNow.explanation : "El sistema puede preparar respuestas según la configuración vigente."}{canSeeAutomation ? <> <Link href="/automation">Cambiar</Link></> : null}</span></div>
        </div>
      </section>

      <section className="dashboard-section" aria-labelledby="attention-heading">
        <div className="dashboard-section__heading"><div><p className="type-micro">Prioridad operativa</p><h2 className="type-title" id="attention-heading">¿Qué necesita mi atención?</h2></div><span className="section-count data-text">{attentionItems.length}</span></div>
        {attentionItems.length ? <ul className="attention-list">{attentionItems.map((item) => <li className="attention-list__item" key={`${item.kind}-${item.id}`}><div className="attention-list__main"><span className="type-micro">{item.kind === "task" ? "Revisión humana" : "Respuesta nueva"}</span><strong>{item.title}</strong><span className="attention-list__reason">{item.reason}</span><span className="attention-list__contact">{item.contactId ? <Link href={`/contacts/${item.contactId}`}>{item.contact}</Link> : item.contact}</span></div><Link className="attention-list__link" href={item.href}>Abrir <ArrowRightOutlined aria-hidden /></Link></li>)}</ul> : <EmptyState headingId="attention-empty-heading" headline="No hay nada pendiente" explanation="Las revisiones y respuestas nuevas aparecerán acá cuando requieran una decisión." actionLabel={isAdmin ? "Configurar primera campaña" : "Ver contactos"} actionHref={isAdmin ? "/campaigns/new" : "/contacts"} />}
      </section>

      <section className="dashboard-section" aria-labelledby="campaigns-heading">
        <div className="dashboard-section__heading"><div><p className="type-micro">Seguimiento</p><h2 className="type-title" id="campaigns-heading">¿Cómo van las campañas?</h2></div></div>
        {activeCampaigns.length ? <Table<DashboardCampaign> rowKey="id" dataSource={activeCampaigns} pagination={false} scroll={{ x: 680 }} columns={[{ title: "Campaña", dataIndex: "name", render: (name: string, campaign) => <Link href={`/campaigns/${campaign.id}`}>{name}</Link> }, { title: "Estado", dataIndex: "state_label", render: (label: string, campaign) => <StatusBadge label={label} level={campaignLevel(campaign.state)} /> }, { title: "Progreso", dataIndex: "discovery_state_label", render: (label: string) => <span>{label || "Sin datos de búsqueda"}</span> }, { title: "Próxima acción", dataIndex: "state", render: (state: string) => <span>{nextCampaignAction(state, isAdmin)}</span> }]} /> : <EmptyState headingId="campaigns-empty-heading" headline="Todavía no hay campañas activas" explanation="Creá una campaña para definir la audiencia y revisar el siguiente paso operativo." actionLabel={isAdmin ? "Crear campaña" : "Ver campañas"} actionHref={isAdmin ? "/campaigns/new" : "/campaigns"} />}
      </section>

      <section className="dashboard-section dashboard-performance" aria-labelledby="performance-heading">
        <div className="dashboard-section__heading"><div><p className="type-micro">Datos acumulados</p><h2 className="type-title" id="performance-heading">Rendimiento</h2></div></div>
        <dl className="metric-table"><div><dt>Mensajes iniciales enviados</dt><dd>{summary.metrics.initial_messages_sent}</dd></div><div><dt>Tasa de respuesta</dt><dd>{percentage(summary.metrics.response_rate)}</dd></div><div><dt>Tasa de respuesta positiva</dt><dd>{percentage(summary.metrics.positive_response_rate)}</dd></div><div><dt>Respondedores humanos únicos</dt><dd>{summary.metrics.unique_human_responders}</dd></div><div><dt>Rebotes</dt><dd>{percentage(summary.metrics.bounce_rate)}</dd></div><div><dt>Bajas</dt><dd>{percentage(summary.metrics.unsubscribe_rate)}</dd></div><div><dt>Primera respuesta mediana</dt><dd>{duration(summary.metrics.median_first_response_seconds)}</dd></div><div><dt>Intervención humana mediana</dt><dd>{duration(summary.metrics.median_human_intervention_seconds)}</dd></div></dl>
      </section>
    </>
  );
}
