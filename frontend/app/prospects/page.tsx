"use client";

import { Alert, Button, Card, Flex, Form, Input, Modal, Select, Table } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { DisabledReason } from "@/components/design-system/disabled-reason";
import { PageHeader } from "@/components/design-system/page-header";
import { AUDIENCE_TABS } from "@/components/design-system/tabs-config";
import { SectionTabs } from "@/components/design-system/section-tabs";
import { EmptyState, LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import { can,
  getCampaigns,
  getProspects,
  problemMessage,
  prospectsExportUrl,
  restoreProspect,
  type DashboardCampaign,
  type Problem,
  type Prospect,
} from "@/lib/api";
import { ProvenanceDetails } from "./provenance-details";
import {
  PIPELINE_STATE_OPTIONS,
  collectAttribution,
  filtersFromForm,
  hasActiveFilters,
  restoreBlockedReason,
  stateLabel,
  stateLevel,
  VERDICT_OPTIONS,
  verdictLabel,
  verdictLevel,
  type FilterFormValues,
} from "./prospect-helpers";

const PAGE_SIZE = 25;
const dateFormatter = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "medium",
  timeZone: "America/Argentina/Buenos_Aires",
});

export default function ProspectsPage() {
  const { session } = useAuth();
  const [form] = Form.useForm<FilterFormValues>();
  const [applied, setApplied] = useState<FilterFormValues>({});
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<Prospect[]>([]);
  const [total, setTotal] = useState(0);
  const [campaigns, setCampaigns] = useState<DashboardCampaign[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [restoring, setRestoring] = useState<Prospect | null>(null);
  const [restoreBusy, setRestoreBusy] = useState(false);

  const isAdmin = can(session, "manage_campaigns");

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    void getCampaigns()
      .then((response) => { if (!cancelled) setCampaigns(response.data); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [isAdmin]);

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    void getProspects(filtersFromForm(applied, page, PAGE_SIZE))
      .then((response) => {
        if (cancelled) return;
        setRows(response.data);
        setTotal(response.meta.total);
        setError(null);
      })
      .catch((problem) => { if (!cancelled) setError(problem); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [isAdmin, applied, page, reload]);

  if (!isAdmin) return <AuthError error={{ detail: "No tenés permisos para ver la audiencia." }} />;
  if (error && !rows.length && !loading) return <AuthError error={error} />;

  async function confirmRestore() {
    if (!restoring) return;
    setRestoreBusy(true);
    try {
      await restoreProspect(restoring.id);
      setRestoring(null);
      setLoading(true);
      setReload((current) => current + 1);
    } catch (problem) {
      setRestoring(null);
      setError(problem);
    } finally {
      setRestoreBusy(false);
    }
  }

  function apply(values: FilterFormValues) {
    setLoading(true);
    setApplied(values);
    setPage(1);
  }

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Audiencia"
        description="Los negocios que encontró la búsqueda, el correo elegido de cada uno y si ya están listos para recibir la propuesta."
        tabs={<SectionTabs label="Audiencia" tabs={AUDIENCE_TABS} />}
        primaryAction={<Button href={prospectsExportUrl(filtersFromForm(applied, 1, PAGE_SIZE))}>Exportar tabla</Button>}
      />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Card>
        <Form form={form} layout="vertical" onFinish={apply} role="search" aria-label="Filtrar audiencia">
          <div className="filters__grid">
            <Form.Item name="q" label="Buscar prospecto"><Input allowClear placeholder="Nombre, correo o dirección" /></Form.Item>
            <Form.Item name="campaign" label="Campaña">
              <Select allowClear placeholder="Todas" options={campaigns.map((campaign) => ({ value: campaign.id, label: campaign.name }))} />
            </Form.Item>
            <Form.Item name="state" label="Estado">
              <Select allowClear placeholder="Todos" options={PIPELINE_STATE_OPTIONS.map(({ value, label }) => ({ value, label }))} />
            </Form.Item>
            <Form.Item name="verdict" label="Evaluación del filtro">
              <Select allowClear placeholder="Todas" options={VERDICT_OPTIONS.map(({ value, label }) => ({ value, label }))} />
            </Form.Item>
            <Form.Item name="category" label="Rubro"><Input allowClear /></Form.Item>
            <Form.Item name="neighborhood" label="Zona"><Input allowClear /></Form.Item>
          </div>
          <Flex gap="small">
            <Button type="primary" htmlType="submit">Aplicar filtros</Button>
            <Button onClick={() => { form.resetFields(); apply({}); }}>Limpiar</Button>
          </Flex>
        </Form>
      </Card>
      <Card>
        {loading ? (
          <LoadingState layout="list" />
        ) : rows.length || hasActiveFilters(applied) ? (
          <>
            <p className="muted">{total} {total === 1 ? "resultado" : "resultados"}.</p>
            <Table<Prospect>
              rowKey="id"
              dataSource={rows}
              scroll={{ x: 900 }}
              expandable={{
                expandedRowRender: (row) => (
                  <>
                    {row.relevance_verdict ? (
                      <section className="prospect-verdict" aria-label="Evaluación del filtro">
                        <h4 className="type-title">Por qué se evaluó así</h4>
                        <p>{row.relevance_reason}</p>
                        {row.relevance_checked_at ? (
                          <time className="data-text" dateTime={row.relevance_checked_at}>
                            {dateFormatter.format(new Date(row.relevance_checked_at))}
                          </time>
                        ) : null}
                        {row.relevance_override ? (
                          <p className="muted">Lo recuperaste a mano. El filtro no lo va a descartar de nuevo.</p>
                        ) : null}
                      </section>
                    ) : null}
                    <ProvenanceDetails provenance={row.provenance} />
                  </>
                ),
                rowExpandable: () => true,
              }}
              pagination={{ current: page, pageSize: PAGE_SIZE, total, showSizeChanger: false, onChange: (next) => { setLoading(true); setPage(next); } }}
              locale={{ emptyText: "Ningún prospecto coincide con los filtros." }}
              columns={[
                {
                  title: "Negocio",
                  dataIndex: "name",
                  render: (name: string, row) => <><strong>{name}</strong><br /><span className="data-text">{row.primary_email ?? "Sin correo seleccionado"}</span></>,
                },
                { title: "Campaña", dataIndex: "campaign", render: (campaign: Prospect["campaign"]) => <Link href={`/campaigns/${campaign.id}`}>{campaign.name}</Link> },
                { title: "Rubro / zona", render: (_: unknown, row) => [row.category, row.neighborhood].filter(Boolean).join(" · ") || "—" },
                {
                  title: "Estado",
                  dataIndex: "pipeline_state",
                  render: (state: string, row) => <StatusBadge label={stateLabel(state, row.pipeline_state_label)} level={stateLevel(state)} />,
                },
                {
                  title: "Evaluación",
                  dataIndex: "relevance_verdict",
                  render: (verdict: string | null) =>
                    verdict ? <StatusBadge label={verdictLabel(verdict)} level={verdictLevel(verdict)} /> : "—",
                },
                {
                  title: "Evaluación anterior",
                  dataIndex: "historical_score",
                  align: "right",
                  render: (score: number | null) => (score === null ? "—" : score),
                },
                { title: "Alta", dataIndex: "created_at", render: (value: string) => <time className="data-text" dateTime={value}>{dateFormatter.format(new Date(value))}</time> },
                {
                  title: "Acciones",
                  render: (_: unknown, row) => {
                    if (row.pipeline_state !== "SKIPPED_IRRELEVANT") return null;
                    const blocked = restoreBlockedReason(row.campaign_state);
                    return (
                      <DisabledReason disabled={blocked !== null} reason={blocked ?? ""}>
                        <Button size="small" onClick={() => setRestoring(row)}>Recuperar</Button>
                      </DisabledReason>
                    );
                  },
                },
              ]}
            />
            {collectAttribution(rows).length ? (
              <footer className="provenance-attribution" aria-label="Atribución de datos">
                {collectAttribution(rows).map((line) => <p key={line} className="muted">{line}</p>)}
              </footer>
            ) : null}
          </>
        ) : (
          <EmptyState headline="Todavía no hay prospectos" explanation="Los negocios aparecen acá cuando una campaña termina su búsqueda." actionLabel="Ir a campañas" actionHref="/campaigns" />
        )}
      </Card>
      <Modal
        open={restoring !== null}
        title="Recuperar este negocio"
        okText="Recuperar"
        cancelText="Cancelar"
        confirmLoading={restoreBusy}
        onOk={() => void confirmRestore()}
        onCancel={() => setRestoring(null)}
      >
        <p>Vuelve a la audiencia de la campaña. El filtro no lo va a descartar de nuevo, aunque cambies el criterio más adelante.</p>
      </Modal>
    </Flex>
  );
}
