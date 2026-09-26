"use client";

import { Alert, Button, Card, Drawer, Flex, Form, Input, Modal, Select } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import {
  createMessageTemplate,
  getMessageTemplates,
  problemMessage,
  type MessageTemplate,
  type Problem,
} from "@/lib/api";

const labels: Record<MessageTemplate["kind"], string> = {
  INITIAL: "Propuesta inicial",
  REMINDER: "Recordatorio",
  REFERRED_PROPOSAL: "Propuesta referida",
};

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export default function MessageTemplatesPage() {
  const { session } = useAuth();
  const [form] = Form.useForm<{ kind: MessageTemplate["kind"]; subject: string; body: string }>();
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [feedback, setFeedback] = useState<
    | { state: "idle" | "saving"; message?: string }
    | { state: "saved" | "error"; message: string }
  >({ state: "idle" });

  useEffect(() => {
    void getMessageTemplates().then(setTemplates).catch(setError).finally(() => setLoading(false));
  }, []);

  async function submit(values: { kind: MessageTemplate["kind"]; subject: string; body: string }) {
    setSaving(true); setFeedback({ state: "saving", message: "Guardando revisión…" }); setError(null);
    try {
      const created = await createMessageTemplate(values);
      setTemplates((current) => [created, ...current.map((item) => item.kind === created.kind ? { ...item, active: false } : item)]);
      setDirty(false);
      setDrawerOpen(false);
      setFeedback({ state: "saved", message: "Revisión guardada." });
      form.resetFields();
    } catch (problem) {
      setError(problem); setFeedback({ state: "error", message: "No se pudo guardar la revisión." });
    } finally { setSaving(false); }
  }

  function openDrawer() {
    form.resetFields();
    setFeedback({ state: "idle" });
    setDirty(false);
    setDrawerOpen(true);
  }

  function requestClose() {
    if (dirty) setCancelOpen(true);
    else setDrawerOpen(false);
  }

  function discardChanges() {
    form.resetFields();
    setDirty(false);
    setCancelOpen(false);
    setDrawerOpen(false);
    setFeedback({ state: "idle" });
  }

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para editar mensajes." }} />;
  if (loading) return <LoadingState layout="list" />;
  if (error && !templates.length) return <AuthError error={error} />;

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Mensajes de campaña"
        description="Las campañas aprobadas conservan el contenido que tenían cuando se aprobaron. Cada cambio crea una nueva revisión."
        primaryAction={<Button type="primary" onClick={openDrawer}>Crear nueva revisión</Button>}
      />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      {feedback.state === "saved" ? <p className="form-save-feedback form-save-feedback--saved" role="status">{feedback.message}</p> : null}
      {templates.length === 0 ? (
        <EmptyState headline="Todavía no hay revisiones" explanation="Creá la primera revisión para que las campañas puedan usar un mensaje aprobado." actionLabel="Crear primera revisión" onAction={openDrawer} />
      ) : (
        <Card title="Historial de revisiones">
          <div className="template-history" role="list">
            {templates.map((template) => (
              <div className="template-history__entry" role="listitem" key={template.id}>
                <div className="template-history__main"><strong>{labels[template.kind]}</strong><span className="template-history__revision">Revisión {template.revision}</span><span className="template-history__subject">{template.subject || "Sin asunto"}</span></div>
                <StatusBadge label={template.active ? "Activa" : "Histórica"} level={template.active ? "success" : "inactive"} />
                <time className="template-history__date" dateTime={template.approved_at}>{formatDate(template.approved_at)}</time>
              </div>
            ))}
          </div>
        </Card>
      )}
      <Drawer title="Crear nueva revisión" open={drawerOpen} onClose={requestClose} width={560} destroyOnClose>
        <p className="drawer-explanation">La revisión se registra y pasa a ser la activa para su tipo. Las campañas ya aprobadas mantienen su contenido actual.</p>
        <Form form={form} layout="vertical" validateTrigger="onBlur" onValuesChange={() => { setDirty(true); setFeedback({ state: "idle" }); }} onFinish={(values) => void submit(values)}>
          <FormSection title="Contenido de la revisión" description="Definí el tipo de mensaje y el texto que quedará auditado.">
            <Form.Item name="kind" label="Tipo de mensaje" extra="Elegí en qué momento de la campaña se usará." rules={[{ required: true, message: "Elegí un tipo de mensaje." }]}>
              <Select options={Object.entries(labels).map(([value, label]) => ({ value, label }))} />
            </Form.Item>
            <Form.Item name="subject" label="Asunto" extra="Un asunto claro ayuda al contacto a reconocer la propuesta."><Input maxLength={255} /></Form.Item>
            <Form.Item name="body" label="Cuerpo" extra="El contenido queda guardado como una revisión auditable." rules={[{ required: true, message: "Escribí el cuerpo del mensaje." }, { max: 12000, message: "No puede superar 12000 caracteres." }]}>
              <Input.TextArea rows={12} maxLength={12000} showCount />
            </Form.Item>
          </FormSection>
        </Form>
        <StickySaveBar dirty={dirty} feedback={saving ? { state: "saving", message: "Guardando revisión…" } : feedback} onSave={() => void form.submit()} onCancel={requestClose} />
      </Drawer>
      <Modal open={cancelOpen} title="Descartar revisión sin guardar" onCancel={() => setCancelOpen(false)} onOk={discardChanges} okText="Descartar cambios" cancelText="Seguir editando" okButtonProps={{ danger: true }}>
        <p>El contenido que editaste se perderá si cerrás este panel.</p>
      </Modal>
    </Flex>
  );
}
