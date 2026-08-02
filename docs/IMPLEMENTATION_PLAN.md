# Plan de implementación — producto revisado

## Reglas del plan

Este plan evoluciona el monolito Django/HTMX existente. Se ejecuta en orden y cada fase deja la
aplicación migrable, con tests y defaults seguros. Toda modificación de una decisión actualiza en
el mismo cambio PRODUCT_SPEC, ARCHITECTURE, DATA_MODEL, STATE_MACHINES, SECURITY, INTEGRATIONS,
TEST_PLAN, ASSUMPTIONS y la matriz inferior cuando corresponda.

- Sólo migraciones forward; no editar ni renumerar las migraciones Overture actuales.
- Preservar mensajes, adjuntos, IDs Gmail/RFC, auditoría, ciphertext y campañas históricas.
- Expand/backfill/switch/contract para relaciones contact-centric; validar conteos/hashes antes de
  retirar legacy.
- Ningún test usa red. Cada fase corre format, Ruff, mypy, migration check, unit/integration y fake
  E2E relevante.
- Defaults: dry-run, send kill switch, auto-reply kill switch, relationship kill switch y SHADOW.
- La UI de cada fase usa español simple, progressive disclosure, accesibilidad y permisos desde el
  primer corte; no se difiere “hacerla entendible”.

## Fase 0 — Reset de especificaciones y baseline

**Objetivo:** fijar el nuevo contrato antes de cambiar schema o comportamiento.

- Actualizar conjuntamente los nueve documentos gobernantes con Workspace/roles, Contactos,
  provincias, contenido fijo, approval campaign-level, múltiples PDFs, same-day, reminder,
  reply decisions, redirects, alerts, scheduled contacts, métricas y readiness.
- Remover supuestos “local-only”, “single user”, “no follow-ups” y “no automatic replies”.
- Declarar que TLS/proxy real sigue diferido y que no hay LangGraph.
- Inventariar dirty worktree/migrations y ejecutar `make check` como baseline; documentar cualquier
  fallo preexistente antes de crear migraciones nuevas.

**Migraciones:** ninguna.

**Terminado:** docs y trazabilidad no se contradicen, baseline conocido, live sigue cerrado.

## Fase 1 — Workspace, roles y autenticación

**Objetivo:** convertir ownership de un usuario a una empresa multiusuario segura.

- Crear singleton `Workspace`, `Membership(ADMIN|VENDEDOR)`, activation/reset token,
  LoginThrottle y recovery codes; integrar `django-otp` TOTP.
- Backfill propietario actual como primer admin. Mover BusinessProfile,
  IntegrationConfiguration, PromptConfiguration, GmailConnection, Campaign y Catalog a Workspace
  manteniendo user FKs de atribución.
- Centralizar capabilities y reemplazar filtros `campaign.created_by == request.user` en views,
  services, downloads, exports y tasks.
- Implementar user management, link single-use 24 h, last-admin protection, session invalidation,
  fifth-attempt 30min fixed lock, IP spray, admin unlock y comando de emergencia.
- Aplicar navegación/responses exactas para VENDEDOR y no-store a bodies/contact data.

**Migraciones:** expand Workspace/membership/auth state; data backfill; constraints singleton/last
admin en servicio. No borrar User/history.

**Pruebas:** matriz completa route-method/service, auth concurrency, TOTP/recovery, trusted IP,
sesiones y segundo admin sobre campaña ajena.

**Terminado:** ninguna autorización depende del creador y VENDEDOR no muta ni ve información
excluida.

## Fase 2 — Modelo contact-centric y backfill

**Objetivo:** separar identidad empresarial, relación comercial y participación de campaña.

- Expandir `Organization`, `OrganizationIdentity`, `EmailAddress`, `Contact`,
  `CommunicationRestriction`, `Conversation` y `CampaignEnrollment`.
- Backfill un Organization/Enrollment por Prospect, fusionando mediante claves globales actuales.
- Promover Contacts desde replies humanos y restricciones manuales; unsubscribe humano se marca
  “Baja solicitada”; AUTO_REPLY/BOUNCE permanecen eventos no-contact.
- Backfill Gmail thread IDs a Conversation y vincular inbound/outbound sin alterar IDs/cuerpos.
- Cambiar discovery, eligibility, mailbox y consultas UI a las nuevas FKs.
- Excluir toda Organization con Contact; implementar scopes contact/email y reglas irreversibles.
- Sólo tras reportes count/hash limpios, retirar ContactLedger, ContactOverride e identidad legacy
  campaign-owned no necesaria.

**Migraciones:** al menos expand, data backfill, switch FKs/constraints y contract separados.

**Pruebas:** fresh/upgrade, dedupe/concurrencia, promotions exactas, restrictions, multi-email,
multi-thread, cancel reminder y reversibilidad admin auditada.

**Terminado:** cada envío futuro se decide por Organization/EmailAddress/Enrollment y la historia
legacy sigue legible.

## Fase 3 — Provincias, distritos y Overture particionado

**Objetivo:** seleccionar múltiples provincias sin imports/bboxes nacionales.

- Extender SearchZone con código/nivel/padre/provincia/selectable/source/attribution y uniqueness
  jerárquica.
- Agregar seed oficial versionado para todas las provincias y divisiones; CABA barrios se conservan
  y las custom zones quedan sólo como compatibilidad legacy no visible.
- Implementar selector expandible/buscable por provincia con select all/clear y etiquetas locales.
- Separar `OvertureRelease` y `OvertureCoveragePartition` READY provincial; agregar ordered
  CampaignCoverageSelection con same-release constraint.
- Importar una bbox por provincia, filtrar distritos exactos y hacer que cada SearchQuery apunte a
  su partition.
- Backfill snapshot actual como CABA y preservar coverage inesperada histórica.
- Bloquear discovery con mensaje accionable si falta READY.

**Pruebas:** duplicate district names, hierarchy/CABA, mixed release, two distant bbox reads,
activation/rollback/retention y UX multi-provincia.

**Terminado:** una campaña puede combinar distritos de varias provincias del mismo release con
lecturas provinciales reproducibles.

## Fase 4 — Contenido fijo, adjuntos múltiples y aprobación

**Objetivo:** retirar IA del contacto inicial y aprobar una campaña sobre audiencia final.

- Crear revisiones de WorkspaceMessageTemplate y seed exacto; componer firma BusinessProfile en
  dominio. Congelar body/signature en Campaign.
- Cambiar lifecycle a DRAFT/DISCOVERING/AWAITING_APPROVAL/RUNNING/COMPLETED y paths laterales.
- Discovery crea enrollments y mensajes deterministas; cero LLM relevance/copy. AIAnalysis legacy
  read-only.
- Implementar approval mode CAMPAIGN default con hashes/snapshot y PER_MESSAGE con start separado.
- Crear CampaignAttachment ordenado y OutboundAttachment inmutable; initial requiere al menos uno.
- Validar 15 MiB por PDF, 17 MiB source sum y 24 MiB serialized MIME; integrity fail pauses, no
  partial.
- Generalizar OutboundMessage kinds sin romper FIRST_CONTACT/MANUAL_REPLY históricos.

**Pruebas:** byte exacto/cero LLM, lifecycle/approval races, freeze hashes, multiple MIME/order/
limits/tamper y permisos/UX.

**Terminado:** admin aprueba exactamente “a quién, qué, cuándo y con qué PDFs” antes de delivery.

## Fase 5 — Elegibilidad, protección same-day y un reminder

**Objetivo:** permitir recontacto futuro sin saturar el mismo día y automatizar un seguimiento.

- Rechecks comunes en preparation/approval/queue/pre-Gmail sobre EmailAddress, Contact,
  restriction, mode/barriers y attachments.
- Reemplazar ledger permanente por `CampaignDeliveryReservation(email, local_date)` única; mover
  conflictos a siguiente business window con copy friendly. No agregar cooldown.
- Agregar reminder opcional, máximo uno, delay default 3 días desde sent_at, due shifted a window,
  reply thread headers y attachments none.
- Cancelar ante human reply, Contact manual, unsubscribe o bounce; no ante auto-reply.
- Ajustar completion para esperar estado terminal del reminder y recovery idempotente.

**Pruebas:** concurrencia same-day/timezone, cross-campaign later eligibility, reminder clock/
headers/cancellation races/double Beat/completion.

**Terminado:** ningún email recibe dos iniciales/reminders de campañas el mismo día y nunca más de
un reminder por initial.

## Fase 6 — Contactos y conversaciones fáciles de usar

**Objetivo:** hacer de la relación comercial la UI principal.

- Reemplazar navegación primaria Prospectos/Supresiones/Respuestas por Contactos y Necesita
  atención; audiencia queda dentro de Campaign.
- Lista con display/company, preferred email, friendly status, last interaction, next contact y
  task badge.
- Detalle cronológico agrupado por thread: emails/provenance, campaigns, restrictions/history,
  notes, automation, tasks y plan.
- Agregar Contact manual, edición de canales/preferencia, validación MX asíncrona con retry visible,
  no-contact controls y restricciones en el contexto del Contact.
- VENDEDOR obtiene timeline sin controls/technical details. Admin ve sección técnica colapsada.
- Aplicar no-store, responsive, teclado/foco, semantic fieldsets y error/empty states accionables.

**Pruebas:** role-specific nav/access, ordering/grouping, multiple threads/emails, validación manual
sin DNS en request, restriction UI, Spanish labels/accessibility/cache.

**Terminado:** el operador comprende estado y próxima acción sin conocer providers, hashes o IDs.

## Fase 7 — Conocimiento, contexto y decisiones SHADOW

**Objetivo:** analizar respuestas de forma medible sin autorizar Gmail.

- Crear contexto general versionado, KnowledgeFact/Revision con approval y área “Información para
  responder consultas”; no parsear PDFs.
- Crear `EmbeddingProvider`, configuración de proveedor/modelo/dimensiones, embeddings cacheados de
  facts aprobados y búsqueda semántica que selecciona hasta tres facts o escala por baja/ambigua.
- Persistir Gmail/deterministic effects y publicar LLM on_commit fuera del sync lock.
- Extraer <=10 EmailCandidates literales/regionales y resolver sintaxis/MX/restricción/ownership.
- Crear ConversationMemory source-linked y bounded context builder de 24k con mandatory/global
  context/recent/memory/RAG facts rules. Overflow mandatory crea HumanTask.
- Extender LLMProvider con strict `decide_reply`; dynamic candidate/fact/action enums, manifest/hash
  y no raw invalid output.
- Crear ReplyDecision, feedback admin y OFF/SHADOW(default)/LIVE setting; SHADOW cero Gmail.
- Calcular gate >=30/>=10/>=90%/cero unsafe y exigir reauth para enable LIVE.

**Pruebas:** candidates/IDN/quoted/obfuscated, embeddings/RAG low/ambiguous/cache/provider,
prompt injection, cross-thread bounded context, unknown IDs/schema, zero SHADOW effects,
qualification boundaries.

**Terminado:** admins pueden revisar qué habría hecho, facts usados y exactitud con trazabilidad
acotada.

## Fase 8 — Acciones automáticas, redirects y tareas/alertas

**Objetivo:** activar sólo efectos allowlisted y escalar el resto.

- Implementar policy engine y intent matrix; confidence >=0.90 necesaria. No-action para polite ack
  y not interested; HumanTask para toda categoría riesgosa/fallo.
- HumanTask OPEN suspende Conversation; admin resolve/dismiss, vendedor read-only.
- Reutilizar executor durable Gmail para AUTOMATIC_REPLY con context/original/parent guarantees,
  pre-send rechecks y `AUTO_REPLY_KILL_SWITCH`.
- Implementar redirect saga candidate lock -> EmailAddress same Contact -> REFERRED_PROPOSAL new
  thread + origin PDFs -> confirmed -> exact REDIRECT_ACK original thread. Separar keys/semantic
  action; failure -> task, no false ACK.
- Reservar rate limits 3/Conversation/24h y 20/Workspace/day.
- Crear NotificationDelivery: badge durable + generic email a admins con secure link,
  PUBLIC_BASE_URL, reconciliation; no inbound data.

**Pruebas:** policy matrix, suspension/limits/kill switch, redirect E2E success/fail/ambiguity/
reconciliation/double task y notification privacy/idempotency/fallback.

**Terminado:** sólo requests claramente seguras pueden enviar; todo riesgo queda visible y no se
pierde si falla el email de alerta.

## Fase 9 — Comunicación programada con Contactos

**Objetivo:** contacto periódico opt-in separado de campañas.

- Crear FollowUpTopic global y ContactCommunicationPlan como aprobación por contacto; disabled
  default, preferred email, cadence global default 30/min 7, review default, auto optional,
  pause/snooze/next due derivado.
- Scheduler decide vencimiento/idempotency desde tema global + historial. LLM propone body con mismo
  bounded context/knowledge.
- REVIEW crea draft; AUTOMATIC atraviesa gate/policy/executor y relationship kill switch, en hilo
  nuevo.
- Bloquear con restriction, HumanTask, suspension, context insufficiency o no preferred email.
- Recalcular next due desde genuine interaction o confirmed sent_at.

**Pruebas:** clocks/cadence/snooze, review vs auto, every blocker/recovery, new thread and campaign
ineligibility unchanged.

**Terminado:** sólo Contactos opt-in reciben comunicaciones y el admin siempre conoce próxima fecha.

## Fase 10 — Resumen y métricas

**Objetivo:** mostrar resultados útiles sin contadores mutables ni doble conteo.

- Queries derivadas para recipients/initial/reminder/auto/scheduled, unique human responders,
  response and positive rates, Contacts, bounce/unsubscribe rates, auto-resolved, human required/open,
  median response/intervention y source stage initial/reminder.
- Denominadores exactos por Enrollment/confirmed initial; múltiples replies no duplican.
- Workspace all-time y campaign filter; zero = `—` con explicación. Diferir arbitrary date cohorts.
- Presentación responsive y lenguaje no técnico, con definiciones breves.

**Pruebas:** fixture con múltiples replies/threads/campaigns, medianas, zero denominators, filters,
query counts y role visibility.

**Terminado:** Resumen permite evaluar outreach, automatización y carga humana de un vistazo.

## Fase 11 — Readiness, hardening y rollout

**Objetivo:** cerrar seguridad de aplicación y probar upgrade/operación; no provisionar TLS.

- Production settings para exact hosts/origins, secure cookies, proxy HTTPS, SSL redirect, staged
  HSTS, CSP, referrer/nosniff/frame/permissions protections.
- Mover inline JS a static; proteger health detallado y todos los body endpoints.
- Agregar `check --deploy`, permission matrix y trusted-proxy spoof tests; actualizar runbooks,
  backup/restore y kill-switch procedures.
- Ejecutar fresh/upgrade migrations, full fake E2E y rollout controlado:
  backup -> migration validation -> fixed dry-run -> campaign sending -> Contactos -> SHADOW
  evaluation -> explicit LIVE -> scheduled Contact pilot.
- Mantener loopback default y un gate externo marcado pendiente para reverse proxy/TLS/certificados.

**Migraciones:** sólo ajustes forward detectados; `makemigrations --check` limpio.

**Terminado:** app-side checks pasan, backups/restores verificados y ninguna documentación afirma
Internet go-live. Todas las automatizaciones conservan kill switches independientes.

## Matriz de trazabilidad requisito -> fase

| Requisito | Fases | Evidencia principal |
| --- | --- | --- |
| FR-01 Workspace/roles/auth | 1, 6, 11 | models/capabilities/TOTP/lockout/permission matrix |
| FR-02 Organizations/Contacts/restrictions | 2, 5, 6 | backfill, eligibility y Contact UI |
| FR-03 Provincias/distritos | 3 | hierarchy seed y selector mapa/lista multi-provincia |
| FR-04 Overture partitions | 3, 11 | release/partition/import/read tests |
| FR-05 Discovery/eligibility | 2–5 | Organization enrollment, local search, common rechecks |
| FR-06 Fixed content/lifecycle/approval | 4 | seed, zero-LLM, hashes y modes |
| FR-07 Multiple PDFs | 4, 8 | attachment snapshots, MIME y redirect |
| FR-08 Sending/same-day | 4, 5, 8 | reservations, executor/reconciliation/barriers |
| FR-09 One reminder | 5 | scheduling, cancellation y completion |
| FR-10 Gmail/promotion | 2, 5, 7 | sync on_commit, Contact/Conversation, reminder cancellation |
| FR-11 Knowledge/candidates/context | 7 | global context, revisions, embeddings/RAG, extractor, memory, manifests |
| FR-12 Decisions/SHADOW/LIVE gate | 7, 8 | provider schema, feedback, qualification/policy |
| FR-13 Auto/redirect/human tasks | 8 | policy, saga, rate limits y suspension |
| FR-14 Human alerts | 8 | task badge y NotificationDelivery |
| FR-15 Scheduled Contacts | 9 | follow-up topics, approvals, attempts, scheduler/executor |
| FR-16 Contact/conversation UI | 2, 6, 8, 9 | timeline, threads, list no-contact checkbox, restrictions/tasks/topics |
| FR-17 Metrics | 10 | derived query service and dashboard |
| FR-18 Internet readiness | 1, 11 | auth, settings/headers/proxy tests and deferred TLS gate |
| FR-19 State/idempotency/audit/interfaces | 1–11 | services, constraints, jobs, fakes and audit |
| FR-20 UX/accessibility | 1–11 | phase-level UI criteria and UX suite |
| OPS-01 Stack | 0, 11 | existing scaffold and final deploy checks |
| OPS-02 Operations | 0, 11 | make gates, backup/restore/runbooks |
| DM-01 Data | 1–10 | versioned migrations and DATA_MODEL entities |
| QA-01 Tests | 0–11 | TEST_PLAN suites and network-blocked E2E |

## Riesgos que bloquean rollout live

1. Backfill count/hash mismatch o historia/IDs/ciphertext alterados.
2. Bypass de roles, TOTP/lockout/proxy defectuoso o último admin no protegido.
3. Restricción/contact exclusion/same-day/reminder/idempotency defectuosos.
4. PDF parcial/tamper o reconciliación Gmail que permita duplicados/false ACK.
5. SHADOW gate incompleto, task suspension/rate limits/kill switch defectuosos.
6. Secrets/PII/prompt bodies en logs, alerts o vistas indebidas.
7. Backup/restore no probado o revisión legal/deliverability ausente.
8. Para exposición pública: proxy TLS/certificados/monitoring/runbook aún no implementados.

Ningún riesgo se acepta silenciosamente; la fase correspondiente permanece cerrada.
