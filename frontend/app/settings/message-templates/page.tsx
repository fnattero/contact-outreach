"use client";

import { Alert, Button, Card, Flex, Form, Input, List, Select, Skeleton, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
  createMessageTemplate,
  getMessageTemplates,
  problemMessage,
  type MessageTemplate,
  type Problem,
} from "@/lib/api";

const labels = { INITIAL: "Propuesta inicial", REMINDER: "Recordatorio", REFERRED_PROPOSAL: "Propuesta referida" };

export default function MessageTemplatesPage() {
  const { session } = useAuth();
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void getMessageTemplates().then(setTemplates).catch(setError);
  }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para editar mensajes." }} />;
  if (error && !templates.length) return <AuthError error={error} />;
  if (!templates.length) return <Skeleton active paragraph={{ rows: 12 }} />;

  async function submit(values: { kind: MessageTemplate["kind"]; subject: string; body: string }) {
    setSaving(true);
    setError(null);
    try {
      const created = await createMessageTemplate(values);
      setTemplates((current) => [created, ...current.map((item) => item.kind === created.kind ? { ...item, active: false } : item)]);
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Mensajes de campaña</Typography.Title>
        <Typography.Paragraph type="secondary">Cada cambio crea una revisión aprobada; las campañas ya aprobadas conservan su contenido.</Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card title="Nueva revisión">
        <Form layout="vertical" onFinish={(values) => void submit(values as { kind: MessageTemplate["kind"]; subject: string; body: string })}>
          <Form.Item name="kind" label="Tipo" rules={[{ required: true }]}><Select options={Object.entries(labels).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="subject" label="Asunto"><Input maxLength={255} /></Form.Item>
          <Form.Item name="body" label="Cuerpo" rules={[{ required: true, max: 12000 }]}><Input.TextArea rows={8} maxLength={12000} showCount /></Form.Item>
          <Button type="primary" htmlType="submit" loading={saving}>Aprobar revisión</Button>
        </Form>
      </Card>
      <Card title="Historial">
        <List dataSource={templates} renderItem={(template) => <List.Item><List.Item.Meta title={`${labels[template.kind]} · revisión ${template.revision}`} description={template.subject || "Sin asunto"} /><Tag color={template.active ? "green" : "default"}>{template.active ? "Activa" : "Histórica"}</Tag></List.Item>} />
      </Card>
    </Flex>
  );
}
