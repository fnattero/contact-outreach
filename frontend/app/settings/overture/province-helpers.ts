import type { SemanticLevel } from "@/components/design-system/status-badge";
import type { ProvinceCoverage } from "@/lib/api";

type State = ProvinceCoverage["state"];

export const PROVINCE_STATES: Record<State, { label: string; level: SemanticLevel }> = {
  READY: { label: "Con datos", level: "success" },
  IMPORTING: { label: "Cargando…", level: "info" },
  FAILED: { label: "Falló la carga", level: "danger" },
  MISSING: { label: "Sin datos", level: "inactive" },
};

/** What the button of a province says, or why it cannot be pressed. */
export function provinceAction(
  state: State,
  options: { hasVerifiedVersion: boolean; anotherLoading: boolean },
): { label: string; blockedReason: string | null } {
  const label = state === "READY" ? "Actualizar datos" : state === "FAILED" ? "Reintentar" : "Cargar datos";
  if (state === "IMPORTING") return { label: "Cargando…", blockedReason: "Esta provincia se está cargando." };
  if (!options.hasVerifiedVersion) {
    return { label, blockedReason: "Todavía no hay una versión de datos verificada. Probá de nuevo en unos minutos." };
  }
  if (options.anotherLoading) {
    return { label, blockedReason: "Se está cargando otra provincia. Se carga de a una." };
  }
  return { label, blockedReason: null };
}

/** The provinces with the ones just queued shown as loading before the server reports them. */
export function withQueued(provinces: readonly ProvinceCoverage[], queued: ReadonlySet<string>): ProvinceCoverage[] {
  return provinces.map((province) =>
    queued.has(province.code) && province.state !== "READY" ? { ...province, state: "IMPORTING" as const } : province,
  );
}
