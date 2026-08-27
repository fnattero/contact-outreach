"use client";

import { Alert, Button, Card, Flex, Form, Input, InputNumber, Skeleton, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import {
  getBusinessProfile,
  problemMessage,
  updateBusinessProfile,
  type BusinessProfile,
  type Problem,
} from "@/lib/api";

export default function ProfileSettingsPage() {
  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    void getBusinessProfile()
      .then(setProfile)
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  async function submit(values: Partial<BusinessProfile>) {
    setSaving(true);
    setError(null);
    try {
      setProfile(await updateBusinessProfile(values));
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(false);
    }
  }

  if (error && !profile && !loading) return <AuthError error={error} />;
  if (loading) return <Skeleton active paragraph={{ rows: 12 }} />;

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Perfil comercial</Typography.Title>
        <Typography.Paragraph type="secondary">
          Información aprobada que puede usar el motor de contenido y las campañas.
        </Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card>
        <Form
          key={profile?.profile_version ?? "empty"}
          layout="vertical"
          initialValues={profile ?? undefined}
          onFinish={(values) => void submit(values as Partial<BusinessProfile>)}
        >
          <Form.Item label="Empresa" name="company_name" rules={[{ required: true }]}> <Input /> </Form.Item>
          <Form.Item label="Vendedor/a" name="salesperson_name"> <Input /> </Form.Item>
          <Form.Item label="Teléfono" name="phone"> <Input /> </Form.Item>
          <Form.Item label="WhatsApp" name="whatsapp"> <Input /> </Form.Item>
          <Form.Item label="Sitio web" name="website" rules={[{ type: "url", message: "Indicá una URL válida." }]}> <Input /> </Form.Item>
          <Form.Item label="Descripción" name="description"> <Input.TextArea rows={4} /> </Form.Item>
          <Form.Item label="Productos" name="products"> <Input.TextArea rows={4} /> </Form.Item>
          <Form.Item label="Diferenciadores" name="differentiators"> <Input.TextArea rows={4} /> </Form.Item>
          <Form.Item label="Dirección" name="address"> <Input /> </Form.Item>
          <Form.Item label="Firma" name="signature"> <Input.TextArea rows={3} /> </Form.Item>
          <Form.Item label="Instrucciones adicionales" name="additional_instructions"> <Input.TextArea rows={4} /> </Form.Item>
          <Form.Item label="Umbral de relevancia" name="relevance_threshold">
            <InputNumber min={0} max={100} style={{ width: "100%" }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving}>
            Guardar perfil
          </Button>
        </Form>
      </Card>
    </Flex>
  );
}
