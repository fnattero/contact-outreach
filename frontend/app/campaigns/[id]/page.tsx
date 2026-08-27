"use client";

import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Flex,
  Popconfirm,
  Skeleton,
  Tag,
  Typography,
} from "antd";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
  getCampaign,
  problemMessage,
  runCampaignAction,
  type CampaignAction,
  type CampaignDetail,
  type Problem,
} from "@/lib/api";

export default function CampaignDetailPage() {
  const params = useParams<{ id: string }>();
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busyAction, setBusyAction] = useState<CampaignAction | null>(null);
  const { session } = useAuth();

  useEffect(() => {
    let cancelled = false;
    void getCampaign(params.id)
      .then((nextCampaign) => {
        if (!cancelled) setCampaign(nextCampaign);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      });
    return () => {
      cancelled = true;
    };
  }, [params.id]);

  if (error) return <AuthError error={error} />;
  if (!campaign) return <Skeleton active paragraph={{ rows: 8 }} />;
  const currentCampaign = campaign;

  async function performAction(action: CampaignAction) {
    setBusyAction(action);
    setError(null);
    try {
      setCampaign(await runCampaignAction(currentCampaign.id, action));
    } catch (problem) {
      setError(problem);
    } finally {
      setBusyAction(null);
    }
  }

  const actionLabel: Record<CampaignAction, string> = {
    "start-discovery": "Iniciar descubrimiento",
    approve: "Aprobar audiencia y contenido",
    "start-approved": "Iniciar entrega",
    pause: "Pausar",
    resume: "Reanudar",
    cancel: "Cancelar",
  };
  const availableActions: CampaignAction[] =
    campaign.state === "DRAFT"
      ? ["start-discovery"]
      : campaign.state === "AWAITING_APPROVAL"
        ? [campaign.approval_mode === "PER_MESSAGE" ? "start-approved" : "approve"]
        : campaign.state === "RUNNING"
            ? ["pause", "cancel"]
          : campaign.state === "PAUSED"
            ? ["resume", "cancel"]
            : [];

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>{campaign.name}</Typography.Title>
        <Flex gap="small" wrap>
          <Tag>{campaign.state_label}</Tag>
          <Tag>{campaign.delivery_mode}</Tag>
          <Tag>{campaign.discovery_state_label}</Tag>
        </Flex>
      </div>
      {session?.role === "ADMIN" && availableActions.length ? (
        <Card title="Acciones de campaña">
          <Flex gap="small" wrap>
            {availableActions.map((action) => (
              <Popconfirm
                key={action}
                title={action === "cancel" ? "¿Cancelar esta campaña?" : actionLabel[action]}
                description={
                  action === "start-approved"
                    ? "El backend volverá a comprobar las restricciones y los kill switches antes de cada efecto."
                    : undefined
                }
                okText="Confirmar"
                cancelText="Volver"
                onConfirm={() => void performAction(action)}
              >
                <Button
                  danger={action === "cancel"}
                  type={action === "start-approved" ? "primary" : "default"}
                  loading={busyAction === action}
                >
                  {actionLabel[action]}
                </Button>
              </Popconfirm>
            ))}
          </Flex>
        </Card>
      ) : null}
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card title="Configuración">
        <Descriptions column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Objetivo">{campaign.objective ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Ubicación">{campaign.location_text ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Aprobación">{campaign.approval_mode}</Descriptions.Item>
          <Descriptions.Item label="Límite diario">{campaign.daily_limit ?? "—"}</Descriptions.Item>
        </Descriptions>
      </Card>
      {campaign.metrics ? (
        <Card title="Progreso">
          <Descriptions column={{ xs: 1, sm: 2 }}>
            <Descriptions.Item label="Destinatarios">{campaign.metrics.enrollments}</Descriptions.Item>
            <Descriptions.Item label="Prospectos">{campaign.metrics.prospects}</Descriptions.Item>
            <Descriptions.Item label="Mensajes iniciales">{campaign.metrics.initial_messages}</Descriptions.Item>
            <Descriptions.Item label="Enviados">{campaign.metrics.sent}</Descriptions.Item>
            <Descriptions.Item label="En revisión">{campaign.metrics.review_ready}</Descriptions.Item>
            <Descriptions.Item label="En cola">{campaign.metrics.queued}</Descriptions.Item>
          </Descriptions>
        </Card>
      ) : <Empty description="No hay detalles operativos para este rol." />}
      {campaign.status_reason ? <Alert type="warning" showIcon message={campaign.status_reason} /> : null}
    </Flex>
  );
}
