"use client";

import { Alert, Button, Card, Empty, Flex, Form, Input, Skeleton, Tag, Typography, message } from "antd";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { getInboundThread, problemMessage, sendManualReply, type InboundThread, type Problem } from "@/lib/api";

export default function ResponseThreadPage() {
  const params = useParams<{ id: string }>();
  const { session } = useAuth();
  const [thread, setThread] = useState<InboundThread | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [replyBusy, setReplyBusy] = useState(false);
  const [pendingReply, setPendingReply] = useState<string | null>(null);

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

  const canReply = session?.capabilities.includes("send_replies") ?? false;

  async function submitReply(bodyText: string) {
    setReplyBusy(true);
    try {
      await sendManualReply(params.id, bodyText, crypto.randomUUID());
      message.success("Respuesta autorizada y encolada para Gmail.");
      setPendingReply(null);
      setThread(await getInboundThread(params.id));
    } catch (replyError) {
      message.error(problemMessage((replyError as Problem) ?? {}));
    } finally {
      setReplyBusy(false);
    }
  }

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
      {canReply && thread.inbound.is_human ? (
        <Card title="Responder manualmente">
          <Alert
            type="warning"
            showIcon
            message="La respuesta manual requiere envío en vivo y Gmail probado."
            description="El backend volverá a comprobar la conversación, las restricciones y los bloqueos antes de encolar el envío."
            style={{ marginBottom: 16 }}
          />
          <Form layout="vertical" onFinish={(values: { body_text: string }) => setPendingReply(values.body_text)}>
            <Form.Item
              name="body_text"
              label="Texto de la respuesta"
              rules={[{ required: true, message: "Escribí una respuesta." }, { max: 10000 }]}
            >
              <Input.TextArea rows={6} showCount maxLength={10000} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={replyBusy}>
              Revisar y autorizar
            </Button>
          </Form>
        </Card>
      ) : null}
      {pendingReply !== null ? (
        <ConfirmDangerModal
          open
          title="Autorizar respuesta"
          consequences={[
            "El mensaje se enviará desde Gmail al contacto y no se puede retirar.",
            "Antes de enviar se vuelven a comprobar las restricciones, la conversación y las tareas abiertas.",
          ]}
          confirmationWord="CONFIRMAR"
          dangerLabel="Autorizar y enviar"
          confirming={replyBusy}
          onCancel={() => setPendingReply(null)}
          onConfirm={() => void submitReply(pendingReply)}
        />
      ) : null}
    </Flex>
  );
}
