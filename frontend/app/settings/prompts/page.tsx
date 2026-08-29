"use client";

import { Alert, Flex, Form, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import { PageHeader } from "@/components/design-system/page-header";
import { LoadingState } from "@/components/design-system/states";
import {
  getPromptConfiguration,
  problemMessage,
  updateAutomaticReplyPrompt,
  updatePromptConfiguration,
  type Problem,
  type PromptConfiguration,
} from "@/lib/api";

type Feedback =
  | { state: "idle" | "saving"; message?: string }
  | { state: "saved" | "error"; message: string };
type PromptFormKey = "email" | "reply";

const LIMIT = 4000;

function CharacterFooter({ count }: { count: number }) {
  const nearLimit = count >= LIMIT * 0.9;
  return <div className={`character-footer${nearLimit ? " character-footer--warning" : ""}`} aria-live="polite">{count}/{LIMIT}</div>;
}

export default function PromptsSettingsPage() {
  const { session } = useAuth();
  const [emailForm] = Form.useForm<{ prompt: string }>();
  const [replyForm] = Form.useForm<{ prompt: string }>();
  const [configuration, setConfiguration] = useState<PromptConfiguration | null>(null);
  const [saving, setSaving] = useState<PromptFormKey | null>(null);
  const [dirty, setDirty] = useState<Record<PromptFormKey, boolean>>({ email: false, reply: false });
  const [feedback, setFeedback] = useState<Record<PromptFormKey, Feedback>>({ email: { state: "idle" }, reply: { state: "idle" } });
  const [lengths, setLengths] = useState({ email: 0, reply: 0 });
  const [cancelOpen, setCancelOpen] = useState<PromptFormKey | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    void getPromptConfiguration().then((next) => {
      setConfiguration(next);
      emailForm.setFieldsValue({ prompt: next.email_drafting_prompt });
      replyForm.setFieldsValue({ prompt: next.automatic_reply_prompt });
      setLengths({ email: next.email_drafting_prompt.length, reply: next.automatic_reply_prompt.length });
    }).catch(setError);
  }, [emailForm, replyForm]);

  async function saveEmail(values: { prompt: string }) {
    setSaving("email"); setFeedback((current) => ({ ...current, email: { state: "saving", message: "Guardando revisión…" } })); setError(null);
    try {
      setConfiguration(await updatePromptConfiguration(values.prompt));
      setDirty((current) => ({ ...current, email: false }));
      setFeedback((current) => ({ ...current, email: { state: "saved", message: "Revisión guardada." } }));
    } catch (problem) {
      setError(problem); setFeedback((current) => ({ ...current, email: { state: "error", message: "No se pudo guardar esta revisión." } }));
    } finally { setSaving(null); }
  }

  async function saveReply(values: { prompt: string }) {
    setSaving("reply"); setFeedback((current) => ({ ...current, reply: { state: "saving", message: "Guardando revisión…" } })); setError(null);
    try {
      const saved = await updateAutomaticReplyPrompt(values.prompt);
      setConfiguration((current) => current ? { ...current, automatic_reply_prompt: saved.automatic_reply_prompt } : current);
      setDirty((current) => ({ ...current, reply: false }));
      setFeedback((current) => ({ ...current, reply: { state: "saved", message: "Revisión guardada." } }));
    } catch (problem) {
      setError(problem); setFeedback((current) => ({ ...current, reply: { state: "error", message: "No se pudo guardar esta revisión." } }));
    } finally { setSaving(null); }
  }

  function requestCancel(kind: PromptFormKey) {
    if (dirty[kind]) setCancelOpen(kind);
    else (kind === "email" ? emailForm : replyForm).resetFields();
  }

  function discardChanges() {
    const kind = cancelOpen;
    if (!kind) return;
    const form = kind === "email" ? emailForm : replyForm;
    form.resetFields();
    setDirty((current) => ({ ...current, [kind]: false }));
    setFeedback((current) => ({ ...current, [kind]: { state: "idle" } }));
    setCancelOpen(null);
  }

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para editar instrucciones." }} />;
  if (error && !configuration) return <AuthError error={error} />;
  if (!configuration) return <LoadingState layout="form" />;

  return (
    <Flex vertical gap="large">
      <PageHeader title="Instrucciones de escritura" description="Revisá las reglas que guían la redacción antes de guardarlas." />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <section className="instruction-notice" aria-labelledby="instruction-notice-title">
        <h2 className="type-title" id="instruction-notice-title">Revisiones auditadas y aisladas</h2>
        <p>Estas instrucciones quedan registradas como revisiones auditadas. No otorgan al modelo acceso a Gmail, red, archivos ni herramientas: sólo orientan el texto que propone.</p>
      </section>
      <div className="form-column">
        <FormSection title="Redacción de propuestas" description="Reglas para preparar el primer mensaje de una campaña.">
          <Form form={emailForm} layout="vertical" validateTrigger="onBlur" onValuesChange={(_, values) => { const value = String(values.prompt ?? ""); setLengths((current) => ({ ...current, email: value.length })); setDirty((current) => ({ ...current, email: true })); setFeedback((current) => ({ ...current, email: { state: "idle" } })); }} onFinish={(values) => void saveEmail(values)}>
            <Form.Item name="prompt" label="Instrucciones de redacción" extra="Describí el tono, los límites y la información aprobada que debe respetar." rules={[{ required: true, message: "Escribí las instrucciones." }, { max: LIMIT, message: `No puede superar ${LIMIT} caracteres.` }]}>
              <Input.TextArea rows={9} maxLength={LIMIT} showCount={false} />
            </Form.Item>
            <CharacterFooter count={lengths.email} />
          </Form>
        </FormSection>
        <StickySaveBar dirty={dirty.email} feedback={saving === "email" ? { state: "saving", message: "Guardando revisión…" } : feedback.email} onSave={() => void emailForm.submit()} onCancel={() => requestCancel("email")} />
        <FormSection title="Respuestas automáticas" description="Reglas para redactar respuestas cuando la política permite una propuesta automática.">
          <Form form={replyForm} layout="vertical" validateTrigger="onBlur" onValuesChange={(_, values) => { const value = String(values.prompt ?? ""); setLengths((current) => ({ ...current, reply: value.length })); setDirty((current) => ({ ...current, reply: true })); setFeedback((current) => ({ ...current, reply: { state: "idle" } })); }} onFinish={(values) => void saveReply(values)}>
            <Form.Item name="prompt" label="Instrucciones de respuesta" extra="Indicá qué puede contestar y cuándo debe pedir revisión humana." rules={[{ required: true, message: "Escribí las instrucciones." }, { max: LIMIT, message: `No puede superar ${LIMIT} caracteres.` }]}>
              <Input.TextArea rows={11} maxLength={LIMIT} showCount={false} />
            </Form.Item>
            <CharacterFooter count={lengths.reply} />
          </Form>
        </FormSection>
        <StickySaveBar dirty={dirty.reply} feedback={saving === "reply" ? { state: "saving", message: "Guardando revisión…" } : feedback.reply} onSave={() => void replyForm.submit()} onCancel={() => requestCancel("reply")} />
      </div>
      <Modal open={cancelOpen !== null} title="Descartar cambios sin guardar" onCancel={() => setCancelOpen(null)} onOk={discardChanges} okText="Descartar cambios" cancelText="Seguir editando" okButtonProps={{ danger: true }}>
        <p>La revisión que editaste se perderá si descartás los cambios.</p>
      </Modal>
    </Flex>
  );
}
