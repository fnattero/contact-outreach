"use client";

import { Button, Segmented, Table } from "antd";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge, type SemanticLevel } from "@/components/design-system";
import { useAuth } from "@/components/auth-provider";
import { getCampaigns, problemMessage, type DashboardCampaign, type Problem } from "@/lib/api";

const lifecycle = [
  { value: "DRAFT", label: "Borradores" },
  { value: "DISCOVERING", label: "Descubrimiento" },
  { value: "AWAITING_APPROVAL", label: "Aprobación" },
  { value: "RUNNING", label: "En curso" },
  { value: "PAUSED", label: "Pausadas" },
  { value: "COMPLETED", label: "Completadas" },
  { value: "STOPPED_ERROR", label: "Con error" },
] as const;

function campaignLevel(state: string): SemanticLevel {
  if (state === "RUNNING") return "success";
  if (state === "PAUSED") return "warning";
  if (state === "STOPPED_ERROR") return "inactive";
  if (state === "CANCELLED" || state === "COMPLETED") return "inactive";
  return "info";
}

function nextAction(state: string, isAdmin: boolean): string {
  if (!isAdmin) return "Consultar detalle";
  switch (state) {
    case "DRAFT": return "Iniciar descubrimiento";
    case "DISCOVERING": return "Esperar resultados";
    case "AWAITING_APPROVAL": return "Aprobar campaña";
    case "RUNNING": return "Pausar campaña";
    case "PAUSED": return "Reanudar campaña";
    default: return "Sin acciones disponibles";
  }
}

function progress(campaign: DashboardCampaign): string {
  const metrics = campaign.metrics;
  if (metrics && metrics.initial_messages > 0) return `${metrics.sent}/${metrics.initial_messages} mensajes`;
  if (metrics && metrics.enrollments > 0) return `${metrics.enrollments} destinatarios`;
  return campaign.discovery_state_label || "Sin datos de búsqueda";
}

export default function CampaignsPage() {
  const { session } = useAuth();
  const [campaigns, setCampaigns] = useState<DashboardCampaign[]>([]);
  const [selectedState, setSelectedState] = useState("ALL");
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void getCampaigns()
      .then((response) => { if (!cancelled) setCampaigns(response.data); })
      .catch((problem) => { if (!cancelled) setError(problem); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const counts = useMemo(() => Object.fromEntries(lifecycle.map(({ value }) => [value, campaigns.filter((campaign) => campaign.state === value).length])), [campaigns]);
  const filteredCampaigns = selectedState === "ALL" ? campaigns : campaigns.filter((campaign) => campaign.state === selectedState);
  const options = [
    { value: "ALL", label: `Todas (${campaigns.length})` },
    ...lifecycle.map(({ value, label }) => ({ value, label: `${label} (${counts[value] ?? 0})` })),
  ];

  if (loading) return <LoadingState layout="list" label="Cargando campañas" />;
  if (error) return <ErrorState failed="No se pudieron cargar las campañas" instruction={problemMessage(error as Problem)} onRetry={() => window.location.reload()} />;

  return (
    <>
      <PageHeader
        title="Campañas"
        description="Audiencias, aprobaciones y entregas de la empresa."
        primaryAction={session?.role === "ADMIN" ? <Link href="/campaigns/new"><Button type="primary">Nueva campaña</Button></Link> : undefined}
        filters={(
          <div className="campaign-state-filter">
            <Segmented
              aria-label="Filtrar campañas por estado"
              className="campaign-state-filter__control"
              options={options}
              value={selectedState}
              onChange={(value) => setSelectedState(String(value))}
            />
          </div>
        )}
      />
      {campaigns.length ? (
        <section className="campaign-table-section" aria-label="Campañas por etapa del ciclo de vida">
          <Table<DashboardCampaign>
            rowKey="id"
            dataSource={filteredCampaigns}
            pagination={false}
            scroll={{ x: 760 }}
            locale={{ emptyText: <span>No hay campañas en esta etapa.</span> }}
            columns={[
              { title: "Campaña", dataIndex: "name", render: (name: string, campaign) => <Link href={`/campaigns/${campaign.id}`}>{name}</Link> },
              { title: "Estado", dataIndex: "state_label", render: (label: string, campaign) => <StatusBadge label={label} level={campaignLevel(campaign.state)} /> },
              { title: "Audiencia", dataIndex: "metrics", render: (metrics: DashboardCampaign["metrics"]) => <span className={metrics ? "data-text" : "campaign-table__restricted"}>{metrics ? metrics.enrollments : "Solo administración"}</span> },
              { title: "Progreso", dataIndex: "discovery_state_label", render: (_label: string, campaign) => <span>{progress(campaign)}</span> },
              { title: "Próxima acción", dataIndex: "state", render: (state: string) => <span>{nextAction(state, session?.role === "ADMIN")}</span> },
              { title: "", key: "detail", render: (_value: unknown, campaign) => <Link href={`/campaigns/${campaign.id}`}>Ver detalle</Link> },
            ]}
          />
        </section>
      ) : (
        <EmptyState headline="Todavía no hay campañas" explanation="Creá la primera campaña para definir audiencia, contenido y calendario." actionLabel={session?.role === "ADMIN" ? "Crear campaña" : "Volver al resumen"} actionHref={session?.role === "ADMIN" ? "/campaigns/new" : "/dashboard"} />
      )}
    </>
  );
}
