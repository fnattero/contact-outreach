"use client";

import { Alert, Card, Empty, Flex, Skeleton, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { getOvertureStatus, problemMessage, type OvertureStatus, type Problem } from "@/lib/api";

export default function OvertureSettingsPage() {
  const { session } = useAuth();
  const [status, setStatus] = useState<OvertureStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { void getOvertureStatus().then(setStatus).catch(setError); }, []);
  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para ver Overture." }} />;
  if (error) return <AuthError error={error} />;
  if (!status) return <Skeleton active paragraph={{ rows: 10 }} />;
  return <Flex vertical gap="large">
    <div><Typography.Title level={2}>Cobertura Overture</Typography.Title><Typography.Paragraph type="secondary">El worker de mantenimiento importa particiones verificadas. La API sólo muestra estado seguro y no expone proveedores ni credenciales.</Typography.Paragraph></div>
    <Alert type="info" showIcon message={`Snapshot activo: ${status.active_snapshot_id ?? "ninguno"}`} description={`Último snapshot: ${status.latest_snapshot_id ?? "ninguno"}`} />
    <Card title="Particiones">
      {status.partitions.length ? <Table rowKey="id" dataSource={status.partitions} pagination={{ pageSize: 10, responsive: true }} columns={[{ title: "Provincia", dataIndex: "province_name" }, { title: "Release", dataIndex: "release_id" }, { title: "Estado", dataIndex: "status", render: (value: string) => <Tag>{value}</Tag> }, { title: "Lugares", dataIndex: "place_count" }, { title: "Activa", dataIndex: "is_active", render: (value: boolean) => value ? "Sí" : "No" }]} /> : <Empty description="No hay particiones importadas." />}
    </Card>
    {error ? <Alert type="error" message={problemMessage(error as Problem)} /> : null}
  </Flex>;
}
