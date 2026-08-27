"use client";

import { Alert, Card, Col, Empty, Flex, Row, Select, Skeleton, Statistic, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getDashboardSummary, type DashboardSummary } from "@/lib/api";

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

export default function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [campaignId, setCampaignId] = useState<string>();
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void getDashboardSummary(campaignId)
      .then((nextSummary) => {
        if (!cancelled) {
          setSummary(nextSummary);
          setError(null);
        }
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  if (error) return <AuthError error={error} />;
  if (loading && !summary) {
    return <Skeleton active paragraph={{ rows: 8 }} />;
  }
  if (!summary) return <Empty description="No hay datos para mostrar." />;

  const { metrics } = summary;
  return (
    <Flex vertical gap="large">
      <Flex justify="space-between" align="center" gap="middle" wrap>
        <div>
          <Typography.Title level={2}>Resumen</Typography.Title>
          <Typography.Paragraph type="secondary">
            Métricas derivadas de los efectos durables de la operación.
          </Typography.Paragraph>
        </div>
        <Select
          allowClear
          aria-label="Filtrar por campaña"
          placeholder="Todas las campañas"
          value={campaignId}
          onChange={setCampaignId}
          options={summary.campaigns.map((campaign) => ({ label: campaign.name, value: campaign.id }))}
          style={{ minWidth: 220 }}
        />
      </Flex>

      <Row gutter={[16, 16]}>
        {[
          ["Campañas", summary.summary.campaigns],
          ["Catálogos activos", summary.summary.catalogs],
          ["Respuestas", summary.summary.responses],
          ["Tareas abiertas", summary.attention.open_human_tasks],
        ].map(([title, value]) => (
          <Col key={title} xs={24} sm={12} lg={6}>
            <Card><Statistic title={title} value={value} /></Card>
          </Col>
        ))}
      </Row>

      <Card title="Rendimiento">
        <Row gutter={[16, 24]}>
          <Col xs={12} sm={8} lg={6}><Statistic title="Enviados iniciales" value={metrics.initial_messages_sent} /></Col>
          <Col xs={12} sm={8} lg={6}><Statistic title="Tasa de respuesta" value={percentage(metrics.response_rate)} /></Col>
          <Col xs={12} sm={8} lg={6}><Statistic title="Tasa positiva" value={percentage(metrics.positive_response_rate)} /></Col>
          <Col xs={12} sm={8} lg={6}><Statistic title="Rebotes" value={percentage(metrics.bounce_rate)} /></Col>
          <Col xs={12} sm={8} lg={6}><Statistic title="Bajas" value={percentage(metrics.unsubscribe_rate)} /></Col>
          <Col xs={12} sm={8} lg={6}><Statistic title="Respondedores únicos" value={metrics.unique_human_responders} /></Col>
          <Col xs={12} sm={8} lg={6}><Statistic title="Primera respuesta mediana" value={duration(metrics.median_first_response_seconds)} /></Col>
          <Col xs={12} sm={8} lg={6}><Statistic title="Intervención mediana" value={duration(metrics.median_human_intervention_seconds)} /></Col>
        </Row>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title="Campañas">
            {summary.campaigns.length ? summary.campaigns.map((campaign) => (
              <Flex key={campaign.id} justify="space-between" align="center" wrap gap="small" style={{ padding: "10px 0" }}>
                <Typography.Text strong>{campaign.name}</Typography.Text>
                <Tag>{campaign.state_label}</Tag>
              </Flex>
            )) : <Empty description="Todavía no hay campañas." />}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="Controles de seguridad">
            <Flex vertical gap="small">
              <Typography.Text>Modo de envío: <Tag color={summary.safety.send_mode === "dry-run" ? "green" : "orange"}>{summary.safety.send_mode}</Tag></Typography.Text>
              <Typography.Text>Envío protegido: <Tag color={summary.safety.send_kill_switch ? "green" : "red"}>{summary.safety.send_kill_switch ? "bloqueado" : "habilitado"}</Tag></Typography.Text>
              <Typography.Text>Respuestas automáticas: <Tag color={summary.safety.auto_reply_kill_switch ? "green" : "red"}>{summary.safety.auto_reply_kill_switch ? "bloqueadas" : "habilitadas"}</Tag></Typography.Text>
              {summary.attention.paused_campaigns > 0 ? <Alert type="warning" showIcon message={`${summary.attention.paused_campaigns} campaña(s) requieren atención.`} /> : null}
            </Flex>
          </Card>
        </Col>
      </Row>
    </Flex>
  );
}
