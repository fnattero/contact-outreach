"use client";

import { Alert, Button, Card, Flex, Form, Input, Typography } from "antd";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { createContact, problemMessage, type Problem } from "@/lib/api";

type ContactForm = {
  email: string;
  organization_name?: string;
  contact_name?: string;
};

export default function NewContactPage() {
  const { session } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  if (session?.role !== "ADMIN") {
    return <AuthError error={{ detail: "No tenés permisos para crear contactos." }} />;
  }

  async function submit(values: ContactForm) {
    setSaving(true);
    setError(null);
    try {
      const contact = await createContact(values);
      router.push(`/contacts/${contact.id}`);
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(false);
    }
  }

  const fieldErrors = (error as Problem | null)?.field_errors;
  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Nuevo contacto</Typography.Title>
        <Typography.Paragraph type="secondary">
          Un contacto excluye a toda su organización de nuevas audiencias.
        </Typography.Paragraph>
      </div>
      {error ? (
        <Alert
          type="error"
          showIcon
          message={problemMessage(error as Problem)}
          description={fieldErrors ? JSON.stringify(fieldErrors) : undefined}
        />
      ) : null}
      <Card>
        <Form layout="vertical" onFinish={(values) => void submit(values as ContactForm)}>
          <Form.Item
            label="Email"
            name="email"
            rules={[{ required: true, type: "email", message: "Indicá un email válido." }]}
          >
            <Input type="email" autoComplete="email" />
          </Form.Item>
          <Form.Item label="Nombre de la organización" name="organization_name">
            <Input />
          </Form.Item>
          <Form.Item label="Nombre del contacto" name="contact_name">
            <Input autoComplete="name" />
          </Form.Item>
          <Flex gap="small" wrap>
            <Button type="primary" htmlType="submit" loading={saving}>
              Crear contacto
            </Button>
            <Link href="/contacts">
              <Button>Cancelar</Button>
            </Link>
          </Flex>
        </Form>
      </Card>
    </Flex>
  );
}
