"use client";

import { Alert, Button, Card, Empty, Flex, Input, List, Skeleton, Tag, Typography } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { getAttention, problemMessage, resolveHumanTask, type AttentionTask, type Problem } from "@/lib/api";

export default function AttentionPage() {
  const { session } = useAuth();
  const [tasks, setTasks] = useState<AttentionTask[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { void getAttention().then(setTasks).catch(setError); }, []);
  if (!session) return <AuthError error={{ detail: "Iniciá sesión para continuar." }} />;
  if (error) return <AuthError error={error} />;
  if (!tasks) return <Skeleton active paragraph={{ rows: 8 }} />;
  const currentTasks = tasks;
  async function close(id: string, action: "resolve" | "dismiss") {
    setBusy(id); setError(null);
    try { await resolveHumanTask(id, action, note[id] ?? "Revisado por el equipo."); setTasks(currentTasks.filter((task) => task.id !== id)); }
    catch (problem) { setError(problem); } finally { setBusy(null); }
  }
  return <Flex vertical gap="large">
    <div><Typography.Title level={2}>Atención</Typography.Title><Typography.Paragraph type="secondary">Revisiones humanas que suspenden automatizaciones hasta una decisión explícita.</Typography.Paragraph></div>
    {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
    <Card>{tasks.length ? <List dataSource={tasks} renderItem={(task) => <List.Item><Flex vertical gap="small" style={{ width: "100%" }}><Flex justify="space-between" wrap gap="small"><Typography.Text strong>{task.title}</Typography.Text><Tag color="warning">Pendiente</Tag></Flex><Link href={`/contacts/${task.contact_id}`}>{task.contact_name}</Link><Typography.Text>{task.summary}</Typography.Text><Typography.Text type="secondary">Siguiente paso: {task.next_step || "Revisar la conversación."}</Typography.Text><Input.TextArea placeholder="Nota de resolución" value={note[task.id] ?? ""} onChange={(event) => setNote({ ...note, [task.id]: event.target.value })} rows={2} /><Flex gap="small" wrap><Button type="primary" onClick={() => void close(task.id, "resolve")} loading={busy === task.id}>Resolver</Button><Button onClick={() => void close(task.id, "dismiss")} loading={busy === task.id}>Descartar</Button></Flex></Flex></List.Item>} /> : <Empty description="No hay revisiones abiertas." />}</Card>
  </Flex>;
}
