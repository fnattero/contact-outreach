"use client";

import { Alert, Button, Card, Flex, Form, Input, Radio, Skeleton, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
  getAutomationConfiguration,
  problemMessage,
  reauthenticate,
  setAutomationLive,
  updateAutomationMode,
  type AutomationConfiguration,
  type Problem,
} from "@/lib/api";

export default function AutomationPage() {
  const { session } = useAuth();
  const [configuration, setConfiguration] = useState<AutomationConfiguration | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void getAutomationConfiguration().then(setConfiguration).catch(setError);
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
    </Flex>
  );
}
