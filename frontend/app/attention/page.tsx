"use client";

import { Button, Input } from "antd";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal, EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge, type SemanticLevel } from "@/components/design-system";
import { getAttention, problemMessage, resolveHumanTask, type AttentionTask, type Problem } from "@/lib/api";

type Urgency = { label: string; rank: number; level: SemanticLevel };

function urgencyFor(reason: string): Urgency {
  if (["COMPLAINT", "LEGAL_OR_PRIVACY", "POLICY_RECHECK_FAILED", "GMAIL_NOT_READY"].includes(reason)) return { label: "Alta", rank: 3, level: "danger" };
  if (["MEETING_OR_DATE", "PRICING_OR_QUOTE", "NEGOTIATION", "SCHEDULED_DELIVERY_FAILED"].includes(reason)) return { label: "Media", rank: 2, level: "warning" };
  return { label: "Normal", rank: 1, level: "info" };
}

export default function AttentionPage() {
  const { session } = useAuth();
  const [tasks, setTasks] = useState<AttentionTask[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<Record<string, string>>({});
  const [pendingAction, setPendingAction] = useState<{ task: AttentionTask; action: "resolve" | "dismiss" } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getAttention().then((next) => { if (!cancelled) setTasks(next); }).catch((problem) => { if (!cancelled) setError(problem); });
    return () => { cancelled = true; };
  }, []);

  const orderedTasks = useMemo(() => [...(tasks ?? [])].sort((left, right) => {
    const urgencyDifference = urgencyFor(right.reason).rank - urgencyFor(left.reason).rank;
    return urgencyDifference || left.opened_at.localeCompare(right.opened_at);
  }), [tasks]);

  async function closeTask() {
    if (!pendingAction) return;
    setBusy(true);
    setError(null);
    try {
      await resolveHumanTask(pendingAction.task.id, pendingAction.action, note[pendingAction.task.id] || "Revisado por el equipo.");
      setTasks((current) => current?.filter((task) => task.id !== pendingAction.task.id) ?? []);
      setPendingAction(null);
    } catch (problem) {
      setError(problem);
    } finally {
      setBusy(false);
    }
  }

  if (error && !tasks) return <ErrorState failed="No se pudo cargar la cola de atención" instruction={problemMessage(error as Problem)} onRetry={() => window.location.reload()} />;
  if (!tasks) return <LoadingState layout="list" label="Cargando cola de atención" />;

  return <>
    <PageHeader title="Necesita atención" description="Revisá cada caso antes de permitir que la automatización continúe." status={<StatusBadge label="Las revisiones abiertas suspenden la automatización" level="warning" />} />
    {error ? <div className="dashboard-inline-error" role="alert">{problemMessage(error as Problem)}</div> : null}
    {orderedTasks.length ? <ul className="attention-queue" aria-label="Revisiones pendientes">{orderedTasks.map((task) => { const urgency = urgencyFor(task.reason); return <li className="attention-queue__item" key={task.id}><div className="attention-queue__urgency"><StatusBadge label={urgency.label} level={urgency.level} /><time dateTime={task.opened_at}>{new Date(task.opened_at).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" })}</time></div><div className="attention-queue__content"><span className="type-micro">Motivo de revisión</span><h2 className="type-title">{task.title}</h2><p>{task.summary}</p><p className="attention-queue__next"><strong>Próximo paso:</strong> {task.next_step || "Revisar el caso y decidir."}</p><Link href={`/contacts/${task.contact_id}`}>{task.contact_name}</Link></div>{session?.role === "ADMIN" ? <div className="attention-queue__decision"><Input.TextArea aria-label={`Nota para ${task.contact_name}`} placeholder="Nota de resolución" value={note[task.id] ?? ""} onChange={(event) => setNote((current) => ({ ...current, [task.id]: event.target.value }))} rows={2} /><div><Button type="primary" onClick={() => setPendingAction({ task, action: "resolve" })}>Resolver</Button><Button onClick={() => setPendingAction({ task, action: "dismiss" })}>Descartar</Button></div></div> : <p className="attention-queue__blocker">Tu rol es de solo lectura; la decisión corresponde a administración.</p>}</li>; })}</ul> : <EmptyState headline="No hay revisiones abiertas" explanation="La automatización puede continuar: si un caso necesita una decisión, aparecerá en esta cola." actionLabel="Volver al resumen" actionHref="/dashboard" />}
    {pendingAction ? <ConfirmDangerModal open title={pendingAction.action === "resolve" ? "Resolver revisión" : "Descartar revisión"} consequences={pendingAction.action === "resolve" ? ["La revisión se cerrará y la automatización podrá continuar para este contacto si las demás condiciones se cumplen.", "La decisión quedará registrada con la nota indicada."] : ["La revisión se quitará de la cola y dejará de bloquear este caso.", "La decisión quedará registrada con la nota indicada."]} confirmationWord="CONFIRMAR" dangerLabel={pendingAction.action === "resolve" ? "Resolver revisión" : "Descartar revisión"} safeLabel="Volver" confirming={busy} onCancel={() => setPendingAction(null)} onConfirm={() => void closeTask()} /> : null}
  </>;
}
