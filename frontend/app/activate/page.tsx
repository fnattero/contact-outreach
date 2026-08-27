"use client";

import { Alert, Card, Typography } from "antd";

export default function ActivatePage() {
  return (
    <main className="centered-page">
      <Card className="auth-card">
        <Typography.Title level={1}>Activar cuenta</Typography.Title>
        <Alert
          type="info"
          message="La activación segura se habilitará junto con la API de autenticación."
        />
      </Card>
    </main>
  );
}
