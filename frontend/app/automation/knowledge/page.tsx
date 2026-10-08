"use client";

import { Alert, Button, Card, Flex, Form, Input, List, Tag } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { PageHeader } from "@/components/design-system/page-header";
import { SectionTabs } from "@/components/design-system/section-tabs";
import { LoadingState } from "@/components/design-system/states";
import { AUTOMATION_TABS } from "@/components/design-system/tabs-config";
import {
  can,
  approveKnowledgeContext,
  approveKnowledgeFact,
  createKnowledgeContext,
  createKnowledgeFact,
  getKnowledgeContexts,
  getKnowledgeFacts,
  previewKnowledge,
  problemMessage,
  type KnowledgeContextRevision,
  type KnowledgeFactRevision,
  type KnowledgeRevisionState,
  type Problem,
} from "@/lib/api";

// The API asks the admin to type this word before content becomes usable by the agent.
const CONFIRMATION_WORD = "CONFIRMAR";

const revisionStateTag: Record<KnowledgeRevisionState, { color: string; label: string }> = {
  APPROVED: { color: "green", label: "Aprobada" },
  DRAFT: { color: "orange", label: "Borrador sin aprobar" },
  SUPERSEDED: { color: "default", label: "Reemplazada" },
};

export default function KnowledgePage() {
  const { session } = useAuth();
  const allowed = can(session, "manage_automation");
  const [facts, setFacts] = useState<KnowledgeFactRevision[] | null>(null);
  const [contexts, setContexts] = useState<KnowledgeContextRevision[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<{ status: string; matches: Array<{ title: string; score: number; selected: boolean }> } | null>(null);
  const [pendingApproval, setPendingApproval] = useState<{ kind: "fact" | "context"; id: string; label: string } | null>(null);

  useEffect(() => {
    if (!allowed) return;
    void Promise.all([getKnowledgeFacts(), getKnowledgeContexts()])
      .then(([nextFacts, nextContexts]) => {
        setFacts(nextFacts);
        setContexts(nextContexts);
      })
      .catch(setError);
  }, [allowed]);

  if (!allowed) return <AuthError error={{ detail: "No tenés permisos para configurar automatización." }} />;
  if (error && !facts) return <AuthError error={error} />;
  if (!facts) return <LoadingState layout="form" />;

  async function saveFact(values: { title: string; category?: string; text: string }) {
    setSaving(true); setError(null);
    try { const created = await createKnowledgeFact(values); setFacts((current) => [created, ...(current ?? [])]); }
    catch (problem) { setError(problem); } finally { setSaving(false); }
  }

  async function saveContext(values: { context_text: string }) {
    setSaving(true); setError(null);
    try { const created = await createKnowledgeContext(values.context_text); setContexts((current) => [created, ...current]); }
    catch (problem) { setError(problem); } finally { setSaving(false); }
  }

  async function approveRevision() {
    if (!pendingApproval) return;
    setSaving(true); setError(null);
    try {
      if (pendingApproval.kind === "fact") {
        await approveKnowledgeFact(pendingApproval.id);
        setFacts(await getKnowledgeFacts());
      } else {
        await approveKnowledgeContext(pendingApproval.id);
        setContexts(await getKnowledgeContexts());
      }
      setPendingApproval(null);
    } catch (problem) { setError(problem); } finally { setSaving(false); }
  }

  async function runPreview(values: { query: string }) {
    setSaving(true); setError(null);
    try { setPreview(await previewKnowledge(values.query)); }
    catch (problem) { setError(problem); } finally { setSaving(false); }
  }

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Respuestas automáticas"
        description="La información con la que la app puede contestar. Solo usa lo que aprobaste."
        tabs={<SectionTabs label="Respuestas automáticas" tabs={AUTOMATION_TABS} />}
      />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <div className="knowledge-grid">
        <Card title="Datos de tu empresa y tus productos">
          <p className="integration-muted">Lo que guardás queda como borrador. El agente sólo usa información aprobada.</p>
          <Form layout="vertical" onFinish={(values) => void saveFact(values as { title: string; category?: string; text: string })}>
            <Form.Item name="title" label="Título" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="category" label="Categoría"><Input /></Form.Item>
            <Form.Item name="text" label="Información verificable" rules={[{ required: true }]}><Input.TextArea rows={4} maxLength={4000} showCount /></Form.Item>
            <Button htmlType="submit" loading={saving}>Guardar borrador</Button>
          </Form>
          <List style={{ marginTop: 16 }} dataSource={facts} renderItem={(fact) => <List.Item actions={fact.state === "DRAFT" ? [<Button key="approve" size="small" onClick={() => setPendingApproval({ kind: "fact", id: fact.id, label: `${fact.title} · v${fact.version}` })}>Aprobar</Button>] : undefined}><List.Item.Meta title={`${fact.title} · v${fact.version}`} description={fact.text} /><Tag color={revisionStateTag[fact.state].color}>{revisionStateTag[fact.state].label}</Tag></List.Item>} />
        </Card>
        <Card title="Contexto general">
          <p className="integration-muted">El contexto guardado queda como borrador hasta que lo aprobás.</p>
          <Form layout="vertical" onFinish={(values) => void saveContext(values as { context_text: string })}>
            <Form.Item name="context_text" label="Contexto" rules={[{ required: true }]}><Input.TextArea rows={4} maxLength={4000} showCount /></Form.Item>
            <Button htmlType="submit" loading={saving}>Guardar borrador</Button>
          </Form>
          <List style={{ marginTop: 16 }} dataSource={contexts} renderItem={(context) => <List.Item actions={context.state === "DRAFT" ? [<Button key="approve" size="small" onClick={() => setPendingApproval({ kind: "context", id: context.id, label: `Revisión ${context.version}` })}>Aprobar</Button>] : undefined}><List.Item.Meta title={`Revisión ${context.version}`} description={context.context_text} /><Tag color={revisionStateTag[context.state].color}>{revisionStateTag[context.state].label}</Tag></List.Item>} />
        </Card>
      </div>
      <Card title="Probar qué información encuentra">
        <Form layout="vertical" onFinish={(values) => void runPreview(values as { query: string })}>
          <Form.Item name="query" label="Pregunta de ejemplo" rules={[{ required: true }]}><Input /></Form.Item>
          <Button htmlType="submit" loading={saving}>Probar</Button>
        </Form>
        {preview ? <Alert style={{ marginTop: 16 }} type={preview.status === "SELECTED" ? "success" : "info"} message={`Resultado: ${preview.status}`} description={preview.matches.map((match) => `${match.title} (${match.score.toFixed(2)})`).join(" · ") || "Sin coincidencias."} /> : null}
      </Card>
      {pendingApproval ? (
        <ConfirmDangerModal
          open
          title={`Aprobar ${pendingApproval.label}`}
          consequences={[
            "El agente podrá usar este contenido para responder a los contactos.",
            "La versión aprobada anterior quedará reemplazada.",
            "La aprobación queda registrada con tu usuario.",
          ]}
          confirmationWord={CONFIRMATION_WORD}
          dangerLabel="Aprobar contenido"
          confirming={saving}
          onCancel={() => setPendingApproval(null)}
          onConfirm={() => void approveRevision()}
        />
      ) : null}
    </Flex>
  );
}
