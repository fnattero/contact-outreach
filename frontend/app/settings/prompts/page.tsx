"use client";

import { Alert, Button, Card, Flex, Form, Input, Skeleton, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
  getPromptConfiguration,
  problemMessage,
  updateAutomaticReplyPrompt,
  updatePromptConfiguration,
  type Problem,
  type PromptConfiguration,
} from "@/lib/api";

export default function PromptsSettingsPage() {
  const { session } = useAuth();
  const [configuration, setConfiguration] = useState<PromptConfiguration | null>(null);
  const [saving, setSaving] = useState<"email" | "reply" | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    void getPromptConfiguration().then(setConfiguration).catch(setError);
  }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para editar instrucciones." }} />;
  if (error && !configuration) return <AuthError error={error} />;
  if (!configuration) return <Skeleton active paragraph={{ rows: 12 }} />;
  const currentConfiguration = configuration;

  async function saveEmail(values: { prompt: string }) {
    setSaving("email");
    setError(null);
    try {
      setConfiguration(await updatePromptConfiguration(values.prompt));
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(null);
    }
  }

  async function saveReply(values: { prompt: string }) {
    setSaving("reply");
    setError(null);
    try {
      const saved = await updateAutomaticReplyPrompt(values.prompt);
      setConfiguration({
        ...currentConfiguration,
        automatic_reply_prompt: saved.automatic_reply_prompt,
      });
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(null);
    }
  }

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Instrucciones de escritura</Typography.Title>
        <Typography.Paragraph type="secondary">
          Son revisiones auditadas. No otorgan al modelo acceso a Gmail, red, archivos ni herramientas.
        </Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card title="Redacción de propuestas">
        <Form initialValues={{ prompt: configuration.email_drafting_prompt }} layout="vertical" onFinish={(values) => void saveEmail(values as { prompt: string })}>
          <Form.Item name="prompt" label="Instrucciones" rules={[{ required: true, max: 4000 }]}>
            <Input.TextArea rows={8} showCount maxLength={4000} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving === "email"}>Guardar revisión</Button>
        </Form>
      </Card>
      <Card title="Respuestas automáticas">
        <Form initialValues={{ prompt: configuration.automatic_reply_prompt }} layout="vertical" onFinish={(values) => void saveReply(values as { prompt: string })}>
          <Form.Item name="prompt" label="Instrucciones" rules={[{ required: true, max: 4000 }]}>
            <Input.TextArea rows={10} showCount maxLength={4000} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving === "reply"}>Guardar revisión</Button>
        </Form>
      </Card>
    </Flex>
  );
}
