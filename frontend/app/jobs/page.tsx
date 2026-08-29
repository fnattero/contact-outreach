"use client";

import { Alert, Button, Card, Flex, Input, Table } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { DisabledReason } from "@/components/design-system/disabled-reason";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import { getBackgroundJobs, problemMessage, retryBackgroundJob, type BackgroundJob, type Problem } from "@/lib/api";

const dateFormatter = new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeStyle: "short", timeZone: "America/Argentina/Buenos_Aires" });
const taskLabels: Record<string, string> = { "campaign.discovery": "Descubrimiento de campaña", "campaign.delivery": "Entrega de campaña", "gmail.sync": "Sincronización de Gmail", "prospect.enrichment": "Enriquecimiento de contactos" };
const entityLabels: Record<string, string> = { OutboundMessage: "Mensaje", Campaign: "Campaña", Contact: "Contacto" };

function taskLabel(value: string): string { return taskLabels[value] ?? (value.includes("discovery") ? "Descubrimiento de campaña" : value.includes("send") || value.includes("delivery") ? "Entrega de campaña" : "Tarea operativa"); }
function entityLabel(value: string): string { return entityLabels[value] ?? "Recurso operativo"; }
function statusLevel(state: string): "success" | "warning" | "danger" | "inactive" | "info" { return state === "SUCCEEDED" || state === "COMPLETED" ? "success" : state === "FAILED" ? "danger" : state === "RUNNING" ? "info" : "inactive"; }

export default function JobsPage() {
  const { session } = useAuth();
  const [jobs, setJobs] = useState<BackgroundJob[]>([]);
  const [reason, setReason] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  function refresh() { setLoading(true); void getBackgroundJobs().then((response) => setJobs(response.data)).catch(setError).finally(() => setLoading(false)); }
  useEffect(() => { void getBackgroundJobs().then((response) => setJobs(response.data)).catch(setError).finally(() => setLoading(false)); }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para ver tareas." }} />;
  if (loading) return <LoadingState layout="list" />;
  if (error && !jobs.length) return <ErrorState failed="No se pudieron cargar las tareas" instruction={problemMessage(error as Problem)} onRetry={refresh} />;

  async function retry(job: BackgroundJob) {
    setBusy(job.id); setError(null);
    try { await retryBackgroundJob(job.id, reason[job.id] ?? "Se corrigió la causa del fallo y se reintentará la misma operación."); refresh(); }
    catch (problem) { setError(problem); }
    finally { setBusy(null); }
  }

  return <Flex vertical gap="large">
    <PageHeader title="Tareas" description="Estado durable de trabajos del backend. Los errores se muestran con una explicación clara." />
    {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
    {jobs.length ? <Card><Table<BackgroundJob> rowKey="id" dataSource={jobs} pagination={{ pageSize: 20, responsive: true }} columns={[{ title: "Estado", dataIndex: "state", render: (_: string, job) => <StatusBadge label={job.state_label || (job.state === "FAILED" ? "Falló" : "En curso")} level={statusLevel(job.state)} /> }, { title: "Tipo", dataIndex: "task_name", render: (value: string) => taskLabel(value) }, { title: "Última ejecución", dataIndex: "finished_at", render: (value: string | null, job) => <time className="data-text" dateTime={value ?? job.started_at ?? job.created_at}>{dateFormatter.format(new Date(value ?? job.started_at ?? job.created_at))}</time> }, { title: "Próxima ejecución", dataIndex: "next_retry_at", render: (value: string | null) => value ? <time className="data-text" dateTime={value}>{dateFormatter.format(new Date(value))}</time> : "No programada" }, { title: "Resultado", render: (_: unknown, job) => job.error ? <span className="job-outcome job-outcome--error">La tarea falló; abrí el detalle para ver la causa.</span> : job.state === "SUCCEEDED" ? "Completada" : "Sin resultado final" }]} expandable={{ expandedRowRender: (job) => <div className="job-detail"><p><strong>{entityLabel(job.entity_type)}</strong> · el trabajo tiene {job.attempts} intento(s).</p>{job.error ? <details className="integration-technical"><summary>Detalles técnicos del error</summary><p className="job-error-plain">{job.error}</p><code>{job.error}</code></details> : <p>Esta tarea no registró un error.</p>}{job.entity_type === "OutboundMessage" && job.state === "FAILED" ? <Flex gap="small" wrap><Input aria-label={`Motivo de reintento ${job.id}`} placeholder="Motivo de reintento (mínimo 10 caracteres)" value={reason[job.id] ?? ""} onChange={(event) => setReason((current) => ({ ...current, [job.id]: event.target.value }))} /><DisabledReason disabled={busy === job.id || (reason[job.id] ?? "").trim().length < 10} reason={busy === job.id ? "El reintento está en curso." : "Escribí al menos 10 caracteres para explicar el reintento."}><Button loading={busy === job.id} onClick={() => void retry(job)}>Reintentar</Button></DisabledReason></Flex> : null}</div> }} /></Card> : <EmptyState headline="No hay tareas registradas" explanation="Las tareas aparecerán cuando el sistema procese una importación, campaña o sincronización." actionLabel="Actualizar estado" onAction={refresh} />}
  </Flex>;
}
