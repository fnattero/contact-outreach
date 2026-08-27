"use client";

import { Card, Descriptions, Flex, Skeleton, Tag, Typography } from "antd";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getOutboundMessage, type OutboundMessage } from "@/lib/api";

export default function OutboundDetailPage() {
  const params = useParams<{ id: string }>();
  const [message, setMessage] = useState<OutboundMessage | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    void getOutboundMessage(params.id)
      .then((nextMessage) => {
        if (!cancelled) setMessage(nextMessage);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      });
    return () => {
      cancelled = true;
    };
  }, [params.id]);

  if (error) return <AuthError error={error} />;
  if (!message) return <Skeleton active paragraph={{ rows: 8 }} />;

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>{message.subject || "Mensaje sin asunto"}</Typography.Title>
        <Tag>{message.state_label}</Tag>
      </div>
      <Card title="Detalles">
        <Descriptions column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Destinatario">{message.recipient}</Descriptions.Item>
          <Descriptions.Item label="Tipo">{message.kind_label}</Descriptions.Item>
          <Descriptions.Item label="Fecha">{new Date(message.sent_at ?? message.created_at).toLocaleString("es-AR")}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="Contenido">
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>{message.body_text}</Typography.Paragraph>
      </Card>
    </Flex>
  );
}
