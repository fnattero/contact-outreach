"use client";

import { Button, Card, Flex, Typography } from "antd";
import Link from "next/link";

export default function HomePage() {
  return (
    <main className="centered-page">
      <Card className="welcome-card">
        <Flex vertical gap="middle">
          <Typography.Title level={1}>Contact Outreach</Typography.Title>
          <Typography.Paragraph>
            Herramienta privada para contactar negocios y seguir sus respuestas. El acceso es solo para usuarios
            invitados.
          </Typography.Paragraph>
          <Link href="/login">
            <Button type="primary" size="large">
              Iniciar sesión
            </Button>
          </Link>
          <Link href="/privacidad">Política de privacidad</Link>
        </Flex>
      </Card>
    </main>
  );
}
