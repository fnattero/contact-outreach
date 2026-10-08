"use client";

import { Alert, Button, Card, Collapse, Drawer, Flex, Form, Input, Modal } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { StickySaveBar } from "@/components/design-system/forms";
import { PageHeader } from "@/components/design-system/page-header";
import { LoadingState } from "@/components/design-system/states";
import {
  can,
  createMessageTemplate,
  getMessageTemplates,
  problemMessage,
  type MessageTemplate,
  type Problem,
} from "@/lib/api";
import { useHasCampaignDraft } from "@/lib/campaign-draft";

type Kind = MessageTemplate["kind"];
type FormValues = { subject: string; body: string };
type Feedback =
  | { state: "idle" | "saving"; message?: string }
  | { state: "saved" | "error"; message: string };

const KINDS: ReadonlyArray<{ kind: Kind; title: string; when: string; hasSubject: boolean; where: string; href: string; linkLabel: string }> = [
  {
    kind: "INITIAL",
    title: "Propuesta inicial",
    when: "El primer correo que recibe cada negocio de la campaña.",
    hasSubject: true,
    where: "Se elige al crear cada campaña.",
    href: "/campaigns/new",
    linkLabel: "Crear una campaña",
  },
  {
    kind: "REMINDER",
    title: "Recordatorio",
    when: "Se envía una sola vez, en el mismo hilo, si el negocio no respondió. Usa el asunto de la propuesta.",
    hasSubject: false,
    where: "Se activa en cada campaña, con los días de espera que elijas.",
    href: "/campaigns",
    linkLabel: "Ver campañas",
  },
  {
    kind: "REFERRED_PROPOSAL",
    title: "Propuesta reenviada",
    when: "Se usa cuando alguien te pide que le escribas a otra persona: la propuesta va a esa nueva dirección.",
    hasSubject: true,
    where: "No se elige en las campañas: se usa al derivar la propuesta, desde los correos recibidos.",
    href: "/responses",
    linkLabel: "Ver correos recibidos",
  },
];

const dateFormatter = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/Argentina/Buenos_Aires",
});

export default function MessageTemplatesPage() {
  const { session } = useAuth();
  const allowed = can(session, "manage_configuration");
  const [form] = Form.useForm<FormValues>();
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState<Kind | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>({ state: "idle" });
  const hasCampaignDraft = useHasCampaignDraft();

  useEffect(() => {
    if (!allowed) return;
    void getMessageTemplates().then(setTemplates).catch(setError).finally(() => setLoading(false));
  }, [allowed]);

  const active = (kind: Kind) => templates.find((item) => item.kind === kind && item.active);
  const editingKind = KINDS.find((item) => item.kind === editing);

  function startEditing(kind: Kind) {
    const current = active(kind);
    form.setFieldsValue({ subject: current?.subject ?? "", body: current?.body ?? "" });
    setDirty(false);
    setFeedback({ state: "idle" });
    setEditing(kind);
  }

  async function save(values: FormValues) {
    if (!editing) return;
    setSaving(true);
    setError(null);
    try {
      const created = await createMessageTemplate({ kind: editing, subject: values.subject ?? "", body: values.body });
      setTemplates((current) => [
        created,
        ...current.map((item) => (item.kind === created.kind ? { ...item, active: false } : item)),
      ]);
      setDirty(false);
      setEditing(null);
      setFeedback({ state: "saved", message: "Mensaje guardado. Las campañas nuevas ya lo usan." });
    } catch (problem) {
      setError(problem);
      setFeedback({ state: "error", message: "No se pudo guardar el mensaje." });
    } finally {
      setSaving(false);
    }
  }

  function requestClose() {
    if (dirty) setCancelOpen(true);
    else setEditing(null);
  }

  if (!allowed) return <AuthError error={{ detail: "No tenés permisos para editar mensajes." }} />;
  if (loading) return <LoadingState layout="list" />;
  if (error && !templates.length) return <AuthError error={error} />;

  const previous = templates.filter((item) => !item.active);

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Mensajes de campaña"
        description="Los textos que reciben todos los negocios de una campaña. Si cambiás uno, las campañas que ya aprobaste conservan el texto que tenían."
      />
      {hasCampaignDraft ? (
        <Alert
          type="info"
          showIcon
          message="Tenés una campaña a medio crear"
          description={<span>Lo que completaste se guardó. Cuando termines de editar, <Link href="/campaigns/new">volvé a la campaña</Link> y seguí desde donde estabas.</span>}
        />
      ) : null}
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      {feedback.state === "saved" ? (
        <p className="form-save-feedback form-save-feedback--saved" role="status">{feedback.message}</p>
      ) : null}
      <div className="message-cards">
        {KINDS.map((item) => {
          const current = active(item.kind);
          return (
            <Card
              key={item.kind}
              title={item.title}
              extra={<Button onClick={() => startEditing(item.kind)}>{current ? "Editar" : "Escribir"}</Button>}
            >
              <p className="muted">{item.when}</p>
              <p className="muted">
                {item.where} <Link href={item.href}>{item.linkLabel}</Link>
              </p>
              {current ? (
                <div className="message-preview">
                  {item.hasSubject ? (
                    <p className="message-preview__subject">
                      <span className="muted">Asunto:</span> {current.subject}
                    </p>
                  ) : null}
                  <p className="message-preview__body">{current.body}</p>
                </div>
              ) : (
                <p>Todavía no hay texto para este mensaje.</p>
              )}
            </Card>
          );
        })}
      </div>
      <p className="muted">
        La firma se agrega sola al final de cada mensaje. Se escribe en el <Link href="/settings/profile">perfil comercial</Link>.
      </p>
      {previous.length ? (
        <Collapse
          items={[
            {
              key: "previous",
              label: `Versiones anteriores (${previous.length})`,
              children: (
                <div className="template-history" role="list">
                  {previous.map((template) => (
                    <div className="template-history__entry" role="listitem" key={template.id}>
                      <div className="template-history__main">
                        <strong>{KINDS.find((item) => item.kind === template.kind)?.title}</strong>
                        <span className="template-history__subject">{template.subject || "Sin asunto"}</span>
                      </div>
                      <span className="template-history__revision">Versión {template.revision}</span>
                      <time className="template-history__date" dateTime={template.approved_at}>
                        {dateFormatter.format(new Date(template.approved_at))}
                      </time>
                    </div>
                  ))}
                </div>
              ),
            },
          ]}
        />
      ) : null}
      <Drawer
        title={editingKind ? `Editar: ${editingKind.title}` : ""}
        open={editing !== null}
        onClose={requestClose}
        width={640}
        destroyOnClose
      >
        <p className="drawer-explanation">{editingKind?.when}</p>
        <Form
          form={form}
          layout="vertical"
          validateTrigger="onBlur"
          onValuesChange={() => {
            setDirty(true);
            setFeedback({ state: "idle" });
          }}
          onFinish={(values) => void save(values)}
        >
          {editingKind?.hasSubject ? (
            <Form.Item
              name="subject"
              label="Asunto"
              extra="Un asunto claro ayuda a que reconozcan la propuesta."
              rules={[{ required: true, message: "Escribí el asunto." }]}
            >
              <Input maxLength={255} />
            </Form.Item>
          ) : null}
          <Form.Item
            name="body"
            label="Texto del correo"
            extra="El mismo texto para todos los negocios: no admite datos variables como el nombre del negocio."
            rules={[
              { required: true, message: "Escribí el texto del correo." },
              { max: 12000, message: "No puede superar 12000 caracteres." },
            ]}
          >
            <Input.TextArea rows={14} maxLength={12000} showCount />
          </Form.Item>
        </Form>
        <StickySaveBar
          dirty={dirty}
          feedback={saving ? { state: "saving", message: "Guardando…" } : feedback}
          onSave={() => void form.submit()}
          onCancel={requestClose}
        />
      </Drawer>
      <Modal
        open={cancelOpen}
        title="Descartar cambios sin guardar"
        onCancel={() => setCancelOpen(false)}
        onOk={() => {
          setDirty(false);
          setCancelOpen(false);
          setEditing(null);
        }}
        okText="Descartar cambios"
        cancelText="Seguir editando"
        okButtonProps={{ danger: true }}
      >
        <p>Lo que editaste se perderá si cerrás este panel.</p>
      </Modal>
    </Flex>
  );
}
