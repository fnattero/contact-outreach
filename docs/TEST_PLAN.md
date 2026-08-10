# Plan de pruebas

## 1. Estrategia y gates

pytest/pytest-django cubren dominio, PostgreSQL, workers y E2E HTTP/HTMX. `pytest-socket` bloquea
red; HTTP/DNS/Gmail/Overture/LLM siempre usan fakes. Celery corre eager para unit/integration y con
worker de test en escenarios de recovery/concurrencia.

Cada fase ejecuta:

```bash
make lint
make typecheck
make test
make test-e2e
make check       # incluye makemigrations --check --dry-run
```

Antes de revisión también se ejecutan migraciones frescas/upgrade, tests de seguridad relevantes y
`git diff --check`. El gate inicial exige 83% de líneas sobre código de aplicación, excluyendo
migraciones generadas; la meta de endurecimiento siguiente es 85% total y 95% en autorización,
elegibilidad/restricción, transiciones, idempotencia, SSRF, contexto/policy, MIME y Gmail. Coverage
nunca sustituye negativos.

## 2. Migraciones y compatibilidad

- Fresh install hasta head con Workspace/admin seed, mensajes fijos y geografía.
- Upgrade desde fixtures con campañas DRAFT/RUNNING/COMPLETED/FAILED, prospects duplicados,
  emails secundarios, respuestas/threads, manual suppressions, unsubscribe, bounce, auto-reply,
  PDFs, ciphertext y audit.
- Verificar que UUIDs, Gmail/RFC IDs, bodies, timestamps, attachment hashes/order, restricciones,
  ciphertext, ProviderUsage y AuditEvent no cambian.
- Backfill Prospect -> Organization/Identity/EmailAddress/Enrollment con merge por email, GERS,
  domain y name/address; conflictos concurrentes convergen.
- Backfill Contact sólo desde reply humano/manual restriction/manual entry; unsubscribe queda Contact
  no-contact; AUTO_REPLY/BOUNCE no promueven.
- Backfill thread -> Conversation y vínculos inbound/outbound, incluyendo varios threads por
  Contact.
- Validación count/hash detiene contract si algo no coincide; ContactLedger/ContactOverride sólo se
  retiran después. No se alteran migraciones Overture existentes.
- Protección del último admin y singleton Workspace bajo transacciones concurrentes.

## 3. Autenticación, permisos e Internet readiness

- Matriz completa por ruta y método para anonymous, ADMIN y VENDEDOR; también llamar servicios sin
  view para probar autorización de dominio.
- VENDEDOR sólo lee Resumen, campañas no-borrador, SENT y Contact timelines; no POST excepto logout,
  no drafts/audience/export/PDF/config/integration/jobs/audit/technical detail.
- Segundo admin opera campaña ajena; creator nunca limita.
- Token activation/reset aleatorio, sólo digest, mostrado una vez, single-use, 24 h, expirado/usado.
- Intentos 1–4 neutrales, quinto fija 30 min, bloqueado devuelve 429/Retry-After sin extender,
  success limpia par; username normalization/HMAC y admin/emergency unlock.
- IP spray: 20 failures/30min cruzando usernames; concurrencia no pierde counts. Proxy header
  spoofing desde origen no confiable no cambia IP; proxy allowlisted sí.
- TOTP admin requerido, optional seller, QR enrollment, 10 recovery hashes de un uso,
  regeneración/invalidation, last-admin and session invalidation on role/deactivation.
- CSRF en cada mutación/HTMX; GET no muta; no-store en auth/message/contact/health detail.
- Production settings: exact hosts/origins, secure cookies, SSL redirect, trusted proxy, staged
  HSTS, CSP sin inline scripts, referrer/nosniff/frame. `manage.py check --deploy` profile pasa.
- Health público no filtra topología; detalle sólo admin. Loopback continúa default.

## 4. Organizaciones, Contactos y restricciones

- Normalización email Unicode/IDNA/puntuación; un email único Workspace y una Organization; merge
  concurrente de GERS/domain/name-address.
- Email preferred parcial unique, ownership conflict y provenance de Overture/web/inbound/manual.
- Reply genuino promueve una sola vez, crea Conversation y cancela reminders; Contact manual sólo
  email; Organization completa queda excluida en todos sus canales.
- Email manual se valida fuera del request con resolver fake; `VALID/TRANSIENT/INVALID`, tres
  retries y la acción visible de reintento nunca habilitan un envío antes de `VALID`.
- AUTO_REPLY/BOUNCE no Contact; unsubscribe humano sí y estado friendly correcto.
- “No contactar contacto” bloquea todos emails; “No usar email” sólo canal. Unsubscribe no se
  levanta; bounce sólo invalida email; manual lift exige ADMIN+reason+audit.
- Supresiones históricas aparecen dentro de Contact y no existe page primaria Supresiones.
- Cambio de restricción entre preparation/approval/queue/send cancela fail-closed.

## 5. Geografía y Overture

- Jerarquía/códigos oficiales, parent required, duplicate district names bajo provincias distintas,
  labels Partidos/Departamentos/Comunas/Barrios, CABA 48 barrios y ausencia de sección visible para
  zonas custom.
- UI multiprovincia: mapa clickeable por provincia, expand/search/select all/clear por provincia,
  fieldsets/keyboard, snapshots no cambian si se edita seed.
- Release/partition constraints: independent READY, mixed-release rejection, missing coverage copy
  incluye provincia y acción Datos de búsqueda.
- Dos provincias lejanas generan dos `record_batch_reader` con bboxes acotados; luego exact
  point-in-polygon. Nunca un bbox combinado.
- Import streaming, duplicate GERS, schema/taxonomy/basic_category fail closed, permanently_closed,
  source/license/NOTICE, max rows, lock, rollback/activation/retention provincial.
- Backfill legacy CABA y preservación de cobertura inesperada histórica.
- SearchQuery usa partition de distrito; orden/cursor/replay stable, rules/confidence/raw cap y cero
  sockets durante campaña.

## 6. Campañas, copy y aprobación

- Nueva campaña produce exactamente cero calls LLM analyze/draft/classify.
- Seeds byte-for-byte, saltos LF y firma BusinessProfile determinista; todos recipients reciben
  mismo subject/body/signature sin placeholders.
- DRAFT -> DISCOVERING -> AWAITING_APPROVAL: audiencia final existe antes de confirmar.
- CAMPAIGN approval congela audience/content/signature/attachments/schedule hashes y actor/date;
  doble click idempotente, cambios posteriores no entran.
- PER_MESSAGE permite edit/review, start separado y excluye no aprobados; vendedor/anonymous no
  pueden actuar.
- Historical AIAnalysis/campaign remains readable and cannot regenerate/resend.
- Eligibility en preparation/approval/queue/pre-Gmail: selected valid email, no Contact,
  restriction, state/mode, barriers y attachment integrity.
- Pausa/cancel/recovery/restart no omiten approval ni generan efectos tardíos.

## 7. PDFs, MIME y delivery

- Ordered multiple PDFs preserve order/filename/content; minimum one initial.
- 15 MiB por archivo; 17 MiB boundary exacto y excedido; 24 MiB serialized MIME boundary exacto y
  excedido con base64/headers reales.
- Alterar/faltar cualquiera cambia/invalida hash, pausa y produce cero send; jamás partial set.
- Initial/referred include all campaign PDFs; reminder/manual/auto/ack/scheduled none by default.
- MIME text/plain UTF-8, single To, no CC/BCC, deterministic opaque headers/Message-ID.
- SEND_MODE/kill/campaign mode/Gmail/quota/window preflight, 403/429/auth/permanent, ambiguous timeout
  and reconciliation no duplicate.
- Dry-run validates MIME but no Gmail; review-only no MIME/Gmail.

## 8. Same-day y recordatorio

- Concurrent campaigns reserving same email/local date produce exactly one reservation; loser moves
  to next configured business day and friendly message appears.
- Timezone midnight/DST-independent Argentina cases, weekends/windows, release after cancellation
  only when reconciliation-safe.
- No cross-campaign cooldown: unanswered email eligible later; Contact is not.
- Zero/one reminder, default delay 3 calendar days from confirmed sent_at, business-window shift,
  same thread/subject/References/In-Reply-To and unique idempotency.
- Human reply/manual Contact/unsubscribe/bounce cancels at every race point; auto-reply does not.
- Campaign completion waits sent/cancelled/ineligible/failed reminder terminal; restart/double Beat
  cannot make second.

## 9. Sync, candidates y bounded context

- Gmail baseline, paginated history, cursor after commit, duplicate events, 404 fallback bounded,
  unrelated inbox ignored, direct inbound from existing Contact imported, unknown sender ignored,
  manual Gmail `SENT` reply projected into Contact timeline, own outbound deduplicated, task closed,
  queued automatic reply cancelled and no new decision/send; auth degradation and per-connection
  lock.
- Prove decide_reply starts after commit/lock release; LLM failure preserves inbound/cursor.
- Deterministic unsubscribe/bounce/auto precedence prevents LLM effect.
- Candidate extraction: plain/mailto, punctuation, Unicode/IDN, dedupe, maximum ten,
  NEW_CONTENT/SIGNATURE/QUOTED, quoted markers, obfuscated rejected, multiple ambiguous -> human.
- Context always contains full authored inbound plus current global context. Campaign replies add
  original/direct parent; direct Contact inbound adds Contact profile. It may also include up to 6
  cross-thread recent, source-linked memory and <=3 approved facts selected by embeddings. No
  PDF/raw HTML/unapproved facts.
- RAG retrieval: deterministic fake embeddings, OpenAI-compatible adapter payload validation,
  cached embedding reuse, model/dimension hash invalidation, low similarity/ambiguous near-tie ->
  <=3 suggested facts with `may_be_irrelevant=true`, and provider/schema failure -> HumanTask when
  facts are needed.
- Mandatory exact boundary/overflow -> HumanTask without provider; total <=24.000 chars and stable
  manifest IDs/versions/retrieval status/hash. DB/logs do not duplicate prompt bodies.
- Admin writing instructions save from the dashboard, count toward the bounded LLM input and arrive
  as `ADMIN_WRITING_INSTRUCTIONS` without weakening fixed policy.
- Prompt injection in inbound/signature/quoted/memory/fact cannot change schema/action/fact IDs.

## 10. Reply decisions, SHADOW y policy

- Strict output validates classification/intent/action/confidence/candidate/fact/body/reason and
  rejects extra/unknown IDs, wrong region, stale revision or incompatibility.
- OFF and SHADOW create zero Gmail authorizations/calls under all provider outputs; SHADOW only
  persists the proposal and facts used.
- LIVE sends the validated LLM `proposed_body` as the final reply body, while selected approved
  facts remain required evidence and are never concatenated as replacement paragraphs.
- LIVE enable requires admin reauth and audit. Confidence 0.899/0.90 cannot override intent policy.
- Auto allowlist only product/company/simple clarification with approved facts and explicit
  redirect. Polite ack/not interested no reply.
- Every meeting/date, pricing, negotiation, complaint, legal/privacy, unsupported technical,
  multiple intent, ambiguous candidate, ownership conflict, insufficient context and
  provider/schema failure opens task.
- Prompt regression requires explicit call/meeting coordination to return `HUMAN`; policy regression
  overrides a misclassified automatic reply for that case while leaving general attention-hours
  and phone-information questions eligible for normal policy evaluation.
- HumanTask opens once under double processing, suspends Conversation, prevents subsequent auto,
  vendor view-only; resolve/dismiss only admin and resumes only when no open task.
- Manual reply success resolves the inbound's open `REPLY_REVIEW` task and resumes the Conversation
  only when no other task remains; Gmail failure/reconciliation leaves the task open.
- Limits: fourth automatic in Conversation rolling 24h and 21st Workspace/day blocked; concurrent
  reservations; AUTO_REPLY_KILL_SWITCH checked immediately pre-send.
- Recheck regression: an automatic reply sent later in another thread for the same Contact does
  not stale an already prepared decision, while later human/manual context still blocks.

## 11. Redirect E2E y alerts

Redirect happy path:

1. inbound human promotes Contact;
2. NEW_CONTENT candidate selected;
3. service validates MX/restriction/ownership and adds EmailAddress with source inbound;
4. fixed proposal + every origin campaign PDF opens new thread;
5. confirmed/reconciled success authorizes exact ACK in original thread;
6. timeline shows both threads under one Contact.

Cover invalid/ambiguous/multiple/signature/quoted candidate, other Organization, late restriction,
missing PDF, Gmail fail before/after accept, reconciliation and double task. Failure never produces
false ACK. Proposal and ACK Message-IDs/keys differ; one semantic action per inbound.

Human alert tests: persistent badge first; one generic Gmail notification per active admin with
exact subject and secure task link, no inbound/contact data; PUBLIC_BASE_URL missing -> notification
failed but task open; duplicate/reconciliation idempotent; vendedor not recipient.

## 12. Comunicación programada

- Global FollowUpTopic form validates objective, cadence default 30/min 7, mode, active flag and next
  due date; Contact only approves/pauses/disables topics and requires preferred email.
- Scheduler clock/idempotency derives due from topic + contact history; LLM uses same context/facts
  and cannot choose date.
- REVIEW creates draft/no Gmail; AUTO still needs gate/policy/limits and relationship kill switch.
- Restriction, open task, suspension, insufficient context, no preferred email and kill switch
  result in no send/task as specified.
- Genuine interaction pushes due >= topic cadence; confirmed scheduled send computes from sent_at.
- New thread and no campaign same-day reservation; topic approval never changes Contact exclusion.

## 13. Resumen y UX

- Derive every metric from fixtures with multiple threads/replies: unique initial recipients,
  message-kind sends, unique human responders, positive, Contacts, bounce/unsubscribe, auto-resolved,
  human-required/open, median response/intervention and after-initial/after-reminder.
- Response denominator only enrollments with confirmed initial; multiple replies not double counted;
  zero shows `—` plus explanation. Workspace all-time and campaign filter agree.
- Spanish labels/empty/error states, result-first statuses, toggle consequences, progressive
  technical details admin-only, no technical IDs in normal/vendor views.
- Contact list/timeline grouped by thread, multi-email/provenance/campaign/restriction/task/topics;
  no-contact checkbox creates/revokes manual contact restrictions; redirect threads ordered
  chronologically.
- Responsive, semantic fieldsets, keyboard operation/focus, screen-reader status and color not sole
  signal. Cache headers on body endpoints.

## 14. E2E fake y rollout

E2E fake ejecuta con formularios/views/tasks reales:

1. fresh Workspace, admin TOTP, vendedor y permission checks;
2. seed profile/messages/knowledge/geography, two READY provinces and multiple PDFs;
3. deterministic discovery and campaign approval with zero initial LLM;
4. dry-run/live fake, same-day reschedule and reminder;
5. inbound promotion, SHADOW review, LIVE reauth and LIVE policy;
6. safe fact reply, redirect two-thread saga, meeting HumanTask/notifications;
7. scheduled Contact review/automatic and metrics.

Rollout verification order: backup -> migrations/backfill hashes -> fixed-message dry-run ->
campaign sending -> Contactos -> SHADOW evaluation -> explicit LIVE enable -> scheduled Contact
pilot. Independent kill switches stay active until their step.

## 15. Quality review final

- No pending migrations, lint/type/tests/E2E green, red bloqueada.
- Fresh and representative upgrade migration green; no migration histórica modificada.
- Diff review: duplicate sends, invalid transition, service permission bypass, missing restriction,
  stale context, leaked secret/body, unsafe URL, partial PDF, missing retry/reconciliation.
- Production-like deploy checks and trusted-proxy suite green; actual HTTPS provisioning remains an
  explicitly uncompleted external go-live gate.
