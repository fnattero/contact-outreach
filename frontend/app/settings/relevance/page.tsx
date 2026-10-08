"use client";

import { Alert, Button, Flex, Form, Input, Modal, Radio } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import { PageHeader } from "@/components/design-system/page-header";
import { AUDIENCE_TABS } from "@/components/design-system/tabs-config";
import { SectionTabs } from "@/components/design-system/section-tabs";
import { LoadingState } from "@/components/design-system/states";
import {
  can,
  getIntegrationStatus,
  getRelevanceFilter,
  problemMessage,
  updateRelevanceFilter,
  type Problem,
  type RelevanceFilter,
  type RelevanceFilterMode,
} from "@/lib/api";
import { MODE_OPTIONS } from "./relevance-helpers";

type Feedback =
  | { state: "idle" | "saving"; message?: string }
  | { state: "saved" | "error"; message: string };
type FormValues = { mode: RelevanceFilterMode; criteria: string };

const EXAMPLE =
  "Nos sirven los talleres que reparan o rebobinan motores eléctricos. No nos sirven las casas que sólo venden motores nuevos sin taller. Si no podés confirmar que tienen taller propio, marcalo como dudoso.";

function CharacterFooter({ count, limit }: { count: number; limit: number }) {
  const nearLimit = count >= limit * 0.9;
  return (
    <div className={`character-footer${nearLimit ? " character-footer--warning" : ""}`} aria-live="polite">
      {count}/{limit}
    </div>
  );
}

export default function RelevanceSettingsPage() {
  const { session } = useAuth();
  const [form] = Form.useForm<FormValues>();
  const [filter, setFilter] = useState<RelevanceFilter | null>(null);
  const [realProvider, setRealProvider] = useState<boolean | null>(null);
  const [dirty, setDirty] = useState(false);
  const [length, setLength] = useState(0);
  const [feedback, setFeedback] = useState<Feedback>({ state: "idle" });
  const [error, setError] = useState<unknown>(null);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [confirmStrict, setConfirmStrict] = useState<FormValues | null>(null);
  const [saving, setSaving] = useState(false);

  const allowed = can(session, "manage_configuration");

  useEffect(() => {
    if (!allowed) return;
    void getRelevanceFilter()
      .then((next) => {
        setFilter(next);
        form.setFieldsValue({ mode: next.mode, criteria: next.criteria });
        setLength(next.criteria.length);
      })
      .catch(setError);
    // Only used to warn that no real provider is connected; the page works without it.
    void getIntegrationStatus()
      .then((status) => setRealProvider(status.llm.provider !== "fake"))
      .catch(() => undefined);
  }, [form, allowed]);

  async function persist(values: FormValues) {
    setSaving(true);
    setFeedback({ state: "saving", message: "Guardando cambios…" });
    setError(null);
    try {
      const saved = await updateRelevanceFilter(values.mode, values.criteria);
      setFilter(saved);
      form.setFieldsValue({ mode: saved.mode, criteria: saved.criteria });
      setDirty(false);
      setFeedback({ state: "saved", message: "Filtro guardado." });
    } catch (problem) {
      setError(problem);
      setFeedback({ state: "error", message: "No se pudo guardar el filtro." });
    } finally {
      setSaving(false);
    }
  }

  function submit(values: FormValues) {
    // Discarding more is the dangerous direction, so only Estricto asks for a typed confirmation.
    if (values.mode === "STRICT" && filter?.mode !== "STRICT") {
      setConfirmStrict(values);
      return;
    }
    void persist(values);
  }

  function applyCriteria(text: string) {
    form.setFieldsValue({ criteria: text });
    setLength(text.length);
    setDirty(true);
    setFeedback({ state: "idle" });
  }

  function requestCancel() {
    if (dirty) setCancelOpen(true);
  }

  function discardChanges() {
    if (filter) {
      form.setFieldsValue({ mode: filter.mode, criteria: filter.criteria });
      setLength(filter.criteria.length);
    }
    setDirty(false);
    setFeedback({ state: "idle" });
    setCancelOpen(false);
  }

  if (!allowed) {
    return <AuthError error={{ detail: "No tenés permisos para editar el filtro de audiencia." }} />;
  }
  if (error && !filter) return <AuthError error={error} />;
  if (!filter) return <LoadingState layout="form" />;

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Audiencia"
        description="Decidí qué negocios encontrados se descartan antes de que entren a una campaña."
        tabs={<SectionTabs label="Audiencia" tabs={AUDIENCE_TABS} dirty={dirty} />}
      />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      {realProvider === false ? (
        <Alert
          type="warning"
          showIcon
          message="Todavía no hay un proveedor de IA conectado"
          description={
            <>
              Mientras tanto el filtro usa respuestas de prueba y no evalúa negocios de verdad.{" "}
              <Link href="/settings/integrations">Conectar un proveedor</Link>
            </>
          }
        />
      ) : null}
      <Form
        form={form}
        layout="vertical"
        validateTrigger="onBlur"
        onValuesChange={(_, values) => {
          setLength(String(values.criteria ?? "").length);
          setDirty(true);
          setFeedback({ state: "idle" });
        }}
        onFinish={submit}
      >
        <div className="form-column">
          <FormSection
            defaultOpen
            title="¿Cuándo descartar un negocio?"
            description="El filtro sólo puede descartar. Nunca agrega un negocio que la búsqueda no encontró, y vos seguís aprobando la campaña antes de que salga cualquier mensaje."
          >
            <Form.Item name="mode" label="Sensibilidad" rules={[{ required: true }]}>
              <Radio.Group className="campaign-choice-group">
                {MODE_OPTIONS.map((option) => (
                  <Radio key={option.value} value={option.value}>
                    <span>
                      <strong>{option.title}</strong>
                      <small>{option.explanation}</small>
                    </span>
                  </Radio>
                ))}
              </Radio.Group>
            </Form.Item>
            <p className="muted">
              Si el proveedor de IA falla, el negocio se conserva. El filtro nunca descarta por un error técnico.
            </p>
          </FormSection>
          <FormSection
            defaultOpen
            title="¿Qué negocios te sirven?"
            description="Acá afinás cómo se juzga cada negocio. Arriba elegís qué pasa con los dudosos."
          >
            <p className="muted">Por ejemplo: “{EXAMPLE}”</p>
            <Form.Item
              name="criteria"
              label="Describí a quién le vendés"
              extra="Escribilo como se lo explicarías a un vendedor nuevo el primer día: qué tipo de negocios te compran, cuáles no, y qué hacer cuando no está claro. Se usa tal cual, sin cambios, para revisar cada negocio que encuentra la búsqueda."
              rules={[{ max: filter.criteria_limit, message: `No puede superar ${filter.criteria_limit} caracteres.` }]}
            >
              <Input.TextArea rows={8} maxLength={filter.criteria_limit} showCount={false} />
            </Form.Item>
            <CharacterFooter count={length} limit={filter.criteria_limit} />
            <Flex gap="small" wrap>
              <Button onClick={() => applyCriteria(filter.default_criteria)}>Volver al texto sugerido</Button>
            </Flex>
          </FormSection>
        </div>
      </Form>
      <StickySaveBar
        dirty={dirty}
        feedback={saving ? { state: "saving", message: "Guardando cambios…" } : feedback}
        onSave={() => void form.submit()}
        onCancel={requestCancel}
      />
      <ConfirmDangerModal
        open={confirmStrict !== null}
        title="Activar el filtro estricto"
        consequences={[
          "Se van a descartar los negocios dudosos, no sólo los que claramente no sirven.",
          "La audiencia de las campañas que empiecen a buscar desde ahora va a ser más chica.",
          "Un negocio descartado se recupera de a uno, y sólo mientras la campaña no empiece a enviar.",
        ]}
        confirmationWord="ESTRICTO"
        dangerLabel="Activar estricto"
        confirming={saving}
        onCancel={() => setConfirmStrict(null)}
        onConfirm={() => {
          const values = confirmStrict;
          setConfirmStrict(null);
          if (values) void persist(values);
        }}
      />
      <Modal
        open={cancelOpen}
        title="Descartar cambios sin guardar"
        onCancel={() => setCancelOpen(false)}
        onOk={discardChanges}
        okText="Descartar cambios"
        cancelText="Seguir editando"
        okButtonProps={{ danger: true }}
      >
        <p>Lo que editaste se perderá si descartás los cambios.</p>
      </Modal>
    </Flex>
  );
}
