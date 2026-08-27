"use client";

import { Button, Card, Form, Input, Typography } from "antd";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Flex } from "antd";
import { useAuth } from "@/components/auth-provider";
import { login, problemMessage, type Problem } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (values: { username: string; password: string }) => {
    setError(null);
    setSubmitting(true);
    try {
      await login(values.username, values.password);
      await refresh();
      router.replace("/dashboard");
    } catch (problem) {
      setError(problemMessage(problem as Problem));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="centered-page">
      <Card className="auth-card">
        <Typography.Title level={1}>Iniciar sesión</Typography.Title>
        <Flex vertical gap="middle">
          {error ? <Alert type="error" showIcon message={error} /> : null}
          <Form layout="vertical" requiredMark="optional" onFinish={submit}>
          <Form.Item label="Usuario" name="username" rules={[{ required: true }]}>
            <Input autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item label="Contraseña" name="password" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={submitting}>
            Iniciar sesión
          </Button>
          </Form>
        </Flex>
      </Card>
    </main>
  );
}
