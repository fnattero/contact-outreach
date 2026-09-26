# UI and API audit

Audit date: 2026-08-28

Primary design source: `docs/DESIGN.md` (read before code inspection)

Scope: the Next.js application in `frontend/`, the Django REST API under `/api/v1/`, and the still-addressable server-rendered Django UI.

## Conventions

- `PUBLIC` means no authenticated session is required.
- `ADMIN` and `VENDEDOR` are the only membership roles.
- `BOTH` means authenticated `ADMIN` or `VENDEDOR`.
- Every protected Next.js route also calls `GET /api/v1/auth/session/` through `AuthProvider`; signing out calls `POST /api/v1/auth/logout/`. Those two shell calls are omitted from individual route rows.
- The Next.js catch-all route `/api/v1/[...path]` proxies `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, and `OPTIONS` to the Django `/api/v1/*` endpoint. It strips internal/forwarded headers, adds the internal proxy token, and returns private/no-store responses.
- Normal JSON success responses use `{data: T}`. Paged responses add `meta: {page, page_size, total}`. Validation/auth failures use the repository's problem shape `{type?, title?, status?, code?, detail?, correlation_id?, field_errors?}`. This envelope is abbreviated below as `Data<T>` and `Page<T>`.
- UUIDs, timestamps, nullable values, and arrays are written in TypeScript-like notation for compactness. `enum(...)` identifies a closed enum. `literal(...)` identifies a closed action/status vocabulary implemented as strings rather than a model enum.

## 1. Route map

### Next.js routes

| Route | Reachable role | Page-specific API calls |
|---|---|---|
| `/` | BOTH | None. Protected by the shell even though the page itself is a welcome/link page. |
| `/login` | PUBLIC | `POST /auth/login/`; the provider then refreshes `/auth/session/`. |
| `/activate` | PUBLIC | `POST /auth/activate/`; the provider then refreshes `/auth/session/`. |
| `/dashboard` | BOTH | `GET /dashboard/summary/?campaign_id=...`. |
| `/campaigns` | BOTH; VENDEDOR never receives drafts | `GET /campaigns/`. |
| `/campaigns/new` | ADMIN; explicit page guard | `GET /search-categories/`, `GET /search-zones/?level=PROVINCE`, `GET /search-zones/`, `GET /catalogs/`, `POST /campaigns/`. |
| `/campaigns/[id]` | BOTH; draft is ADMIN-only | `GET /campaigns/{id}/`; ADMIN can `POST /campaigns/{id}/actions/{start-discovery|approve|start-approved|pause|resume|cancel}/`. |
| `/contacts` | BOTH | `GET /contacts/`. |
| `/contacts/new` | ADMIN; explicit page guard | `POST /contacts/`. |
| `/contacts/[id]` | BOTH | `GET /contacts/{id}/`; ADMIN also calls `GET/POST /contacts/{id}/communication-plans/`, `POST /contacts/{id}/emails/`, `PATCH .../preferred/`, `POST .../validate/`, `POST .../restrictions/`, and `POST .../restrictions/{restriction}/revoke/`. |
| `/responses` | BOTH | `GET /inbound-messages/`. |
| `/responses/[id]` | BOTH | `GET /inbound-messages/{id}/thread/`; a user with `send_replies` (ADMIN today) can `POST .../manual-reply/`; refresh repeats the GET. |
| `/attention` | BOTH | `GET /attention/`; the page exposes `POST /human-tasks/{id}/{resolve|dismiss}/` to both roles even though the API rejects VENDEDOR. |
| `/outbound` | BOTH; VENDEDOR receives `SENT` only | `GET /outbound-messages/`. |
| `/outbound/[id]` | BOTH; VENDEDOR can open `SENT` only | `GET /outbound-messages/{id}/`; ADMIN can `PATCH .../draft/` and `POST .../authorize/`. |
| `/catalogs` | ADMIN by hidden navigation and API permission only; no page guard | `GET/POST /catalogs/`; download link calls `GET /catalogs/{id}/download/`. |
| `/automation` | ADMIN; explicit page guard | `GET/PATCH /automation/configuration/`, `GET/PATCH /automation/writing-instructions/`, `POST /auth/reauthenticate/`, `POST /automation/actions/{enable-live|disable-live}/`, `GET/POST /knowledge/facts/`, `GET/POST /knowledge/global-context-revisions/`, `POST /knowledge/search-preview/`. |
| `/settings/profile` | ADMIN by hidden navigation and API permission only; no page guard | `GET/PATCH /workspace/profile/`. |
| `/settings/message-templates` | ADMIN; explicit page guard | `GET/POST /message-template-revisions/`. |
| `/settings/prompts` | ADMIN; explicit page guard | `GET/PATCH /prompts/`, `PATCH /automation/writing-instructions/`. |
| `/settings/categories` | ADMIN; explicit page guard | `GET/POST /search-categories/`, `PATCH /search-categories/{id}/rules/`. |
| `/settings/integrations` | ADMIN by hidden navigation and API permission only; no page guard | `GET /integrations/status/`, `GET /integrations/gmail/connection/`, `POST .../oauth/start/`, `POST .../test/`, `POST .../disconnect/`; OAuth returns through `GET .../oauth/callback/`. |
| `/settings/overture` | ADMIN; explicit page guard | `GET /overture/status/`. (`POST /overture/sync/` has a client function but no control on this page.) |
| `/settings/users` | ADMIN; explicit page guard | `GET/POST /users/`, `PATCH /users/{id}/role/`, `PATCH /users/{id}/status/`. |
| `/audit` | ADMIN; explicit page guard | `GET /audit-events/`. |
| `/jobs` | ADMIN; explicit page guard | `GET /background-jobs/`, `POST /background-jobs/{id}/retry/`; refresh repeats the GET. |
| `/api/v1/[...path]` | Same as the target Django API | Transparent same-origin proxy for the methods listed above. |

The sidebar is a second, independent reachability mechanism. BOTH roles see Resumen, Contactos, Campañas, Respuestas, Necesita atención, and Envíos. Only ADMIN sees Catálogos, all settings entries, Respuesta automática, Auditoría, and Jobs. Hiding a sidebar entry is not a route guard.

### Server-rendered Django routes

These routes remain directly addressable. They do **not** call `/api/v1/`; their views query models and call domain services directly. This means the repository currently has two UI surfaces and two HTTP-handling implementations for many operations.

| Route | Method/role | Direct behavior |
|---|---|---|
| `/login/` | GET/POST PUBLIC | Throttled Django login. |
| `/logout/` | authenticated | Django logout. |
| `/activar/{token}/` | GET/POST PUBLIC | Account activation/reset. |
| `/seguridad/` | GET BOTH | Account security/session page. |
| `/` | GET BOTH (`view_summary`) | Legacy dashboard. |
| `/usuarios/` | GET/POST ADMIN | List/create managed users. |
| `/usuarios/desbloquear/` | POST ADMIN | Clear login throttle. |
| `/usuarios/{id}/rol/` | POST ADMIN | Change role. |
| `/usuarios/{id}/estado/` | POST ADMIN | Activate/deactivate user. |
| `/usuarios/{id}/nuevo-enlace/` | POST ADMIN | Issue activation/reset link. |
| `/contactos/` | GET BOTH (`view_contacts`) | Contact list. |
| `/contactos/nuevo/` | GET/POST ADMIN | Manual contact creation. |
| `/contactos/{id}/` | GET BOTH (`view_contacts`) | Contact detail. |
| `/contactos/{id}/no-contactar/toggle/` | POST ADMIN | Toggle contact-wide manual restriction. |
| `/contactos/{id}/seguimiento/{topic}/aprobar/` | POST ADMIN | Approve a topic for a contact. |
| `/contactos/{id}/proximo-contacto/guardar/` | POST ADMIN | Save communication plan. |
| `/contactos/{id}/seguimiento/{plan}/estado/{state}/` | POST ADMIN | Change plan state. |
| `/contactos/{id}/proximo-contacto/posponer/` | POST ADMIN | Snooze plan. |
| `/contactos/{id}/proximo-contacto/{attempt}/borrador/` | POST ADMIN | Edit scheduled draft. |
| `/contactos/{id}/proximo-contacto/{attempt}/autorizar/` | POST ADMIN | Authorize scheduled send. |
| `/contactos/{id}/emails/agregar/` | POST ADMIN | Add email address. |
| `/contactos/{id}/emails/{email}/preferido/` | POST ADMIN | Select preferred address. |
| `/contactos/{id}/emails/{email}/validar/` | POST ADMIN | Queue validation. |
| `/contactos/{id}/no-contactar/` | POST ADMIN | Create contact restriction. |
| `/contactos/{id}/emails/{email}/no-usar/` | POST ADMIN | Create address restriction. |
| `/contactos/{id}/restricciones/{restriction}/habilitar/` | POST ADMIN | Revoke a manual restriction. |
| `/contactos/{id}/tareas/{task}/cerrar/` | POST ADMIN | Resolve/dismiss human task. |
| `/necesita-atencion/` | GET BOTH (`view_contacts`) | Open human-task list. |
| `/prospectos/` | GET ADMIN | Prospect list. |
| `/prospectos/exportar.csv` | GET ADMIN | Prospect CSV. |
| `/envios/` | GET BOTH (`view_sent_messages`; VENDEDOR sent-only) | Outbound list. |
| `/envios/exportar.csv` | GET ADMIN | Outbound CSV. |
| `/envios/{id}/` | GET BOTH (`view_sent_messages`; VENDEDOR sent-only) | Outbound detail. |
| `/envios/{id}/editar/` | POST ADMIN | Edit reviewable draft. |
| `/envios/{id}/aprobar/` | POST ADMIN | Authorize delivery. |
| `/perfil/` | GET/POST ADMIN | Business profile. |
| `/mensajes-fijos/` | GET/POST ADMIN | Message-template revisions. |
| `/prompts/` | GET/POST ADMIN | Drafting prompt. |
| `/respuesta-automatica/` | GET ADMIN | Automation settings. |
| `/respuesta-automatica/instrucciones/guardar/` | POST ADMIN | Save automatic-reply instructions. |
| `/respuesta-automatica/modo/` | POST ADMIN | Set OFF/SHADOW or reauthenticate and set LIVE. |
| `/respuesta-automatica/temas/guardar/` | POST ADMIN | Save follow-up topic. |
| `/respuesta-automatica/contexto-general/nuevo/` | POST ADMIN | Create and approve a context revision. |
| `/respuesta-automatica/contexto-general/{revision}/aprobar/` | POST ADMIN | Approve context revision. |
| `/respuesta-automatica/informacion/nueva/` | POST ADMIN | Create and approve fact revision. |
| `/respuesta-automatica/informacion/probar-busqueda/` | POST ADMIN | Knowledge retrieval preview. |
| `/respuesta-automatica/informacion/{revision}/aprobar/` | POST ADMIN | Approve fact revision. |
| `/integraciones/` | GET/POST ADMIN | Integration status/configuration. |
| `/integraciones/overture/` | GET ADMIN | Overture status. |
| `/integraciones/overture/sincronizar/` | POST ADMIN | Queue Overture sync. |
| `/rubros/` | GET/POST ADMIN | Category configuration. |
| `/configuracion/{kind}/{id}/toggle/` | POST ADMIN | Activate/deactivate a configuration item. |
| `/configuracion/{kind}/{id}/delete/` | POST ADMIN | Archive/delete a configuration item through its service. |
| `/catalogos/` | GET/POST ADMIN | Catalog list/upload. |
| `/catalogos/{id}/descargar/` | GET ADMIN | Catalog PDF. |
| `/campanas/` | GET BOTH (`view_campaigns`; VENDEDOR no drafts) | Campaign list. |
| `/campanas/nueva/` | GET/POST ADMIN | Campaign creation. |
| `/campanas/nueva/provincias/{province}/mapa/` | GET ADMIN | SVG-map JSON payload. |
| `/campanas/{id}/` | GET BOTH (`view_campaigns`; draft ADMIN-only) | Campaign detail. |
| `/campanas/{id}/aprobar/` | POST ADMIN | Campaign-level approval. |
| `/campanas/{id}/iniciar-aprobados/` | POST ADMIN | Start per-message campaign. |
| `/campanas/{id}/prospectos/{prospect}/regenerar/` | POST ADMIN | Regenerate prospect draft. |
| `/campanas/{id}/reanalizar-contrato-ia/` | POST ADMIN | Regenerate outdated AI analyses. |
| `/campanas/{id}/{action}/` | POST ADMIN | `start`, `pause`, `resume`, or `cancel`. |
| `/supresiones/` | GET ADMIN | Suppression list. |
| `/auditoria/` | GET ADMIN | Audit log. |
| `/jobs/` | GET ADMIN | Job list. |
| `/jobs/{id}/reintentar/` | POST ADMIN | Retry eligible failed outbound job. |
| `/gmail/` | GET ADMIN | Gmail status. |
| `/gmail/conectar/` | POST ADMIN | Start OAuth. |
| `/gmail/oauth/callback/` | GET ADMIN | Complete OAuth. |
| `/gmail/probar/` | POST ADMIN | Test Gmail. |
| `/gmail/desconectar/` | POST ADMIN | Disconnect Gmail. |
| `/gmail/fake/respuesta/` | POST ADMIN | Inject fake inbound message (fake provider only). |
| `/respuestas/` | GET BOTH (`view_contacts`) | Inbound list. |
| `/respuestas/exportar.csv` | GET ADMIN | Inbound CSV. |
| `/respuestas/{id}/` | GET BOTH (`view_contacts`) | Thread. |
| `/respuestas/{id}/enviar/` | POST ADMIN (`send_replies`) | Authorize manual reply. |
| `/health/`, `/health/live/`, `/health/ready/` | GET PUBLIC | Health probes. |
| `/health/degraded/` | GET ADMIN | Detailed provider/storage health. |
| `/api/schema/`, `/api/docs/` | GET, DEBUG only | OpenAPI schema and Swagger UI; production does not register them. |

## 2. API inventory

### Shared response shapes

The following aliases give every field returned by the JSON endpoints. Fields marked `enum` have their exact vocabulary in the enum registry following this section.

- `Session = {id, username, email, role: enum(Role), workspace_id, workspace_name, capabilities: enum(Capability)[], session_expires_at, reauthentication_active}`.
- `ManagedUser = {id, username, email, role: enum(Role), is_active, date_joined}`.
- `Profile = {company_name, salesperson_name, phone, whatsapp, description, products, differentiators, address, website, signature, additional_instructions, relevance_threshold, profile_version}`.
- `Template = {id, kind: enum(TemplateKind), subject, body, revision, content_hash, approved_at, active}`.
- `PromptConfig = {email_drafting_prompt, automatic_reply_prompt, revision}`.
- `AutomationConfig = {mode: enum(AutomationMode), mode_label, policy_version, live_enabled_at, live_enabled_by}`.
- `FactRevision = {id, fact_id, title, category, version, text, source_notes, content_hash, approved, approved_at}`.
- `ContextRevision = {id, version, context_text, source_notes, content_hash, approved, approved_at}`.
- `FollowUpTopic = {id, name, objective, instructions, cadence_days, mode: enum(FollowUpMode), next_due_at, active}`.
- `CommunicationPlan = {id, contact_id, topic_id, topic_name, preferred_email_id, state: enum(PlanState), state_label, mode: enum(FollowUpMode), next_due_at, snoozed_until}`.
- `ScheduledAttempt = {id, plan_id, due_at, state: enum(AttemptState), state_label, reason, outbound_message_id, subject, body_text}`.
- `AttentionTask = {id, contact_id, contact_name, kind: internal string, reason: internal string, status: enum(HumanTaskStatus), title, summary, next_step, opened_at}`.
- `GmailConnection = {connected, status: enum(GmailStatus), email, scopes: string[], last_tested_at, error}`.
- `CategoryRule = {id, taxonomy_code, name_terms: string[], active, sort_order}`; `SearchCategory = {id, name, sort_order, rules_revision, rules: CategoryRule[]}`.
- `SearchZone = {id, name, official_code, level: enum(ZoneLevel), province_code, province_name, parent_id, selectable, location_text, boundary_revision, boundary_hash}`.
- `Catalog = {id, name, version, original_filename, detected_mime, byte_size, sha256, active, missing, created_at}`.
- `Campaign = {id, name, state: enum(CampaignState), state_label, discovery_state: enum(DiscoveryState), discovery_state_label, delivery_mode: enum(DeliveryMode), approval_mode: enum(ApprovalMode), created_at, updated_at, started_at, finished_at}`. ADMIN responses additionally contain `{location_text, objective, max_raw_records, daily_limit, message_interval_minutes, weekdays: number[], window_start, window_end, timezone_name, relevance_threshold, reminder_enabled, reminder_delay_days, status_reason, catalog:{id,name,version}, categories:{id,name,sort_order}[], zones:{id,name,sort_order}[], attachments:{catalog_id,name,version,position}[], metrics:{enrollments,prospects,initial_messages,sent,review_ready,queued,errors}, audience_hash, content_hash, attachment_hash, schedule_hash}`.
- `Inbound = {id, external_at, sender, subject, classification: enum(InboundClassification), classification_label, is_human, is_read, gmail_thread_id, campaign_id, body_preview?}`. The detail/thread form replaces `body_preview` with `body_text`.
- `Outbound = {id, created_at, recipient, subject, kind: enum(OutboundKind), kind_label, state: enum(OutboundState), state_label, sent_at, simulated_at, campaign_id, body_text}`. ADMIN responses additionally contain `{approved_at, error, message_id, gmail_thread_id, attachments:{filename,position,byte_size}[]}`.
- `Contact = {id, name, organization_name, preferred_email, status: enum(ContactStatus), last_interaction_at, open_task_count, next_follow_up_at}`.
- `ContactDetail = Contact & {organization_id, emails: ContactEmail[], restrictions: Restriction[], timelines: Timeline[]}`. `ContactEmail = {id, original_email, label, is_preferred, validity: enum(EmailValidity), validated_at, invalid_reason, active_restriction_count}`. `Restriction = {id, scope: enum(RestrictionScope), kind: enum(RestrictionKind), evidence, revoked_at, created_at, email_address_id}`. `Timeline = {subject,first_at,last_at,automation_label,open_task_count,items:{direction:enum(TimelineDirection),happened_at,sender,recipient,subject,body,outcome,simulated,needs_attention}[]}`.
- `AuditEvent = {id, created_at, actor, actor_type: enum(ActorType), action: internal string, entity_type: internal string, entity_id, correlation_id}`.
- `BackgroundJob = {id, created_at, task_name, entity_type, entity_id, queue, state: enum(JobState), state_label, attempts, heartbeat_at, started_at, finished_at, next_retry_at, error}`.

### Enum registry

| Enum | Exact values |
|---|---|
| `Role` | `ADMIN`, `VENDEDOR` |
| `Capability` | `view_summary`, `view_campaigns`, `view_sent_messages`, `view_contacts`, `manage_users`, `manage_campaigns`, `approve_campaigns`, `send_replies`, `manage_contacts`, `manage_knowledge`, `manage_automation`, `manage_configuration`, `manage_integrations`, `download_pdfs`, `export_data`, `view_jobs`, `view_audit` |
| `TemplateKind` | `INITIAL`, `REMINDER`, `REFERRED_PROPOSAL` |
| `AutomationMode` | `OFF`, `SHADOW`, `LIVE` |
| `FollowUpMode` | `REVIEW_BEFORE_SEND`, `AUTOMATIC` |
| `PlanPurpose` | `CHECK_IN`, `PRODUCT_FEEDBACK`, `ADMIN_GOAL` |
| `PlanState` | `DISABLED`, `ACTIVE`, `PAUSED` |
| `AttemptState` | `DUE`, `DRAFT_REVIEW`, `AUTHORIZED`, `SENT`, `HUMAN_REQUIRED`, `CANCELLED`, `INELIGIBLE` |
| `HumanTaskStatus` | `OPEN`, `RESOLVED`, `DISMISSED` |
| `GmailStatus` | `CONNECTED`, `ERROR`, `DISCONNECTED`; `/integrations/status/` additionally synthesizes `NOT_CONNECTED` |
| `ZoneLevel` | `COUNTRY`, `PROVINCE`, `DISTRICT`, `NEIGHBORHOOD`, `CUSTOM` |
| `CampaignState` | `DRAFT`, `DISCOVERING`, `AWAITING_APPROVAL`, `RUNNING`, `PAUSED`, `CANCELLED`, `COMPLETED`, `STOPPED_ERROR` |
| `DiscoveryState` | `PENDING`, `RUNNING`, `TARGET_REACHED`, `EXHAUSTED_QUERIES`, `EXHAUSTED_RAW_LIMIT`, `EXHAUSTED_COST`, `FAILED_PROVIDER` |
| `DeliveryMode` | `DRY_RUN`, `REVIEW_ONLY`, `LIVE` |
| `ApprovalMode` | `CAMPAIGN`, `PER_MESSAGE` |
| `SearchRunState` | `PENDING`, `RUNNING`, `SUCCEEDED`, `RETRY_WAIT`, `FAILED_PERMANENT`, `CANCELLED` |
| `EnrollmentState` | `DISCOVERED`, `ELIGIBLE`, `PREPARED`, `INITIAL_SENT`, `RESPONDED`, `REMINDER_DUE`, `REMINDER_SENT`, `INELIGIBLE`, `CANCELLED` |
| `ProspectPipelineState` | `DISCOVERED`, `EMAIL_FOUND`, `ENRICHED`, `ANALYZED`, `SKIPPED_NO_EMAIL`, `SKIPPED_DUPLICATE`, `SKIPPED_IRRELEVANT`, `QUEUED`, `ERROR` |
| `InboundClassification` | `INTERESTED`, `NOT_INTERESTED`, `UNSUBSCRIBE`, `AUTO_REPLY`, `BOUNCE`, `OTHER` |
| `OutboundKind` | `FIRST_CONTACT`, `INITIAL`, `CAMPAIGN_REMINDER`, `MANUAL_REPLY`, `AUTOMATIC_REPLY`, `REFERRED_PROPOSAL`, `REDIRECT_ACK`, `SCHEDULED_CONTACT` |
| `OutboundState` | `PREPARED`, `REVIEW_READY`, `QUEUED`, `SENDING`, `RECONCILING`, `SENT`, `DRY_RUN_COMPLETED`, `INELIGIBLE`, `SEND_FAILED`, `CANCELLED` |
| `ContactStatus` | `ACTIVE`, `DO_NOT_CONTACT`, `UNSUBSCRIBED` |
| `EmailValidity` | `VALID`, `INVALID`, `TRANSIENT`, `UNKNOWN` |
| `RestrictionScope` | `CONTACT`, `EMAIL` |
| `RestrictionKind` | `UNSUBSCRIBE`, `BOUNCE`, `MANUAL` |
| `TimelineDirection` | `inbound`, `outbound` |
| `ActorType` | `USER`, `SYSTEM` |
| `JobState` | `PENDING`, `RUNNING`, `SUCCEEDED`, `RETRY_WAIT`, `FAILED`, `CANCELLED` |
| `OvertureReleaseCheckStatus` | `SUCCEEDED`, `FAILED` |
| `OverturePartitionStatus` | `IMPORTING`, `READY`, `SUPERSEDED`, `FAILED` |
| `KnowledgeSearchStatus` | `NO_QUERY`, `NO_APPROVED_FACTS`, `LOW_SIMILARITY`, `AMBIGUOUS`, `SELECTED` |
| `SendMode` | Runtime setting currently represented as lowercase `dry-run` or `live` |
| `SecretSource` | `ENVIRONMENT`, `ENCRYPTED`, `NONE` |
| `ExtractorProvider` | `fake`, `overture` |
| `WebsiteFetcher` | `fake`, `http` |
| `LLMProvider` | `fake`, `ollama`, `openai-compatible` |
| `EmbeddingProvider` | `fake`, `openai-compatible` |
| `GmailProvider` | `fake`, `api` |

### Endpoint-by-endpoint inventory

Permissions in this table are the effective permission, including inline/service checks where the declared DRF permission is weaker.

#### Authentication and users

| Endpoint | Permission | Request | Response | Enum fields |
|---|---|---|---|---|
| `GET /auth/csrf/` | PUBLIC | none | `Data<{csrf_token}>` | none |
| `POST /auth/login/` | PUBLIC | `{username, password}` | `Data<Session>` | `Session.role` |
| `POST /auth/activate/` | PUBLIC | `{token, password, password_confirmation}` | `Data<Session>` | `Session.role` |
| `GET /auth/session/` | BOTH | none | `Data<Session>` | `Session.role` |
| `POST /auth/logout/` | BOTH | none | `204` | none |
| `POST /auth/reauthenticate/` | BOTH | `{password}` | `Data<{reauthentication_active:true}>` | none |
| `GET /users/` | ADMIN | none | `Data<ManagedUser[]>` | `role` |
| `POST /users/` | ADMIN | `{username,email,role:enum(Role)}` | `201 Data<{user:ManagedUser,activation_url,expires_at}>` | request/response `role` |
| `POST /users/unlock-login/` | ADMIN | `{username?:string,client_ip?:IP}`; at least one required | `Data<{cleared:number}>` | none |
| `PATCH /users/{id}/role/` | ADMIN | `{role:enum(Role)}` | `Data<ManagedUser>` | request/response `role` |
| `PATCH /users/{id}/status/` | ADMIN | `{is_active:boolean}` | `Data<ManagedUser>` | response `role` |
| `POST /users/{id}/activation-link/` | ADMIN | none | `Data<{activation_url,expires_at}>` | none |

#### Workspace, templates, prompts, automation, and knowledge

| Endpoint | Permission | Request | Response | Enum fields |
|---|---|---|---|---|
| `GET /workspace/profile/` | ADMIN | none | `Data<Profile|null>` plus `ETag` when present | none |
| `PATCH /workspace/profile/` | ADMIN | partial `Profile` without `profile_version`; `If-Match` required when a profile exists | `Data<Profile>` plus `ETag` | none |
| `GET /message-template-revisions/` | ADMIN | none | `Data<Template[]>` | `kind` |
| `POST /message-template-revisions/` | ADMIN | `{kind:enum(TemplateKind),subject?:string,body:string}` | `201 Data<Template>` | `kind` |
| `GET /prompts/` | ADMIN | none | `Data<PromptConfig>` | none |
| `PATCH /prompts/` | ADMIN | `{email_drafting_prompt}` | `Data<PromptConfig>` | none |
| `GET /automation/configuration/` | ADMIN | none | `Data<AutomationConfig>` | `mode` |
| `PATCH /automation/configuration/` | ADMIN | `{mode:enum(AutomationMode)}`; `LIVE` is rejected here | `Data<AutomationConfig>` | request/response `mode` |
| `GET /automation/writing-instructions/` | **BOTH in code** | none | `Data<{automatic_reply_prompt}>` | none |
| `PATCH /automation/writing-instructions/` | ADMIN effectively (service check) | `{automatic_reply_prompt}` | same | none |
| `POST /automation/actions/{enable-live\|disable-live}/` | ADMIN effectively (service check); `enable-live` also needs active 10-minute reauthentication | none | `Data<AutomationConfig>` | `mode`; path action is a closed literal |
| `GET /knowledge/facts/` | ADMIN | none | `Data<FactRevision[]>` | none |
| `POST /knowledge/facts/` | ADMIN | `{title,category?:string,text,source_notes?:string}` | `201 Data<FactRevision>`; the save service also approves it | none |
| `POST /knowledge/fact-revisions/{id}/approve/` | ADMIN | none | `Data<FactRevision>` | none |
| `GET /knowledge/global-context-revisions/` | ADMIN | none | `Data<ContextRevision[]>` | none |
| `POST /knowledge/global-context-revisions/` | ADMIN | `{context_text}` | `201 Data<ContextRevision>`; the save service also approves it | none |
| `POST /knowledge/global-context-revisions/{id}/approve/` | ADMIN | none | `Data<ContextRevision>` | none |
| `POST /knowledge/search-preview/` | ADMIN | `{query}` | `Data<{status:enum(KnowledgeSearchStatus),manifest:object,matches:{revision_id,title,score,selected,may_be_irrelevant}[]}>` | `status` |
| `GET /follow-up-topics/` | ADMIN | none | `Data<FollowUpTopic[]>` | `mode` |
| `POST /follow-up-topics/` | ADMIN | `{name,objective,instructions?,cadence_days[7..365],mode:enum(FollowUpMode),next_due_at?,active?}` | `201 Data<FollowUpTopic>` | `mode` |
| `PATCH /follow-up-topics/{id}/` | ADMIN | same complete input as POST | `Data<FollowUpTopic>` | `mode` |

#### Attention, Overture, and integrations

| Endpoint | Permission | Request | Response | Enum fields |
|---|---|---|---|---|
| `GET /attention/` | BOTH | none | `Data<AttentionTask[]>` (first 100 open tasks) | `status`; `kind`/`reason` are internal vocabularies without labels |
| `POST /human-tasks/{id}/{resolve\|dismiss}/` | ADMIN | `{note}` | `Data<{id,status:enum(HumanTaskStatus)}>` | `status`; path action literal |
| `GET /overture/status/` | ADMIN | none | `Data<{latest_snapshot_id,active_snapshot_id,release_checks:{status:enum(OvertureReleaseCheckStatus),latest_release,created_at}[],partitions:{id,release_id,province_code,province_name,status:enum(OverturePartitionStatus),is_active,place_count,imported_at,error}[]}>` | both `status` fields |
| `POST /overture/sync/` | ADMIN | `{release_id,province_code}` | `202 Data<{status:literal(queued),celery_task_id,province_code}>` | `status` literal |
| `GET /integrations/status/` | ADMIN | none | `Data<{extractor:{provider,overture_min_confidence},website_fetcher:{provider},llm:{provider,model,credential_source,configured},embeddings:{provider,model,dimensions},gmail:{provider,oauth_client_id_configured,credential_source,credential_configured,connection_status:enum(GmailStatus)+NOT_CONNECTED,email},revision}>` | `extractor.provider: ExtractorProvider`; `website_fetcher.provider: WebsiteFetcher`; `llm.provider: LLMProvider`; `embeddings.provider: EmbeddingProvider`; `gmail.provider: GmailProvider`; credential sources: `SecretSource`; Gmail status |
| `GET /integrations/gmail/connection/` | ADMIN | none | `Data<GmailConnection>` | `status` |
| `POST /integrations/gmail/oauth/start/` | ADMIN | none | `Data<{authorization_url}>` | none |
| `GET /integrations/gmail/oauth/callback/` | ADMIN | query `{state,code}` | `302` to `/settings/integrations?gmail=connected` or `...?gmail=oauth_failed` | redirect result literal |
| `POST /integrations/gmail/test/` | ADMIN | none | `Data<{status:literal(tested),email}>` | `status` literal |
| `POST /integrations/gmail/disconnect/` | ADMIN | none | `Data<GmailConnection>` | `status` |

#### Configuration, dashboard, campaigns, and catalogs

| Endpoint | Permission | Request | Response | Enum fields |
|---|---|---|---|---|
| `GET /search-categories/` | ADMIN | none | `Data<SearchCategory[]>` | none |
| `POST /search-categories/` | ADMIN | `{name,sort_order?:number}` | `201 Data<SearchCategory>` | none |
| `GET /search-categories/{id}/rules/` | ADMIN | none | `Data<SearchCategory>` | none |
| `POST` or `PATCH /search-categories/{id}/rules/` | ADMIN | `{rules:{taxonomy_code?:string,name_terms?:string[]}[]}` | `Data<SearchCategory>` | none |
| `GET /search-zones/` | ADMIN | query `{level?:enum(ZoneLevel),parent_id?:UUID}` (the code does not serializer-validate `level`) | `Data<SearchZone[]>` | request/response `level` |
| `GET /search-zones/{id}/geometry/` | ADMIN | none | `Data<{id,boundary_revision,boundary_hash,geojson,bbox}>` | none |
| `GET /dashboard/summary/` | BOTH | query `{campaign_id?:UUID}` | `Data<{metrics:{unique_initial_recipients,initial_messages_sent,reminders_sent,automatic_replies_sent,scheduled_contacts_sent,unique_human_responders,response_rate,positive_response_rate,contacts_created,bounce_rate,unsubscribe_rate,automatically_resolved,human_required,open_human_tasks,median_first_response_seconds,median_human_intervention_seconds,responses_after_initial,responses_after_reminder},campaigns:{id,name,state,state_label,discovery_state,discovery_state_label,delivery_mode}[],summary:{campaigns,catalogs,categories,zones,responses},attention:{open_human_tasks,paused_campaigns},safety:{send_mode,send_kill_switch,auto_reply_kill_switch,relationship_kill_switch},admin?:{profile_configured,gmail_connected,problem_jobs,prospects,sent_messages}}>` | campaign `state`, `discovery_state`, `delivery_mode`; `safety.send_mode` |
| `GET /campaigns/` | BOTH | query `{q?,state?:enum(CampaignState),page?,page_size?}` | `Page<Campaign>` | query/response campaign enums |
| `POST /campaigns/` | ADMIN | `{name,delivery_mode:enum(DeliveryMode),approval_mode:enum(ApprovalMode),reminder_enabled,reminder_delay_days,location_text,objective,max_raw_records,overture_min_confidence,daily_limit,message_interval_minutes,weekdays:number[],window_start,window_end,timezone_name,relevance_threshold,catalog?:UUID,catalogs:UUID[],categories:UUID[],zones:UUID[],provinces?:UUID[],confirm_live?}`. `catalogs`, `categories`, and `zones` must be non-empty; `confirm_live` must be true for `LIVE`. Provider/model fields may be submitted but are forcibly replaced by runtime configuration. | `201 Data<Campaign>` | request and response campaign enums |
| `GET /campaigns/{id}/` | BOTH; VENDEDOR cannot retrieve draft | none | `Data<Campaign>` plus `ETag` | campaign enums |
| `GET /campaigns/{id}/coverage-map/` | BOTH; no VENDEDOR draft | none | `Data<{campaign_id,categories:{id,name,sort_order}[],zones:{id,name,sort_order}[],search_runs:{id,state:enum(SearchRunState),raw_count,email_count,error}[]}>` | `search_runs.state` |
| `GET /campaigns/{id}/enrollments/` | BOTH; no VENDEDOR draft | none | `Data<{id,organization_name,selected_email,state:enum(EnrollmentState),state_label,exclusion_reason}[]>`; VENDEDOR gets blank exclusion reason | `state` |
| `GET /campaigns/{id}/messages/` | BOTH; no VENDEDOR draft | none | `Data<Outbound[]>`; VENDEDOR receives public fields only | outbound enums |
| `GET /campaigns/{id}/prospects/` | ADMIN | none | `Data<{id,name,address,neighborhood,category,website,pipeline_state:enum(ProspectPipelineState),emails:string[]}[]>` | `pipeline_state` |
| `POST /campaigns/{id}/actions/{action}/` | ADMIN; approval actions require approval capability | UUID `Idempotency-Key`; body `{reason?:string}`; action is `start-discovery`, `approve`, `start-approved`, `pause`, `resume`, or `cancel` | `Data<Campaign>` | path action literal and campaign response enums |
| `GET /prospects/export.csv` | ADMIN | current prospect filters | CSV download | none in JSON; exported state is presented as text |
| `GET /catalogs/` | ADMIN | none | `Data<Catalog[]>` | none |
| `POST /catalogs/` | ADMIN | multipart `{name,file}` | `201 Data<Catalog>` | none |
| `GET /catalogs/{id}/download/` | ADMIN | none | private/no-store `application/pdf` download | none |

#### Mailbox, operations, contacts, and health

| Endpoint | Permission | Request | Response | Enum fields |
|---|---|---|---|---|
| `GET /inbound-messages/` | BOTH | query `{q?,classification?:enum(InboundClassification),page?,page_size?}` | `Page<Inbound>` | query/response `classification` |
| `GET /inbound-messages/export.csv` | ADMIN | current inbound filters | CSV download | classification rendered as its label |
| `GET /inbound-messages/{id}/thread/` | BOTH | none | `Data<{inbound:Inbound(with body_text),timeline:{direction:enum(TimelineDirection),at,sender,body_text,classification}[]}>`; timeline `classification` is an inbound classification for inbound rows and an outbound kind for outbound rows | `direction`; polymorphic `classification` |
| `POST /inbound-messages/{id}/manual-reply/` | ADMIN | `{body_text,idempotency_key:UUID}` | `200/202 Data<{created,message:Outbound}>` | outbound enums |
| `GET /outbound-messages/` | BOTH; VENDEDOR sent-only | query `{q?,classification?:enum(InboundClassification),page?,page_size?}`; `classification` is accepted by the shared serializer but is not applied as an outbound enum filter | `Page<Outbound>` | response outbound enums; mismatched query enum |
| `GET /outbound-messages/export.csv` | ADMIN | current outbound filters | CSV download | kind/state rendered as labels |
| `GET /outbound-messages/{id}/` | BOTH; VENDEDOR sent-only | none | `Data<Outbound>` | outbound enums |
| `PATCH /outbound-messages/{id}/draft/` | ADMIN | `{subject,body_text}` | `Data<Outbound>` | outbound enums |
| `POST /outbound-messages/{id}/authorize/` | ADMIN | UUID `Idempotency-Key`; empty body | `Data<Outbound>` | outbound enums |
| `GET /audit-events/` | ADMIN | query `{q?,action?,entity?,page?,page_size?}` | `Page<AuditEvent>` | `actor_type`; `action` and `entity_type` are open internal vocabularies |
| `GET /background-jobs/` | ADMIN | query `{q?,state?:enum(JobState),queue?,page?,page_size?}` | `Page<BackgroundJob>` | query/response `state` |
| `GET /background-jobs/{id}/` | ADMIN | none | `Data<BackgroundJob>` | `state` |
| `POST /background-jobs/{id}/retry/` | ADMIN | UUID `Idempotency-Key`; `{reason:string}` | `Data<Outbound>` | outbound enums |
| `GET /contacts/` | BOTH | query `{q?,status?:enum(ContactStatus),attention?:literal(open\|clear),page?,page_size?}` | `Page<Contact>` | query/response `status`; `attention` literal |
| `POST /contacts/` | ADMIN | `{email,organization_name?,contact_name?}` | `201 Data<Contact>` | response `status` |
| `GET /contacts/{id}/` | BOTH | none | `Data<ContactDetail>` plus `ETag`; ADMIN timelines include simulated messages, VENDEDOR timelines do not | contact/email/restriction/direction enums |
| `PATCH /contacts/{id}/` | ADMIN | partial `{name?}` plus required `If-Match` | `Data<Contact>` plus `ETag` | response `status` |
| `GET /contacts/{id}/communication-plans/` | ADMIN | none | `Data<CommunicationPlan[]>` | `state`, `mode` |
| `POST /contacts/{id}/communication-plans/` | ADMIN | `{preferred_email_id,purpose:enum(PlanPurpose),goal_text?,cadence_days[7..365],mode:enum(FollowUpMode),enabled?,next_due_at?}` | `201 Data<CommunicationPlan>` | request `purpose`/`mode`; response `state`/`mode` |
| `POST /contacts/{id}/communication-plans/{plan}/{action}/` | ADMIN | if action=`snooze`: `{until}`; otherwise `{state:enum(PlanState)}`. The path accepts any non-`snooze` string rather than an action allowlist. | `Data<CommunicationPlan>` | request/response `state`, response `mode`; path action partly open |
| `PATCH /contacts/{id}/scheduled-attempts/{attempt}/draft/` | ADMIN | `{subject,body_text}` | intended `Data<ScheduledAttempt>` | `state` |
| `POST /contacts/{id}/scheduled-attempts/{attempt}/authorize/` | ADMIN | empty | intended `Data<ScheduledAttempt>` | `state` |
| `POST /contacts/{id}/emails/` | ADMIN | `{email,label?,make_preferred?}` | `201 Data<ContactEmail>` | response `validity` |
| `PATCH /contacts/{id}/emails/{email}/preferred/` | ADMIN | empty | `Data<Contact>` | response `status` |
| `POST /contacts/{id}/emails/{email}/validate/` | ADMIN | empty | `202 Data<{status:literal(queued)}>` | `status` literal |
| `POST /contacts/{id}/restrictions/` | ADMIN | `{scope:enum(RestrictionScope),reason,email_address_id?}` | `201 Data<Restriction>` | request `scope`; response `scope`/`kind` |
| `POST /contacts/{id}/restrictions/{restriction}/revoke/` | ADMIN | `{reason}` | `Data<Restriction>` | `scope`, `kind` |
| `GET /health/live/` | PUBLIC | none | `{status:literal(ok)}` (not wrapped in `data`) | status literal |
| `GET /health/ready/` | PUBLIC | none | `{status:literal(ok\|unavailable)}` (not wrapped) | status literal |
| `GET /health/degraded/` | ADMIN | none | `{status:literal(ok\|degraded),components:{storage,gmail,extractor,llm},storage_free_bytes}` | top-level/component status strings are operational vocabularies |

### Confirmed API/client contract defects

1. Both scheduled-attempt endpoints are currently broken at dispatch. Their URL patterns supply `contact_id`, but `patch()` does not accept it; the authorize URL does not supply the `action` argument required by `post()`. A request reaches Python with mismatched keyword arguments and should fail before the intended service call.
2. `GET /automation/writing-instructions/` declares only `IsAuthenticated` and returns the workspace's automatic-reply prompt to VENDEDOR. PATCH is denied later by the service. The read and write permissions are therefore inconsistent.
3. The frontend `updateContact()` helper does not fetch or send the required `ETag`/`If-Match`, so it cannot successfully update an existing contact. It is currently unused by a page.
4. `GET /outbound-messages/` reuses an inbound query serializer. It accepts an inbound `classification` enum although outbound rows use `kind` and `state`; the view does not apply that validated field as an outbound enum filter.
5. Gmail disconnected state has two API spellings: `/integrations/gmail/connection/` returns `DISCONNECTED`; `/integrations/status/` returns synthetic `NOT_CONNECTED` when there is no row.
6. Several response serializers declare enum-bearing fields as unconstrained strings (`Campaign`, `Contact`, `Email`, `Restriction`, `SearchZone`, automation status objects). The values are closed in models but are not represented as choices in the generated response schema.

### Enum values currently shown as literal/internal text in the Next.js UI

This flags only displayed text. Values used as form values behind a Spanish label are not flagged.

| Page | Literal/internal field shown |
|---|---|
| `/dashboard` | `safety.send_mode` (`dry-run`/`live`). |
| `/campaigns` | `delivery_mode` (`DRY_RUN`, `REVIEW_ONLY`, `LIVE`). |
| `/campaigns/[id]` | `delivery_mode` and `approval_mode`. |
| `/contacts` | `contact.status`. |
| `/contacts/[id]` | `contact.status`, `email.validity`, `restriction.scope`, and `restriction.kind`. |
| `/automation` | OFF/SHADOW/LIVE are used as the primary mode words; knowledge-preview `status` is rendered literally; `policy_version` is exposed as primary text. |
| `/settings/integrations` | Provider IDs, model IDs, credential-source values, and Gmail `connection_status`; Overture confidence is shown as the raw decimal string. |
| `/settings/overture` | Release-check `status`, partition `status`, release/snapshot IDs, and province codes. |
| `/audit` | `action` and `entity_type` internal identifiers, entity ID, and correlation ID. (`actor_type` is returned but not rendered.) |
| `/jobs` | `task_name`, `entity_type`, `entity_id`, `queue`, and raw `error` are primary content. `state_label` itself is localized. |

The UI already uses server-provided labels for campaign state/discovery state, enrollment/outbound state and kind, inbound classification, plan/attempt state, and selected role/template/purpose choices.

## 3. Shared component inventory

### What exists today

| Shared module | Current responsibility | Notes |
|---|---|---|
| `frontend/components/app-providers.tsx` | Ant Design locale/provider, a minimal theme token, Ant Design `App`, and `AuthProvider` | The only theme override is `colorPrimary: #155eef`; it is not the token system specified by `DESIGN.md`. |
| `frontend/components/auth-provider.tsx` | Session loading, login/activation/logout helpers, expired-session UI, application shell, header, and role-dependent sidebar | Authentication state, navigation policy, and visual shell are coupled in one component. The top bar does not show permanent send mode. |
| `useAuth()` | Access to session and auth operations | Used for page guards and control visibility. |
| `AuthError` | A common alert for an auth/session/problem object | Also reused as a general page error, despite its auth-specific name. |
| `frontend/lib/api.ts` | All browser-side request functions and TypeScript response types | It is a shared data client, not a visual component. It contains several contract drifts called out above. |
| `frontend/lib/correlation.ts` | Correlation-ID helper | Shared technical utility. |

There are no shared page headers, loading/empty/error shells, status badges, confirmation dialogs, data-list shells, form shells, date/number formatters, role gates, or technical-details components.

### Duplicated across pages

| Repeated pattern | Current duplication |
|---|---|
| Page title and explanatory copy | Recreated with `Typography.Title`/`Paragraph` on nearly every route. Spacing, hierarchy, and action placement vary. |
| Loading/error/empty branches | Each page hand-writes `Skeleton`, `AuthError`, and `Empty` branches. This is the main cause of inconsistent state coverage. |
| Status display | Pages construct raw `Tag` elements ad hoc. There is no semantic status-to-label/color registry, and several pages render enum values literally. |
| ADMIN/permission gates | Ten pages repeat `session?.role !== "ADMIN"`; detail controls use separate inline role checks; one page uses a capability string. This has already drifted on Attention and three settings/config pages. |
| List/card presentation | Campaigns, contacts, responses, attention, outbound, catalogs, audit, and jobs each create their own `Card` + `List` composition. |
| Forms and save errors | `saving`, `error`, `try/catch`, `problemMessage`, and post-save refresh/reset are reimplemented per page. |
| Date/time formatting | `Intl.DateTimeFormat` and `toLocaleString` are instantiated or called independently in contacts, contact detail, audit, jobs, and message pages. |
| Confirmation UI | Campaigns/contact restriction/outbound use unrelated `Popconfirm` text and styling; other effect-bearing buttons use no confirmation. |
| Section empty states | Detail pages insert plain `Empty` blocks with different wording and no consistent next action. |

### One-offs that should be shared under the design contract

These are architectural component opportunities discovered from repeated behavior, not requests to add new product scope.

- `PageHeader`: eyebrow/title/description, breadcrumb/back link, status, and a consistent right-side action slot.
- `DataPageState` or separate `LoadingState`, `EmptyState`, `ErrorState`, and `ForbiddenState`: purposeful copy, retry, and role-aware primary action.
- `StatusBadge`: centralized enum-to-Spanish-label and semantic color mapping. It should accept server labels where present and prevent raw fallback from becoming primary UI text.
- `AdminOnly`/`CapabilityGate`: one route/control primitive based on capabilities rather than repeated role strings.
- `EffectConfirmation`: modal confirmation with consequence, cancel, red/danger confirm where appropriate, and optional typed confirmation. LIVE activation is a specialized variant with password handling.
- `TechnicalDetails`: collapsed, bottom-of-page, ADMIN-only presentation for providers, models, IDs, hashes, manifests, queue names, correlation IDs, and raw errors.
- Shared `formatDateTime`, `formatDate`, `formatPercent`, `formatDuration`, and Buenos Aires timezone helpers.
- `EntityListCard`/`DescriptionSection` for the repeated list and detail layouts.
- Extracted `AppShell`, `TopBarSafetyMode`, and `SidebarNavigation` from `AuthProvider` so auth lifecycle and visual navigation do not evolve as one unit.

## 4. State coverage matrix

Legend: ✅ explicit and usable; ◐ present but generic/incomplete; ❌ absent or functionally wrong; N/A means the route is intentionally available to both roles and has no role-forbidden state. “Forbidden” evaluates a direct URL visit and mutation controls, not merely whether the sidebar link is hidden.

| Data-heavy Next.js page | Loading | Empty | Error | Forbidden | Evidence/qualification |
|---|:---:|:---:|:---:|:---:|---|
| `/dashboard` | ✅ | ◐ | ✅ | N/A | Whole-response null and campaign empty exist; zero-valued summary/metrics still render cards and offer no next action. |
| `/campaigns` | ✅ | ◐ | ✅ | N/A | Generic `Empty`; no create action or VENDEDOR-specific explanation in the empty block. |
| `/campaigns/[id]` | ✅ | ◐ | ✅ | ✅ | Missing operational details have a role message; admin actions are hidden for VENDEDOR. |
| `/campaigns/new` | ✅ | ❌ | ✅ | ✅ | Empty categories/zones/catalogs become empty selects without a blocking explanation or route to configure them. |
| `/contacts` | ✅ | ◐ | ✅ | N/A | Generic empty with no primary action. |
| `/contacts/[id]` | ✅ | ◐ | ✅ | ✅ | Per-section empty blocks exist; admin mutation sections are hidden. |
| `/responses` | ✅ | ◐ | ✅ | N/A | Generic empty with no cross-link/next action. |
| `/responses/[id]` | ✅ | ◐ | ✅ | ✅ | Empty thread is generic; reply box is capability-gated. |
| `/attention` | ✅ | ◐ | ✅ | ❌ | Generic empty. VENDEDOR sees Resolve/Discard controls; only the API rejects the mutation. |
| `/outbound` | ✅ | ◐ | ✅ | N/A | Generic empty. |
| `/outbound/[id]` | ✅ | ❌ | ✅ | ✅ | No distinct content-empty treatment; edit/authorize is hidden for VENDEDOR. |
| `/catalogs` | ✅ | ◐ | ✅ | ❌ | Empty list exists next to upload form, but direct VENDEDOR access has no page guard and becomes an API error. |
| `/automation` | ✅ | ❌ | ✅ | ✅ | Empty fact/context lists render blank areas. |
| `/settings/profile` | ✅ | ◐ | ✅ | ❌ | Null profile becomes a blank form rather than a defined first-use state; no frontend role guard. |
| `/settings/message-templates` | ✅ | ❌ | ✅ | ✅ | If the API successfully returns `[]`, the page renders `Skeleton` indefinitely. |
| `/settings/prompts` | ✅ | ❌ | ✅ | ✅ | A null config would render `Skeleton` indefinitely; the current API normally synthesizes defaults. |
| `/settings/categories` | ✅ | ✅ | ✅ | ✅ | Empty list is paired with the create form. |
| `/settings/integrations` | ✅ | ✅ | ✅ | ❌ | Disconnected Gmail has a connect action; direct VENDEDOR access has no page guard. |
| `/settings/overture` | ✅ | ◐ | ✅ | ✅ | Generic empty; no sync/maintenance next action. |
| `/settings/users` | ✅ | ❌ | ✅ | ✅ | No explicit empty state (the active administrator normally guarantees at least one row). |
| `/audit` | ✅ | ◐ | ✅ | ✅ | Generic empty, no filter reset or explanation. |
| `/jobs` | ✅ | ◐ | ✅ | ✅ | Generic empty, no filter reset or explanation. |

Global initial-auth loading and expired-session UI exist in `AuthProvider`; they do not replace page-level empty/error/forbidden states.

## 5. Permission enforcement

### Source of truth and layers

1. `Membership.Role` defines `ADMIN` and `VENDEDOR`.
2. `apps.accounts.permissions.Capability` defines 17 capabilities. `VENDEDOR_CAPABILITIES` contains exactly `view_summary`, `view_campaigns`, `view_sent_messages`, and `view_contacts`; ADMIN is granted every capability.
3. `has_capability()`, `require_user_capability()`, and `workspace_for_user()` are the central checks and enforce active membership/workspace scope.
4. REST views normally use `CapabilityPermission` subclasses at the DRF boundary. Mixed GET/POST views sometimes add inline checks. Mutation services normally repeat the capability/workspace check at the domain boundary.
5. Legacy views normally use `@require_capability` and then call the same services.
6. The Next.js shell hides ADMIN navigation and pages/controls add role/capability checks. These checks are usability controls only; Django remains the security boundary.

### Role behavior that is enforced consistently

- VENDEDOR can read summary, non-draft campaigns, contacts, inbound messages/threads, and sent outbound messages.
- VENDEDOR cannot receive draft campaigns or non-sent outbound messages even by ID; the backend query is filtered, not merely the UI.
- VENDEDOR contact timelines omit simulated outbound messages.
- Campaign/detail mutations, manual replies, contact mutation, configuration, users, integration management, exports, audit, and jobs require ADMIN capabilities on the backend.
- Contact, campaign, and mailbox querysets are workspace-scoped.

### Checks present in only one layer or inconsistent between layers

| Surface | Frontend | API/view | Service/domain | Finding |
|---|---|---|---|---|
| Catalogs page | Hidden nav only | `manage_configuration` | Catalog services check workspace/capability | No direct-route forbidden state; backend-only for page reachability. |
| Business profile page | Hidden nav only | `manage_configuration` | `save_business_profile` checks capability | No direct-route forbidden state. |
| Integrations page | Hidden nav only | `manage_integrations` | Gmail services enforce ownership/workspace | No direct-route forbidden state. |
| Attention task close | **Controls shown to VENDEDOR** | Inline `manage_automation` check | `close_human_task` checks policy/workspace | Security survives, but UI role enforcement is absent and violates the read-only VENDEDOR design. |
| Automation writing instructions GET | Admin page/hidden nav | Only `IsAuthenticated` | No service is called for GET | VENDEDOR can directly read the approved automatic-reply prompt. This is genuinely enforced in neither API capability nor domain layer. |
| Automation writing instructions PATCH | Admin page guard | Only `IsAuthenticated` | `save_automatic_reply_prompt` checks `manage_automation` | Mutation is service-only after authentication rather than declared at the API boundary. |
| LIVE enable/disable API | Admin page guard | Only `IsAuthenticated`; enable checks session reauth inline | `set_live_mode`/`set_non_live_mode` check capability and safety policy | Effective security is in service/inline checks, but the API permission declaration understates it. |
| Campaign action API | Admin controls | Only `IsAuthenticated`, then inline capability/workspace check | approval/transition services check capability | Enforced twice effectively, but not through a declarative API permission. |
| Outbound draft/authorize API | Admin controls | Only `IsAuthenticated`, then inline capability check | message services revalidate policy/capability | Enforced effectively, but not declaratively at the API boundary. |
| Overture status/sync API | Admin page guard | Only `IsAuthenticated`, then inline `manage_integrations` | Task receives only release/province values | Sync authorization exists only in the HTTP view; the queued task has no actor to re-check. |
| Contact POST and PATCH | Admin controls/page | Base class is `view_contacts`; each mutation uses inline `manage_contacts` | POST uses service; PATCH directly assigns/saves `Contact.name` | Contact-name PATCH has no domain mutation service and is guarded only at the API/view layer. |
| Search-category POST | Admin page guard | `manage_configuration` | Direct `SearchCategory.objects.create()` in the API view | Category creation is checked only at the UI/API layers; there is no domain mutation service check/audit call on this path. |
| Automation configuration GET | Admin page guard | `manage_automation` | No domain service; the GET uses `get_or_create()` | A nominally read-only request can create the default configuration directly in the view. |
| Background-job retry | Admin page guard | `view_jobs` (ADMIN-only under today's all-or-nothing role mapping) | Delivery retry service checks mutation rules | It relies on “all ADMIN capabilities” rather than a dedicated retry/approve permission at the API boundary. |
| Next route guards generally | Mixed role strings and one capability string | Capability-based | Capability-based | The UI has two authorization models and has already drifted. |
| Legacy versus Next | Separate templates/decorators | Separate REST views | Mostly shared services | Parallel HTTP/UI layers can diverge even where services remain common. |

## 6. Campaign state machine

### Exact states

| State | Code label | Terminal |
|---|---|:---:|
| `DRAFT` | Borrador | No |
| `DISCOVERING` | Buscando destinatarios | No |
| `AWAITING_APPROVAL` | Lista para aprobar | No |
| `RUNNING` | En curso | No |
| `PAUSED` | Pausada | No |
| `CANCELLED` | Cancelada | Yes |
| `COMPLETED` | Completada | Yes |
| `STOPPED_ERROR` | Detenida por error | Yes |

### Exact permitted transitions from `ALLOWED_TRANSITIONS`

| From | Permitted targets |
|---|---|
| `DRAFT` | `DISCOVERING`, `RUNNING`, `CANCELLED` |
| `DISCOVERING` | `AWAITING_APPROVAL`, `PAUSED`, `CANCELLED`, `STOPPED_ERROR` |
| `AWAITING_APPROVAL` | `RUNNING`, `CANCELLED` |
| `RUNNING` | `PAUSED`, `CANCELLED`, `COMPLETED`, `STOPPED_ERROR` |
| `PAUSED` | `RUNNING`, `CANCELLED` |
| `CANCELLED` | none |
| `COMPLETED` | none |
| `STOPPED_ERROR` | none |

The discovery sub-state is separate: `PENDING`, `RUNNING`, `TARGET_REACHED`, `EXHAUSTED_QUERIES`, `EXHAUSTED_RAW_LIMIT`, `EXHAUSTED_COST`, or `FAILED_PROVIDER`.

### UI/system action mapping

| Trigger | Transition | Exposed where | Notes |
|---|---|---|---|
| Start discovery | `DRAFT → DISCOVERING` | Next `start-discovery`; legacy `start` | Freezes the draft snapshots and queues extraction orchestration. |
| Discovery completes | `DISCOVERING → AWAITING_APPROVAL` | Background workflow | No direct UI action. |
| Discovery failure | `DISCOVERING → STOPPED_ERROR` | Background workflow | Terminal. |
| Approve campaign | `AWAITING_APPROVAL → RUNNING` | Next/legacy “approve” for `CAMPAIGN` approval mode | Approval service freezes/approves audience and content. |
| Start approved messages | `AWAITING_APPROVAL → RUNNING` | Next/legacy “start-approved” for `PER_MESSAGE` mode | Starts delivery of individually approved messages. |
| Pause | `RUNNING → PAUSED` in both UIs | Next/legacy | The state machine also permits `DISCOVERING → PAUSED`, but neither current detail UI offers it. |
| Resume | `PAUSED → RUNNING` | Next/legacy | Delivery/orchestration can continue after backend rechecks. |
| Cancel | Current Next: `RUNNING|PAUSED → CANCELLED`; legacy/API: any permitted nonterminal source | Next/legacy | The service permits cancellation from `DRAFT`, `DISCOVERING`, and `AWAITING_APPROVAL`, but Next does not render those actions. |
| Complete | `RUNNING → COMPLETED` | Background workflow | No direct UI action. |
| Stop on delivery error | `RUNNING → STOPPED_ERROR` | Background workflow | Terminal. |
| Direct start | `DRAFT → RUNNING` | No current Next or legacy action maps to this edge | The transition remains allowed by the service. |

There is no recovery transition or UI action from `STOPPED_ERROR`; it is explicitly terminal in code.

## 7. Dangerous/effect-bearing action inventory

“Dangerous” here includes sends, actions that can cause later sends, state transitions that stop/restart work, permission changes, suppressions, external-account changes, and destructive configuration changes. A warning sentence is recorded separately from an actual confirmation guard.

### Next.js surface

| Trigger | Consequence | Current guard |
|---|---|---|
| Automation: Activate LIVE | Enables automatic replies after policy checks | Inline password field and red button only. No modal, no typed confirmation word, and password uses `autocomplete="current-password"` rather than the design-required disabled/new-password behavior. Backend also requires a reauthentication less than 10 minutes old. |
| Automation: Disable LIVE | Switches to SHADOW | No confirmation; red button. Safety-increasing but operationally material. |
| Response detail: Authorize response | Creates/authorizes a manual reply and queues delivery | Warning copy above the form, but submit is immediate. No modal/Popconfirm. |
| Outbound detail: Authorize | Approves and may queue the message | `Popconfirm` with a backend-policy warning. Confirm action is not styled danger and does not state recipient/irreversibility. |
| Campaign: Start discovery | Freezes the draft and queues background discovery | `Popconfirm` title only. |
| Campaign: Approve audience/content | May transition into a running campaign | `Popconfirm` title only. |
| Campaign: Start approved delivery | Starts delivery | `Popconfirm` with generic policy-recheck description; no recipient count, typed confirmation, or danger confirm. |
| Campaign: Pause | Stops ongoing work | `Popconfirm` title only. |
| Campaign: Resume | Restarts ongoing discovery/delivery effects after checks | `Popconfirm` title only. |
| Campaign: Cancel | Terminally cancels campaign | `Popconfirm` title “¿Cancelar esta campaña?” and red trigger button; confirm button is not configured danger, no consequence text, no typed word, and the request supplies no operator reason. |
| Contact: Create contact/email restriction | Blocks future delivery to contact/address | Immediate form submit; no confirmation. This is safety-increasing but materially changes eligibility. |
| New contact: Create contact | Establishes the Organization as a Contact, excludes it from future campaigns, and can cancel campaign work that is no longer eligible | Immediate form submit; no consequence summary or confirmation. |
| Contact: Revoke manual restriction | Re-enables a contact/address for future eligibility | `Popconfirm` title only; fixed API reason; confirm button is not styled danger. |
| Contact: Set preferred email | Changes the address future deliveries select | Immediate click; no confirmation. |
| Contact: Activate periodic review | Creates/enables a 30-day `REVIEW_BEFORE_SEND` plan | Immediate form submit; no confirmation and the fixed cadence/mode are not summarized at confirmation time. |
| Attention: Resolve | Closes a human task and can allow automation to proceed | Immediate click; no confirmation; blank note becomes a fixed default. Incorrectly shown to VENDEDOR. |
| Attention: Discard | Dismisses a human task and can remove it from the safety queue | Immediate click; no confirmation; blank note becomes a fixed default. Incorrectly shown to VENDEDOR. |
| Jobs: Retry | Retries the same durable failed outbound row and may queue Gmail delivery | Immediate click; no confirmation; blank reason becomes fixed default text. |
| Gmail: Disconnect | Revokes/discards the stored Gmail connection | Red button, no confirmation. |
| Users: Change role | Immediately changes all effective permissions and invalidates sessions via membership versioning | Select change submits immediately; no confirmation. |
| Users: Activate/deactivate | Enables or cuts off account access | Immediate button; no confirmation. Self-status button is disabled but does not explain why. |
| Users: Create user | Creates a workspace member with the chosen role and reveals an activation link | Immediate form submit; the chosen permissions are visible, but there is no confirmation. |
| Message templates: Approve revision | Makes a new approved template revision available to later drafting | Primary submit only; no confirmation. |
| Prompts/writing instructions: Save | Changes instructions used by future generated content/replies | Primary submit only; no confirmation or change summary. |
| Knowledge fact/context: Save | Creates **and approves** a revision used by retrieval | Primary submit only; the UI presents it as approved without a separate approval confirmation. |
| Categories/rules: Save | Changes future prospect discovery inputs | Primary submit only; no impact summary. |
| Business profile: Save | Changes approved company facts/copy inputs for future drafts | Primary submit only; optimistic concurrency guards stale writes, but there is no effect confirmation. |
| Catalog: Upload | Adds a PDF that can be attached/referenced later | Immediate upload submit; file name is visible, no confirmation. |
| Gmail: Connect/test | Starts external OAuth or calls Gmail to test credentials | Immediate buttons; OAuth itself supplies provider consent for connect. |

There is no Next control for Overture sync, follow-up-topic editing, scheduled-attempt authorization, activation-link regeneration, login unlock, exports, suppression management, prospect regeneration, or configuration deletion/toggle, even though REST or legacy operations exist.

### Legacy Django surface

| Trigger | Current guard |
|---|---|
| Campaign start/approve/start-approved/pause/resume | Collapsible `<details class="confirm">` confirmation with varying consequence text; no modal/typed word, and confirms are generally not danger-styled. |
| Campaign cancel | Collapsible confirmation, terminal-consequence copy, required reason, and danger button. No typed word/modal. |
| Prospect message regeneration / campaign reanalysis | Direct POST controls; no destructive confirmation comparable to cancellation. |
| Outbound approval and scheduled-contact authorization | Collapsible confirmation with consequence text; confirm button is not danger-styled. |
| Manual reply | Warning text and direct submit; no confirmation step. |
| Job retry | Collapsible explanation and required reason (minimum length); not a danger-styled modal. |
| Gmail disconnect | Collapsible consequence text and danger button. |
| Configuration delete | Collapsible consequence text and danger button. |
| Configuration toggle | Immediate POST; no confirmation. |
| Contact-wide “No contactar” checkbox | Auto-submits on change; no confirmation. |
| Manual contact creation | Establishes the Organization as a Contact and changes campaign eligibility | Direct form submit; no consequence confirmation. |
| Create contact/email restriction | Collapsible explanation, required reason, and danger button. |
| Revoke restriction | Collapsible explanation and required reason; confirm is not danger-styled. |
| Human-task resolve/dismiss | Collapsible explanation and required note; no danger distinction between outcomes. |
| Set preferred email / validate email | Immediate POST controls; no confirmation. |
| Plan activate/pause/disable/snooze | Immediate POST forms; no confirmation. |
| User creation/role/status/reset link/unlock | Direct forms; no confirmation. |
| Automation LIVE | Password reauthentication in the same form; no modal or typed confirmation word. |
| Follow-up topic, prompt, business profile, template, fact/context saves/approvals | Direct forms; no impact confirmation. Fact/context “create” calls save services that also approve. |
| Overture sync | Direct form POST; no confirmation of release/province/import impact. |
| Gmail test/connect | Direct POST; OAuth provider consent guards connect. |
| Fake inbound injection | Direct POST, ADMIN-only and fake-provider constrained. |

### API-only or externally callable effect points

All mutation endpoints remain callable independently of the UI. Backend capability, workspace, state, suppression, idempotency, kill-switch, and send-mode checks are the actual security/safety controls; UI confirmation is not an API guarantee. In particular:

- Campaign actions, outbound authorization, manual replies, and job retry can queue work. Campaign actions and outbound/job operations use an `Idempotency-Key`; manual reply carries its UUID in the body.
- Contact plan state, scheduled-attempt draft/authorize, restrictions/revocation, preferred email, and email validation are effect-bearing endpoints. The scheduled-attempt endpoints are currently unusable because of the confirmed dispatch signature defects.
- LIVE activation requires recent reauthentication and independent backend policies, but the API does not require a typed confirmation token.
- Overture sync queues a Celery task after an ADMIN-only inline view check.
- Knowledge fact/context POST currently saves and approves immediately; the separate approve endpoints therefore guard only revisions produced through other/internal creation paths.
- User activation-link generation returns a raw single-use URL to the ADMIN caller; the Next UI does not expose that existing endpoint.
