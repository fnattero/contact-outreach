"use client";

import { Button, Form, Input } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal, DisabledReason, EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/design-system";
import {
  getAttention,
  getAutomationConfiguration,
  getCampaigns,
  getDashboardSummary,
  getInboundMessages,
  getInboundThread,
  problemMessage,
  sendManualReply,
  type AttentionTask,
  type AutomationConfiguration,
  type DashboardSummary,
  type InboundMessage,
  type InboundThread,
  type Problem,
} from "@/lib/api";

function dateLabel(value: string): string {
  return new Date(value).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
}

function automationReplyText(configuration: AutomationConfiguration | null, summary: DashboardSummary | null): string {
  if (summary?.safety.send_mode !== "live" || summary.safety.send_kill_switch) return "La respuesta manual no está disponible: el envío global está en simulación o protegido.";
  if (!configuration) return "Envío real: Gmail y las políticas se vuelven a comprobar antes de encolar.";
  if (configuration.mode === "OFF") return "La automatización está desactivada; una respuesta manual sigue sujeta al envío real y a las políticas.";
  if (configuration.mode === "SHADOW") return "La automatización está en observación; la respuesta manual se envía solo después de las comprobaciones de seguridad.";
  return "La automatización está activa; la respuesta manual se encola después de las comprobaciones de seguridad.";
}

function Conversation({ thread }: { thread: InboundThread }) {
  return <div className="conversation" aria-label="Conversación en orden cronológico">{thread.timeline.length ? thread.timeline.map((item, index) => <article className={`conversation-message conversation-message--${item.direction}`} key={`${item.at}-${index}`}><div className="conversation-message__meta"><span className="type-micro">{item.direction === "inbound" ? "Recibido" : "Enviado"}</span><span>{item.sender}</span><time dateTime={item.at}>{dateLabel(item.at)}</time></div><p>{item.body_text}</p></article>) : <p className="responses-empty-thread">No hay mensajes en este hilo.</p>}</div>;
}

export default function ResponsesPage() {
  const { session } = useAuth();
  const [messages, setMessages] = useState<InboundMessage[]>([]);
  const [tasks, setTasks] = useState<AttentionTask[]>([]);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [automation, setAutomation] = useState<AutomationConfiguration | null>(null);
  const [campaignNames, setCampaignNames] = useState<Record<string, string>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [thread, setThread] = useState<InboundThread | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [replyBody, setReplyBody] = useState("");
  const [confirmReply, setConfirmReply] = useState(false);
  const [replyBusy, setReplyBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const automationRequest = session?.role === "ADMIN" ? getAutomationConfiguration() : Promise.resolve(null);
    void Promise.all([getInboundMessages(), getAttention(), getDashboardSummary(), automationRequest, getCampaigns()])
      .then(([inbound, attention, nextSummary, nextAutomation, campaignPage]) => {
        if (cancelled) return;
        setMessages(inbound.data);
        setTasks(attention);
        setSummary(nextSummary);
        setAutomation(nextAutomation);
        setCampaignNames(Object.fromEntries(campaignPage.data.map((campaign) => [campaign.id, campaign.name])));
        setSelectedId((current) => current ?? inbound.data[0]?.id ?? null);
      })
      .catch((problem) => { if (!cancelled) setError(problem); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [session?.role]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    void getInboundThread(selectedId).then((next) => { if (!cancelled) setThread(next); }).catch((problem) => { if (!cancelled) setError(problem); });
    return () => { cancelled = true; };
  }, [selectedId]);

  const selectedMessage = useMemo(() => messages.find((message) => message.id === selectedId) ?? null, [messages, selectedId]);
  const reviewPending = selectedMessage ? tasks.some((task) => task.contact_name.toLocaleLowerCase() === selectedMessage.sender.toLocaleLowerCase()) : false;
  const canReply = Boolean(session?.capabilities.includes("send_replies") && selectedMessage?.is_human && summary?.safety.send_mode === "live" && !summary.safety.send_kill_switch);
  const replyBlocker = !selectedMessage?.is_human ? "Solo se puede responder a mensajes de una persona." : summary?.safety.send_mode !== "live" ? "El modo de envío no es Envío real." : summary?.safety.send_kill_switch ? "El envío está protegido por el interruptor de seguridad." : "Escribí una respuesta para continuar.";

  async function submitReply() {
    if (!selectedId || !replyBody.trim()) return;
    setReplyBusy(true);
    setError(null);
    try {
      await sendManualReply(selectedId, replyBody.trim(), crypto.randomUUID());
      setReplyBody("");
      setConfirmReply(false);
      setThread(await getInboundThread(selectedId));
    } catch (problem) {
      setError(problem);
    } finally {
      setReplyBusy(false);
    }
  }

  if (loading) return <LoadingState layout="detail" label="Cargando respuestas" />;
  if (error && !messages.length) return <ErrorState failed="No se pudieron cargar las respuestas" instruction={problemMessage(error as Problem)} onRetry={() => window.location.reload()} />;

  return <>
    <PageHeader title="Respuestas" description="Conversaciones recibidas y clasificadas por el backend." />
    {error ? <div className="dashboard-inline-error" role="alert">{problemMessage(error as Problem)}</div> : null}
    {messages.length ? <div className="responses-inbox">
      <aside className="responses-inbox__list" aria-label="Lista de conversaciones">
        <div className="responses-inbox__list-heading"><h2 className="type-title">Bandeja</h2><span className="data-text">{messages.length}</span></div>
        <ul>{messages.map((message) => {
          const unread = !message.is_read;
          const pending = tasks.some((task) => task.contact_name.toLocaleLowerCase() === message.sender.toLocaleLowerCase());
          return <li key={message.id}><button className={`response-thread-row${selectedId === message.id ? " response-thread-row--selected" : ""}${unread ? " response-thread-row--unread" : ""}`} type="button" onClick={() => { setThread(null); setSelectedId(message.id); }} aria-current={selectedId === message.id ? "true" : undefined}><span className="response-thread-row__top"><strong>{message.sender}</strong><time dateTime={message.external_at}>{dateLabel(message.external_at)}</time></span><span className="response-thread-row__subject">{message.subject || "Sin asunto"}</span><span className="response-thread-row__snippet">{message.body_preview}</span><span className="response-thread-row__markers">{unread ? <span className="response-marker response-marker--unread"><span aria-hidden>●</span> No leída</span> : null}{pending ? <span className="response-marker response-marker--review">Revisión pendiente</span> : null}</span><span className="response-thread-row__campaign">{message.campaign_id ? campaignNames[message.campaign_id] || "Campaña vinculada" : "Respuesta directa"}</span></button></li>;
        })}</ul>
      </aside>
      <section className="responses-inbox__conversation" aria-label="Conversación seleccionada">
        {!thread ? <LoadingState layout="detail" label="Cargando conversación" /> : <><header className="conversation-header"><div><p className="type-micro">{selectedMessage?.sender}</p><h2 className="type-title">{selectedMessage?.subject || "Conversación"}</h2></div><div className="conversation-header__markers">{selectedMessage?.classification_label ? <StatusBadge label={selectedMessage.classification_label} level="info" /> : null}{reviewPending ? <StatusBadge label="Revisión pendiente" level="warning" /> : null}</div></header><Conversation thread={thread} />{selectedMessage?.is_human && session?.capabilities.includes("send_replies") ? <div className="reply-panel"><div className="reply-panel__heading"><h3 className="type-title">Responder</h3><p>{automationReplyText(automation, summary)}</p></div><Form layout="vertical" onFinish={() => setConfirmReply(true)}><Form.Item label="Texto de la respuesta" required><Input.TextArea rows={5} value={replyBody} onChange={(event) => setReplyBody(event.target.value)} maxLength={10000} showCount /></Form.Item><DisabledReason disabled={!canReply || !replyBody.trim()} reason={replyBlocker}><Button type="primary" htmlType="submit" disabled={!canReply || !replyBody.trim()}>Revisar y autorizar</Button></DisabledReason></Form></div> : null}</>}
      </section>
    </div> : <EmptyState headline="Todavía no hay respuestas" explanation="Las conversaciones entrantes aparecerán acá cuando Gmail las sincronice." actionLabel={session?.role === "ADMIN" ? "Configurar Gmail" : "Volver al resumen"} actionHref={session?.role === "ADMIN" ? "/settings/integrations" : "/dashboard"} />}
    {confirmReply ? <ConfirmDangerModal open title="Autorizar respuesta" consequences={["La respuesta se enviará al contacto de esta conversación si todas las comprobaciones siguen siendo válidas.", "El backend volverá a comprobar el modo de envío, Gmail, restricciones y el contexto del hilo."]} confirmationWord="CONFIRMAR" dangerLabel="Autorizar y enviar" confirming={replyBusy} onCancel={() => setConfirmReply(false)} onConfirm={() => void submitReply()} /> : null}
  </>;
}
