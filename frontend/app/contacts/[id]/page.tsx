"use client";

import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Flex,
  Form,
  Input,
  List,
  Popconfirm,
  Select,
  Skeleton,
  Tag,
  Typography,
} from "antd";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
  addContactEmail,
  createContactRestriction,
  createCommunicationPlan,
  getContact,
  getCommunicationPlans,
  revokeContactRestriction,
  setPreferredEmail,
  validateContactEmail,
  type ContactDetail,
  type CommunicationPlan,
} from "@/lib/api";

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
  const [busy, setBusy] = useState<string | null>(null);
  const [plans, setPlans] = useState<CommunicationPlan[]>([]);
  const { session } = useAuth();

  function refresh() {
    void getContact(params.id).then(setContact).catch(setError);
    if (session?.role === "ADMIN") void getCommunicationPlans(params.id).then(setPlans).catch(setError);
  }

  useEffect(() => {
    let cancelled = false;
    void getContact(params.id)
      .then((nextContact) => {
        if (!cancelled) setContact(nextContact);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      });
    if (session?.role === "ADMIN") void getCommunicationPlans(params.id).then(setPlans).catch(setError);
    return () => {
      cancelled = true;
    };
  }, [params.id, session?.role]);

  if (error) return <AuthError error={error} />;
  if (!contact) return <Skeleton active paragraph={{ rows: 10 }} />;

  async function addEmail(values: { email: string; label?: string }) {
    setBusy("email"); setError(null);
    try { await addContactEmail(params.id, values.email, values.label ?? "", false); refresh(); }
    catch (problem) { setError(problem); } finally { setBusy(null); }
  }

  async function addRestriction(values: { scope: "CONTACT" | "EMAIL"; email_address_id?: string; reason: string }) {
    setBusy("restriction"); setError(null);
    try { await createContactRestriction(params.id, values.scope, values.reason, values.email_address_id); refresh(); }
    catch (problem) { setError(problem); } finally { setBusy(null); }
  }

  async function choosePreferred(emailId: string) {
    setBusy(emailId); setError(null);
    try { await setPreferredEmail(params.id, emailId); refresh(); }
    catch (problem) { setError(problem); } finally { setBusy(null); }
  }

  async function validateEmail(emailId: string) {
    setBusy(emailId); setError(null);
    try { await validateContactEmail(params.id, emailId); refresh(); }
    catch (problem) { setError(problem); } finally { setBusy(null); }
  }

  async function revokeRestriction(restrictionId: string) {
    setBusy(restrictionId); setError(null);
    try { await revokeContactRestriction(params.id, restrictionId, "Revisión manual del equipo."); refresh(); }
    catch (problem) { setError(problem); } finally { setBusy(null); }
  }

  async function addPlan(values: { preferred_email_id: string; purpose: string; goal_text?: string }) {
    setBusy("plan"); setError(null);
    try {
      const plan = await createCommunicationPlan(params.id, { ...values, cadence_days: 30, mode: "REVIEW_BEFORE_SEND", enabled: true });
      setPlans((current) => [...current, plan]);
    } catch (problem) { setError(problem); } finally { setBusy(null); }
  }

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
                  {session?.role === "ADMIN" && !email.is_preferred ? (
                    <Button size="small" loading={busy === email.id} onClick={() => void choosePreferred(email.id)}>Preferir</Button>
                  ) : null}
                  {session?.role === "ADMIN" && email.validity !== "VALID" ? (
                    <Button size="small" loading={busy === email.id} onClick={() => void validateEmail(email.id)}>Validar</Button>
                  ) : null}
                </Flex>
              </List.Item>
            )}
          />
        ) : (
          <Empty description="No hay emails registrados." />
        )}
      </Card>

      {session?.role === "ADMIN" ? (
        <Card title="Agregar email">
          <Form layout="vertical" onFinish={(values) => void addEmail(values as { email: string; label?: string })}>
            <Form.Item name="email" label="Email" rules={[{ required: true, type: "email" }]}><Input /></Form.Item>
            <Form.Item name="label" label="Etiqueta"><Input placeholder="Trabajo, ventas…" /></Form.Item>
            <Button type="primary" htmlType="submit" loading={busy === "email"}>Agregar email</Button>
          </Form>
        </Card>
      ) : null}

      {contact.restrictions.length || session?.role === "ADMIN" ? (
        <Card title="Restricciones">
          {session?.role === "ADMIN" ? (
            <Form layout="vertical" onFinish={(values) => void addRestriction(values as { scope: "CONTACT" | "EMAIL"; email_address_id?: string; reason: string })}>
              <Flex gap="middle" wrap>
                <Form.Item name="scope" label="Alcance" rules={[{ required: true }]}><Select style={{ minWidth: 180 }} options={[{ value: "CONTACT", label: "Todo el contacto" }, { value: "EMAIL", label: "Sólo este email" }]} /></Form.Item>
                <Form.Item name="email_address_id" label="Email (si corresponde)"><Select allowClear style={{ minWidth: 240 }} options={contact.emails.map((email) => ({ value: email.id, label: email.original_email }))} /></Form.Item>
              </Flex>
              <Form.Item name="reason" label="Motivo" rules={[{ required: true, min: 3, max: 1000 }]}><Input.TextArea rows={2} /></Form.Item>
              <Button htmlType="submit" loading={busy === "restriction"}>Agregar restricción manual</Button>
            </Form>
          ) : null}
          <List
            dataSource={contact.restrictions}
            renderItem={(restriction) => (
              <List.Item>
                <List.Item.Meta
                  title={`${restriction.scope} · ${restriction.kind}`}
                  description={restriction.evidence}
                />
                <Flex gap="small" align="center">
                  <Tag color={restriction.revoked_at ? "default" : "red"}>{restriction.revoked_at ? "Revocada" : "Activa"}</Tag>
                  {session?.role === "ADMIN" && restriction.kind === "MANUAL" && !restriction.revoked_at ? (
                    <Popconfirm title="¿Revocar esta restricción?" onConfirm={() => void revokeRestriction(restriction.id)} okText="Revocar" cancelText="Volver">
                      <Button size="small" loading={busy === restriction.id}>Revocar</Button>
                    </Popconfirm>
                  ) : null}
                </Flex>
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

      {session?.role === "ADMIN" ? (
        <Card title="Seguimiento programado">
          <Form layout="vertical" onFinish={(values) => void addPlan(values as { preferred_email_id: string; purpose: string; goal_text?: string })}>
            <Form.Item name="preferred_email_id" label="Email preferido" rules={[{ required: true }]}><Select options={contact.emails.filter((email) => email.is_preferred && email.validity === "VALID").map((email) => ({ value: email.id, label: email.original_email }))} /></Form.Item>
            <Form.Item name="purpose" label="Objetivo" rules={[{ required: true }]}><Select options={[{ value: "CHECK_IN", label: "Preguntar cómo está" }, { value: "PRODUCT_FEEDBACK", label: "Pedir opinión del producto" }, { value: "ADMIN_GOAL", label: "Objetivo personalizado" }]} /></Form.Item>
            <Form.Item name="goal_text" label="Objetivo personalizado"><Input.TextArea rows={2} /></Form.Item>
            <Button htmlType="submit" loading={busy === "plan"}>Activar revisión periódica</Button>
          </Form>
          {plans.length ? <List style={{ marginTop: 16 }} dataSource={plans} renderItem={(plan) => <List.Item><List.Item.Meta title={plan.topic_name} description={`Próximo: ${formatDate(plan.next_due_at)}`} /><Tag>{plan.state_label}</Tag></List.Item>} /> : <Empty style={{ marginTop: 16 }} description="No hay seguimientos configurados." />}
        </Card>
      ) : null}

      <Alert
        type="info"
        message="La ficha muestra texto plano"
        description="El contenido de los emails no se inserta como HTML dentro de la aplicación."
      />
    </Flex>
  );
}
