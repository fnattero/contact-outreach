"use client";

import { Alert, Card, Descriptions, Flex, Skeleton, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getIntegrationStatus, type IntegrationStatus } from "@/lib/api";

function configured(value: boolean): string {
  return value ? "Configurada" : "No configurada";
}

export default function IntegrationsSettingsPage() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    void getIntegrationStatus().then(setStatus).catch(setError);
  }, []);

  if (error) return <AuthError error={error} />;
  if (!status) return <Skeleton active paragraph={{ rows: 10 }} />;

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Integraciones</Typography.Title>
        <Typography.Paragraph type="secondary">
          Estado operativo seguro. Las credenciales pertenecen al entorno y nunca se muestran aquí.
        </Typography.Paragraph>
      </div>
      <Alert
        type="info"
        showIcon
        message="Las credenciales se administran como secretos del backend"
        description="Esta pantalla sólo expone proveedor, configuración segura y estado de conexión."
      />
      <Card title="Procesamiento">
        <Descriptions column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Extractor">{status.extractor.provider}</Descriptions.Item>
          <Descriptions.Item label="Confianza mínima">{status.extractor.overture_min_confidence}</Descriptions.Item>
          <Descriptions.Item label="Fetcher web">{status.website_fetcher.provider}</Descriptions.Item>
          <Descriptions.Item label="LLM">{status.llm.provider} · {status.llm.model}</Descriptions.Item>
          <Descriptions.Item label="Credencial LLM">
            <Tag color={status.llm.configured ? "green" : "default"}>{configured(status.llm.configured)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Embeddings">{status.embeddings.provider} · {status.embeddings.model}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="Gmail">
        <Descriptions column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Proveedor">{status.gmail.provider}</Descriptions.Item>
          <Descriptions.Item label="Cuenta conectada">{status.gmail.email ?? "Ninguna"}</Descriptions.Item>
          <Descriptions.Item label="Cliente OAuth">
            <Tag color={status.gmail.oauth_client_id_configured ? "green" : "default"}>
              {configured(status.gmail.oauth_client_id_configured)}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Secreto OAuth">
            <Tag color={status.gmail.credential_configured ? "green" : "default"}>
              {configured(status.gmail.credential_configured)}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Estado">{status.gmail.connection_status}</Descriptions.Item>
        </Descriptions>
      </Card>
    </Flex>
  );
}
