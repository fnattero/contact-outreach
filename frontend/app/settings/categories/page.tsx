"use client";

import { Alert, Button, Card, Collapse, Empty, Flex, Form, Input, Skeleton, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
  createSearchCategory,
  getSearchCategories,
  problemMessage,
  updateSearchCategoryRules,
  type Problem,
  type SearchCategory,
} from "@/lib/api";

export default function CategoriesPage() {
  const { session } = useAuth();
  const [categories, setCategories] = useState<SearchCategory[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    void getSearchCategories().then(setCategories).catch(setError).finally(() => setLoading(false));
  }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para editar rubros." }} />;
  if (error && !categories.length && !loading) return <AuthError error={error} />;

  async function addCategory(values: { name: string }) {
    setError(null);
    try {
      const created = await createSearchCategory(values.name);
      setCategories((current) => [...current, created]);
    } catch (problem) {
      setError(problem);
    }
  }

  async function saveRules(category: SearchCategory, values: { rules: string }) {
    setSaving(category.id);
    setError(null);
    const rules = values.rules.split("\n").map((term) => term.trim()).filter(Boolean).map((term) => ({ name_terms: [term] }));
    try {
      const saved = await updateSearchCategoryRules(category.id, rules);
      setCategories((current) => current.map((item) => item.id === saved.id ? saved : item));
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(null);
    }
  }

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Rubros de búsqueda</Typography.Title>
        <Typography.Paragraph type="secondary">Configurá variantes literales que el backend usa para buscar lugares. Una variante por línea.</Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card title="Nuevo rubro">
        <Form layout="vertical" onFinish={(values) => void addCategory(values as { name: string })}>
          <Form.Item name="name" label="Nombre" rules={[{ required: true, max: 160 }]}><Input /></Form.Item>
          <Button type="primary" htmlType="submit">Crear rubro</Button>
        </Form>
      </Card>
      <Card title="Rubros activos">
        {loading ? <Skeleton active paragraph={{ rows: 8 }} /> : categories.length ? (
          <Collapse items={categories.map((category) => ({
            key: category.id,
            label: `${category.name} · ${category.rules.length} reglas`,
            children: (
              <Form
                layout="vertical"
                initialValues={{ rules: category.rules.flatMap((rule) => rule.name_terms).join("\n") }}
                onFinish={(values) => void saveRules(category, values as { rules: string })}
              >
                <Form.Item name="rules" label="Variantes">
                  <Input.TextArea rows={6} maxLength={4000} showCount />
                </Form.Item>
                <Button htmlType="submit" loading={saving === category.id}>Guardar reglas</Button>
              </Form>
            ),
          }))} />
        ) : <Empty description="Todavía no hay rubros." />}
      </Card>
    </Flex>
  );
}
