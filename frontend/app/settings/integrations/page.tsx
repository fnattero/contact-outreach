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

export default function IntegrationsSettingsPage() {
  const { session } = useAuth();
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [gmail, setGmail] = useState<GmailConnection | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [gmailBusy, setGmailBusy] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const technicalRef = useRef<HTMLDetailsElement>(null);
  // Google sends the person back here with the result of the authorization.
  const [gmailResult, setGmailResult] = useState<"connected" | "oauth_failed" | null>(() => {
    if (typeof window === "undefined") return null;
    const result = new URLSearchParams(window.location.search).get("gmail");
    return result === "connected" || result === "oauth_failed" ? result : null;
  });

  useEffect(() => {
    // Keep the address clean once the result has been read.
    if (gmailResult) window.history.replaceState(null, "", window.location.pathname);
  }, [gmailResult]);

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
      <PageHeader title="Integraciones" description="Conectá tu cuenta de Gmail y el servicio de inteligencia artificial. Las credenciales se guardan cifradas y nunca se muestran." />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      {gmailResult === "connected" ? <Alert type="success" showIcon closable onClose={() => setGmailResult(null)} message="Gmail quedó conectado." /> : null}
      {gmailResult === "oauth_failed" ? <Alert type="error" showIcon closable onClose={() => setGmailResult(null)} message="No se pudo conectar Gmail" description="Google no autorizó la conexión. Podés volver a intentarlo con “Conectar Gmail”." /> : null}
      <Card title="Conexiones">
        <div className="integration-list">
          <div className="integration-row">
            <div className="integration-row__name"><strong>Inteligencia artificial</strong><span>Revisa la audiencia y propone respuestas a los correos que llegan.</span></div>
            <StatusBadge label={status.llm.configured ? "Configurado" : "No configurado"} level={status.llm.configured ? "success" : "warning"} />
            <span className="integration-row__account">{status.llm.configured ? "Clave guardada" : "Sin clave"}</span>
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
