export type Problem = {
  type?: string;
  title?: string;
  status?: number;
  code?: string;
  detail?: string;
  correlation_id?: string;
  field_errors?: Record<string, string | string[]>;
};

export type UserSession = {
  id: number;
  username: string;
  email: string;
  role: "ADMIN" | "VENDEDOR";
  workspace_id: string;
  workspace_name: string;
  capabilities: string[];
  session_expires_at: string;
  reauthentication_active: boolean;
};

export type DashboardCampaign = {
  id: string;
  name: string;
  state: string;
  state_label: string;
  discovery_state: string;
  discovery_state_label: string;
  delivery_mode: string;
};

export type CampaignDetail = DashboardCampaign & {
  approval_mode: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  location_text?: string;
  objective?: number;
  daily_limit?: number;
  message_interval_minutes?: number;
  status_reason?: string;
  categories?: Array<{ id: string; name: string; sort_order: number }>;
  zones?: Array<{ id: string; name: string; sort_order: number }>;
  attachments?: Array<{ catalog_id: string; name: string; version: number; position: number }>;
  metrics?: {
    enrollments: number;
    prospects: number;
    initial_messages: number;
    sent: number;
    review_ready: number;
    queued: number;
    errors: number;
  };
};

export type InboundMessage = {
  id: string;
  external_at: string;
  sender: string;
  subject: string;
  classification: string;
  classification_label: string;
  is_human: boolean;
  is_read: boolean;
  gmail_thread_id: string;
  campaign_id: string | null;
  body_preview: string;
};

export type InboundThread = {
  inbound: InboundMessage & { body_text: string };
  timeline: Array<{
    direction: "inbound" | "outbound";
    at: string;
    sender: string;
    body_text: string;
    classification: string;
  }>;
};

export type OutboundMessage = {
  id: string;
  created_at: string;
  recipient: string;
  subject: string;
  kind: string;
  kind_label: string;
  state: string;
  state_label: string;
  sent_at: string | null;
  simulated_at: string | null;
  campaign_id: string | null;
  body_text: string;
  approved_at?: string | null;
  error?: string | null;
};

export type Contact = {
  id: string;
  name: string;
  organization_name: string;
  preferred_email: string | null;
  status: string;
  last_interaction_at: string | null;
  open_task_count: number;
  next_follow_up_at: string | null;
};

export type ContactEmail = {
  id: string;
  original_email: string;
  label: string;
  is_preferred: boolean;
  validity: string;
  validated_at: string | null;
  invalid_reason: string;
  active_restriction_count: number;
};

export type ContactRestriction = {
  id: string;
  scope: string;
  kind: string;
  evidence: string;
  revoked_at: string | null;
  created_at: string;
};

export type ContactTimeline = {
  subject: string;
  first_at: string;
  last_at: string;
  automation_label: string;
  open_task_count: number;
  items: Array<{
    direction: string;
    happened_at: string;
    sender: string;
    recipient: string;
    subject: string;
    body: string;
    outcome: string;
    simulated: boolean;
    needs_attention: boolean;
  }>;
};

export type ContactDetail = Contact & {
  organization_id: string;
  emails: ContactEmail[];
  restrictions: ContactRestriction[];
  timelines: ContactTimeline[];
};

export type Catalog = {
  id: string;
  name: string;
  version: number;
  original_filename: string;
  detected_mime: string;
  byte_size: number;
  sha256: string;
  active: boolean;
  missing: boolean;
  created_at: string;
};

export type BusinessProfile = {
  company_name: string;
  salesperson_name: string;
  phone: string;
  whatsapp: string;
  description: string;
  products: string;
  differentiators: string;
  address: string;
  website: string;
  signature: string;
  additional_instructions: string;
  relevance_threshold: number;
  profile_version: number;
};

export type ManagedUser = {
  id: number;
  username: string;
  email: string;
  role: "ADMIN" | "VENDEDOR";
  is_active: boolean;
  date_joined: string;
};

export type CreatedUser = {
  user: ManagedUser;
  activation_url: string;
  expires_at: string;
};

export type IntegrationStatus = {
  extractor: { provider: string; overture_min_confidence: string };
  website_fetcher: { provider: string };
  llm: { provider: string; model: string; credential_source: string; configured: boolean };
  embeddings: { provider: string; model: string; dimensions: number };
  gmail: {
    provider: string;
    oauth_client_id_configured: boolean;
    credential_source: string;
    credential_configured: boolean;
    connection_status: string;
    email: string | null;
  };
  revision: number;
};

export type SearchCategory = {
  id: string;
  name: string;
  sort_order: number;
  rules_revision: number;
  rules: Array<{
    id: string;
    taxonomy_code: string;
    name_terms: string[];
    active: boolean;
    sort_order: number;
  }>;
};

export type SearchZone = {
  id: string;
  name: string;
  official_code: string;
  level: string;
  province_code: string;
  province_name: string;
  parent_id: string | null;
  selectable: boolean;
  location_text: string;
  boundary_revision: number;
  boundary_hash: string;
};

export type GmailConnection = {
  connected: boolean;
  status: string;
  email: string | null;
  scopes: string[];
  last_tested_at: string | null;
  error: string | null;
};

export type MessageTemplate = {
  id: string;
  kind: "INITIAL" | "REMINDER" | "REFERRED_PROPOSAL";
  subject: string;
  body: string;
  revision: number;
  content_hash: string;
  approved_at: string;
  active: boolean;
};

export type PromptConfiguration = {
  email_drafting_prompt: string;
  automatic_reply_prompt: string;
  revision: number;
};

export type AutomationConfiguration = {
  mode: "OFF" | "SHADOW" | "LIVE";
  mode_label: string;
  policy_version: string;
  live_enabled_at: string | null;
  live_enabled_by: string | null;
};

export type DashboardMetrics = {
  unique_initial_recipients: number;
  initial_messages_sent: number;
  reminders_sent: number;
  automatic_replies_sent: number;
  scheduled_contacts_sent: number;
  unique_human_responders: number;
  response_rate: number | null;
  positive_response_rate: number | null;
  contacts_created: number;
  bounce_rate: number | null;
  unsubscribe_rate: number | null;
  automatically_resolved: number;
  human_required: number;
  open_human_tasks: number;
  median_first_response_seconds: number | null;
  median_human_intervention_seconds: number | null;
  responses_after_initial: number;
  responses_after_reminder: number;
};

export type DashboardSummary = {
  metrics: DashboardMetrics;
  campaigns: DashboardCampaign[];
  summary: {
    campaigns: number;
    catalogs: number;
    categories: number;
    zones: number;
    responses: number;
  };
  attention: {
    open_human_tasks: number;
    paused_campaigns: number;
  };
  safety: {
    send_mode: string;
    send_kill_switch: boolean;
    auto_reply_kill_switch: boolean;
    relationship_kill_switch: boolean;
  };
  admin?: {
    profile_configured: boolean;
    gmail_connected: boolean;
    problem_jobs: number;
    prospects: number;
    sent_messages: number;
  };
};

type ApiEnvelope<T> = { data: T };
type ApiPage<T> = ApiEnvelope<T> & {
  meta: { page: number; page_size: number; total: number };
};

let csrfToken: string | null = null;

export type VersionedResource<T> = {
  data: T;
  etag: string | null;
};

function correlationId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function responseError(response: Response): Promise<Problem> {
  try {
    return (await response.json()) as Problem;
  } catch {
    return {
      status: response.status,
      code: "invalid_response",
      detail: "No fue posible interpretar la respuesta del servidor.",
    };
  }
}

export async function getCsrfToken(): Promise<string> {
  const response = await fetch("/api/v1/auth/csrf/", {
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  const body = (await response.json()) as ApiEnvelope<{ csrf_token: string }>;
  csrfToken = body.data.csrf_token;
  return csrfToken;
}

async function requestEnvelope<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApiEnvelope<T> & Partial<Pick<ApiPage<T>, "meta">>> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("X-Correlation-ID", correlationId());
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRFToken", csrfToken ?? (await getCsrfToken()));
  }
  const response = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    const problem = await responseError(response);
    if (response.status === 401) {
      csrfToken = null;
    }
    throw problem;
  }
  if (response.status === 204) {
    return { data: undefined as T };
  }
  return (await response.json()) as ApiEnvelope<T> & Partial<Pick<ApiPage<T>, "meta">>;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await requestEnvelope<T>(path, init)).data;
}

async function requestVersioned<T>(path: string, init: RequestInit = {}): Promise<VersionedResource<T>> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("X-Correlation-ID", correlationId());
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRFToken", csrfToken ?? (await getCsrfToken()));
  }
  const response = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) throw await responseError(response);
  const body = (await response.json()) as ApiEnvelope<T>;
  const etag = response.headers.get("ETag");
  return { data: body.data, etag };
}

export function getSession(): Promise<UserSession> {
  return request<UserSession>("/api/v1/auth/session/");
}

export function getDashboardSummary(campaignId?: string): Promise<DashboardSummary> {
  const query = campaignId ? `?campaign_id=${encodeURIComponent(campaignId)}` : "";
  return request<DashboardSummary>(`/api/v1/dashboard/summary/${query}`);
}

export function getCampaigns(): Promise<ApiPage<DashboardCampaign[]>> {
  return requestEnvelope<DashboardCampaign[]>("/api/v1/campaigns/") as Promise<
    ApiPage<DashboardCampaign[]>
  >;
}

export function getCampaign(id: string): Promise<CampaignDetail> {
  return request<CampaignDetail>(`/api/v1/campaigns/${encodeURIComponent(id)}/`);
}

export type CampaignAction =
  | "start-discovery"
  | "approve"
  | "start-approved"
  | "pause"
  | "resume"
  | "cancel";

export function runCampaignAction(
  id: string,
  action: CampaignAction,
  reason = "",
): Promise<CampaignDetail> {
  const idempotencyKey =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return request<CampaignDetail>(
    `/api/v1/campaigns/${encodeURIComponent(id)}/actions/${action}/`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(reason ? { reason } : {}),
    },
  );
}

export function getInboundMessages(): Promise<ApiPage<InboundMessage[]>> {
  return requestEnvelope<InboundMessage[]>("/api/v1/inbound-messages/") as Promise<
    ApiPage<InboundMessage[]>
  >;
}

export function getInboundThread(id: string): Promise<InboundThread> {
  return request<InboundThread>(`/api/v1/inbound-messages/${encodeURIComponent(id)}/thread/`);
}

export function sendManualReply(
  inboundId: string,
  bodyText: string,
  idempotencyKey: string,
): Promise<{ created: boolean; message: OutboundMessage }> {
  return request<{ created: boolean; message: OutboundMessage }>(
    `/api/v1/inbound-messages/${encodeURIComponent(inboundId)}/manual-reply/`,
    {
      method: "POST",
      body: JSON.stringify({ body_text: bodyText, idempotency_key: idempotencyKey }),
    },
  );
}

export function getOutboundMessages(): Promise<ApiPage<OutboundMessage[]>> {
  return requestEnvelope<OutboundMessage[]>("/api/v1/outbound-messages/") as Promise<
    ApiPage<OutboundMessage[]>
  >;
}

export function getOutboundMessage(id: string): Promise<OutboundMessage> {
  return request<OutboundMessage>(`/api/v1/outbound-messages/${encodeURIComponent(id)}/`);
}

export function getContacts(): Promise<ApiPage<Contact[]>> {
  return requestEnvelope<Contact[]>("/api/v1/contacts/") as Promise<ApiPage<Contact[]>>;
}

export function getContact(id: string): Promise<ContactDetail> {
  return request<ContactDetail>(`/api/v1/contacts/${encodeURIComponent(id)}/`);
}

export function createContact(input: {
  email: string;
  organization_name?: string;
  contact_name?: string;
}): Promise<Contact> {
  return request<Contact>("/api/v1/contacts/", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateContact(id: string, name: string): Promise<Contact> {
  return request<Contact>(`/api/v1/contacts/${encodeURIComponent(id)}/`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export function getCatalogs(): Promise<Catalog[]> {
  return request<Catalog[]>("/api/v1/catalogs/");
}

export function uploadCatalog(name: string, file: File): Promise<Catalog> {
  const formData = new FormData();
  formData.set("name", name);
  formData.set("file", file);
  return request<Catalog>("/api/v1/catalogs/", {
    method: "POST",
    body: formData,
  });
}

export function getBusinessProfile(): Promise<BusinessProfile | null> {
  return request<BusinessProfile | null>("/api/v1/workspace/profile/");
}

export function getBusinessProfileVersioned(): Promise<VersionedResource<BusinessProfile | null>> {
  return requestVersioned<BusinessProfile | null>("/api/v1/workspace/profile/");
}

export function updateBusinessProfile(
  values: Partial<BusinessProfile>,
  etag?: string,
): Promise<BusinessProfile> {
  return request<BusinessProfile>("/api/v1/workspace/profile/", {
    method: "PATCH",
    headers: etag ? { "If-Match": etag } : undefined,
    body: JSON.stringify(values),
  });
}

export function getUsers(): Promise<ManagedUser[]> {
  return request<ManagedUser[]>("/api/v1/users/");
}

export function createUser(input: {
  username: string;
  email: string;
  role: "ADMIN" | "VENDEDOR";
}): Promise<CreatedUser> {
  return request<CreatedUser>("/api/v1/users/", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateUserRole(id: number, role: "ADMIN" | "VENDEDOR"): Promise<ManagedUser> {
  return request<ManagedUser>(`/api/v1/users/${id}/role/`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export function updateUserStatus(id: number, isActive: boolean): Promise<ManagedUser> {
  return request<ManagedUser>(`/api/v1/users/${id}/status/`, {
    method: "PATCH",
    body: JSON.stringify({ is_active: isActive }),
  });
}

export function getIntegrationStatus(): Promise<IntegrationStatus> {
  return request<IntegrationStatus>("/api/v1/integrations/status/");
}

export function getGmailConnection(): Promise<GmailConnection> {
  return request<GmailConnection>("/api/v1/integrations/gmail/connection/");
}

export function startGmailOAuth(): Promise<{ authorization_url: string }> {
  return request<{ authorization_url: string }>("/api/v1/integrations/gmail/oauth/start/", {
    method: "POST",
    body: "{}",
  });
}

export function testGmailConnection(): Promise<{ status: string; email: string }> {
  return request<{ status: string; email: string }>("/api/v1/integrations/gmail/test/", {
    method: "POST",
    body: "{}",
  });
}

export function disconnectGmail(): Promise<GmailConnection> {
  return request<GmailConnection>("/api/v1/integrations/gmail/disconnect/", {
    method: "POST",
    body: "{}",
  });
}

export function getMessageTemplates(): Promise<MessageTemplate[]> {
  return request<MessageTemplate[]>("/api/v1/message-template-revisions/");
}

export function createMessageTemplate(input: {
  kind: MessageTemplate["kind"];
  subject: string;
  body: string;
}): Promise<MessageTemplate> {
  return request<MessageTemplate>("/api/v1/message-template-revisions/", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getPromptConfiguration(): Promise<PromptConfiguration> {
  return request<PromptConfiguration>("/api/v1/prompts/");
}

export function updatePromptConfiguration(emailDraftingPrompt: string): Promise<PromptConfiguration> {
  return request<PromptConfiguration>("/api/v1/prompts/", {
    method: "PATCH",
    body: JSON.stringify({ email_drafting_prompt: emailDraftingPrompt }),
  });
}

export function updateAutomaticReplyPrompt(prompt: string): Promise<{ automatic_reply_prompt: string }> {
  return request<{ automatic_reply_prompt: string }>("/api/v1/automation/writing-instructions/", {
    method: "PATCH",
    body: JSON.stringify({ automatic_reply_prompt: prompt }),
  });
}

export function getAutomationConfiguration(): Promise<AutomationConfiguration> {
  return request<AutomationConfiguration>("/api/v1/automation/configuration/");
}

export function updateAutomationMode(
  mode: "OFF" | "SHADOW",
): Promise<AutomationConfiguration> {
  return request<AutomationConfiguration>("/api/v1/automation/configuration/", {
    method: "PATCH",
    body: JSON.stringify({ mode }),
  });
}

export function setAutomationLive(action: "enable-live" | "disable-live"): Promise<AutomationConfiguration> {
  return request<AutomationConfiguration>(`/api/v1/automation/actions/${action}/`, {
    method: "POST",
    body: "{}",
  });
}

export function reauthenticate(password: string): Promise<{ reauthentication_active: boolean }> {
  return request<{ reauthentication_active: boolean }>("/api/v1/auth/reauthenticate/", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function getSearchCategories(): Promise<SearchCategory[]> {
  return request<SearchCategory[]>("/api/v1/search-categories/");
}

export function getSearchZones(level?: string): Promise<SearchZone[]> {
  const query = level ? `?level=${encodeURIComponent(level)}` : "";
  return request<SearchZone[]>(`/api/v1/search-zones/${query}`);
}

export function createCampaign(input: Record<string, unknown>): Promise<CampaignDetail> {
  return request<CampaignDetail>("/api/v1/campaigns/", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function login(username: string, password: string): Promise<UserSession> {
  return request<UserSession>("/api/v1/auth/login/", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function activate(
  token: string,
  password: string,
  passwordConfirmation: string,
): Promise<UserSession> {
  return request<UserSession>("/api/v1/auth/activate/", {
    method: "POST",
    body: JSON.stringify({
      token,
      password,
      password_confirmation: passwordConfirmation,
    }),
  });
}

export function logout(): Promise<void> {
  return request<void>("/api/v1/auth/logout/", { method: "POST", body: "{}" });
}

export function problemMessage(problem: Problem): string {
  return problem.detail ?? "No fue posible completar la solicitud.";
}
