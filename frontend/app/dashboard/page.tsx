"use client";

import { Card, Flex, Typography } from "antd";

export default function DashboardPage() {
  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Resumen</Typography.Title>
        <Typography.Paragraph type="secondary">
          Desde acá vas a poder revisar contactos, campañas y tareas de atención.
        </Typography.Paragraph>
      </div>
      <Card>
        <Typography.Title level={4}>Migración en progreso</Typography.Title>
        <Typography.Paragraph>
          La sesión y la autorización ya funcionan sobre la nueva API. Los módulos de negocio se
          están incorporando de forma gradual y verificable.
        </Typography.Paragraph>
      </Card>
    </Flex>
  );
}
