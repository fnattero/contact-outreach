"use client";

import { Alert, App, Button, Card, Collapse, Drawer, Flex, Form, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import { createSearchCategory, deleteSearchCategory, getSearchCategories, problemMessage, toggleSearchCategory, updateSearchCategoryRules, type Problem, type SearchCategory } from "@/lib/api";

export default function CategoriesPage() {
  const { session } = useAuth();
  const { message } = App.useApp();
  const [createForm] = Form.useForm<{ name: string }>();
  const [categories, setCategories] = useState<SearchCategory[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [createDirty, setCreateDirty] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [toDelete, setToDelete] = useState<SearchCategory | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => { void getSearchCategories(true).then(setCategories).catch(setError).finally(() => setLoading(false)); }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para editar rubros." }} />;
  if (error && !categories.length && !loading) return <AuthError error={error} />;

  async function addCategory(values: { name: string }) {
    setError(null);
    try { const created = await createSearchCategory(values.name); setCategories((current) => [...current, created]); setDrawerOpen(false); createForm.resetFields(); setCreateDirty(false); }
    catch (problem) { setError(problem); }
  }

  async function saveRules(category: SearchCategory, values: { rules: string }) {
    setSaving(category.id); setError(null);
    const rules = values.rules.split("\n").map((term) => term.trim()).filter(Boolean).map((term) => ({ name_terms: [term] }));
    try { const saved = await updateSearchCategoryRules(category.id, rules); setCategories((current) => current.map((item) => item.id === saved.id ? saved : item)); setDirty((current) => ({ ...current, [category.id]: false })); }
    catch (problem) { setError(problem); }
    finally { setSaving(null); }
  }

  async function toggle(category: SearchCategory) {
    setBusy(category.id); setError(null);
    try {
      const saved = await toggleSearchCategory(category.id);
      setCategories((current) => current.map((item) => item.id === saved.id ? saved : item));
      void message.success(saved.active ? "El rubro volvió a estar activo." : "El rubro quedó inactivo y no se ofrece en campañas nuevas.");
    } catch (problem) { setError(problem); }
    finally { setBusy(null); }
  }

  async function remove(category: SearchCategory) {
    setBusy(category.id); setError(null);
    try {
      const { outcome } = await deleteSearchCategory(category.id);
      setCategories((current) => current.filter((item) => item.id !== category.id));
      void message.success(outcome === "archived" ? "El rubro se archivó porque hay campañas que lo usaron." : "El rubro se eliminó.");
      setToDelete(null);
    } catch (problem) { setError(problem); setToDelete(null); }
    finally { setBusy(null); }
  }

  function closeCreate() {
    if (createDirty) setCancelOpen(true);
    else setDrawerOpen(false);
  }

  function discardCreate() {
    createForm.resetFields(); setCreateDirty(false); setCancelOpen(false); setDrawerOpen(false);
  }

  return (
    <Flex vertical gap="large">
      <PageHeader title="Rubros de búsqueda" description="Configurá variantes literales que el backend usa para buscar lugares. Una variante por línea." primaryAction={<Button type="primary" onClick={() => setDrawerOpen(true)}>Crear rubro</Button>} />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card title="Rubros">
        {loading ? <LoadingState layout="list" /> : categories.length ? <Collapse items={categories.map((category) => {
          const variants = category.rules.flatMap((rule) => rule.name_terms);
          return {
            key: category.id,
            label: <span className="category-collapse-label"><strong>{category.name}</strong>{category.active ? null : <StatusBadge label="Inactivo" level="inactive" />}<span>{variants.length} {variants.length === 1 ? "variante" : "variantes"}</span></span>,
            children: <div className="category-rules-editor">
              <div className="category-rules-list"><span className="type-micro">Variantes activas</span>{variants.length ? <ul>{variants.map((term, index) => <li key={`${term}-${index}`}>{term}</li>)}</ul> : <p>No hay variantes todavía.</p>}</div>
              <Form layout="vertical" validateTrigger="onBlur" initialValues={{ rules: variants.join("\n") }} onValuesChange={() => setDirty((current) => ({ ...current, [category.id]: true }))} onFinish={(values) => void saveRules(category, values as { rules: string })}>
                <Form.Item name="rules" label="Editar variantes" extra="Una variante por línea. Guardar reemplaza la lista actual de este rubro."><Input.TextArea rows={6} maxLength={4000} showCount={false} /></Form.Item>
                <Flex align="center" gap="small"><Button htmlType="submit" loading={saving === category.id}>Guardar variantes</Button>{saving === category.id ? <span className="form-save-feedback">Guardando…</span> : dirty[category.id] ? <span className="form-save-feedback">Cambios sin guardar</span> : <span className="form-save-feedback form-save-feedback--saved">Guardado</span>}</Flex>
              </Form>
              <Flex gap="small" wrap className="category-rules-actions">
                <Button loading={busy === category.id} onClick={() => void toggle(category)}>{category.active ? "Desactivar rubro" : "Activar rubro"}</Button>
                <Button danger disabled={busy === category.id} onClick={() => setToDelete(category)}>Eliminar rubro</Button>
              </Flex>
            </div>,
          };
        })} /> : <EmptyState headline="Todavía no hay rubros" explanation="Creá un rubro para definir las variantes que se usarán en las búsquedas." actionLabel="Crear primer rubro" onAction={() => setDrawerOpen(true)} />}
      </Card>
      <Drawer title="Crear rubro" open={drawerOpen} onClose={closeCreate} width={480}>
        <p className="drawer-explanation">El nombre identifica el rubro en las búsquedas y puede ampliarse luego con variantes.</p>
        <Form form={createForm} layout="vertical" validateTrigger="onBlur" onValuesChange={() => setCreateDirty(true)} onFinish={(values) => void addCategory(values)}>
          <FormSection title="Nuevo rubro" description="Definí el nombre que verá el equipo al configurar una búsqueda.">
            <Form.Item name="name" label="Nombre" rules={[{ required: true, message: "Indicá un nombre para el rubro." }, { max: 160, message: "No puede superar 160 caracteres." }]}><Input /></Form.Item>
          </FormSection>
        </Form>
        <StickySaveBar dirty={createDirty} feedback={createDirty ? { state: "idle", message: "Hay cambios sin guardar." } : { state: "idle" }} onSave={() => void createForm.submit()} onCancel={closeCreate} />
      </Drawer>
      <ConfirmDangerModal
        open={toDelete !== null}
        title={`Eliminar el rubro «${toDelete?.name ?? ""}»`}
        consequences={["Si ninguna campaña usó el rubro, se elimina junto con sus variantes.", "Si alguna campaña ya lo usó, se archiva: deja de ofrecerse y las campañas conservan su copia."]}
        confirmationWord="ELIMINAR"
        dangerLabel="Eliminar rubro"
        confirming={toDelete !== null && busy === toDelete.id}
        onCancel={() => setToDelete(null)}
        onConfirm={() => { if (toDelete) void remove(toDelete); }}
      />
      <Modal open={cancelOpen} title="Descartar rubro sin guardar" onCancel={() => setCancelOpen(false)} onOk={discardCreate} okText="Descartar cambios" cancelText="Seguir editando" okButtonProps={{ danger: true }}><p>El nombre que escribiste se perderá si cerrás este panel.</p></Modal>
    </Flex>
  );
}
