"use client";

import { Alert, Card, Typography } from "antd";

export default function ContactsPage() {
  return (
    <Card>
      <Typography.Title level={2}>Contactos</Typography.Title>
      <Alert type="info" message="La vista de contactos se está incorporando a la nueva API." />
    </Card>
  );
}
