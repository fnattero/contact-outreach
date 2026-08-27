"use client";

import { Alert, Card, Descriptions, Empty, Flex, List, Skeleton, Tag, Typography } from "antd";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getContact, type ContactDetail } from "@/lib/api";

const dateFormatter = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/Argentina/Buenos_Aires",
});

function formatDate(value: string | null): string {
  return value ? dateFormatter.format(new Date(value)) : "—";
}

export default function ContactDetailPage() {
  const params = useParams<{ id: string }>();
  const [contact, setContact] = useState<ContactDetail | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    void getContact(params.id)
      .then((nextContact) => {
        if (!cancelled) setContact(nextContact);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      });
    return () => {
      cancelled = true;
    };
  }, [params.id]);

  if (error) return <AuthError error={error} />;
  if (!contact) return <Skeleton active paragraph={{ rows: 10 }} />;

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>{contact.name || contact.organization_name}</Typography.Title>
        <Flex gap="small" wrap>
          <Tag>{contact.status}</Tag>
          <Typography.Text type="secondary">{contact.organization_name}</Typography.Text>
        </Flex>
      </div>

      <Card title="Datos principales">
        <Descriptions column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Organización">{contact.organization_name}</Descriptions.Item>
          <Descriptions.Item label="Email preferido">
            {contact.preferred_email ?? "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Última interacción">
            {formatDate(contact.last_interaction_at)}
          </Descriptions.Item>
          <Descriptions.Item label="Próximo contacto">
            {formatDate(contact.next_follow_up_at)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="Emails">
        {contact.emails.length ? (
          <List
            dataSource={contact.emails}
            renderItem={(email) => (
              <List.Item>
                <List.Item.Meta
                  title={email.original_email}
                  description={email.label || "Sin etiqueta"}
                />
                <Flex gap="small" wrap justify="end">
                  {email.is_preferred ? <Tag color="blue">Preferido</Tag> : null}
                  <Tag>{email.validity}</Tag>
                  {email.active_restriction_count ? (
                    <Tag color="red">Restringido</Tag>
                  ) : null}
                </Flex>
              </List.Item>
            )}
          />
        ) : (
          <Empty description="No hay emails registrados." />
        )}
      </Card>

      {contact.restrictions.length ? (
        <Card title="Restricciones">
          <List
            dataSource={contact.restrictions}
            renderItem={(restriction) => (
              <List.Item>
                <List.Item.Meta
                  title={`${restriction.scope} · ${restriction.kind}`}
                  description={restriction.evidence}
                />
                <Tag color={restriction.revoked_at ? "default" : "red"}>
                  {restriction.revoked_at ? "Revocada" : "Activa"}
                </Tag>
              </List.Item>
            )}
          />
        </Card>
      ) : null}

      <Card title="Conversaciones">
        {contact.timelines.length ? (
          <Flex vertical gap="large">
            {contact.timelines.map((timeline) => (
              <div key={`${timeline.subject}-${timeline.first_at}`}>
                <Typography.Title level={5}>{timeline.subject || "Sin asunto"}</Typography.Title>
                <Typography.Text type="secondary">
                  {formatDate(timeline.first_at)} — {formatDate(timeline.last_at)}
                </Typography.Text>
                <List
                  itemLayout="vertical"
                  dataSource={timeline.items}
                  renderItem={(item) => (
                    <List.Item>
                      <Typography.Text strong>
                        {item.direction === "inbound" ? "Entrante" : "Saliente"} · {item.sender}
                      </Typography.Text>
                      <Typography.Text type="secondary">{formatDate(item.happened_at)}</Typography.Text>
                      <Typography.Paragraph className="message-plain-text">
                        {item.body}
                      </Typography.Paragraph>
                      {item.needs_attention ? <Tag color="warning">Requiere atención</Tag> : null}
                    </List.Item>
                  )}
                />
              </div>
            ))}
          </Flex>
        ) : (
          <Empty description="No hay conversaciones registradas." />
        )}
      </Card>

      <Alert
        type="info"
        message="La ficha muestra texto plano"
        description="El contenido de los emails no se inserta como HTML dentro de la aplicación."
      />
    </Flex>
  );
}
