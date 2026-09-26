"use client";

import { Alert, Button, Card, Form, Input } from "antd";
import { useRouter } from "next/navigation";
import { useState, type ComponentProps, type CSSProperties, type FocusEvent } from "react";
import { useAuth } from "@/components/auth-provider";
import { DisabledReason } from "@/components/design-system/disabled-reason";
import { login, problemMessage, type Problem } from "@/lib/api";
import { colors } from "@/src/theme/tokens";

type LoginInputProps = ComponentProps<typeof Input> & { password?: boolean };
const focusRingStyle: CSSProperties = {
  outlineColor: colors.focus,
  outlineOffset: 2,
  outlineStyle: "solid",
  outlineWidth: 2,
};

function LoginInput({ password = false, onFocus, onBlur, ...props }: LoginInputProps) {
  const [focused, setFocused] = useState(false);
  const focus = (event: FocusEvent<HTMLInputElement>) => {
    setFocused(true);
    onFocus?.(event);
  };
  const blur = (event: FocusEvent<HTMLInputElement>) => {
    setFocused(false);
    onBlur?.(event);
  };

  return (
    <div
      className={`login-control-frame${focused ? " login-control-frame--focused" : ""}`}
      style={focused ? focusRingStyle : undefined}
    >
      {password ? (
        <Input.Password {...props} onFocus={focus} onBlur={blur} />
      ) : (
        <Input {...props} onFocus={focus} onBlur={blur} />
      )}
    </div>
  );
}

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
    <main className="login-page">
      <Card className="login-panel">
        <div className="login-panel__brand type-micro">Contact Outreach</div>
        <h1 className="login-panel__title type-display">Iniciar sesión</h1>
        <p className="login-panel__description">Accedé al panel privado de operaciones comerciales.</p>
        {error ? <Alert className="login-panel__error" type="error" showIcon message={error} /> : null}
        <Form className="login-panel__form" layout="vertical" requiredMark={false} onFinish={submit}>
          <Form.Item label="Usuario" name="username" rules={[{ required: true }]}>
            <LoginInput
              id="username"
              className="login-panel__input"
              autoComplete="username"
            />
          </Form.Item>
          <Form.Item label="Contraseña" name="password" rules={[{ required: true }]}>
            <LoginInput
              password
              id="password"
              className="login-panel__password"
              autoComplete="current-password"
            />
          </Form.Item>
          <DisabledReason disabled={submitting} reason="La sesión se está iniciando.">
            <Button className="login-panel__submit" type="primary" htmlType="submit" block>
              {submitting ? "Iniciando sesión…" : "Iniciar sesión"}
            </Button>
          </DisabledReason>
        </Form>
      </Card>
    </main>
  );
}
