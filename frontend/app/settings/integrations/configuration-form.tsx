"use client";

import { Alert, Button, Checkbox, Flex, Form, Input, InputNumber, Modal, Select } from "antd";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { FormSection } from "@/components/design-system/forms";
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
  "ollama_base_url", "openai_compatible_base_url", "llm_api_key", "embedding_provider", "embedding_model",
  "embedding_dimensions", "gmail_provider", "gmail_oauth_client_id", "gmail_oauth_client_secret",
] as const;
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
  const provider = Form.useWatch("llm_provider", form);

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
      form.setFieldsValue({ ...next, llm_api_key: "", gmail_oauth_client_secret: "", remove_llm_api_key: false, remove_gmail_oauth_client_secret: false });
      setSaved(true);
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
      <Form form={form} layout="vertical" onFinish={submit}>
        <FormSection title="Inteligencia artificial" description="Para que la app revise tu audiencia y analice las respuestas. Nunca redacta el primer contacto.">
          <div className="form-grid">
            <Form.Item name="llm_provider" label="Servicio de IA" extra="Con “Sin conectar” la app usa respuestas de prueba y no evalúa nada de verdad.">
              <Select options={[{ value: "fake", label: "Sin conectar (modo de prueba)" }, { value: "openai-compatible", label: "Compatible con OpenAI" }, { value: "ollama", label: "Ollama (en tu equipo)" }]} />
            </Form.Item>
            {provider === "openai-compatible" ? <Form.Item name="openai_compatible_base_url" label="Dirección del servicio" extra="Debe empezar con https://"><Input /></Form.Item> : null}
            {provider === "ollama" ? <Form.Item name="ollama_base_url" label="Dirección de Ollama"><Input /></Form.Item> : null}
            <Form.Item name="llm_model" label="Modelo" extra="El que usa la app para analizar las respuestas."><Input maxLength={120} /></Form.Item>
            <Form.Item name="relevance_llm_model" label="Modelo para el filtro de audiencia" extra="Opcional. Revisar cada negocio es una tarea simple: un modelo más económico alcanza. Vacío usa el de al lado."><Input maxLength={120} /></Form.Item>
            <Form.Item className="form-grid__full" name="llm_api_key" label="Nueva clave de API" extra={`Estado actual: ${credentialStatus(current.llm_credential)}. Dejala vacía para conservarla; nunca vuelve a mostrarse.`}><Input.Password autoComplete="new-password" spellCheck={false} /></Form.Item>
            <Form.Item className="form-grid__full" name="remove_llm_api_key" valuePropName="checked"><Checkbox>Eliminar la clave guardada</Checkbox></Form.Item>
          </div>
        </FormSection>
        <h2 className="type-title advanced-heading">Opciones avanzadas</h2>
        <p className="muted advanced-intro">Solo hace falta tocarlas para cambiar de proveedor o si algo dejó de funcionar.</p>
        <FormSection defaultOpen={false} title="Búsqueda y lectura de sitios" description="Cómo se encuentran negocios y se leen sus sitios web.">
          <div className="form-grid">
            <Form.Item name="extractor_provider" label="Búsqueda de negocios"><Select options={[fake, { value: "overture", label: "Overture Maps Places" }]} /></Form.Item>
            <Form.Item name="website_fetcher" label="Lectura de sitios web" extra="La lectura real sólo visita sitios públicos y bloquea redes internas."><Select options={[fake, { value: "http", label: "HTTP seguro" }]} /></Form.Item>
            <Form.Item name="overture_min_confidence" label="Confianza mínima de la búsqueda" extra="Entre 0 y 1. Mide si el negocio existe, no si te sirve."><Input inputMode="decimal" /></Form.Item>
          </div>
        </FormSection>
        <FormSection defaultOpen={false} title="Búsqueda en tu información" description="Cómo la app encuentra los datos aprobados que sirven para contestar.">
          <div className="form-grid">
            <Form.Item name="embedding_provider" label="Servicio"><Select options={[fake, { value: "openai-compatible", label: "Compatible con OpenAI" }]} /></Form.Item>
            <Form.Item name="embedding_model" label="Modelo"><Input maxLength={120} /></Form.Item>
            <Form.Item name="embedding_dimensions" label="Tamaño del vector"><InputNumber min={64} max={3072} /></Form.Item>
          </div>
        </FormSection>
        <FormSection defaultOpen={false} title="Credenciales de Gmail" description="Para cambiar las credenciales hay que desconectar Gmail primero.">
          <div className="form-grid">
            <Form.Item name="gmail_provider" label="Servicio"><Select options={[fake, { value: "api", label: "API de Google Gmail" }]} /></Form.Item>
            <Form.Item name="gmail_oauth_client_id" label="Identificador de cliente OAuth"><Input maxLength={500} /></Form.Item>
            <Form.Item className="form-grid__full" name="gmail_oauth_client_secret" label="Nuevo secreto de cliente" extra={`Estado actual: ${credentialStatus(current.gmail_credential)}. Dejalo vacío para conservarlo; nunca vuelve a mostrarse.`}><Input.Password autoComplete="new-password" spellCheck={false} /></Form.Item>
            <Form.Item className="form-grid__full" name="remove_gmail_oauth_client_secret" valuePropName="checked"><Checkbox>Eliminar el secreto guardado</Checkbox></Form.Item>
          </div>
        </FormSection>
        <Flex><Button type="primary" htmlType="submit">Guardar cambios</Button></Flex>
      </Form>
      <Modal open={pending !== null} title="Confirmá tu contraseña" okText="Confirmar y guardar" cancelText="Cancelar" confirmLoading={saving} okButtonProps={{ disabled: !password }} onOk={() => void confirmAndSave()} onCancel={() => { setPending(null); setPassword(""); }}>
        <p>Cambiar proveedores o credenciales redirige envíos y secretos. Volvé a ingresar tu contraseña para continuar.</p>
        <Input.Password aria-label="Contraseña actual" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
      </Modal>
    </div>
  );
}
