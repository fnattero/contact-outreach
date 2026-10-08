"use client";

import { App, Alert, Button, Card, Drawer, Flex, Form, Input, Select, Table } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import { can,
  createSuppression,
  getSuppressions,
  problemMessage,
  type Problem,
  type Suppression,
  type SuppressionReason,
} from "@/lib/api";
import { applyFieldErrors } from "@/lib/form-errors";

type SuppressionForm = { email: string; reason: SuppressionReason; evidence?: string };

const PAGE_SIZE = 25;
const FORM_FIELDS = ["email", "reason", "evidence"] as const;
const dateFormatter = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/Argentina/Buenos_Aires",
});
const reasonOptions: Array<{ value: SuppressionReason; label: string }> = [
  { value: "MANUAL", label: "Bloqueo manual" },
  { value: "UNSUBSCRIBE", label: "Baja solicitada" },
  { value: "BOUNCE", label: "Rebote" },
];

export default function SuppressionsPage() {
  const { session } = useAuth();
  const { message } = App.useApp();
  const [form] = Form.useForm<SuppressionForm>();
  const [rows, setRows] = useState<Suppression[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [dirty, setDirty] = useState(false);

  const isAdmin = can(session, "manage_contacts");

  // Handlers flag `loading` before changing page/search/reloadKey; the effect only reports results.
  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    getSuppressions({ q: search, page, page_size: PAGE_SIZE })
      .then((response) => {
        if (cancelled) return;
        setRows(response.data);
        setTotal(response.meta.total);
        setError(null);
      })
      .catch((problem) => { if (!cancelled) setError(problem); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [isAdmin, page, search, reloadKey]);

  if (!isAdmin) return <AuthError error={{ detail: "No tenés permisos para administrar los correos bloqueados." }} />;
  if (error && !rows.length && !loading) return <AuthError error={error} />;

  async function add(values: SuppressionForm) {
    setSaving(true);
    setError(null);
    try {
      await createSuppression({ ...values, evidence: values.evidence ?? "" });
      void message.success("Correo agregado a los bloqueados.");
      form.resetFields();
      setDirty(false);
      setDrawerOpen(false);
      setLoading(true);
      setPage(1);
      setReloadKey((current) => current + 1);
    } catch (problem) {
      // Field-level errors stay on the inputs; anything else goes to the page-level alert.
      if (!applyFieldErrors(form, problem, FORM_FIELDS)) setError(problem);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Correos bloqueados"
        description="Correos a los que nunca se vuelve a escribir: bajas (permanentes), rebotes y bloqueos que agregues."
        primaryAction={<Button type="primary" onClick={() => setDrawerOpen(true)}>Agregar correo</Button>}
        filters={
          <Input.Search
            allowClear
            aria-label="Buscar correo"
            placeholder="Buscar correo"
            onSearch={(value) => {
              setLoading(true);
              setSearch(value.trim());
              setPage(1);
            }}
          />
        }
      />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Alert
        type="warning"
        showIcon
        message="Control prioritario"
        description="La app revisa esta lista al preparar cada campaña y justo antes de enviar."
      />
      <Card>
        {loading ? (
          <LoadingState layout="list" />
        ) : rows.length || search ? (
          <Table<Suppression>
            rowKey="id"
            dataSource={rows}
            scroll={{ x: 680 }}
            pagination={{ current: page, pageSize: PAGE_SIZE, total, showSizeChanger: false, onChange: (next) => { setLoading(true); setPage(next); } }}
            locale={{ emptyText: "Ningún correo coincide con la búsqueda." }}
            columns={[
              { title: "Correo", dataIndex: "email", render: (value: string) => <span className="data-text">{value}</span> },
              {
                title: "Motivo",
                dataIndex: "reason_label",
                render: (label: string, row) => <StatusBadge label={label} level={row.reason === "MANUAL" ? "info" : "warning"} />,
              },
              { title: "Evidencia", dataIndex: "evidence", render: (value: string) => value || "—" },
              {
                title: "Fecha",
                dataIndex: "created_at",
                render: (value: string) => <time className="data-text" dateTime={value}>{dateFormatter.format(new Date(value))}</time>,
              },
            ]}
          />
        ) : (
          <EmptyState
            headline="La lista está vacía"
            explanation="Todavía no hay correos bloqueados. Las bajas y los rebotes se agregan solos cuando llegan las respuestas."
            actionLabel="Agregar el primer correo"
            onAction={() => setDrawerOpen(true)}
          />
        )}
      </Card>
      <Drawer title="Agregar correo" open={drawerOpen} onClose={() => setDrawerOpen(false)} width={480}>
        <p className="drawer-explanation">Usá esta acción para bloqueos manuales o para dejar constancia de una baja.</p>
        <Form
          form={form}
          layout="vertical"
          validateTrigger="onBlur"
          initialValues={{ reason: "MANUAL" }}
          onValuesChange={() => setDirty(true)}
          onFinish={(values) => void add(values)}
        >
          <FormSection defaultOpen title="Nuevo bloqueo" description="El correo se normaliza antes de guardarse.">
            <Form.Item
              name="email"
              label="Correo electrónico"
              rules={[
                { required: true, message: "Indicá el correo a bloquear." },
                { type: "email", message: "Indicá un correo válido." },
                { max: 320, message: "No puede superar 320 caracteres." },
              ]}
            >
              <Input autoComplete="off" />
            </Form.Item>
            <Form.Item name="reason" label="Motivo" rules={[{ required: true, message: "Elegí un motivo." }]}>
              <Select options={reasonOptions} />
            </Form.Item>
            <Form.Item name="evidence" label="Evidencia" extra="Opcional. Por ejemplo, quién lo pidió y cuándo." rules={[{ max: 2000, message: "No puede superar 2000 caracteres." }]}>
              <Input.TextArea rows={3} />
            </Form.Item>
          </FormSection>
        </Form>
        <StickySaveBar
          dirty={dirty}
          feedback={saving ? { state: "saving" } : { state: "idle" }}
          onSave={() => void form.submit()}
          onCancel={() => setDrawerOpen(false)}
        />
      </Drawer>
    </Flex>
  );
}
