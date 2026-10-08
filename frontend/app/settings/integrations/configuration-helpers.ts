import type { IntegrationConfiguration, IntegrationConfigurationPatch } from "@/lib/api";

export type ConfigurationFormValues = Omit<IntegrationConfiguration, "llm_credential" | "relevance_llm_credential" | "gmail_credential" | "revision"> & {
  llm_api_key?: string;
  remove_llm_api_key?: boolean;
  relevance_llm_api_key?: string;
  remove_relevance_llm_api_key?: boolean;
  gmail_oauth_client_secret?: string;
  remove_gmail_oauth_client_secret?: boolean;
};

const PLAIN_FIELDS = [
  "extractor_provider", "overture_min_confidence", "website_fetcher", "llm_provider", "llm_model", "relevance_llm_model", "relevance_llm_provider", "relevance_llm_base_url",
  "ollama_base_url", "openai_compatible_base_url", "embedding_provider", "embedding_model",
  "embedding_dimensions", "gmail_provider", "gmail_oauth_client_id",
] as const;

/**
 * Only what the user changed. Settings that did not change are left out so the server keeps them,
 * a blank secret means "keep the current one", and a removal flag is sent only when ticked.
 */
export function buildConfigurationPatch(
  values: ConfigurationFormValues,
  current: IntegrationConfiguration,
): IntegrationConfigurationPatch {
  const patch: Record<string, unknown> = {};
  for (const field of PLAIN_FIELDS) {
    if (values[field] !== undefined && values[field] !== current[field]) patch[field] = values[field];
  }
  const llmKey = values.llm_api_key?.trim();
  if (llmKey) patch.llm_api_key = llmKey;
  if (values.remove_llm_api_key) patch.remove_llm_api_key = true;
  const relevanceKey = values.relevance_llm_api_key?.trim();
  if (relevanceKey) patch.relevance_llm_api_key = relevanceKey;
  if (values.remove_relevance_llm_api_key) patch.remove_relevance_llm_api_key = true;
  const gmailSecret = values.gmail_oauth_client_secret?.trim();
  if (gmailSecret) patch.gmail_oauth_client_secret = gmailSecret;
  if (values.remove_gmail_oauth_client_secret) patch.remove_gmail_oauth_client_secret = true;
  return patch as IntegrationConfigurationPatch;
}

export function hasChanges(patch: IntegrationConfigurationPatch): boolean {
  return Object.keys(patch).length > 0;
}

const SOURCE_LABELS: Record<string, string> = {
  ENVIRONMENT: "variables del entorno",
  ENCRYPTED: "almacenamiento cifrado",
  NONE: "sin origen configurado",
  SHARED: "la misma clave que las respuestas",
};

export function credentialStatus(state: { configured: boolean; source: string }): string {
  return `${state.configured ? "Configurada" : "No configurada"} (${SOURCE_LABELS[state.source] ?? "origen desconocido"})`;
}
