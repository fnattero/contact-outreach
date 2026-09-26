"use client";

import { Alert, Flex, Form, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import { LoadingState } from "@/components/design-system/states";
import { PageHeader } from "@/components/design-system/page-header";
import {
  getBusinessProfileVersioned,
  problemMessage,
  updateBusinessProfile,
  type BusinessProfile,
  type Problem,
} from "@/lib/api";

type Feedback =
  | { state: "idle" | "saving"; message?: string }
  | { state: "saved" | "error"; message: string };

export default function ProfileSettingsPage() {
  const [form] = Form.useForm<Partial<BusinessProfile>>();
  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>({ state: "idle" });
  const [error, setError] = useState<unknown>(null);
  const [etag, setEtag] = useState<string | null>(null);

  useEffect(() => {
    void getBusinessProfileVersioned()
      .then(({ data, etag: nextEtag }) => {
        setProfile(data);
        setEtag(nextEtag);
        if (data) form.setFieldsValue(data);
      })
      .catch(setError)
      .finally(() => setLoading(false));
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

  if (error && !profile && !loading) return <AuthError error={error} />;
  if (loading) return <LoadingState layout="form" />;

  return (
    <Flex vertical gap="large">
      <PageHeader title="Perfil comercial" description="Información aprobada que puede usar el motor de contenido y las campañas." />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      {feedback.state === "saved" ? <p className="form-save-feedback form-save-feedback--saved" role="status">{feedback.message}</p> : null}
      <Form
        form={form}
        layout="vertical"
        validateTrigger="onBlur"
        initialValues={profile ?? undefined}
        onValuesChange={() => { setDirty(true); setFeedback({ state: "idle" }); }}
        onFinish={(values) => void submit(values as Partial<BusinessProfile>)}
      >
        <div className="form-column">
          <FormSection title="Identidad" description="Los datos que identifican a tu empresa en cada propuesta.">
            <Form.Item label="Empresa" name="company_name" rules={[{ required: true, message: "Indicá el nombre de la empresa." }]}>
              <Input />
            </Form.Item>
            <Form.Item label="Vendedor/a" name="salesperson_name" extra="Nombre que aparecerá como persona de contacto.">
              <Input />
            </Form.Item>
          </FormSection>
          <FormSection title="Contacto" description="Canales y ubicación para que los contactos puedan responderte.">
            <Form.Item label="Teléfono" name="phone" extra="Usá el formato con código de país si corresponde."><Input /></Form.Item>
            <Form.Item label="WhatsApp" name="whatsapp" extra="Número que se incluirá cuando una propuesta lo necesite."><Input /></Form.Item>
            <Form.Item label="Sitio web" name="website" extra="Incluí la dirección completa, por ejemplo https://tuempresa.com." rules={[{ type: "url", message: "Indicá una URL válida." }]}><Input /></Form.Item>
            <Form.Item label="Dirección" name="address"><Input /></Form.Item>
          </FormSection>
          <FormSection title="Oferta" description="Información verificable que puede usar el contenido de las campañas.">
            <Form.Item label="Descripción" name="description" extra="Explicá brevemente qué hace la empresa."><Input.TextArea rows={4} /></Form.Item>
            <Form.Item label="Productos" name="products" extra="Enumerá productos o servicios que ofrecés."><Input.TextArea rows={4} /></Form.Item>
            <Form.Item label="Diferenciadores" name="differentiators" extra="Contá qué te distingue frente a otras opciones."><Input.TextArea rows={4} /></Form.Item>
            <Form.Item label="Firma" name="signature" extra="Texto que cierra los mensajes enviados."><Input.TextArea rows={3} /></Form.Item>
            <Form.Item label="Instrucciones adicionales" name="additional_instructions" extra="Reglas específicas para redactar contenido."><Input.TextArea rows={4} /></Form.Item>
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
