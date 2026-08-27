"use client";

import { Card, Empty, Flex, List, Skeleton, Tag, Typography } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getOutboundMessages, type OutboundMessage } from "@/lib/api";

export default function OutboundPage() {
  const [messages, setMessages] = useState<OutboundMessage[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void getOutboundMessages()
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
        <Typography.Title level={2}>Envíos</Typography.Title>
        <Typography.Paragraph type="secondary">
          Mensajes y estados de entrega disponibles para tu rol.
        </Typography.Paragraph>
      </div>
      <Card>
        {messages.length ? (
          <List
            dataSource={messages}
            renderItem={(message) => (
              <List.Item actions={[<Link key="detail" href={`/outbound/${message.id}`}>Ver mensaje</Link>]}> 
                <List.Item.Meta
                  title={message.subject || "Sin asunto"}
                  description={`${message.recipient} · ${message.kind_label}`}
                />
                <Tag>{message.state_label}</Tag>
              </List.Item>
            )}
          />
        ) : <Empty description="Todavía no hay envíos." />}
      </Card>
    </Flex>
  );
}
