"use client";

import { Alert, Flex, Form, Input, Modal } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import { LoadingState } from "@/components/design-system/states";
import { PageHeader } from "@/components/design-system/page-header";
import { emailEnding } from "./profile-helpers";
import { can,
  getBusinessProfileVersioned,
  getMessageTemplates,
  problemMessage,
  updateBusinessProfile,
  type BusinessProfile,
  type Problem,
} from "@/lib/api";

type Feedback =
  | { state: "idle" | "saving"; message?: string }
  | { state: "saved" | "error"; message: string };

export default function ProfileSettingsPage() {
  const { session } = useAuth();
  const [form] = Form.useForm<Partial<BusinessProfile>>();
  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>({ state: "idle" });
  const [error, setError] = useState<unknown>(null);
  const [etag, setEtag] = useState<string | null>(null);
  const [proposalBody, setProposalBody] = useState<string | null>(null);
  const signature = Form.useWatch("signature", form) ?? "";

  useEffect(() => {
    void getBusinessProfileVersioned()
      .then(({ data, etag: nextEtag }) => {
        setProfile(data);
        setEtag(nextEtag);
        if (data) form.setFieldsValue(data);
      })
      .catch(setError)
      .finally(() => setLoading(false));
    // Only to show how a real proposal ends; the page works without it.
    void getMessageTemplates()
      .then((templates) => setProposalBody(templates.find((item) => item.kind === "INITIAL" && item.active)?.body ?? null))
      .catch(() => undefined);
  }, [form]);

  async function submit(values: Partial<BusinessProfile>) {
    setFeedback({ state: "saving", message: "Guardando cambios…" });
    setError(null);
    try {
      const saved = await updateBusinessProfile(values, etag ?? undefined);
      setProfile(saved);
      const refreshed = await getBusinessProfileVersioned();
      setEtag(refreshed.etag);
      setDirty(false);
      setFeedback({ state: "saved", message: "Perfil guardado." });
    } catch (problem) {
      setError(problem);
      setFeedback({ state: "error", message: "No se pudo guardar el perfil." });
    }
  }

  function requestCancel() {
    if (dirty) setCancelOpen(true);
    else form.resetFields();
  }

  function discardChanges() {
    form.resetFields();
    setDirty(false);
    setFeedback({ state: "idle" });
    setCancelOpen(false);
  }

  if (!can(session, "manage_configuration")) return <AuthError error={{ detail: "No tenés permisos para editar el perfil comercial." }} />;
  if (error && !profile && !loading) return <AuthError error={error} />;
  if (loading) return <LoadingState layout="form" />;

  const ending = emailEnding(proposalBody ?? "", String(signature));

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Perfil comercial"
        description="Datos que identifican a tu empresa y la firma de tus correos."
      />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      {feedback.state === "saved" ? <p className="form-save-feedback form-save-feedback--saved" role="status">{feedback.message}</p> : null}
      <Alert
        type="info"
        showIcon
        message="Esta página no es lo que lee la inteligencia artificial"
        description={
          <>
            La IA contesta con la información que aprobaste en <Link href="/automation/knowledge">Respuestas automáticas</Link>, y
            decide a quién le escribís según lo que describís en <Link href="/prospects">Audiencia</Link>.
          </>
        }
      />
      <Form
        form={form}
        layout="vertical"
        validateTrigger="onBlur"
        initialValues={profile ?? undefined}
        onValuesChange={() => { setDirty(true); setFeedback({ state: "idle" }); }}
        onFinish={(values) => void submit(values as Partial<BusinessProfile>)}
      >
        <div className="form-column">
          <FormSection
            defaultOpen
            title="Tu empresa"
            description="Los tres primeros datos identifican quién envía y son obligatorios para lanzar campañas. No se agregan solos al correo: si querés que aparezcan, escribilos en la firma."
          >
            <div className="form-grid">
              <Form.Item label="Empresa" name="company_name" extra="El nombre de tu empresa." rules={[{ required: true, whitespace: true, message: "Indicá el nombre de la empresa." }]}>
                <Input />
              </Form.Item>
              <Form.Item label="Vendedor/a" name="salesperson_name" extra="La persona de contacto." rules={[{ required: true, whitespace: true, message: "Indicá quién firma como persona de contacto." }]}>
                <Input />
              </Form.Item>
              <Form.Item className="form-grid__full" label="Dirección" name="address" extra="El domicilio de tu empresa." rules={[{ required: true, whitespace: true, message: "Indicá la dirección de la empresa." }]}>
                <Input />
              </Form.Item>
              <Form.Item
                className="form-grid__full"
                label="Firma"
                name="signature"
                extra="Fija: va al final de las propuestas, los recordatorios y los mensajes programados, tal cual la escribís. Las respuestas automáticas no la llevan."
                rules={[{ required: true, whitespace: true, message: "Escribí la firma que cierra tus correos." }]}
              >
                <Input.TextArea rows={4} />
              </Form.Item>
            </div>
            <section className="signature-preview" aria-label="Vista previa del final del correo">
              <h3 className="type-micro">Así termina cada correo</h3>
              {ending.tail ? <p className="signature-preview__tail">…{"\n"}{ending.tail}</p> : null}
              <p className="signature-preview__signature">{ending.signature || "Tu firma va a aparecer acá."}</p>
            </section>
          </FormSection>
        </div>
      </Form>
      <StickySaveBar dirty={dirty} feedback={feedback} onSave={() => void form.submit()} onCancel={requestCancel} />
      <Modal
        open={cancelOpen}
        title="Descartar cambios sin guardar"
        onCancel={() => setCancelOpen(false)}
        onOk={discardChanges}
        okText="Descartar cambios"
        cancelText="Seguir editando"
        okButtonProps={{ danger: true }}
      >
        <p>Los cambios que hiciste en el perfil se perderán si salís ahora.</p>
      </Modal>
    </Flex>
  );
}
