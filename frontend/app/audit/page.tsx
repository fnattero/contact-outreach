"use client";

import { Card, Empty, Flex, List, Skeleton, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { getAuditEvents, type AuditEvent } from "@/lib/api";

const dateFormatter = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/Argentina/Buenos_Aires",
});

export default function AuditPage() {
  const { session } = useAuth();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void getAuditEvents().then((response) => setEvents(response.data)).catch(setError).finally(() => setLoading(false));
  }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para ver auditoría." }} />;
  if (error) return <AuthError error={error} />;
  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Auditoría</Typography.Title>
        <Typography.Paragraph type="secondary">Registro append-only de cambios y acciones sensibles.</Typography.Paragraph>
      </div>
      <Card>
        {loading ? <Skeleton active paragraph={{ rows: 8 }} /> : events.length ? (
          <List dataSource={events} renderItem={(event) => (
            <List.Item>
              <List.Item.Meta
                title={<Flex gap="small" wrap><Typography.Text strong>{event.action}</Typography.Text><Tag>{event.entity_type}</Tag></Flex>}
                description={`${dateFormatter.format(new Date(event.created_at))} · ${event.actor ?? "Sistema"} · ${event.entity_id}`}
              />
              <Typography.Text code>{event.correlation_id}</Typography.Text>
            </List.Item>
          )} />
        ) : <Empty description="Todavía no hay eventos." />}
      </Card>
    </Flex>
  );
}
