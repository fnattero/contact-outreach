"use client";

import { Alert, Button, Card, Flex, Form, Input, Typography } from "antd";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { activate, problemMessage, type Problem } from "@/lib/api";
import { useAuth } from "@/components/auth-provider";

export default function ActivatePage() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const value = fragment.get("token");
    // The token is intentionally read from the fragment only after hydration,
    // then held in memory for the one activation request.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setToken(value);
    window.history.replaceState(null, "", window.location.pathname);
  }, []);

  const submit = async (values: { password: string; password_confirmation: string }) => {
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      await activate(token, values.password, values.password_confirmation);
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
        <Typography.Title level={1}>Activar cuenta</Typography.Title>
        <Flex vertical gap="middle">
          {error ? <Alert type="error" showIcon message={error} /> : null}
          {!token ? <Alert type="warning" message="El enlace de activación no es válido." /> : null}
          <Form layout="vertical" onFinish={submit} requiredMark="optional">
            <Form.Item
              label="Nueva contraseña"
              name="password"
              rules={[{ required: true }, { min: 14, message: "Usá al menos 14 caracteres." }]}
            >
              <Input.Password autoComplete="new-password" size="large" />
            </Form.Item>
            <Form.Item
              label="Repetir contraseña"
              name="password_confirmation"
              dependencies={["password"]}
              rules={[{ required: true }, ({ getFieldValue }) => ({
                validator(_, value) {
                  return !value || getFieldValue("password") === value
                    ? Promise.resolve()
                    : Promise.reject(new Error("Las contraseñas no coinciden."));
                },
              })]}
            >
              <Input.Password autoComplete="new-password" size="large" />
            </Form.Item>
            <Button type="primary" htmlType="submit" size="large" block loading={submitting} disabled={!token}>
              Activar cuenta
            </Button>
          </Form>
        </Flex>
      </Card>
    </main>
  );
}
