"use client";

import { Card, Empty, Flex, Skeleton, Tag, Typography } from "antd";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getInboundThread, type InboundThread } from "@/lib/api";

export default function ResponseThreadPage() {
  const params = useParams<{ id: string }>();
  const [thread, setThread] = useState<InboundThread | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    void getInboundThread(params.id)
      .then((nextThread) => {
        if (!cancelled) setThread(nextThread);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      });
    return () => {
      cancelled = true;
    };
  }, [params.id]);

  if (error) return <AuthError error={error} />;
  if (!thread) return <Skeleton active paragraph={{ rows: 8 }} />;

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>{thread.inbound.subject || "Conversación"}</Typography.Title>
        <Tag>{thread.inbound.classification_label}</Tag>
      </div>
      {thread.timeline.length ? thread.timeline.map((item, index) => (
        <Card key={`${item.at}-${index}`} title={item.direction === "inbound" ? "Recibido" : "Enviado"}>
          <Typography.Text type="secondary">{item.sender} · {new Date(item.at).toLocaleString("es-AR")}</Typography.Text>
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>{item.body_text}</Typography.Paragraph>
        </Card>
      )) : <Empty description="No hay mensajes en este hilo." />}
    </Flex>
  );
}
