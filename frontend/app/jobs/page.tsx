"use client";

import { Alert, Button, Card, Empty, Flex, Input, List, Skeleton, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
  getBackgroundJobs,
  problemMessage,
  retryBackgroundJob,
  type BackgroundJob,
  type Problem,
} from "@/lib/api";

export default function JobsPage() {
  const { session } = useAuth();
  const [jobs, setJobs] = useState<BackgroundJob[]>([]);
  const [reason, setReason] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  function refresh() {
    setLoading(true);
    void getBackgroundJobs()
      .then((response) => setJobs(response.data))
      .catch(setError)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    void getBackgroundJobs()
      .then((response) => setJobs(response.data))
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para ver tareas." }} />;
  if (error && !jobs.length && !loading) return <AuthError error={error} />;

  async function retry(job: BackgroundJob) {
    setBusy(job.id);
    setError(null);
    try {
      await retryBackgroundJob(job.id, reason[job.id] ?? "Se corrigió la causa del fallo y se reintentará la misma operación.");
      refresh();
    } catch (problem) {
      setError(problem);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Tareas</Typography.Title>
        <Typography.Paragraph type="secondary">
          Estado durable de trabajos del backend. Los errores se muestran redactados.
        </Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card>
        {loading ? <Skeleton active paragraph={{ rows: 8 }} /> : jobs.length ? (
          <List
            dataSource={jobs}
            renderItem={(job) => (
              <List.Item>
                <Flex vertical gap="small" style={{ width: "100%" }}>
                  <Flex justify="space-between" wrap gap="small">
                    <Typography.Text strong>{job.task_name}</Typography.Text>
                    <Tag>{job.state_label}</Tag>
                  </Flex>
                  <Typography.Text type="secondary">{job.entity_type} · {job.entity_id} · cola {job.queue}</Typography.Text>
                  {job.error ? <Alert type="warning" message={job.error} /> : null}
                  {job.entity_type === "OutboundMessage" && job.state === "FAILED" ? (
                    <Flex gap="small" wrap>
                      <Input
                        aria-label={`Motivo de reintento ${job.id}`}
                        placeholder="Motivo de reintento (mínimo 10 caracteres)"
                        value={reason[job.id] ?? ""}
                        onChange={(event) => setReason({ ...reason, [job.id]: event.target.value })}
                        style={{ minWidth: 260, flex: 1 }}
                      />
                      <Button loading={busy === job.id} onClick={() => void retry(job)}>Reintentar</Button>
                    </Flex>
                  ) : null}
                </Flex>
              </List.Item>
            )}
          />
        ) : <Empty description="No hay tareas registradas." />}
      </Card>
    </Flex>
  );
}
