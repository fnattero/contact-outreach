"use client";

import { Alert, Button, Card, Descriptions, Flex, Skeleton, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import {
  disconnectGmail,
  getGmailConnection,
  getIntegrationStatus,
  problemMessage,
  startGmailOAuth,
  testGmailConnection,
  type GmailConnection,
  type IntegrationStatus,
  type Problem,
} from "@/lib/api";

function configured(value: boolean): string {
  return value ? "Configurada" : "No configurada";
}

export default function IntegrationsSettingsPage() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [gmail, setGmail] = useState<GmailConnection | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [gmailBusy, setGmailBusy] = useState(false);

  useEffect(() => {
    void Promise.all([getIntegrationStatus(), getGmailConnection()])
      .then(([nextStatus, nextGmail]) => {
        setStatus(nextStatus);
        setGmail(nextGmail);
      })
      .catch(setError);
  }, []);

  if (error && !status) return <AuthError error={error} />;
  if (!status) return <Skeleton active paragraph={{ rows: 10 }} />;

  async function connect() {
    setGmailBusy(true);
    setError(null);
    try {
      const result = await startGmailOAuth();
      window.location.assign(result.authorization_url);
    } catch (problem) {
      setError(problem);
      setGmailBusy(false);
    }
  }

  async function test() {
    setGmailBusy(true);
    setError(null);
    try {
      await testGmailConnection();
      setGmail(await getGmailConnection());
    } catch (problem) {
      setError(problem);
    } finally {
      setGmailBusy(false);
    }
  }

  async function disconnect() {
    setGmailBusy(true);
    setError(null);
    try {
      setGmail(await disconnectGmail());
    } catch (problem) {
      setError(problem);
    } finally {
      setGmailBusy(false);
    }
  }

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Integraciones</Typography.Title>
        <Typography.Paragraph type="secondary">
          Estado operativo seguro. Las credenciales pertenecen al entorno y nunca se muestran aquí.
        </Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
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
        <Flex gap="small" wrap style={{ marginBottom: 16 }}>
          {gmail?.connected ? (
            <>
              <Button onClick={() => void test()} loading={gmailBusy}>Probar conexión</Button>
              <Button danger onClick={() => void disconnect()} loading={gmailBusy}>Desconectar</Button>
            </>
          ) : (
            <Button type="primary" onClick={() => void connect()} loading={gmailBusy}>Conectar Gmail</Button>
          )}
        </Flex>
        <Descriptions column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Proveedor">{status.gmail.provider}</Descriptions.Item>
          <Descriptions.Item label="Cuenta conectada">{gmail?.email ?? status.gmail.email ?? "Ninguna"}</Descriptions.Item>
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
          <Descriptions.Item label="Estado">{gmail?.status ?? status.gmail.connection_status}</Descriptions.Item>
        </Descriptions>
      </Card>
    </Flex>
  );
}
