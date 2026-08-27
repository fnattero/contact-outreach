"use client";

import { Card, Empty, Flex, List, Skeleton, Tag, Typography } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getInboundMessages, type InboundMessage } from "@/lib/api";

export default function ResponsesPage() {
  const [messages, setMessages] = useState<InboundMessage[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void getInboundMessages()
      .then((response) => {
        if (!cancelled) setMessages(response.data);
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
  if (loading) return <Skeleton active paragraph={{ rows: 6 }} />;

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Respuestas</Typography.Title>
        <Typography.Paragraph type="secondary">
          Conversaciones recibidas y clasificadas por el backend.
        </Typography.Paragraph>
      </div>
      <Card>
        {messages.length ? (
          <List
            dataSource={messages}
            renderItem={(message) => (
              <List.Item actions={[<Link key="thread" href={`/responses/${message.id}`}>Abrir hilo</Link>]}> 
                <List.Item.Meta
                  title={message.subject || "Sin asunto"}
                  description={`${message.sender} · ${new Date(message.external_at).toLocaleString("es-AR")}`}
                />
                <Flex align="center" gap="small">
                  <Tag>{message.classification_label}</Tag>
                  {!message.is_read ? <Tag color="blue">Nueva</Tag> : null}
                </Flex>
              </List.Item>
            )}
          />
        ) : <Empty description="Todavía no hay respuestas." />}
      </Card>
    </Flex>
  );
}
