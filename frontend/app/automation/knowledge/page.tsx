"use client";

import { Alert, Button, Card, Collapse, Flex, Form, Input, List, Tag } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { PageHeader } from "@/components/design-system/page-header";
import { SectionTabs } from "@/components/design-system/section-tabs";
import { LoadingState } from "@/components/design-system/states";
import { AUTOMATION_TABS } from "@/components/design-system/tabs-config";
import {
  can,
  approveKnowledgeFact,
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
  const [pendingApproval, setPendingApproval] = useState<{ id: string; label: string } | null>(null);

  useEffect(() => {
    if (!allowed) return;
    void Promise.all([getKnowledgeFacts(), getKnowledgeContexts()])
      .then(([nextFacts, nextContexts]) => {
        setFacts(nextFacts);
        setContexts(nextContexts);
      })
      .catch(setError);
  }, [allowed]);

  const unusedContext = contexts.find((context) => context.state === "APPROVED") ?? null;

  if (!allowed) return <AuthError error={{ detail: "No tenés permisos para configurar automatización." }} />;
  if (error && !facts) return <AuthError error={error} />;
  if (!facts) return <LoadingState layout="form" />;

  async function saveFact(values: { title: string; category?: string; text: string }) {
    setSaving(true); setError(null);
    try { const created = await createKnowledgeFact(values); setFacts((current) => [created, ...(current ?? [])]); }
    catch (problem) { setError(problem); } finally { setSaving(false); }
  }

  async function approveRevision() {
    if (!pendingApproval) return;
    setSaving(true); setError(null);
    try {
      await approveKnowledgeFact(pendingApproval.id);
      setFacts(await getKnowledgeFacts());
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
      <Card title="Cómo usa la IA esta información">
        <ol className="knowledge-steps">
          <li>Llega un correo de un negocio.</li>
          <li>La app busca, entre los datos aprobados de abajo, los que más se parecen a lo que preguntó. Toma hasta 3.</li>
          <li>La IA recibe el mensaje, los anteriores del hilo, tus instrucciones de escritura y esos datos.</li>
          <li>Solo puede afirmar lo que dicen esos datos. Si ninguno alcanza, no contesta: te lo deja en “Necesita atención”.</li>
        </ol>
        <p className="muted">Por eso conviene un dato por tema, escrito como lo contestarías (un horario, un plazo de entrega, una garantía). No hace falta repetir cómo escribir: eso va en “Cómo escribe”.</p>
      </Card>
      {unusedContext ? (
        <Alert
          type="info"
          showIcon
          message="El contexto general dejó de usarse"
          description={
            <Collapse
              ghost
              items={[{ key: "text", label: "Ver el texto que tenías aprobado", children: <p className="muted">{unusedContext.context_text}</p> }]}
            />
          }
        />
      ) : null}
      <Card title="Datos que la IA puede usar">
        <p className="integration-muted">Lo que guardás queda como borrador. La IA solo usa información aprobada.</p>
        <Form layout="vertical" onFinish={(values) => void saveFact(values as { title: string; category?: string; text: string })}>
          <div className="form-grid">
            <Form.Item name="title" label="Título" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="category" label="Categoría"><Input /></Form.Item>
            <Form.Item className="form-grid__full" name="text" label="Información verificable" rules={[{ required: true }]}><Input.TextArea rows={4} maxLength={4000} showCount /></Form.Item>
          </div>
          <Button htmlType="submit" loading={saving}>Guardar borrador</Button>
        </Form>
        <List style={{ marginTop: 16 }} dataSource={facts} renderItem={(fact) => <List.Item actions={fact.state === "DRAFT" ? [<Button key="approve" size="small" onClick={() => setPendingApproval({ id: fact.id, label: `${fact.title} · v${fact.version}` })}>Aprobar</Button>] : undefined}><List.Item.Meta title={`${fact.title} · v${fact.version}`} description={fact.text} /><Tag color={revisionStateTag[fact.state].color}>{revisionStateTag[fact.state].label}</Tag></List.Item>} />
      </Card>
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
            "La IA podrá usar este contenido para responder a los contactos.",
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
