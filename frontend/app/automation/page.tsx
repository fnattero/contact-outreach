"use client";

import { Alert, Button, Card, Flex, Form, Input, List, Segmented, Tag } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { PageHeader } from "@/components/design-system/page-header";
import { LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import {
  getDashboardSummary,
  getAutomationConfiguration,
  createKnowledgeContext,
  createKnowledgeFact,
  getKnowledgeContexts,
  getKnowledgeFacts,
  previewKnowledge,
  problemMessage,
  reauthenticate,
  setAutomationLive,
  updateAutomationMode,
  type AutomationConfiguration,
  type DashboardSummary,
  type KnowledgeContextRevision,
  type KnowledgeFactRevision,
  type Problem,
} from "@/lib/api";

export default function AutomationPage() {
  const { session } = useAuth();
  const [configuration, setConfiguration] = useState<AutomationConfiguration | null>(null);
  const [safety, setSafety] = useState<DashboardSummary["safety"] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [facts, setFacts] = useState<KnowledgeFactRevision[]>([]);
  const [contexts, setContexts] = useState<KnowledgeContextRevision[]>([]);
  const [preview, setPreview] = useState<{ status: string; matches: Array<{ title: string; score: number; selected: boolean }> } | null>(null);
  const [pendingPassword, setPendingPassword] = useState<string | null>(null);
  const [liveConfirmOpen, setLiveConfirmOpen] = useState(false);

  useEffect(() => {
    void Promise.all([getAutomationConfiguration(), getKnowledgeFacts(), getKnowledgeContexts(), getDashboardSummary()])
      .then(([nextConfiguration, nextFacts, nextContexts, nextSummary]) => {
        setConfiguration(nextConfiguration); setFacts(nextFacts); setContexts(nextContexts); setSafety(nextSummary.safety);
      })
      .catch(setError);
  }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para configurar automatización." }} />;
  if (error && !configuration) return <AuthError error={error} />;
  if (!configuration) return <LoadingState layout="form" />;

  async function saveMode(values: { mode: "OFF" | "SHADOW" }) {
    setSaving(true);
    setError(null);
    try {
      setConfiguration(await updateAutomationMode(values.mode));
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(false);
    }
  }

  async function enableLive(password: string) {
    setSaving(true);
    setError(null);
    try {
      await reauthenticate(password);
      setConfiguration(await setAutomationLive("enable-live"));
      setLiveConfirmOpen(false);
      setPendingPassword(null);
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(false);
    }
  }

  async function disableLive() {
    setSaving(true);
    setError(null);
    try {
      setConfiguration(await setAutomationLive("disable-live"));
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(false);
    }
  }

  async function saveFact(values: { title: string; category?: string; text: string }) {
    setSaving(true); setError(null);
    try { const created = await createKnowledgeFact(values); setFacts((current) => [created, ...current]); }
    catch (problem) { setError(problem); } finally { setSaving(false); }
  }

  async function saveContext(values: { context_text: string }) {
    setSaving(true); setError(null);
    try { const created = await createKnowledgeContext(values.context_text); setContexts((current) => [created, ...current]); }
    catch (problem) { setError(problem); } finally { setSaving(false); }
  }

  async function runPreview(values: { query: string }) {
    setSaving(true); setError(null);
    try { setPreview(await previewKnowledge(values.query)); }
    catch (problem) { setError(problem); } finally { setSaving(false); }
  }

  return (
    <Flex vertical gap="large">
      <PageHeader title="Automatización de respuestas" description="El modo se aplica en el backend y se vuelve a validar justo antes de Gmail." />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <section className={`automation-mode-block automation-mode-block--${configuration.mode.toLowerCase()}`} aria-labelledby="automation-mode-heading">
        <div className="automation-mode-block__heading">
          <span className="type-micro">Estado actual</span>
          <h2 className="type-title" id="automation-mode-heading">¿Qué puede hacer la automatización ahora?</h2>
          <StatusBadge value={configuration.mode} />
        </div>
        <p className="automation-mode-block__meaning">{configuration.mode === "LIVE" ? "Envío real: las respuestas que cumplan todas las condiciones pueden salir de Gmail de verdad." : configuration.mode === "SHADOW" ? "Observación: el sistema redacta respuestas para revisar, pero no las envía." : "Desactivada: el sistema no redacta ni envía respuestas automáticas."}</p>
        <p className="automation-mode-block__boundary">SHADOW nunca produce efectos en Gmail. LIVE requiere reautenticación y continúa sujeto a políticas, tareas humanas y kill switches.</p>
      </section>
      <Alert type={configuration.mode === "LIVE" ? "warning" : "info"} showIcon message={`Modo actual: ${configuration.mode_label}`} description="SHADOW nunca produce efectos en Gmail. LIVE requiere reautenticación y continúa sujeto a políticas, tareas humanas y kill switches." />
      <Card title="Modo operativo">
        <Form initialValues={{ mode: configuration.mode === "LIVE" ? "SHADOW" : configuration.mode }} layout="vertical" onFinish={(values) => void saveMode(values as { mode: "OFF" | "SHADOW" })}>
          <Form.Item name="mode" label="Elegí el alcance de la automatización" rules={[{ required: true }]}>
            <Segmented block options={[
              { value: "OFF", label: <span className="automation-mode-option"><strong>Desactivada</strong><small>No redacta ni envía respuestas.</small></span> },
              { value: "SHADOW", label: <span className="automation-mode-option"><strong>Observación</strong><small>Redacta para revisar; nunca envía.</small></span> },
            ]} />
          </Form.Item>
          <Flex gap="small" wrap>
            <Button type="primary" htmlType="submit" loading={saving}>Guardar modo</Button>
            {configuration.mode === "LIVE" ? <Button danger onClick={() => void disableLive()} loading={saving}>Desactivar LIVE</Button> : null}
          </Flex>
        </Form>
      </Card>
      {configuration.mode !== "LIVE" ? (
        <Card title="Activar LIVE">
          <Alert type="warning" showIcon message="Acción sensible" description="Ingresá tu contraseña nuevamente. Esto no desactiva los kill switches ni permite respuestas fuera de la política aprobada." />
          <Form layout="vertical" onFinish={(values) => { setPendingPassword((values as { password: string }).password); setLiveConfirmOpen(true); }} style={{ marginTop: 16 }}>
            <Form.Item name="password" label="Contraseña actual" rules={[{ required: true }]}>
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Button danger type="primary" htmlType="submit" loading={saving}>Revisar y activar LIVE</Button>
          </Form>
        </Card>
      ) : null}
      <Card title="Interruptores de seguridad y política">
        <p className="integration-muted">Estos controles siguen siendo evaluados por el backend antes de cada efecto. Esta pantalla no los modifica.</p>
        {safety ? <div className="automation-safety-grid">
          <div><span className="type-micro">Envíos</span><StatusBadge label={safety.send_kill_switch ? "Protegidos" : "Habilitados"} level={safety.send_kill_switch ? "success" : "danger"} /><p>{safety.send_kill_switch ? "El interruptor bloquea cualquier envío." : "El interruptor permite envíos si las demás condiciones se cumplen."}</p></div>
          <div><span className="type-micro">Respuestas automáticas</span><StatusBadge label={safety.auto_reply_kill_switch ? "Detenidas" : "Habilitadas"} level={safety.auto_reply_kill_switch ? "inactive" : "warning"} /><p>{safety.auto_reply_kill_switch ? "No se redactan ni envían respuestas automáticas." : "El sistema puede redactar según la configuración vigente."}</p></div>
          <div><span className="type-micro">Relaciones</span><StatusBadge label={safety.relationship_kill_switch ? "Protegidas" : "Habilitadas"} level={safety.relationship_kill_switch ? "success" : "warning"} /><p>{safety.relationship_kill_switch ? "Las acciones relacionales están bloqueadas." : "Las acciones relacionales siguen las políticas vigentes."}</p></div>
        </div> : null}
        <Flex gap="small" wrap>
          <Tag>Política {configuration.policy_version}</Tag>
          {configuration.live_enabled_by ? <Tag>Activada por {configuration.live_enabled_by}</Tag> : null}
        </Flex>
      </Card>
      <Card title="Información aprobada para el agente">
        <Form layout="vertical" onFinish={(values) => void saveFact(values as { title: string; category?: string; text: string })}>
          <Form.Item name="title" label="Título" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="category" label="Categoría"><Input /></Form.Item>
          <Form.Item name="text" label="Información verificable" rules={[{ required: true }]}><Input.TextArea rows={4} maxLength={4000} showCount /></Form.Item>
          <Button htmlType="submit" loading={saving}>Guardar información</Button>
        </Form>
        <List style={{ marginTop: 16 }} dataSource={facts} renderItem={(fact) => <List.Item><List.Item.Meta title={`${fact.title} · v${fact.version}`} description={fact.text} /><Tag color="green">Aprobada</Tag></List.Item>} />
      </Card>
      <Card title="Contexto general aprobado">
        <Form layout="vertical" onFinish={(values) => void saveContext(values as { context_text: string })}>
          <Form.Item name="context_text" label="Contexto" rules={[{ required: true }]}><Input.TextArea rows={4} maxLength={4000} showCount /></Form.Item>
          <Button htmlType="submit" loading={saving}>Guardar contexto</Button>
        </Form>
        <List style={{ marginTop: 16 }} dataSource={contexts} renderItem={(context) => <List.Item><List.Item.Meta title={`Revisión ${context.version}`} description={context.context_text} /><Tag color={context.approved ? "green" : "default"}>{context.approved ? "Aprobada" : "Pendiente"}</Tag></List.Item>} />
      </Card>
      <Card title="Probar recuperación de información">
        <Form layout="vertical" onFinish={(values) => void runPreview(values as { query: string })}>
          <Form.Item name="query" label="Consulta de ejemplo" rules={[{ required: true }]}><Input /></Form.Item>
          <Button htmlType="submit" loading={saving}>Probar</Button>
        </Form>
        {preview ? <Alert style={{ marginTop: 16 }} type={preview.status === "SELECTED" ? "success" : "info"} message={`Resultado: ${preview.status}`} description={preview.matches.map((match) => `${match.title} (${match.score.toFixed(2)})`).join(" · ") || "Sin coincidencias."} /> : null}
      </Card>
      <ConfirmDangerModal
        open={liveConfirmOpen}
        title="Activar Envío real"
        consequences={[
          "Las respuestas automáticas podrán redactarse según la política configurada.",
          "Las respuestas que cumplan todas las condiciones podrán enviarse desde Gmail de verdad.",
          "La reautenticación, Gmail, las restricciones, las tareas humanas y los kill switches se volverán a comprobar antes de cada efecto.",
        ]}
        confirmationWord="CONFIRMAR"
        dangerLabel="Activar Envío real"
        confirming={saving}
        onCancel={() => { setLiveConfirmOpen(false); setPendingPassword(null); }}
        onConfirm={() => { if (pendingPassword) void enableLive(pendingPassword); }}
      />
    </Flex>
  );
}
