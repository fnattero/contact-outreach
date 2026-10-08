"use client";

import { CloseOutlined } from "@ant-design/icons";
import { Alert, App, Button, Card, Drawer, Flex, Form, Input, Modal, Tag } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { StickySaveBar } from "@/components/design-system/forms";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import {
  can,
  createSearchCategory,
  deleteSearchCategory,
  getSearchCategories,
  problemMessage,
  toggleSearchCategory,
  updateSearchCategoryRules,
  type Problem,
  type SearchCategory,
} from "@/lib/api";
import { addTerm, CATEGORY_EXAMPLES, rulesFromTerms, termsOf } from "./category-helpers";

type Editing = { category: SearchCategory | null };

export default function CategoriesPage() {
  const { session } = useAuth();
  const { message } = App.useApp();
  const allowed = can(session, "manage_configuration");
  const [categories, setCategories] = useState<SearchCategory[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [name, setName] = useState("");
  const [terms, setTerms] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [toDelete, setToDelete] = useState<SearchCategory | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!allowed) return;
    void getSearchCategories(true).then(setCategories).catch(setError).finally(() => setLoading(false));
  }, [allowed]);

  if (!allowed) return <AuthError error={{ detail: "No tenés permisos para editar los negocios a buscar." }} />;
  if (error && !categories.length && !loading) return <AuthError error={error} />;

  function open(category: SearchCategory | null, preset?: { name: string; words: readonly string[] }) {
    setEditing({ category });
    setName(category?.name ?? preset?.name ?? "");
    setTerms(category ? termsOf(category) : [...(preset?.words ?? [])]);
    setDraft("");
    setDirty(preset !== undefined);
    setError(null);
  }

  function close() {
    if (dirty) setCancelOpen(true);
    else setEditing(null);
  }

  function addDraft() {
    const next = addTerm(terms, draft);
    setDraft("");
    if (next.length !== terms.length) {
      setTerms(next);
      setDirty(true);
    }
  }

  async function save() {
    if (!editing) return;
    // A word still typed in the box counts: pressing save should not silently drop it.
    const finalTerms = addTerm(terms, draft);
    setSaving(true);
    setError(null);
    try {
      let saved: SearchCategory;
      if (editing.category) {
        saved = await updateSearchCategoryRules(editing.category.id, rulesFromTerms(finalTerms));
        setCategories((current) => current.map((item) => (item.id === saved.id ? saved : item)));
      } else {
        const created = await createSearchCategory(name.trim());
        saved = finalTerms.length ? await updateSearchCategoryRules(created.id, rulesFromTerms(finalTerms)) : created;
        setCategories((current) => [...current, saved]);
      }
      setDirty(false);
      setEditing(null);
      void message.success("Guardado.");
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(false);
    }
  }

  async function toggle(category: SearchCategory) {
    setBusy(category.id);
    setError(null);
    try {
      const saved = await toggleSearchCategory(category.id);
      setCategories((current) => current.map((item) => (item.id === saved.id ? saved : item)));
      void message.success(saved.active ? "Se vuelve a ofrecer en las campañas." : "Pausado: no se ofrece en campañas nuevas.");
    } catch (problem) {
      setError(problem);
    } finally {
      setBusy(null);
    }
  }

  async function remove(category: SearchCategory) {
    setBusy(category.id);
    setError(null);
    try {
      const { outcome } = await deleteSearchCategory(category.id);
      setCategories((current) => current.filter((item) => item.id !== category.id));
      void message.success(outcome === "archived" ? "Se archivó porque hay campañas que lo usaron." : "Se eliminó.");
    } catch (problem) {
      setError(problem);
    } finally {
      setBusy(null);
      setToDelete(null);
    }
  }

  const canSave = editing !== null && name.trim() !== "" && (terms.length > 0 || cleanDraft(draft) !== "");

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Negocios a buscar"
        description="Los tipos de negocio que las campañas pueden buscar. Cada uno se encuentra por palabras que aparecen en el nombre del negocio."
        primaryAction={<Button type="primary" onClick={() => open(null)}>Agregar negocio a buscar</Button>}
      />
      {error && !editing ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card title="Lo que podés buscar">
        {loading ? (
          <LoadingState layout="list" />
        ) : categories.length ? (
          <ul className="search-targets">
            {categories.map((category) => (
              <li className="search-targets__item" key={category.id}>
                <div className="search-targets__main">
                  <strong>{category.name}</strong>
                  {category.active ? null : <StatusBadge label="Pausado" level="inactive" />}
                  <div className="search-targets__words" aria-label={`Palabras de ${category.name}`}>
                    {termsOf(category).map((term) => <Tag key={term}>{term}</Tag>)}
                  </div>
                </div>
                <Flex gap="small" wrap>
                  <Button onClick={() => open(category)}>Editar</Button>
                  <Button loading={busy === category.id} onClick={() => void toggle(category)}>{category.active ? "Pausar" : "Activar"}</Button>
                  <Button danger disabled={busy === category.id} onClick={() => setToDelete(category)}>Eliminar</Button>
                </Flex>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState headline="Todavía no hay negocios para buscar" explanation="Agregá el primero: elegí cómo lo vas a llamar y las palabras que suelen tener en el nombre." actionLabel="Agregar el primero" onAction={() => open(null)} />
        )}
      </Card>
      <Drawer
        title={editing?.category ? `Editar: ${editing.category.name}` : "Agregar negocio a buscar"}
        open={editing !== null}
        onClose={close}
        width={560}
        destroyOnClose
      >
        {error && editing ? <Alert type="error" showIcon message={problemMessage(error as Problem)} style={{ marginBottom: 16 }} /> : null}
        <Form layout="vertical" onFinish={() => void save()}>
          <Form.Item label="¿Qué negocio querés encontrar?" htmlFor="category-name" extra={editing?.category ? "El nombre no se puede cambiar. Si necesitás otro, creá uno nuevo y eliminá este." : "Cómo lo vas a ver al armar una campaña. Por ejemplo: Bobinado de motores."}>
            <Input id="category-name" value={name} disabled={editing?.category != null} maxLength={160} onChange={(event) => { setName(event.target.value); setDirty(true); }} />
          </Form.Item>
          <Form.Item label="Palabras que aparecen en su nombre" htmlFor="category-word" extra="La búsqueda encuentra los negocios cuyo nombre tenga cualquiera de estas palabras o frases, y también su plural. Mientras más específicas, mejores resultados: “bobinado” sirve más que “motor”.">
            <Flex gap="small">
              <Input id="category-word" value={draft} maxLength={80} placeholder="Escribí una palabra o frase y presioná Enter" onChange={(event) => setDraft(event.target.value)} onPressEnter={(event) => { event.preventDefault(); addDraft(); }} />
              <Button onClick={addDraft} disabled={cleanDraft(draft) === ""}>Agregar</Button>
            </Flex>
          </Form.Item>
          {terms.length ? (
            <div className="search-targets__words search-targets__words--edit" aria-label="Palabras elegidas">
              {terms.map((term) => (
                <Tag key={term} closable closeIcon={<CloseOutlined aria-label={`Quitar ${term}`} />} onClose={(event) => { event.preventDefault(); setTerms((current) => current.filter((item) => item !== term)); setDirty(true); }}>{term}</Tag>
              ))}
            </div>
          ) : <p className="muted">Todavía no agregaste ninguna palabra.</p>}
          {!editing?.category ? (
            <section className="search-targets__examples" aria-label="Ejemplos">
              <h3 className="type-micro">Ejemplos para empezar</h3>
              <Flex gap="small" wrap>
                {CATEGORY_EXAMPLES.map((example) => (
                  <Button key={example.name} size="small" onClick={() => { setName(example.name); setTerms([...example.words]); setDraft(""); setDirty(true); }}>{example.name}</Button>
                ))}
              </Flex>
            </section>
          ) : null}
        </Form>
        <StickySaveBar
          dirty={dirty && canSave}
          feedback={saving ? { state: "saving", message: "Guardando…" } : { state: "idle", message: "Hay cambios sin guardar." }}
          onSave={() => void save()}
          onCancel={close}
        />
      </Drawer>
      <ConfirmDangerModal
        open={toDelete !== null}
        title={`Eliminar «${toDelete?.name ?? ""}»`}
        consequences={["Si ninguna campaña lo usó, se elimina junto con sus palabras.", "Si alguna campaña ya lo usó, se archiva: deja de ofrecerse y las campañas conservan su copia."]}
        confirmationWord="ELIMINAR"
        dangerLabel="Eliminar"
        confirming={toDelete !== null && busy === toDelete.id}
        onCancel={() => setToDelete(null)}
        onConfirm={() => { if (toDelete) void remove(toDelete); }}
      />
      <Modal open={cancelOpen} title="Descartar cambios sin guardar" onCancel={() => setCancelOpen(false)} onOk={() => { setCancelOpen(false); setDirty(false); setEditing(null); }} okText="Descartar cambios" cancelText="Seguir editando" okButtonProps={{ danger: true }}>
        <p>Lo que escribiste se perderá si cerrás este panel.</p>
      </Modal>
    </Flex>
  );
}

function cleanDraft(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}
