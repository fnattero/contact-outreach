"use client";

import { Button, Descriptions, Flex } from "antd";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal, ErrorState, LoadingState, PageHeader, StatusBadge, type SemanticLevel } from "@/components/design-system";
import { getCampaign, problemMessage, runCampaignAction, type CampaignAction, type CampaignDetail, type Problem } from "@/lib/api";

const stages = [
  { state: "DRAFT", label: "Borrador" },
  { state: "DISCOVERING", label: "Descubrimiento" },
  { state: "AWAITING_APPROVAL", label: "Aprobación" },
  { state: "RUNNING", label: "En curso" },
  { state: "PAUSED", label: "Pausada" },
  { state: "COMPLETED", label: "Completada" },
  { state: "STOPPED_ERROR", label: "Con error" },
] as const;

const actionLabels: Record<CampaignAction, string> = {
  "start-discovery": "Iniciar descubrimiento",
  approve: "Aprobar campaña",
  "start-approved": "Iniciar entrega",
  pause: "Pausar campaña",
  resume: "Reanudar campaña",
  cancel: "Cancelar campaña",
};

const actionConsequences: Record<CampaignAction, readonly [string, ...string[]]> = {
  "start-discovery": ["Se congela la configuración de la audiencia y se inicia la búsqueda de destinatarios.", "La búsqueda se ejecuta en segundo plano y puede dejar la campaña lista para aprobación."],
  approve: ["La aprobación fija la audiencia, el contenido, los adjuntos y el calendario.", "La campaña pasa a ejecución y el backend volverá a comprobar las condiciones de seguridad antes de cada efecto."],
  "start-approved": ["Se inicia la entrega de los mensajes aprobados.", "El backend volverá a comprobar las restricciones, el modo de envío y los interruptores de seguridad."],
  pause: ["Se detiene el trabajo en curso y no se inician nuevos efectos mientras la campaña esté pausada."],
  resume: ["Se reanuda la búsqueda o entrega después de volver a comprobar las condiciones de seguridad."],
  cancel: ["La campaña queda cancelada de forma permanente.", "No se iniciarán nuevos efectos para esta campaña."],
};

function campaignLevel(state: string): SemanticLevel {
  if (state === "RUNNING") return "success";
  if (state === "PAUSED") return "warning";
  if (state === "STOPPED_ERROR") return "inactive";
  if (state === "CANCELLED" || state === "COMPLETED") return "inactive";
  return "info";
}

function stageIndex(state: string): number {
  if (state === "CANCELLED") return 6;
  return Math.max(0, stages.findIndex((stage) => stage.state === state));
}

function availableActions(campaign: CampaignDetail): CampaignAction[] {
  switch (campaign.state) {
    case "DRAFT": return ["start-discovery", "cancel"];
    case "DISCOVERING": return ["pause", "cancel"];
    case "AWAITING_APPROVAL": return [campaign.approval_mode === "PER_MESSAGE" ? "start-approved" : "approve", "cancel"];
    case "RUNNING": return ["pause", "cancel"];
    case "PAUSED": return ["resume", "cancel"];
    default: return [];
  }
}

function weekdays(value?: number[]): string {
  const labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
  return value?.length ? value.map((day) => labels[day] ?? String(day)).join(", ") : "—";
}

function CampaignStages({ state }: { state: string }) {
  const current = stageIndex(state);
  const labels = state === "CANCELLED" ? stages.map((stage, index) => index === 6 ? { ...stage, label: "Cancelada" } : stage) : stages;
  return <ol className="campaign-stages" aria-label="Etapa de la campaña">{labels.map((stage, index) => <li className={`campaign-stage${index === current ? " campaign-stage--current" : ""}${index < current ? " campaign-stage--complete" : ""}`} key={stage.state}><span className="campaign-stage__marker" aria-hidden>{index < current ? "✓" : index + 1}</span><span>{stage.label}</span></li>)}</ol>;
}

function DetailSection({ title, marker, children }: { title: string; marker?: string; children: ReactNode }) {
  return <section className="campaign-detail-section"><div className="campaign-detail-section__heading"><h2 className="type-title">{title}</h2>{marker ? <span className="approval-marker">{marker}</span> : null}</div>{children}</section>;
}

export default function CampaignDetailPage() {
  const params = useParams<{ id: string }>();
  const { session } = useAuth();
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busyAction, setBusyAction] = useState<CampaignAction | null>(null);
  const [confirmAction, setConfirmAction] = useState<CampaignAction | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getCampaign(params.id).then((next) => { if (!cancelled) setCampaign(next); }).catch((problem) => { if (!cancelled) setError(problem); });
    return () => { cancelled = true; };
  }, [params.id]);

  const actions = useMemo(() => campaign ? availableActions(campaign) : [], [campaign]);
  if (error) return <ErrorState failed="No se pudo cargar la campaña" instruction={problemMessage(error as Problem)} onRetry={() => window.location.reload()} />;
  if (!campaign) return <LoadingState layout="detail" label="Cargando detalle de campaña" />;

  const currentCampaign = campaign;
  const isAdmin = session?.role === "ADMIN";
  const attachments = campaign.attachments?.length ? campaign.attachments : campaign.catalog ? [{ catalog_id: campaign.catalog.id, name: campaign.catalog.name, version: campaign.catalog.version, position: 0 }] : [];

  async function performAction() {
    if (!confirmAction) return;
    setBusyAction(confirmAction);
    setError(null);
    try {
      setCampaign(await runCampaignAction(currentCampaign.id, confirmAction));
      setConfirmAction(null);
    } catch (problem) {
      setError(problem);
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <>
      <PageHeader title={campaign.name} description="Detalle operativo, audiencia y condiciones de ejecución." breadcrumbs={<><Link href="/campaigns">Campañas</Link><span> / {campaign.name}</span></>} status={<StatusBadge label={campaign.state_label} level={campaignLevel(campaign.state)} />} />

      <CampaignStages state={campaign.state} />
      {error ? <div className="dashboard-inline-error" role="alert">{problemMessage(error as Problem)}</div> : null}

      <section className="campaign-actions" aria-labelledby="campaign-actions-heading">
        <div className="campaign-detail-section__heading"><div><p className="type-micro">Siguiente paso</p><h2 className="type-title" id="campaign-actions-heading">Acciones disponibles</h2></div></div>
        {isAdmin && actions.length ? <Flex gap="small" wrap>{actions.map((action) => <Button key={action} danger={action === "cancel"} type={action === "approve" || action === "start-approved" || action === "start-discovery" ? "primary" : "default"} onClick={() => setConfirmAction(action)}>{actionLabels[action]}</Button>)}</Flex> : <p className="campaign-blocker">{isAdmin ? "La campaña está en un estado terminal y no admite nuevas transiciones." : "Tu rol es de solo lectura; las acciones de campaña están reservadas para administración."}</p>}
      </section>

      <DetailSection title="Audiencia" marker="Se fija al aprobar">
        {isAdmin ? <Descriptions column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Destinatarios"><span className="data-text">{campaign.metrics?.enrollments ?? "—"}</span></Descriptions.Item>
          <Descriptions.Item label="Prospectos"><span className="data-text">{campaign.metrics?.prospects ?? "—"}</span></Descriptions.Item>
          <Descriptions.Item label="Rubros">{campaign.categories?.map((category) => category.name).join(", ") || "—"}</Descriptions.Item>
          <Descriptions.Item label="Zonas">{campaign.zones?.map((zone) => zone.name).join(", ") || campaign.location_text || "—"}</Descriptions.Item>
        </Descriptions> : <p className="campaign-blocker">El detalle de audiencia está reservado para administración.</p>}
      </DetailSection>

      <DetailSection title="Contenido" marker="Se fija al aprobar">
        <div className="campaign-revision"><StatusBadge label={!isAdmin ? "Solo administración" : campaign.content_hash ? "Revisión fijada" : "Pendiente de aprobación"} level={!isAdmin ? "info" : campaign.content_hash ? "success" : "info"} /><p>{!isAdmin ? "La revisión de contenido no se incluye en la vista de vendedor." : campaign.content_hash ? "La revisión que se usará en la campaña quedó congelada." : "La revisión de contenido se congelará cuando se apruebe la campaña."}</p></div>
      </DetailSection>

      <DetailSection title="Catálogos adjuntos" marker="Se fijan al aprobar">
        {!isAdmin ? <p className="campaign-blocker">Los catálogos adjuntos se muestran a administración.</p> : attachments.length ? <ul className="campaign-attachments">{attachments.map((attachment) => <li key={`${attachment.catalog_id}-${attachment.position}`}><span>{attachment.name}</span><span className="data-text">v{attachment.version}</span></li>)}</ul> : <p className="campaign-blocker">No hay catálogos adjuntos disponibles.</p>}
      </DetailSection>

      <DetailSection title="Calendario" marker="Se fija al aprobar">
        {isAdmin ? <Descriptions column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Días">{weekdays(campaign.weekdays)}</Descriptions.Item>
          <Descriptions.Item label="Horario">{campaign.window_start && campaign.window_end ? `${campaign.window_start}–${campaign.window_end}` : "—"}</Descriptions.Item>
          <Descriptions.Item label="Zona horaria">{campaign.timezone_name || "—"}</Descriptions.Item>
          <Descriptions.Item label="Límite diario"><span className="data-text">{campaign.daily_limit ?? "—"}</span></Descriptions.Item>
          <Descriptions.Item label="Intervalo entre mensajes"><span className="data-text">{campaign.message_interval_minutes ? `${campaign.message_interval_minutes} min` : "—"}</span></Descriptions.Item>
        </Descriptions> : <p className="campaign-blocker">El calendario se muestra a administración.</p>}
      </DetailSection>

      {confirmAction ? <ConfirmDangerModal open title={actionLabels[confirmAction]} consequences={actionConsequences[confirmAction]} confirmationWord="CONFIRMAR" dangerLabel={actionLabels[confirmAction]} safeLabel="Volver" confirming={busyAction === confirmAction} onCancel={() => setConfirmAction(null)} onConfirm={() => void performAction()} /> : null}
    </>
  );
}
