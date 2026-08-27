"use client";

import { Alert, Button, Card, Empty, Flex, Form, Input, List, Skeleton, Tag, Typography, Upload } from "antd";
import type { UploadFile } from "antd";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getCatalogs, problemMessage, uploadCatalog, type Catalog, type Problem } from "@/lib/api";

const sizeFormatter = new Intl.NumberFormat("es-AR");

export default function CatalogsPage() {
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  function refresh() {
    setLoading(true);
    void getCatalogs()
      .then(setCatalogs)
      .catch(setError)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    let cancelled = false;
    void getCatalogs()
      .then((nextCatalogs) => {
        if (!cancelled) setCatalogs(nextCatalogs);
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

  async function submit(values: { name: string }) {
    const file = fileList[0]?.originFileObj;
    if (!file) {
      setError({ detail: "Seleccioná un PDF para subir." });
      return;
    }
    setUploading(true);
    setError(null);
    try {
      await uploadCatalog(values.name, file);
      setFileList([]);
      refresh();
    } catch (problem) {
      setError(problem);
    } finally {
      setUploading(false);
    }
  }

  if (error && !catalogs.length && !loading) {
    return <AuthError error={error} />;
  }

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Catálogos PDF</Typography.Title>
        <Typography.Paragraph type="secondary">
          Archivos privados que se fijan en la aprobación de una campaña.
        </Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card title="Subir catálogo">
        <Form layout="vertical" onFinish={(values) => void submit(values as { name: string })}>
          <Form.Item
            label="Nombre"
            name="name"
            rules={[{ required: true, message: "Indicá un nombre para el catálogo." }]}
          >
            <Input />
          </Form.Item>
          <Form.Item label="Archivo PDF" required>
            <Upload
              accept="application/pdf,.pdf"
              maxCount={1}
              beforeUpload={() => false}
              fileList={fileList}
              onChange={({ fileList: nextFiles }) => setFileList(nextFiles)}
            >
              <Button>Seleccionar PDF</Button>
            </Upload>
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={uploading}>
            Guardar catálogo
          </Button>
        </Form>
      </Card>
      <Card title="Archivos disponibles">
        {loading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : catalogs.length ? (
          <List
            dataSource={catalogs}
            renderItem={(catalog) => (
              <List.Item
                actions={
                  catalog.missing
                    ? []
                    : [
                        <a key="download" href={`/api/v1/catalogs/${catalog.id}/download/`}>
                          Descargar
                        </a>,
                      ]
                }
              >
                <List.Item.Meta
                  title={`${catalog.name} · v${catalog.version}`}
                  description={`${catalog.original_filename} · ${sizeFormatter.format(catalog.byte_size)} bytes`}
                />
                <Tag color={catalog.missing ? "red" : catalog.active ? "green" : "default"}>
                  {catalog.missing ? "Falta el archivo" : catalog.active ? "Activo" : "Archivado"}
                </Tag>
              </List.Item>
            )}
          />
        ) : (
          <Empty description="Todavía no hay catálogos." />
        )}
      </Card>
      <Alert
        type="info"
        showIcon
        message="Los archivos se descargan únicamente a través del backend autenticado."
      />
    </Flex>
  );
}
