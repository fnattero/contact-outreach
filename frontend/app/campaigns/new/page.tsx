"use client";

import { Alert, Button, Checkbox, Form, Input, InputNumber, Radio, Select, Switch } from "antd";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { WEEK_OPTIONS, pruneZoneSelection } from "@/app/campaigns/new/campaign-form-helpers";
import { ZoneSelector } from "@/app/campaigns/new/zone-selector";
import { useAuth } from "@/components/auth-provider";
import {
  DisabledReason,
  EmptyState,
  ErrorState,
  ForbiddenState,
  LoadingState,
  PageHeader,
} from "@/components/design-system";
import {
  createCampaign,
  getCatalogs,
  getMessageTemplates,
  getSearchCategories,
  getSearchZones,
  problemMessage,
  type Catalog,
  type MessageTemplate,
  type Problem,
  type SearchCategory,
  type SearchZone,
} from "@/lib/api";

type CampaignFormValues = {
  name: string;
  delivery_mode: "DRY_RUN" | "REVIEW_ONLY";
  approval_mode: "CAMPAIGN" | "PER_MESSAGE";
  categories: string[];
  provinces: string[];
  zones: string[];
  catalogs: string[];
  location_text: string;
  objective: number;
  max_raw_records: number;
  overture_min_confidence: string;
  daily_limit: number;
  message_interval_minutes: number;
  weekdays: number[];
  window_start: string;
  window_end: string;
  timezone_name: string;
  relevance_threshold: number;
  reminder_enabled: boolean;
  reminder_delay_days: number;
};

type SetupBlocker = {
  headline: string;
  explanation: string;
  actionLabel: string;
  actionHref: string;
};

const initialValues: CampaignFormValues = {
  name: "",
  delivery_mode: "DRY_RUN",
  approval_mode: "CAMPAIGN",
  categories: [],
  provinces: [],
  zones: [],
  catalogs: [],
  location_text: "Argentina",
  objective: 300,
  max_raw_records: 3000,
  overture_min_confidence: "0.750",
  daily_limit: 30,
  message_interval_minutes: 5,
  weekdays: [0, 1, 2, 3, 4],
  window_start: "09:00",
  window_end: "17:00",
  timezone_name: "America/Argentina/Buenos_Aires",
  relevance_threshold: 70,
  reminder_enabled: false,
  reminder_delay_days: 3,
};

function activeInitialTemplate(templates: MessageTemplate[]): MessageTemplate | undefined {
  return templates.find((template) => template.kind === "INITIAL" && template.active);
}

export default function NewCampaignPage() {
  const { session } = useAuth();
  const router = useRouter();
  const [form] = Form.useForm<CampaignFormValues>();
  const [categories, setCategories] = useState<SearchCategory[]>([]);
  const [provinces, setProvinces] = useState<SearchZone[]>([]);
  const [zones, setZones] = useState<SearchZone[]>([]);
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const selectedProvinceIds = Form.useWatch("provinces", form) ?? [];
  const reminderEnabled = Form.useWatch("reminder_enabled", form) ?? false;
  const deliveryMode = Form.useWatch("delivery_mode", form) ?? initialValues.delivery_mode;

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      getSearchCategories(),
      getSearchZones("PROVINCE"),
      getSearchZones(),
      getCatalogs(),
      getMessageTemplates(),
    ])
      .then(([nextCategories, nextProvinces, nextZones, nextCatalogs, nextTemplates]) => {
        if (cancelled) return;
        setCategories(nextCategories);
        setProvinces(nextProvinces);
        setZones(nextZones.filter((zone) => zone.selectable));
        setCatalogs(nextCatalogs.filter((catalog) => !catalog.missing && catalog.active));
        setTemplates(nextTemplates);
      })
      .catch((problem) => { if (!cancelled) setError(problem); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const template = useMemo(() => activeInitialTemplate(templates), [templates]);
  const catalogMissing = catalogs.length === 0;
  const setupBlocker = useMemo<SetupBlocker | null>(() => {
    if (!categories.length) return {
      headline: "Faltan rubros para buscar",
      explanation: "Configurá al menos un rubro activo antes de crear la audiencia de una campaña.",
      actionLabel: "Configurar rubros",
      actionHref: "/settings/categories",
    };
    if (!provinces.length || !zones.length) return {
      headline: "Faltan zonas oficiales",
      explanation: "La campaña necesita provincias y zonas oficiales disponibles para delimitar la búsqueda.",
      actionLabel: "Revisar datos de búsqueda",
      actionHref: "/settings/overture",
    };
    return null;
  }, [categories.length, provinces.length, zones.length]);

  if (session?.role !== "ADMIN") return <ForbiddenState resource="la creación de campañas" />;
  if (loading) return <LoadingState layout="form" label="Cargando opciones de la campaña" />;
  if (error && !categories.length) return (
    <ErrorState
      failed="No se pudieron cargar las opciones de la campaña"
      instruction={problemMessage(error as Problem)}
      onRetry={() => window.location.reload()}
    />
  );
  if (setupBlocker) return (
    <>
      <PageHeader
        title="Nueva campaña"
        description="Definí la audiencia, el contenido y el calendario antes de iniciar la búsqueda."
        breadcrumbs={<Link href="/campaigns">Campañas</Link>}
      />
      <EmptyState {...setupBlocker} />
    </>
  );

  async function submit(values: CampaignFormValues) {
    setSaving(true);
    setError(null);
    try {
      const campaign = await createCampaign({
        ...values,
        catalog: values.catalogs[0],
        timezone_name: "America/Argentina/Buenos_Aires",
        confirm_live: false,
      });
      router.push(`/campaigns/${campaign.id}`);
    } catch (problem) {
      setError(problem);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } finally {
      setSaving(false);
    }
  }

  function changeProvinces(nextProvinceIds: string[]) {
    const selectedZones = form.getFieldValue("zones") ?? [];
    form.setFieldValue("zones", pruneZoneSelection(selectedZones, zones, nextProvinceIds));
  }

  return (
    <>
      <PageHeader
        title="Nueva campaña"
        description="Definí la audiencia, revisá el contenido y elegí cuándo se prepararán los mensajes."
        breadcrumbs={<Link href="/campaigns">Campañas</Link>}
      />
      {error ? <Alert className="campaign-create__error" type="error" showIcon message="No se pudo crear el borrador" description={problemMessage(error as Problem)} /> : null}
      <div className="campaign-create__notice" role="note">
        <strong>{deliveryMode === "DRY_RUN" ? "Se crea como borrador en modo simulación." : "Se crea como borrador en modo sólo revisión."}</strong>
        <span>No se envía ningún email al guardar. La búsqueda y la aprobación ocurren después, desde el detalle de la campaña.</span>
      </div>

      <Form
        className="campaign-create"
        form={form}
        layout="vertical"
        initialValues={initialValues}
        validateTrigger="onBlur"
        onFinish={(values) => void submit(values)}
      >
        <section className="campaign-form-section" aria-labelledby="campaign-purpose-heading">
          <h2 className="type-title" id="campaign-purpose-heading">Propósito de la campaña</h2>
          <p className="campaign-form-section__description">Dale un nombre reconocible y definí el resultado que querés alcanzar.</p>
          <div className="campaign-form-grid campaign-form-grid--purpose">
            <Form.Item label="Nombre de la campaña" name="name" rules={[{ required: true, message: "Indicá un nombre para reconocer la campaña." }]}>
              <Input placeholder="Ej.: Talleres electromecánicos · Buenos Aires" maxLength={180} autoFocus />
            </Form.Item>
            <Form.Item
              label="Destinatarios válidos que querés alcanzar"
              name="objective"
              extra="Es la cantidad de empresas únicas, sin contacto previo y con un email válido, que la búsqueda intentará preparar. Puede terminar con menos si no encuentra suficientes."
              rules={[{ required: true, message: "Indicá cuántos destinatarios querés alcanzar." }]}
            >
              <InputNumber min={1} precision={0} style={{ width: "100%" }} />
            </Form.Item>
          </div>
        </section>

        <section className="campaign-form-section campaign-form-section--wide" aria-labelledby="campaign-audience-heading">
          <h2 className="type-title" id="campaign-audience-heading">Audiencia y zona</h2>
          <p className="campaign-form-section__description">Cada rubro se combina con cada zona seleccionada para buscar empresas. El mapa usa límites oficiales.</p>
          <Form.Item
            label="Rubros"
            name="categories"
            extra="Elegí las actividades que describen a las empresas que querés encontrar."
            rules={[{ required: true, message: "Elegí al menos un rubro." }]}
          >
            <Select mode="multiple" optionFilterProp="label" placeholder="Seleccioná uno o más rubros" options={categories.map((category) => ({ value: category.id, label: category.name }))} />
          </Form.Item>
          <Form.Item
            label="Provincias"
            name="provinces"
            extra="Podés trabajar con varias provincias en una misma campaña."
            rules={[{ required: true, message: "Elegí al menos una provincia." }]}
          >
            <Select mode="multiple" optionFilterProp="label" placeholder="Seleccioná una o más provincias" options={provinces.map((province) => ({ value: province.id, label: province.name }))} onChange={changeProvinces} />
          </Form.Item>
          <Form.Item
            className="campaign-zone-field"
            label="Zonas específicas"
            name="zones"
            dependencies={["provinces"]}
            validateTrigger="onChange"
            rules={[
              ({ getFieldValue }) => ({
                validator: async (_rule, selectedZoneIds: string[] | undefined) => {
                  if (!selectedZoneIds?.length) throw new Error("Elegí al menos una zona en cada provincia seleccionada.");
                  const selectedZoneIdSet = new Set(selectedZoneIds);
                  const coveredProvinceIds = new Set(
                    zones.filter((zone) => selectedZoneIdSet.has(zone.id)).map((zone) => zone.parent_id),
                  );
                  const missingProvinceIds = ((getFieldValue("provinces") as string[] | undefined) ?? [])
                    .filter((provinceId) => !coveredProvinceIds.has(provinceId));
                  if (missingProvinceIds.length) {
                    const missingNames = provinces
                      .filter((province) => missingProvinceIds.includes(province.id))
                      .map((province) => province.name)
                      .join(", ");
                    throw new Error(`Elegí al menos una zona de ${missingNames}.`);
                  }
                },
              }),
            ]}
          >
            <ZoneSelector provinces={provinces} zones={zones} selectedProvinceIds={selectedProvinceIds} />
          </Form.Item>
        </section>

        <section className="campaign-form-section campaign-form-section--wide" aria-labelledby="campaign-content-heading">
          <h2 className="type-title" id="campaign-content-heading">Contenido de la propuesta</h2>
          <p className="campaign-form-section__description">Confirmá qué texto y qué archivos se copiarán al borrador. La campaña conserva esta revisión aunque el mensaje predeterminado cambie después.</p>
          <div className="campaign-content-grid">
            <article className="campaign-template-preview" aria-labelledby="campaign-template-heading">
              <div className="campaign-template-preview__heading">
                <div>
                  <span className="type-micro">Mensaje predeterminado</span>
                  <h2 id="campaign-template-heading">Propuesta inicial</h2>
                </div>
                <Link href="/settings/message-templates">Configurar mensaje</Link>
              </div>
              {template ? (
                <>
                  <dl className="campaign-template-preview__meta">
                    <div><dt>Asunto</dt><dd>{template.subject || "Sin asunto"}</dd></div>
                    <div><dt>Revisión</dt><dd>{template.revision}</dd></div>
                  </dl>
                  <pre className="campaign-template-preview__body">{template.body}</pre>
                </>
              ) : (
                <p className="campaign-template-preview__missing">No hay una revisión activa. Al crear el borrador se usará la revisión predeterminada del sistema.</p>
              )}
            </article>

            <div className="campaign-content-fields">
              {catalogMissing ? (
                <Alert
                  className="campaign-catalog-required"
                  type="warning"
                  showIcon
                  title="Falta un catálogo para adjuntar"
                  description={(
                    <span>
                      Subí al menos un PDF activo para habilitar la creación del borrador. <Link href="/catalogs">Ir a catálogos</Link>
                    </span>
                  )}
                />
              ) : null}
              <Form.Item
                label="PDFs adjuntos"
                name="catalogs"
                extra="Se adjuntarán en el orden en que los selecciones. La propuesta inicial incluye todos; el recordatorio no adjunta archivos."
                rules={[{ required: true, message: "Elegí al menos un PDF." }]}
              >
                <Select
                  mode="multiple"
                  optionFilterProp="label"
                  placeholder={catalogMissing ? "No hay catálogos disponibles" : "Seleccioná uno o más catálogos"}
                  disabled={catalogMissing}
                  options={catalogs.map((catalog) => ({ value: catalog.id, label: `${catalog.name} · versión ${catalog.version}` }))}
                />
              </Form.Item>
              <Form.Item
                label="Recordatorio"
                name="reminder_enabled"
                valuePropName="checked"
                extra="Si no hay una respuesta humana, se prepara como máximo un recordatorio en el mismo hilo."
              >
                <Switch checkedChildren="Activado" unCheckedChildren="Desactivado" />
              </Form.Item>
              {reminderEnabled ? (
                <Form.Item
                  label="Días de espera antes del recordatorio"
                  name="reminder_delay_days"
                  extra="Se cuentan desde el envío confirmado y se respeta el próximo día y horario permitido."
                  rules={[{ required: true, message: "Indicá cuántos días esperar." }]}
                >
                  <InputNumber min={1} precision={0} style={{ width: "100%" }} />
                </Form.Item>
              ) : null}
            </div>
          </div>
        </section>

        <section className="campaign-form-section campaign-form-section--wide" aria-labelledby="campaign-schedule-heading">
          <h2 className="type-title" id="campaign-schedule-heading">Revisión y calendario</h2>
          <p className="campaign-form-section__description">Elegí cómo querés revisar los mensajes y en qué momentos se podrán procesar.</p>
          <div className="campaign-form-grid campaign-form-grid--choices">
            <Form.Item label="Modo" name="delivery_mode" rules={[{ required: true }]}>
              <Radio.Group className="campaign-choice-group">
                <Radio value="DRY_RUN"><span><strong>Simulación</strong><small>Prepara todo, pero no envía emails.</small></span></Radio>
                <Radio value="REVIEW_ONLY"><span><strong>Sólo revisión</strong><small>Deja cada mensaje preparado para leerlo.</small></span></Radio>
              </Radio.Group>
            </Form.Item>
            <Form.Item label="Cómo aprobar los mensajes" name="approval_mode" rules={[{ required: true }]}>
              <Radio.Group className="campaign-choice-group">
                <Radio value="CAMPAIGN"><span><strong>Campaña completa</strong><small>Una confirmación aprueba audiencia, contenido, adjuntos y calendario.</small></span></Radio>
                <Radio value="PER_MESSAGE"><span><strong>Mensaje por mensaje</strong><small>Permite revisar y editar cada mensaje antes de iniciar.</small></span></Radio>
              </Radio.Group>
            </Form.Item>
          </div>

          <Form.Item
            label="Días permitidos"
            name="weekdays"
            extra="Sábado y domingo están disponibles, pero no se incluyen de forma predeterminada."
            rules={[{ required: true, message: "Elegí al menos un día." }]}
          >
            <Checkbox.Group className="campaign-weekdays">
              {WEEK_OPTIONS.map((day) => <Checkbox key={day.value} value={day.value} aria-label={day.fullLabel}>{day.label}</Checkbox>)}
            </Checkbox.Group>
          </Form.Item>

          <div className="campaign-form-grid campaign-form-grid--schedule">
            <Form.Item label="Desde" name="window_start" extra="Hora de Buenos Aires" rules={[{ required: true, message: "Indicá la hora de inicio." }]}><Input type="time" /></Form.Item>
            <Form.Item label="Hasta" name="window_end" extra="Hora de Buenos Aires" rules={[{ required: true, message: "Indicá la hora de finalización." }]}><Input type="time" /></Form.Item>
            <Form.Item label="Máximo por día" name="daily_limit" extra="Cantidad máxima de emails que se pueden procesar en un día." rules={[{ required: true }]}><InputNumber min={1} precision={0} style={{ width: "100%" }} /></Form.Item>
            <Form.Item label="Espera entre mensajes" name="message_interval_minutes" extra="Minutos entre dos entregas consecutivas." rules={[{ required: true }]}><InputNumber min={1} precision={0} addonAfter="min" style={{ width: "100%" }} /></Form.Item>
          </div>
        </section>

        <details className="campaign-advanced">
          <summary>Opciones avanzadas</summary>
          <p>Estos límites controlan cuánto trabajo puede hacer la búsqueda. Los valores predeterminados sirven para la mayoría de las campañas.</p>
          <div className="campaign-form-grid campaign-form-grid--advanced">
            <Form.Item
              label="Máximo de registros a revisar"
              name="max_raw_records"
              extra="Incluye duplicados y negocios sin email; por eso conviene que sea mayor que el objetivo."
              rules={[{ required: true }]}
            >
              <InputNumber min={1} precision={0} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              label="Confianza mínima del registro"
              name="overture_min_confidence"
              extra="Descarta lugares cuya existencia tenga una confianza menor a este valor."
              rules={[{ required: true }]}
            >
              <InputNumber min={0} max={1} step={0.05} stringMode style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item label="Etiqueta de ubicación" name="location_text" extra="Sirve para reconocer y filtrar la campaña; no cambia el área elegida en el mapa.">
              <Input maxLength={255} />
            </Form.Item>
          </div>
        </details>

        <Form.Item name="timezone_name" hidden><Input /></Form.Item>
        <Form.Item name="relevance_threshold" hidden><InputNumber /></Form.Item>

        <div className="campaign-create-actions" role="region" aria-label="Acciones del borrador">
          <p>
            {catalogMissing ? (
              <><strong>Falta un catálogo:</strong> subí un PDF activo para poder crear el borrador.</>
            ) : (
              <><strong>Próximo paso:</strong> después de guardar, revisá el borrador e iniciá la búsqueda de destinatarios.</>
            )}
          </p>
          <div>
            <Link href="/campaigns"><Button>Cancelar</Button></Link>
            <DisabledReason disabled={catalogMissing} reason="Subí al menos un catálogo PDF para crear el borrador.">
              <Button type="primary" htmlType="submit" loading={saving}>{saving ? "Creando borrador…" : "Crear borrador"}</Button>
            </DisabledReason>
          </div>
        </div>
      </Form>
    </>
  );
}
