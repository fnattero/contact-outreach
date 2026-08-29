"use client";

import { Alert, Button, Card, Dropdown, Flex } from "antd";
import type { MenuProps } from "antd";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { PageHeader } from "@/components/design-system/page-header";
import { LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
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

function percent(value: string): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${Math.round(parsed * 100)}%` : "—";
}

export default function IntegrationsSettingsPage() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [gmail, setGmail] = useState<GmailConnection | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [gmailBusy, setGmailBusy] = useState(false);
  const [technicalOpen, setTechnicalOpen] = useState(false);

  useEffect(() => {
    void Promise.all([getIntegrationStatus(), getGmailConnection()])
      .then(([nextStatus, nextGmail]) => {
        setStatus(nextStatus);
        setGmail(nextGmail);
      })
      .catch(setError);
  }, []);

  if (error && !status) return <AuthError error={error} />;
  if (!status) return <LoadingState layout="list" />;

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

  const gmailActions: MenuProps["items"] = [
    { key: "test", label: "Probar conexión", onClick: () => void test() },
    { key: "disconnect", label: "Desconectar", danger: true, onClick: () => void disconnect() },
  ];

  const technicalAction = <Button type="link" onClick={() => setTechnicalOpen(true)}>Ver detalles técnicos</Button>;

  return (
    <Flex vertical gap="large">
      <PageHeader title="Integraciones" description="Estado operativo seguro. Las credenciales pertenecen al entorno y nunca se muestran aquí." />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Alert
        type="info"
        showIcon
        message="Las credenciales se administran como secretos del backend"
        description="Esta pantalla sólo expone proveedor, configuración segura y estado de conexión."
      />
      <Card title="Servicios conectados">
        <div className="integration-list">
          <div className="integration-row">
            <div className="integration-row__name"><strong>Extractor de búsqueda</strong><span>Obtiene candidatos para las campañas.</span></div>
            <StatusBadge label="Disponible" level="success" />
            <span className="integration-row__account">Configuración del backend</span>
            {technicalAction}
          </div>
          <div className="integration-row">
            <div className="integration-row__name"><strong>Fetcher web</strong><span>Consulta sitios para validar información.</span></div>
            <StatusBadge label="Disponible" level="success" />
            <span className="integration-row__account">Configuración del backend</span>
            {technicalAction}
          </div>
          <div className="integration-row">
            <div className="integration-row__name"><strong>Modelo de redacción</strong><span>Prepara borradores según la política.</span></div>
            <StatusBadge label={status.llm.configured ? "Configurado" : "No configurado"} level={status.llm.configured ? "success" : "warning"} />
            <span className="integration-row__account">{status.llm.configured ? "Credencial del backend" : "Sin credencial disponible"}</span>
            {technicalAction}
          </div>
          <div className="integration-row">
            <div className="integration-row__name"><strong>Búsqueda semántica</strong><span>Relaciona consultas con información aprobada.</span></div>
            <StatusBadge label="Disponible" level="success" />
            <span className="integration-row__account">Configuración del backend</span>
            {technicalAction}
          </div>
          <div className="integration-row">
            <div className="integration-row__name"><strong>Gmail</strong><span>Cuenta desde la que se realizan los envíos permitidos.</span></div>
            {gmail?.connected ? <StatusBadge value="CONNECTED" /> : <StatusBadge value="DISCONNECTED" />}
            <span className="integration-row__account">{gmail?.email ?? status.gmail.email ?? "Ninguna"}</span>
            {gmail?.connected ? <Dropdown menu={{ items: gmailActions }} trigger={["click"]}><Button loading={gmailBusy}>Acciones de Gmail</Button></Dropdown> : <Button type="primary" onClick={() => void connect()} loading={gmailBusy}>Conectar Gmail</Button>}
          </div>
        </div>
      </Card>
      <Card title="Confianza de búsqueda">
        <div className="integration-confidence"><strong>{percent(status.extractor.overture_min_confidence)}</strong><span>Confianza mínima</span><p>Define el mínimo de confianza requerido para aceptar resultados de búsqueda.</p></div>
      </Card>
      <Card title="Detalles técnicos">
        <details open={technicalOpen} onToggle={(event) => setTechnicalOpen(event.currentTarget.open)} className="integration-technical">
          <summary>Mostrar proveedor, modelo y configuración interna</summary>
          <dl>
            <dt>Extractor</dt><dd>{status.extractor.provider}</dd>
            <dt>Fetcher web</dt><dd>{status.website_fetcher.provider}</dd>
            <dt>Modelo de redacción</dt><dd>{status.llm.provider} · {status.llm.model} · {status.llm.credential_source}</dd>
            <dt>Búsqueda semántica</dt><dd>{status.embeddings.provider} · {status.embeddings.model} · {status.embeddings.dimensions} dimensiones</dd>
            <dt>Gmail</dt><dd>{status.gmail.provider} · OAuth {configured(status.gmail.oauth_client_id_configured)} · secreto {configured(status.gmail.credential_configured)} · estado {gmail?.status ?? status.gmail.connection_status}</dd>
            <dt>Umbral interno</dt><dd>{status.extractor.overture_min_confidence}</dd>
            <dt>Revisión de configuración</dt><dd>{status.revision}</dd>
          </dl>
        </details>
      </Card>
    </Flex>
  );
}
