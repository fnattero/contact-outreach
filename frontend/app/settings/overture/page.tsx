"use client";

import { Alert, Card, Flex, Table } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import { getOvertureStatus, problemMessage, type OvertureStatus, type Problem } from "@/lib/api";

const dateFormatter = new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeZone: "America/Argentina/Buenos_Aires" });

export default function OvertureSettingsPage() {
  const { session } = useAuth();
  const [status, setStatus] = useState<OvertureStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  function refresh() { setLoading(true); setError(null); void getOvertureStatus().then(setStatus).catch(setError).finally(() => setLoading(false)); }
  useEffect(() => { void getOvertureStatus().then(setStatus).catch(setError).finally(() => setLoading(false)); }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para ver Overture." }} />;
  if (loading) return <LoadingState layout="detail" />;
  if (error && !status) return <AuthError error={error} />;
  if (!status) return null;

  return <Flex vertical gap="large">
    <PageHeader title="Cobertura Overture" description="El worker de mantenimiento importa particiones verificadas. La API sólo muestra estado seguro y no expone proveedores ni credenciales." />
    {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
    <section className="overture-snapshot-state" aria-labelledby="overture-snapshot-title">
      <span className="type-micro">Estado de cobertura</span>
      <h2 className="type-title" id="overture-snapshot-title">{status.active_snapshot_id ? "Hay un snapshot activo" : "No hay un snapshot activo"}</h2>
      <StatusBadge label={status.active_snapshot_id ? "Disponible" : "Pendiente"} level={status.active_snapshot_id ? "success" : "warning"} />
      <p>Un snapshot es una versión verificada de los datos geográficos que el sistema usa para buscar lugares. Mientras no haya uno activo, las búsquedas no tienen cobertura confirmada.</p>
    </section>
    <Card title="Particiones importadas">
      {status.partitions.length ? <Table rowKey="id" dataSource={status.partitions} pagination={{ pageSize: 10, responsive: true }} columns={[
        { title: "Provincia", dataIndex: "province_name" },
        { title: "Estado", render: (_: unknown, partition) => <StatusBadge label={partition.error ? "Con error" : partition.is_active ? "Activa" : "Importada"} level={partition.error ? "danger" : partition.is_active ? "success" : "inactive"} /> },
        { title: "Lugares", dataIndex: "place_count", render: (value: number) => <span className="data-text">{value}</span> },
        { title: "Importada", dataIndex: "imported_at", render: (value: string | null) => value ? <time className="data-text" dateTime={value}>{dateFormatter.format(new Date(value))}</time> : "Pendiente" },
      ]} /> : <EmptyState headline="Todavía no hay particiones" explanation="Las particiones son los fragmentos por provincia de un snapshot verificado; cuando se importen, la cobertura aparecerá acá." actionLabel="Actualizar estado" onAction={refresh} />}
    </Card>
    <Card title="Detalles técnicos">
      <details className="integration-technical">
        <summary>Mostrar identificadores y controles de importación</summary>
        <dl>
          <dt>Snapshot activo</dt><dd>{status.active_snapshot_id ?? "Ninguno"}</dd>
          <dt>Último snapshot</dt><dd>{status.latest_snapshot_id ?? "Ninguno"}</dd>
          {status.partitions.map((partition) => <div key={partition.id} className="overture-technical-partition"><dt>{partition.province_name}</dt><dd>{partition.release_id} · {partition.province_code}{partition.error ? ` · ${partition.error}` : ""}</dd></div>)}
          {status.release_checks.map((check, index) => <div key={`${check.latest_release}-${index}`}><dt>Control de release</dt><dd>{check.status} · {check.latest_release} · {check.created_at}</dd></div>)}
        </dl>
      </details>
    </Card>
  </Flex>;
}
