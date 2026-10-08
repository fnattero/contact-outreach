"use client";

import { Alert, Button, Card, Flex, Modal, Table } from "antd";
import { useCallback, useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { DisabledReason } from "@/components/design-system/disabled-reason";
import { PageHeader } from "@/components/design-system/page-header";
import { EmptyState, LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import {
  can,
  getOvertureStatus,
  problemMessage,
  syncOverture,
  type OvertureStatus,
  type Problem,
  type ProvinceCoverage,
} from "@/lib/api";
import { PROVINCE_STATES, provinceAction, withQueued } from "./province-helpers";

const dateFormatter = new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeZone: "America/Argentina/Buenos_Aires" });
const POLL_MS = 15_000;

export default function OvertureSettingsPage() {
  const { session } = useAuth();
  const allowed = can(session, "manage_integrations");
  const [status, setStatus] = useState<OvertureStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [queued, setQueued] = useState<ReadonlySet<string>>(new Set());
  const [target, setTarget] = useState<ProvinceCoverage | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(() => {
    void getOvertureStatus()
      .then((next) => {
        setStatus(next);
        setError(null);
        // Once the server reports a province, it no longer needs the local "queued" mark.
        setQueued((current) => {
          const pending = new Set([...current].filter((code) => next.provinces.find((item) => item.code === code)?.state === "MISSING"));
          return pending.size === current.size ? current : pending;
        });
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (allowed) load();
  }, [allowed, load]);

  const provinces = status ? withQueued(status.provinces, queued) : [];
  const anyLoading = provinces.some((province) => province.state === "IMPORTING");

  // While something loads, check again from time to time so the list follows the progress.
  useEffect(() => {
    if (!allowed || !anyLoading) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [allowed, anyLoading, load]);

  async function start() {
    if (!target) return;
    setStarting(true);
    setError(null);
    try {
      await syncOverture(target.code);
      setQueued((current) => new Set(current).add(target.code));
      setTarget(null);
    } catch (problem) {
      setError(problem);
      setTarget(null);
    } finally {
      setStarting(false);
    }
  }

  if (!allowed) return <AuthError error={{ detail: "No tenés permisos para ver las zonas con datos." }} />;
  if (loading) return <LoadingState layout="detail" />;
  if (error && !status) return <AuthError error={error} />;
  if (!status) return null;

  const hasVerifiedVersion = status.latest_verified_release !== null;
  const ready = provinces.filter((province) => province.state === "READY").length;

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Zonas con datos"
        description="Para buscar negocios en una provincia hay que cargar antes sus datos. Acá ves cuáles ya están y cargás las que falten."
      />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      {!hasVerifiedVersion ? (
        <Alert
          type="info"
          showIcon
          message="Todavía no hay una versión de datos para cargar"
          description="La app revisa sola, cada tanto, si hay datos nuevos. Cuando encuentre una versión verificada vas a poder cargar provincias desde acá."
        />
      ) : null}
      <Card title={`Provincias (${ready} de ${provinces.length} con datos)`}>
        {provinces.length ? (
          <Table<ProvinceCoverage>
            rowKey="code"
            dataSource={provinces}
            pagination={false}
            scroll={{ x: 640 }}
            columns={[
              { title: "Provincia", dataIndex: "name" },
              {
                title: "Estado",
                dataIndex: "state",
                render: (state: ProvinceCoverage["state"], row) => (
                  <>
                    <StatusBadge label={PROVINCE_STATES[state].label} level={PROVINCE_STATES[state].level} />
                    {state === "FAILED" && row.error ? <p className="muted">{row.error}</p> : null}
                  </>
                ),
              },
              {
                title: "Negocios",
                dataIndex: "place_count",
                align: "right",
                render: (count: number, row) => (row.state === "READY" ? <span className="data-text">{count}</span> : "—"),
              },
              {
                title: "Actualizado",
                dataIndex: "updated_at",
                render: (value: string | null, row) =>
                  value && row.state === "READY" ? <time className="data-text" dateTime={value}>{dateFormatter.format(new Date(value))}</time> : "—",
              },
              {
                title: "",
                render: (_: unknown, row) => {
                  const action = provinceAction(row.state, { hasVerifiedVersion, anotherLoading: anyLoading });
                  return (
                    <DisabledReason disabled={action.blockedReason !== null} reason={action.blockedReason ?? ""}>
                      <Button size="small" onClick={() => setTarget(row)}>{action.label}</Button>
                    </DisabledReason>
                  );
                },
              },
            ]}
          />
        ) : (
          <EmptyState headline="No hay provincias configuradas" explanation="Cuando haya provincias disponibles van a aparecer acá." actionLabel="Ir al Resumen" actionHref="/dashboard" />
        )}
      </Card>
      <Card title="Atribución y licencias">
        {status.attribution ? (
          <>
            <p>{status.attribution.attribution}</p>
            <p className="muted">Versión {status.attribution.release_id}</p>
            {status.attribution.licenses.length ? <ul>{status.attribution.licenses.map((license) => <li key={license}>{license}</li>)}</ul> : null}
            {status.attribution.notices.length ? <ul>{status.attribution.notices.map((notice) => <li key={notice}>{notice}</li>)}</ul> : null}
          </>
        ) : <p className="muted">La atribución aparecerá acá cuando haya datos cargados.</p>}
      </Card>
      <Card title="Detalles técnicos">
        <details className="integration-technical">
          <summary>Mostrar identificadores y controles de importación</summary>
          <dl>
            <dt>Versión verificada</dt><dd>{status.latest_verified_release ?? "Ninguna"}</dd>
            <dt>Snapshot activo</dt><dd>{status.active_snapshot_id ?? "Ninguno"}</dd>
            <dt>Último snapshot</dt><dd>{status.latest_snapshot_id ?? "Ninguno"}</dd>
            {status.release_checks.map((check, index) => <div key={`${check.latest_release}-${index}`}><dt>Control de versión</dt><dd>{check.status} · {check.latest_release} · {check.created_at}</dd></div>)}
          </dl>
        </details>
      </Card>
      <Modal
        open={target !== null}
        title={target ? `Cargar los datos de ${target.name}` : ""}
        okText="Cargar"
        cancelText="Cancelar"
        confirmLoading={starting}
        onOk={() => void start()}
        onCancel={() => setTarget(null)}
      >
        <p>Se descargan y procesan los negocios de la provincia. Puede tardar varios minutos.</p>
        <p>Mientras tanto podés seguir usando la app. Se carga una provincia por vez.</p>
      </Modal>
    </Flex>
  );
}
