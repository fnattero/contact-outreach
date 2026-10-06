import type { SemanticLevel } from "@/components/design-system/status-badge";
import type { ProspectFilters } from "@/lib/api";

/** Every pipeline state the backend can report, with the label shown to the team. */
export const PIPELINE_STATE_OPTIONS: ReadonlyArray<{ value: string; label: string; level: SemanticLevel }> = [
  { value: "DISCOVERED", label: "Descubierto", level: "info" },
  { value: "EMAIL_FOUND", label: "Correo encontrado", level: "info" },
  { value: "ENRICHED", label: "Enriquecido", level: "info" },
  { value: "ANALYZED", label: "Analizado", level: "info" },
  { value: "QUEUED", label: "En cola", level: "success" },
  { value: "SKIPPED_NO_EMAIL", label: "Sin correo", level: "inactive" },
  { value: "SKIPPED_DUPLICATE", label: "Duplicado", level: "inactive" },
  { value: "SKIPPED_IRRELEVANT", label: "Descartado por el filtro", level: "inactive" },
  { value: "ERROR", label: "Error", level: "danger" },
];

const byValue = new Map(PIPELINE_STATE_OPTIONS.map((option) => [option.value, option]));

export function stateLabel(state: string, fallback: string): string {
  return byValue.get(state)?.label ?? fallback;
}

export function stateLevel(state: string): SemanticLevel {
  return byValue.get(state)?.level ?? "inactive";
}

/** What the audience filter concluded about a business, in the words shown to the team. */
export const VERDICT_OPTIONS: ReadonlyArray<{ value: string; label: string; level: SemanticLevel }> = [
  { value: "FIT", label: "Encaja", level: "success" },
  { value: "UNCLEAR", label: "Dudoso", level: "warning" },
  { value: "UNFIT", label: "No encaja", level: "danger" },
];

const verdictByValue = new Map(VERDICT_OPTIONS.map((option) => [option.value, option]));

export function verdictLabel(verdict: string): string {
  return verdictByValue.get(verdict)?.label ?? "Sin evaluar";
}

export function verdictLevel(verdict: string): SemanticLevel {
  return verdictByValue.get(verdict)?.level ?? "inactive";
}

/**
 * A removed business can come back while the campaign is searching or waiting for approval.
 * Once sending starts the audience is frozen. Returns the reason, or null when it is allowed.
 */
export function restoreBlockedReason(campaignState: string): string | null {
  return campaignState === "DISCOVERING" || campaignState === "AWAITING_APPROVAL"
    ? null
    : "La campaña ya empezó a enviar y no admite cambios en la audiencia.";
}

const CONTACT_SOURCE_LABELS: Record<string, string> = {
  overture: "Overture Places",
  website_mailto: "Sitio web, enlace directo de correo",
  website_visible_text: "Sitio web, texto visible",
};

export function contactSourceLabel(source: string): string {
  return CONTACT_SOURCE_LABELS[source] ?? source;
}

/** Every distinct attribution line shown by the rows, in first-seen order. */
export function collectAttribution(rows: ReadonlyArray<{ provenance: { attribution: string[] } | null }>): string[] {
  return [...new Set(rows.flatMap((row) => row.provenance?.attribution ?? []))];
}

export type FilterFormValues = {
  q?: string;
  campaign?: string;
  state?: string;
  category?: string;
  neighborhood?: string;
  verdict?: string;
};

/** Drop blanks so the URL only carries filters the user actually set. */
export function filtersFromForm(values: FilterFormValues, page: number, pageSize: number): ProspectFilters {
  const trimmed = (value: string | undefined) => value?.trim() || undefined;
  return {
    q: trimmed(values.q),
    campaign: values.campaign || undefined,
    state: values.state || undefined,
    category: trimmed(values.category),
    neighborhood: trimmed(values.neighborhood),
    verdict: values.verdict || undefined,
    page,
    page_size: pageSize,
  };
}

export function hasActiveFilters(values: FilterFormValues): boolean {
  return Boolean(values.q?.trim() || values.campaign || values.state || values.category?.trim() || values.neighborhood?.trim() || values.verdict);
}
