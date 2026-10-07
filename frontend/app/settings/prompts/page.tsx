"use client";

import { Alert, Flex, Form, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import { PageHeader } from "@/components/design-system/page-header";
import { LoadingState } from "@/components/design-system/states";
import {
  can,
  getAutomaticReplyPrompt,
  problemMessage,
  updateAutomaticReplyPrompt,
  type Problem,
} from "@/lib/api";

type Feedback =
  | { state: "idle" | "saving"; message?: string }
  | { state: "saved" | "error"; message: string };

const LIMIT = 4000;

function CharacterFooter({ count }: { count: number }) {
  const nearLimit = count >= LIMIT * 0.9;
  return (
    <div className={`character-footer${nearLimit ? " character-footer--warning" : ""}`} aria-live="polite">
      {count}/{LIMIT}
    </div>
  );
}

export default function PromptsSettingsPage() {
  const { session } = useAuth();
  const [form] = Form.useForm<{ prompt: string }>();
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState("");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>({ state: "idle" });
  const [length, setLength] = useState(0);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    void getAutomaticReplyPrompt()
      .then((next) => {
        form.setFieldsValue({ prompt: next.automatic_reply_prompt });
        setSaved(next.automatic_reply_prompt);
        setLength(next.automatic_reply_prompt.length);
        setLoaded(true);
      })
      .catch(setError);
  }, [form]);

  async function save(values: { prompt: string }) {
    setSaving(true);
    setFeedback({ state: "saving", message: "Guardando revisión…" });
    setError(null);
    try {
      const result = await updateAutomaticReplyPrompt(values.prompt);
      setSaved(result.automatic_reply_prompt);
      setDirty(false);
      setFeedback({ state: "saved", message: "Revisión guardada." });
    } catch (problem) {
      setError(problem);
      setFeedback({ state: "error", message: "No se pudo guardar esta revisión." });
    } finally {
      setSaving(false);
    }
  }

  function requestCancel() {
    if (dirty) setCancelOpen(true);
  }

  function discardChanges() {
    form.setFieldsValue({ prompt: saved });
    setLength(saved.length);
    setDirty(false);
    setFeedback({ state: "idle" });
    setCancelOpen(false);
  }

  if (!can(session, "manage_automation")) {
    return <AuthError error={{ detail: "No tenés permisos para editar instrucciones." }} />;
  }
  if (error && !loaded) return <AuthError error={error} />;
  if (!loaded) return <LoadingState layout="form" />;

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Instrucciones de respuesta"
        description="Revisá las reglas que guían la redacción de las respuestas automáticas antes de guardarlas."
      />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <section className="instruction-notice" aria-labelledby="instruction-notice-title">
        <h2 className="type-title" id="instruction-notice-title">Revisiones auditadas y aisladas</h2>
        <p>
          Estas instrucciones quedan registradas como revisiones auditadas. No otorgan al modelo acceso a Gmail,
          red, archivos ni herramientas: sólo orientan el texto que propone.
        </p>
      </section>
      <div className="form-column">
        <FormSection
          title="Respuestas automáticas"
          description="Reglas para redactar respuestas cuando la política permite una propuesta automática."
        >
          <Form
            form={form}
            layout="vertical"
            validateTrigger="onBlur"
            onValuesChange={(_, values) => {
              setLength(String(values.prompt ?? "").length);
              setDirty(true);
              setFeedback({ state: "idle" });
            }}
            onFinish={(values) => void save(values)}
          >
            <Form.Item
              name="prompt"
              label="Instrucciones de respuesta"
              extra="Indicá qué puede contestar y cuándo debe pedir revisión humana."
              rules={[
                { required: true, message: "Escribí las instrucciones." },
                { max: LIMIT, message: `No puede superar ${LIMIT} caracteres.` },
              ]}
            >
              <Input.TextArea rows={11} maxLength={LIMIT} showCount={false} />
            </Form.Item>
            <CharacterFooter count={length} />
          </Form>
        </FormSection>
        <StickySaveBar
          dirty={dirty}
          feedback={saving ? { state: "saving", message: "Guardando revisión…" } : feedback}
          onSave={() => void form.submit()}
          onCancel={requestCancel}
        />
      </div>
      <Modal
        open={cancelOpen}
        title="Descartar cambios sin guardar"
        onCancel={() => setCancelOpen(false)}
        onOk={discardChanges}
        okText="Descartar cambios"
        cancelText="Seguir editando"
        okButtonProps={{ danger: true }}
      >
        <p>La revisión que editaste se perderá si descartás los cambios.</p>
      </Modal>
    </Flex>
  );
}
