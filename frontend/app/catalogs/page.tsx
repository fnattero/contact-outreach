"use client";

import { Alert, Button, Card, Drawer, Flex, Form, Input, Progress, Table, Upload } from "antd";
import type { UploadFile } from "antd";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import { getCatalogs, problemMessage, uploadCatalog, type Catalog, type Problem } from "@/lib/api";

const sizeFormatter = new Intl.NumberFormat("es-AR");
const dateFormatter = new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeZone: "America/Argentina/Buenos_Aires" });

export default function CatalogsPage() {
  const [form] = Form.useForm<{ name: string }>();
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  function refresh() {
    setLoading(true);
    void getCatalogs().then(setCatalogs).catch(setError).finally(() => setLoading(false));
  }

  useEffect(() => {
    void getCatalogs().then(setCatalogs).catch(setError).finally(() => setLoading(false));
  }, []);

  async function submit(values: { name: string }) {
    const file = fileList[0]?.originFileObj;
    if (!file) { setError({ detail: "Seleccioná un PDF para subir." }); return; }
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) { setError({ detail: "El archivo debe ser un PDF." }); return; }
    setUploading(true); setError(null);
    try {
      await uploadCatalog(values.name, file);
      setFileList([]); form.resetFields(); setDrawerOpen(false); refresh();
    } catch (problem) { setError(problem); } finally { setUploading(false); }
  }

  if (error && !catalogs.length && !loading) return <AuthError error={error} />;

  return (
    <Flex vertical gap="large">
      <PageHeader title="Catálogos PDF" description="Archivos privados que se fijan en la aprobación de una campaña." primaryAction={<Button type="primary" onClick={() => setDrawerOpen(true)}>Subir catálogo</Button>} />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card title="Catálogos disponibles">
        {loading ? <LoadingState layout="list" /> : catalogs.length ? <Table<Catalog> rowKey="id" dataSource={catalogs} pagination={{ pageSize: 10, responsive: true }} columns={[
          { title: "Nombre", dataIndex: "name", render: (value: string, record) => <span><strong>{value}</strong><br /><small>v{record.version} · {record.original_filename}</small></span> },
          { title: "Fecha de carga", dataIndex: "created_at", render: (value: string) => <time className="data-text" dateTime={value}>{dateFormatter.format(new Date(value))}</time> },
          { title: "Tamaño", dataIndex: "byte_size", render: (value: number) => <span className="data-text">{sizeFormatter.format(value)} bytes</span> },
          { title: "Campañas que lo usan", render: () => <span className="catalog-reference-unavailable">No informado por la API</span> },
          { title: "Estado", render: (_: unknown, record) => <StatusBadge label={record.missing ? "Falta el archivo" : record.active ? "Activo" : "Archivado"} level={record.missing ? "danger" : record.active ? "success" : "inactive"} /> },
          { title: "", render: (_: unknown, record) => record.missing ? null : <a href={`/api/v1/catalogs/${record.id}/download/`}>Descargar</a> },
        ]} /> : <EmptyState headline="Todavía no hay catálogos" explanation="Subí el primer PDF para poder adjuntarlo a una campaña durante la aprobación." actionLabel="Subir primer catálogo" onAction={() => setDrawerOpen(true)} />}
      </Card>
      <Alert type="info" showIcon message="Los archivos se descargan únicamente a través del backend autenticado." />
      <Drawer title="Subir catálogo" open={drawerOpen} onClose={() => { if (!uploading) setDrawerOpen(false); }} width={520}>
        <p className="drawer-explanation">Elegí un PDF privado. La validación se realiza antes de enviarlo y el archivo queda disponible para adjuntarlo a campañas.</p>
        <Form form={form} layout="vertical" validateTrigger="onBlur" onFinish={(values) => void submit(values)}>
          <Form.Item label="Nombre" name="name" extra="Usá un nombre que permita reconocer el catálogo en una campaña." rules={[{ required: true, message: "Indicá un nombre para el catálogo." }]}><Input /></Form.Item>
          <Form.Item label="Archivo PDF" required extra="Sólo PDF. El archivo se descarga luego mediante una ruta autenticada.">
            <Upload accept="application/pdf,.pdf" maxCount={1} beforeUpload={(file) => { if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) { setError({ detail: "El archivo debe ser un PDF." }); return Upload.LIST_IGNORE; } return false; }} fileList={fileList} onChange={({ fileList: nextFiles }) => setFileList(nextFiles)}><Button>Seleccionar PDF</Button></Upload>
          </Form.Item>
          {uploading ? <div className="catalog-upload-progress"><Progress status="active" percent={undefined} /><span>Subiendo y validando el archivo…</span></div> : null}
          <Button type="primary" htmlType="submit" loading={uploading}>Guardar catálogo</Button>
        </Form>
      </Drawer>
    </Flex>
  );
}
