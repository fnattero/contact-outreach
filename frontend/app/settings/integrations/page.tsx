"use client";

import { Alert, Button, Card, Dropdown, Flex } from "antd";
import type { MenuProps } from "antd";
import { useEffect, useRef, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { PageHeader } from "@/components/design-system/page-header";
import { LoadingState } from "@/components/design-system/states";
import { ConfigurationForm } from "./configuration-form";
import { StatusBadge } from "@/components/design-system/status-badge";
import { can,
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
  const { session } = useAuth();
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [gmail, setGmail] = useState<GmailConnection | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [gmailBusy, setGmailBusy] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const technicalRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    void Promise.all([getIntegrationStatus(), getGmailConnection()])
      .then(([nextStatus, nextGmail]) => {
        setStatus(nextStatus);
        setGmail(nextGmail);
      })
      .catch(setError);
  }, []);

  if (!can(session, "manage_integrations")) return <AuthError error={{ detail: "No tenés permisos para ver integraciones." }} />;
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
      setDisconnectOpen(false);
    } catch (problem) {
      setError(problem);
    } finally {
      setGmailBusy(false);
    }
  }

  const gmailActions: MenuProps["items"] = [
    { key: "test", label: "Probar conexión", onClick: () => void test() },
    { key: "disconnect", label: "Desconectar", danger: true, onClick: () => setDisconnectOpen(true) },
  ];

  function showTechnical() {
    setTechnicalOpen(true);
    // The details live at the bottom of the page: open them and bring them into view.
    window.requestAnimationFrame(() =>
      technicalRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }

  const technicalAction = <Button type="link" onClick={showTechnical}>Ver detalles técnicos</Button>;

  return (
    <Flex vertical gap="large">
      <PageHeader title="Integraciones" description="Estado de las conexiones y configuración de proveedores. Las credenciales se guardan cifradas y nunca se muestran." />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card title="Servicios conectados">
        <div className="integration-list">
          <div className="integration-row">
            <div className="integration-row__name"><strong>Búsqueda de negocios</strong><span>Encuentra los negocios de cada campaña por rubro y zona.</span></div>
            <StatusBadge label="Disponible" level="success" />
            <span className="integration-row__account">Configuración del backend</span>
            {technicalAction}
          </div>
          <div className="integration-row">
            <div className="integration-row__name"><strong>Lectura de sitios web</strong><span>Lee el sitio de cada negocio para encontrar su correo.</span></div>
            <StatusBadge label="Disponible" level="success" />
            <span className="integration-row__account">Configuración del backend</span>
            {technicalAction}
          </div>
          <div className="integration-row">
            <div className="integration-row__name"><strong>Inteligencia artificial</strong><span>Revisa la audiencia y propone respuestas a los correos que llegan.</span></div>
            <StatusBadge label={status.llm.configured ? "Configurado" : "No configurado"} level={status.llm.configured ? "success" : "warning"} />
            <span className="integration-row__account">{status.llm.configured ? "Credencial del backend" : "Sin credencial disponible"}</span>
            {technicalAction}
          </div>
          <div className="integration-row">
            <div className="integration-row__name"><strong>Búsqueda en tu información</strong><span>Encuentra los datos aprobados que sirven para contestar.</span></div>
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
      {disconnectOpen ? (
        <ConfirmDangerModal
          open
          title="Desconectar Gmail"
          consequences={[
            "Se eliminan las credenciales guardadas de la cuenta de Gmail.",
            "Los envíos y la lectura de respuestas se detienen hasta que vuelvas a conectar una cuenta.",
            "Para cambiar las credenciales de OAuth hay que desconectar primero.",
          ]}
          confirmationWord="CONFIRMAR"
          dangerLabel="Desconectar Gmail"
          confirming={gmailBusy}
          onCancel={() => setDisconnectOpen(false)}
          onConfirm={() => void disconnect()}
        />
      ) : null}
      <ConfigurationForm />
      <Card title="Confianza de búsqueda">
        <div className="integration-confidence"><strong>{percent(status.extractor.overture_min_confidence)}</strong><span>Confianza mínima</span><p>Define el mínimo de confianza requerido para aceptar resultados de búsqueda.</p></div>
      </Card>
      <Card title="Detalles técnicos">
        <details ref={technicalRef} open={technicalOpen} onToggle={(event) => setTechnicalOpen(event.currentTarget.open)} className="integration-technical">
          <summary>Mostrar proveedor, modelo y configuración interna</summary>
          <dl>
            <dt>Búsqueda de negocios</dt><dd>{status.extractor.provider}</dd>
            <dt>Lectura de sitios web</dt><dd>{status.website_fetcher.provider}</dd>
            <dt>Inteligencia artificial</dt><dd>{status.llm.provider} · {status.llm.model} · {status.llm.credential_source}</dd>
            <dt>Modelo del filtro de audiencia</dt><dd>{status.llm.relevance_model || `${status.llm.model} (el mismo de arriba)`}</dd>
            <dt>Búsqueda en tu información</dt><dd>{status.embeddings.provider} · {status.embeddings.model} · {status.embeddings.dimensions} dimensiones</dd>
            <dt>Gmail</dt><dd>{status.gmail.provider} · OAuth {configured(status.gmail.oauth_client_id_configured)} · secreto {configured(status.gmail.credential_configured)} · estado {gmail?.status ?? status.gmail.connection_status}</dd>
            <dt>Umbral interno</dt><dd>{status.extractor.overture_min_confidence}</dd>
            <dt>Revisión de configuración</dt><dd>{status.revision}</dd>
          </dl>
        </details>
      </Card>
    </Flex>
  );
}
