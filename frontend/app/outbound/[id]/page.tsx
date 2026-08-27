"use client";

import { Alert, Button, Card, Descriptions, Flex, Form, Input, Popconfirm, Skeleton, Tag, Typography } from "antd";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { authorizeOutboundMessage, getOutboundMessage, problemMessage, updateOutboundDraft, type OutboundMessage, type Problem } from "@/lib/api";
import { useAuth } from "@/components/auth-provider";

export default function OutboundDetailPage() {
  const params = useParams<{ id: string }>();
  const [message, setMessage] = useState<OutboundMessage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const { session } = useAuth();

  useEffect(() => {
    let cancelled = false;
    void getOutboundMessage(params.id)
      .then((nextMessage) => {
        if (!cancelled) setMessage(nextMessage);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      });
    return () => {
      cancelled = true;
    };
  }, [params.id]);

  if (error) return <AuthError error={error} />;
  if (!message) return <Skeleton active paragraph={{ rows: 8 }} />;
  const currentMessage = message;

  async function saveDraft(values: { subject: string; body_text: string }) {
    setSaving(true); setError(null);
    try { setMessage(await updateOutboundDraft(currentMessage.id, values.subject, values.body_text)); }
    catch (problem) { setError(problem); } finally { setSaving(false); }
  }
  async function authorize() {
    setSaving(true); setError(null);
    try { setMessage(await authorizeOutboundMessage(currentMessage.id)); }
    catch (problem) { setError(problem); } finally { setSaving(false); }
  }

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>{message.subject || "Mensaje sin asunto"}</Typography.Title>
        <Tag>{message.state_label}</Tag>
      </div>
      <Card title="Detalles">
        <Descriptions column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Destinatario">{message.recipient}</Descriptions.Item>
          <Descriptions.Item label="Tipo">{message.kind_label}</Descriptions.Item>
          <Descriptions.Item label="Fecha">{new Date(message.sent_at ?? message.created_at).toLocaleString("es-AR")}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="Contenido">
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>{message.body_text}</Typography.Paragraph>
      </Card>
      {session?.role === "ADMIN" && ["PREPARED", "REVIEW_READY"].includes(message.state) ? (
        <Card title="Revisión administrativa">
          <Form layout="vertical" initialValues={{ subject: message.subject, body_text: message.body_text }} onFinish={(values) => void saveDraft(values as { subject: string; body_text: string })}>
            <Form.Item name="subject" label="Asunto" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="body_text" label="Texto" rules={[{ required: true }]}><Input.TextArea rows={8} /></Form.Item>
            <Flex gap="small" wrap><Button type="primary" htmlType="submit" loading={saving}>Guardar borrador</Button><Popconfirm title="¿Autorizar este mensaje?" description="El backend volverá a validar todas las políticas antes de encolar el envío." onConfirm={() => void authorize()} okText="Autorizar" cancelText="Volver"><Button loading={saving}>Autorizar</Button></Popconfirm></Flex>
          </Form>
        </Card>
      ) : null}
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
    </Flex>
  );
}
