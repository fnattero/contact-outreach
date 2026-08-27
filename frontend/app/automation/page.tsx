"use client";

import { Alert, Button, Card, Flex, Form, Input, List, Radio, Skeleton, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
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
  type KnowledgeContextRevision,
  type KnowledgeFactRevision,
  type Problem,
} from "@/lib/api";

export default function AutomationPage() {
  const { session } = useAuth();
  const [configuration, setConfiguration] = useState<AutomationConfiguration | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [facts, setFacts] = useState<KnowledgeFactRevision[]>([]);
  const [contexts, setContexts] = useState<KnowledgeContextRevision[]>([]);
  const [preview, setPreview] = useState<{ status: string; matches: Array<{ title: string; score: number; selected: boolean }> } | null>(null);

  useEffect(() => {
    void Promise.all([getAutomationConfiguration(), getKnowledgeFacts(), getKnowledgeContexts()])
      .then(([nextConfiguration, nextFacts, nextContexts]) => {
        setConfiguration(nextConfiguration); setFacts(nextFacts); setContexts(nextContexts);
      })
      .catch(setError);
  }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para configurar automatización." }} />;
  if (error && !configuration) return <AuthError error={error} />;
  if (!configuration) return <Skeleton active paragraph={{ rows: 10 }} />;

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

  async function enableLive(values: { password: string }) {
    setSaving(true);
    setError(null);
    try {
      await reauthenticate(values.password);
      setConfiguration(await setAutomationLive("enable-live"));
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
      <div>
        <Typography.Title level={2}>Automatización de respuestas</Typography.Title>
        <Typography.Paragraph type="secondary">El modo se aplica en el backend y se vuelve a validar justo antes de Gmail.</Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Alert type={configuration.mode === "LIVE" ? "warning" : "info"} showIcon message={`Modo actual: ${configuration.mode_label}`} description="SHADOW nunca produce efectos en Gmail. LIVE requiere reautenticación y continúa sujeto a políticas, tareas humanas y kill switches." />
      <Card title="Modo operativo">
        <Form initialValues={{ mode: configuration.mode === "LIVE" ? "SHADOW" : configuration.mode }} layout="vertical" onFinish={(values) => void saveMode(values as { mode: "OFF" | "SHADOW" })}>
          <Form.Item name="mode" label="Modo seguro" rules={[{ required: true }]}>
            <Radio.Group options={[{ value: "OFF", label: "OFF · desactivada" }, { value: "SHADOW", label: "SHADOW · sólo observar" }]} />
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
          <Form layout="vertical" onFinish={(values) => void enableLive(values as { password: string })} style={{ marginTop: 16 }}>
            <Form.Item name="password" label="Contraseña actual" rules={[{ required: true }]}>
              <Input.Password autoComplete="current-password" />
            </Form.Item>
            <Button danger type="primary" htmlType="submit" loading={saving}>Activar LIVE</Button>
          </Form>
        </Card>
      ) : null}
      <Card title="Estado de la política">
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
    </Flex>
  );
}
