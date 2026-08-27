"use client";

import { Alert, Button, Card, Empty, Flex, List, Skeleton, Tag, Typography } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { getContacts, type Contact } from "@/lib/api";

const dateFormatter = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/Argentina/Buenos_Aires",
});

function formatDate(value: string | null): string {
  return value ? dateFormatter.format(new Date(value)) : "Sin interacción registrada";
}

export default function ContactsPage() {
  const { session } = useAuth();
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void getContacts()
      .then((response) => {
        if (!cancelled) setContacts(response.data);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <AuthError error={error} />;
  if (loading) return <Skeleton active paragraph={{ rows: 8 }} />;

  return (
    <Flex vertical gap="large">
      <Flex justify="space-between" align="center" wrap gap="middle">
        <div>
          <Typography.Title level={2}>Contactos</Typography.Title>
          <Typography.Paragraph type="secondary">
            Personas y organizaciones con historial de comunicación.
          </Typography.Paragraph>
        </div>
        {session?.role === "ADMIN" ? (
          <Link href="/contacts/new">
            <Button type="primary">Nuevo contacto</Button>
          </Link>
        ) : null}
      </Flex>
      <Card>
        {contacts.length ? (
          <List
            itemLayout="vertical"
            dataSource={contacts}
            renderItem={(contact) => (
              <List.Item
                actions={[
                  <Link key="view" href={`/contacts/${contact.id}`}>
                    Ver ficha
                  </Link>,
                ]}
              >
                <List.Item.Meta
                  title={contact.name || contact.organization_name}
                  description={
                    <Flex vertical gap={4}>
                      <Typography.Text>{contact.organization_name}</Typography.Text>
                      <Typography.Text type="secondary">
                        {contact.preferred_email ?? "Sin email preferido"}
                      </Typography.Text>
                    </Flex>
                  }
                />
                <Flex gap="small" wrap>
                  <Tag>{contact.status}</Tag>
                  {contact.open_task_count ? (
                    <Tag color="warning">{contact.open_task_count} tarea(s)</Tag>
                  ) : null}
                  <Typography.Text type="secondary">
                    Última interacción: {formatDate(contact.last_interaction_at)}
                  </Typography.Text>
                </Flex>
              </List.Item>
            )}
          />
        ) : (
          <Empty description="Todavía no hay contactos." />
        )}
      </Card>
      <Alert
        type="info"
        showIcon
        message="Las restricciones se aplican en cada etapa de la entrega."
        description="Una baja o restricción activa no puede ser salteada por campañas ni automatizaciones."
      />
    </Flex>
  );
}
