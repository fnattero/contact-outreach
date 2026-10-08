"use client";

import { DatabaseOutlined, EnvironmentOutlined, FilterOutlined, GlobalOutlined, MailOutlined, MessageOutlined } from "@ant-design/icons";
import { Alert, Checkbox, Form, Input, InputNumber, Modal, Select } from "antd";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import {
  getIntegrationConfiguration,
  problemMessage,
  reauthenticate,
  saveIntegrationConfiguration,
  type IntegrationConfiguration,
  type Problem,
} from "@/lib/api";
import { applyFieldErrors } from "@/lib/form-errors";
import { buildConfigurationPatch, credentialStatus, hasChanges, type ConfigurationFormValues } from "./configuration-helpers";

const FIELDS = [
  "extractor_provider", "overture_min_confidence", "website_fetcher", "llm_provider", "llm_model", "relevance_llm_model",
  "relevance_llm_provider", "relevance_llm_base_url", "relevance_llm_api_key",
  "ollama_base_url", "openai_compatible_base_url", "llm_api_key", "embedding_provider", "embedding_model",
  "embedding_dimensions", "gmail_provider", "gmail_oauth_client_id", "gmail_oauth_client_secret",
] as const;
const BLANK_SECRETS = {
  llm_api_key: "", gmail_oauth_client_secret: "", relevance_llm_api_key: "",
  remove_llm_api_key: false, remove_gmail_oauth_client_secret: false, remove_relevance_llm_api_key: false,
};
const fake = { value: "fake", label: "Simulado (sin red)" };

export function ConfigurationForm() {
  const { refresh } = useAuth();
  const [form] = Form.useForm<ConfigurationFormValues>();
  const [current, setCurrent] = useState<IntegrationConfiguration | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [pending, setPending] = useState<ConfigurationFormValues | null>(null);
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const provider = Form.useWatch("llm_provider", form);
  const relevanceProvider = Form.useWatch("relevance_llm_provider", form) ?? "";

  useEffect(() => {
    let cancelled = false;
    void getIntegrationConfiguration()
      .then((config) => { if (!cancelled) { setCurrent(config); form.setFieldsValue(config); } })
      .catch((problem) => { if (!cancelled) setError(problem); });
    return () => { cancelled = true; };
  }, [form]);

  if (!current) return error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null;

  async function confirmAndSave() {
    if (!pending || !current) return;
    setSaving(true);
    setError(null);
    try {
      // Credentials and providers redirect traffic, so a stolen session is not enough.
      await reauthenticate(password);
      const next = await saveIntegrationConfiguration(buildConfigurationPatch(pending, current));
      setCurrent(next);
      form.setFieldsValue({ ...next, ...BLANK_SECRETS });
      setSaved(true);
      setDirty(false);
      setPending(null);
      void refresh();
    } catch (problem) {
      if (!applyFieldErrors(form, problem, FIELDS)) setError(problem);
      setPending(null);
    } finally {
      setPassword("");
      setSaving(false);
    }
  }

  function submit(values: ConfigurationFormValues) {
    setSaved(false);
    if (!hasChanges(buildConfigurationPatch(values, current as IntegrationConfiguration))) {
      setError({ detail: "No hay cambios para guardar." });
      return;
    }
    setError(null);
    setPending(values);
  }

  return (
    <div className="integration-config">
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} style={{ marginBottom: 16 }} /> : null}
      {saved ? <Alert type="success" showIcon message="Integraciones guardadas. Las credenciales anteriores no se pueden consultar." style={{ marginBottom: 16 }} /> : null}
      <Form form={form} layout="vertical" onFinish={submit} onValuesChange={() => { setDirty(true); setSaved(false); }}>
        <FormSection icon={<MailOutlined />} tone="accent" title="Gmail: enviar y recibir correos" description="La cuenta desde la que salen las propuestas y a la que llegan las respuestas. La conexión se hace arriba; acá van las credenciales de tu aplicación de Google.">
          <div className="form-grid">
            <Form.Item name="gmail_provider" label="Servicio"><Select options={[fake, { value: "api", label: "API de Google Gmail" }]} /></Form.Item>
            <Form.Item name="gmail_oauth_client_id" label="Identificador de cliente OAuth"><Input maxLength={500} /></Form.Item>
            <Form.Item className="form-grid__full" name="gmail_oauth_client_secret" label="Nuevo secreto de cliente" extra={`Estado actual: ${credentialStatus(current.gmail_credential)}. Dejalo vacío para conservarlo; nunca vuelve a mostrarse. Para cambiar las credenciales hay que desconectar Gmail primero.`}><Input.Password autoComplete="new-password" spellCheck={false} /></Form.Item>
            <Form.Item className="form-grid__full" name="remove_gmail_oauth_client_secret" valuePropName="checked"><Checkbox>Eliminar el secreto guardado</Checkbox></Form.Item>
          </div>
        </FormSection>
        <FormSection icon={<MessageOutlined />} tone="warning" title="IA: contestar correos" description="Analiza los correos que llegan y prepara la respuesta. Nunca redacta el primer contacto.">
          <div className="form-grid">
            <Form.Item name="llm_provider" label="Servicio de IA" extra="Con “Sin conectar” la app usa respuestas de prueba y no evalúa nada de verdad.">
              <Select options={[{ value: "fake", label: "Sin conectar (modo de prueba)" }, { value: "openai-compatible", label: "Compatible con OpenAI" }, { value: "ollama", label: "Ollama (en tu equipo)" }]} />
            </Form.Item>
            {provider === "openai-compatible" ? <Form.Item name="openai_compatible_base_url" label="Dirección del servicio" extra="Debe empezar con https://"><Input /></Form.Item> : null}
            {provider === "ollama" ? <Form.Item name="ollama_base_url" label="Dirección de Ollama"><Input /></Form.Item> : null}
            <Form.Item name="llm_model" label="Modelo para las respuestas" extra="Conviene uno capaz: lee la conversación y la información que aprobaste."><Input maxLength={120} /></Form.Item>
            <Form.Item className="form-grid__full" name="llm_api_key" label="Nueva clave de API" extra={`Estado actual: ${credentialStatus(current.llm_credential)}. Dejala vacía para conservarla; nunca vuelve a mostrarse.`}><Input.Password autoComplete="new-password" spellCheck={false} /></Form.Item>
            <Form.Item className="form-grid__full" name="remove_llm_api_key" valuePropName="checked"><Checkbox>Eliminar la clave guardada</Checkbox></Form.Item>
          </div>
        </FormSection>
        <FormSection icon={<FilterOutlined />} tone="warning" title="IA: revisar la audiencia" description="Decide qué negocios descartar según el criterio que escribiste en Audiencia → Filtro. Es una tarea simple que se repite por cada negocio: puede usar otro servicio, más económico.">
          <div className="form-grid">
            <Form.Item className="form-grid__full" name="relevance_llm_provider" label="Servicio de IA" extra="Por defecto usa el mismo que para contestar correos, con su dirección y su clave.">
              <Select options={[
                { value: "", label: "El mismo que para contestar correos" },
                { value: "openai-compatible", label: "Otro servicio compatible con OpenAI" },
                { value: "ollama", label: "Ollama (en tu equipo)" },
              ]} />
            </Form.Item>
            {relevanceProvider ? (
              <Form.Item name="relevance_llm_base_url" label="Dirección del servicio" extra={relevanceProvider === "ollama" ? "Vacío usa la dirección de Ollama de arriba." : "Vacío usa la dirección del servicio de arriba. Debe empezar con https://"}><Input /></Form.Item>
            ) : null}
            <Form.Item name="relevance_llm_model" label="Modelo para revisar la audiencia" extra={relevanceProvider ? "Obligatorio: este servicio no puede heredar el modelo de las respuestas." : "Uno más económico alcanza. Si lo dejás vacío, usa el modelo de las respuestas."}><Input maxLength={120} placeholder={relevanceProvider ? "" : "Mismo que el de las respuestas"} /></Form.Item>
            {relevanceProvider === "openai-compatible" ? (
              <>
                <Form.Item className="form-grid__full" name="relevance_llm_api_key" label="Nueva clave de API para revisar la audiencia" extra={`Estado actual: ${credentialStatus(current.relevance_llm_credential)}. Una clave de las respuestas solo se reutiliza si es el mismo servicio. Dejala vacía para conservar la actual; nunca vuelve a mostrarse.`}><Input.Password autoComplete="new-password" spellCheck={false} /></Form.Item>
                <Form.Item className="form-grid__full" name="remove_relevance_llm_api_key" valuePropName="checked"><Checkbox>Eliminar la clave guardada de este servicio</Checkbox></Form.Item>
              </>
            ) : null}
          </div>
        </FormSection>
        <FormSection icon={<EnvironmentOutlined />} title="Búsqueda de negocios" description="De dónde salen los negocios de cada campaña.">
          <div className="form-grid">
            <Form.Item name="extractor_provider" label="Fuente de datos"><Select options={[fake, { value: "overture", label: "Overture Maps Places" }]} /></Form.Item>
            <Form.Item name="overture_min_confidence" label="Confianza mínima" extra="Entre 0 y 1. Mide si el negocio existe, no si te sirve."><Input inputMode="decimal" /></Form.Item>
          </div>
        </FormSection>
        <FormSection icon={<GlobalOutlined />} title="Lectura de sitios web" description="Cómo la app lee el sitio de cada negocio para encontrar su correo.">
          <div className="form-grid">
            <Form.Item name="website_fetcher" label="Lectura de sitios" extra="La lectura real solo visita sitios públicos y bloquea redes internas."><Select options={[fake, { value: "http", label: "HTTP seguro" }]} /></Form.Item>
          </div>
        </FormSection>
        <FormSection icon={<DatabaseOutlined />} title="Búsqueda en tu información" description="Cómo la app encuentra, entre tus datos aprobados, los que sirven para contestar un correo.">
          <div className="form-grid">
            <Form.Item name="embedding_provider" label="Servicio"><Select options={[fake, { value: "openai-compatible", label: "Compatible con OpenAI" }]} /></Form.Item>
            <Form.Item name="embedding_model" label="Modelo"><Input maxLength={120} /></Form.Item>
            <Form.Item name="embedding_dimensions" label="Tamaño del vector"><InputNumber min={64} max={3072} /></Form.Item>
          </div>
        </FormSection>
        <StickySaveBar
          dirty={dirty}
          feedback={saving ? { state: "saving", message: "Guardando…" } : { state: "idle", message: "Hay cambios sin guardar." }}
          onSave={() => void form.submit()}
          onCancel={() => {
            form.setFieldsValue({ ...current, ...BLANK_SECRETS });
            setDirty(false);
          }}
        />
      </Form>
      <Modal open={pending !== null} title="Confirmá tu contraseña" okText="Confirmar y guardar" cancelText="Cancelar" confirmLoading={saving} okButtonProps={{ disabled: !password }} onOk={() => void confirmAndSave()} onCancel={() => { setPending(null); setPassword(""); }}>
        <p>Cambiar proveedores o credenciales redirige envíos y secretos. Volvé a ingresar tu contraseña para continuar.</p>
        <Input.Password aria-label="Contraseña actual" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
      </Modal>
    </div>
  );
}
