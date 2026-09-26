"use client";

import { Alert, Button, Card, Flex, Typography } from "antd";
import Link from "next/link";

export default function HomePage() {
  return (
    <main className="centered-page">
      <Card className="welcome-card">
        <Flex vertical gap="middle">
          <Typography.Title level={1}>Contact Outreach</Typography.Title>
          <Alert
            type="info"
            showIcon
            message="La nueva interfaz está en construcción."
            description="El backend actual continúa disponible mientras migramos cada flujo de forma verificable."
          />
          <Link href="/login">
            <Button type="primary" size="large">
              Iniciar sesión
            </Button>
          </Link>
        </Flex>
      </Card>
    </main>
  );
}
