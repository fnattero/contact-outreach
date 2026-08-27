"use client";

import { Alert, Card, Descriptions, Empty, Flex, Skeleton, Tag, Typography } from "antd";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getCampaign, type CampaignDetail } from "@/lib/api";

export default function CampaignDetailPage() {
  const params = useParams<{ id: string }>();
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);
  const [error, setError] = useState<unknown>(null);

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
