"use client";

import { Alert, Button, Card, Flex, Form, Input, InputNumber, Select, Skeleton, Typography } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
  createCampaign,
  getCatalogs,
  getSearchCategories,
  getSearchZones,
  problemMessage,
  type Catalog,
  type Problem,
  type SearchCategory,
  type SearchZone,
} from "@/lib/api";

type CampaignForm = {
  name: string;
  delivery_mode: string;
  approval_mode: string;
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

const weekOptions = [
  { value: 0, label: "Lun" },
  { value: 1, label: "Mar" },
  { value: 2, label: "Mié" },
  { value: 3, label: "Jue" },
  { value: 4, label: "Vie" },
];

export default function NewCampaignPage() {
  const { session } = useAuth();
  const router = useRouter();
  const [categories, setCategories] = useState<SearchCategory[]>([]);
  const [provinces, setProvinces] = useState<SearchZone[]>([]);
  const [zones, setZones] = useState<SearchZone[]>([]);
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getSearchCategories(), getSearchZones("PROVINCE"), getSearchZones(), getCatalogs()])
      .then(([nextCategories, nextProvinces, nextZones, nextCatalogs]) => {
        if (!cancelled) {
          setCategories(nextCategories);
          setProvinces(nextProvinces);
          setZones(nextZones.filter((zone) => zone.selectable));
          setCatalogs(nextCatalogs.filter((catalog) => !catalog.missing && catalog.active));
        }
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (session?.role !== "ADMIN") {
    return <AuthError error={{ detail: "No tenés permisos para crear campañas." }} />;
  }
  if (error) return <AuthError error={error} />;
  if (loading) return <Skeleton active paragraph={{ rows: 16 }} />;

  async function submit(values: CampaignForm) {
    setSaving(true);
    setError(null);
    try {
      const campaign = await createCampaign({
        ...values,
        catalog: values.catalogs[0],
        window_start: values.window_start,
        window_end: values.window_end,
        timezone_name: "America/Argentina/Buenos_Aires",
        confirm_live: false,
      });
      router.push(`/campaigns/${campaign.id}`);
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Nueva campaña</Typography.Title>
        <Typography.Paragraph type="secondary">
          Se crea como borrador. La audiencia, el texto y los adjuntos se vuelven inmutables al aprobar.
        </Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Alert
        type="warning"
        showIcon
        message="Esta instalación comienza protegida en modo simulación"
        description="El backend volverá a comprobar permisos, restricciones y kill switches antes de cualquier efecto."
      />
      <Card>
        <Form
          layout="vertical"
          initialValues={{
            delivery_mode: "DRY_RUN",
            approval_mode: "CAMPAIGN",
            objective: 300,
            max_raw_records: 3000,
            overture_min_confidence: "0.750",
            daily_limit: 30,
            message_interval_minutes: 5,
            weekdays: [0, 1, 2, 3, 4],
            window_start: "09:00",
            window_end: "17:00",
            relevance_threshold: 70,
            reminder_enabled: false,
            reminder_delay_days: 3,
          }}
          onFinish={(values) => void submit(values as CampaignForm)}
        >
          <Form.Item label="Nombre" name="name" rules={[{ required: true, message: "Indicá un nombre." }]}> <Input /> </Form.Item>
          <Flex gap="middle" wrap>
            <Form.Item label="Modo" name="delivery_mode" rules={[{ required: true }]} style={{ minWidth: 220, flex: 1 }}>
              <Select options={[{ value: "DRY_RUN", label: "Simulación" }, { value: "REVIEW_ONLY", label: "Sólo revisión" }]} />
            </Form.Item>
            <Form.Item label="Aprobación" name="approval_mode" rules={[{ required: true }]} style={{ minWidth: 220, flex: 1 }}>
              <Select options={[{ value: "CAMPAIGN", label: "Aprobar campaña completa" }, { value: "PER_MESSAGE", label: "Revisar cada mensaje" }]} />
            </Form.Item>
          </Flex>
          <Form.Item label="Categorías" name="categories" rules={[{ required: true, message: "Elegí al menos una categoría." }]}> 
            <Select mode="multiple" options={categories.map((category) => ({ value: category.id, label: category.name }))} />
          </Form.Item>
          <Form.Item label="Provincias" name="provinces" rules={[{ required: true, message: "Elegí al menos una provincia." }]}> 
            <Select mode="multiple" options={provinces.map((zone) => ({ value: zone.id, label: zone.name }))} />
          </Form.Item>
          <Form.Item label="Zonas específicas" name="zones" rules={[{ required: true, message: "Elegí al menos una zona." }]}> 
            <Select mode="multiple" options={zones.map((zone) => ({ value: zone.id, label: zone.location_text }))} />
          </Form.Item>
          <Form.Item label="PDFs adjuntos, en orden" name="catalogs" rules={[{ required: true, message: "Elegí al menos un PDF." }]}> 
            <Select mode="multiple" options={catalogs.map((catalog) => ({ value: catalog.id, label: `${catalog.name} · v${catalog.version}` }))} />
          </Form.Item>
          <Form.Item label="Ubicación" name="location_text" initialValue="Argentina">
            <Input />
          </Form.Item>
          <Flex gap="middle" wrap>
            <Form.Item label="Objetivo" name="objective" rules={[{ required: true }]}><InputNumber min={1} style={{ width: "100%" }} /></Form.Item>
            <Form.Item label="Máximo de registros crudos" name="max_raw_records" rules={[{ required: true }]}><InputNumber min={1} style={{ width: "100%" }} /></Form.Item>
            <Form.Item label="Límite diario" name="daily_limit" rules={[{ required: true }]}><InputNumber min={1} style={{ width: "100%" }} /></Form.Item>
          </Flex>
          <Flex gap="middle" wrap>
            <Form.Item label="Días" name="weekdays" rules={[{ required: true }]} style={{ minWidth: 220, flex: 1 }}>
              <Select mode="multiple" options={weekOptions} />
            </Form.Item>
            <Form.Item label="Inicio (Buenos Aires)" name="window_start" rules={[{ required: true }]} style={{ minWidth: 180, flex: 1 }}><Input type="time" /></Form.Item>
            <Form.Item label="Fin (Buenos Aires)" name="window_end" rules={[{ required: true }]} style={{ minWidth: 180, flex: 1 }}><Input type="time" /></Form.Item>
          </Flex>
          <Flex gap="middle" wrap>
            <Form.Item label="Intervalo entre mensajes (minutos)" name="message_interval_minutes" rules={[{ required: true }]}><InputNumber min={1} style={{ width: "100%" }} /></Form.Item>
            <Form.Item label="Umbral de relevancia" name="relevance_threshold" rules={[{ required: true }]}><InputNumber min={0} max={100} style={{ width: "100%" }} /></Form.Item>
          </Flex>
          <Flex gap="small" wrap>
            <Button type="primary" htmlType="submit" loading={saving}>Crear borrador</Button>
            <Link href="/campaigns"><Button>Cancelar</Button></Link>
          </Flex>
        </Form>
      </Card>
    </Flex>
  );
}
