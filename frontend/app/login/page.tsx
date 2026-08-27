"use client";

import { Button, Card, Form, Input, Typography } from "antd";

export default function LoginPage() {
  return (
    <main className="centered-page">
      <Card className="auth-card">
        <Typography.Title level={1}>Iniciar sesión</Typography.Title>
        <Form layout="vertical" requiredMark="optional">
          <Form.Item label="Usuario" name="username" rules={[{ required: true }]}>
            <Input autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item label="Contraseña" name="password" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block disabled>
            Iniciar sesión
          </Button>
        </Form>
      </Card>
    </main>
  );
}
