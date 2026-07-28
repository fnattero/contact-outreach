# Modelo de datos

## 1. Convenciones

Las entidades propias usan UUID, timestamps UTC y zona de negocio
`America/Argentina/Buenos_Aires`. Emails/dominios/nombres conservan original y normalizado. Los
snapshots, revisiones, mensajes enviados, adjuntos de mensaje, restricciones irreversibles,
decisiones y auditoría son append-only o inmutables según se indica. Configuración referenciada se
archiva. Las migraciones son forward y nunca reescriben migraciones Overture existentes.

Las FKs `created_by`, `updated_by`, `approved_by` y `uploaded_by` atribuyen; no autorizan. Toda
entidad de negocio queda acotada al Workspace directa o transitivamente y los uniques globales del
producto incluyen `workspace` salvo IDs externos realmente globales dentro de una conexión.

## 2. Workspace, usuarios y autenticación

### Workspace

Singleton de la instalación: nombre, timezone, active, timestamps. Una constraint/servicio impide
crear una segunda fila. Perfil, integración, Gmail, configuración, catálogos, campañas,
organizaciones y conocimiento pertenecen aquí.

### User y Membership

Se conserva `django.contrib.auth.User`. `Membership` es única por `(workspace, user)` y tiene
`role=ADMIN|VENDEDOR`, `active`, timestamps, actor del último cambio y version de seguridad. El
servicio bloquea borrar usuarios con historia y desactivar/degradar al último ADMIN activo. Un
cambio sensible incrementa version e invalida sesiones.

### AccountActivationToken

Digest aleatorio, usuario, propósito `ACTIVATE|RESET`, expiración máxima 24 h, `used_at`, creador.
El token plano se muestra una vez; sólo el hash llega a DB. Único activo por usuario/propósito.

### LoginThrottle

`key_digest`, `kind=USERNAME_IP|IP`, ventana, `failure_count`, `locked_until`, timestamps. El digest
es HMAC de datos normalizados y una clave separada. `USERNAME_IP` bloquea al quinto fallo por 30
minutos; `IP` al vigésimo fallo agregado dentro de 30 minutos. Índices permiten lock/limpieza sin
guardar usernames ni IPs crudos.

### TOTPDevice y RecoveryCode

TOTP usa `django_otp.plugins.otp_totp.models.TOTPDevice` ligado a User. `RecoveryCode` conserva
digest, `used_at`, creación y set version; exactamente diez códigos nuevos se muestran una vez al
regenerar. Admin activo exige device confirmado o estado de enrollment temporal controlado.

## 3. Configuración del Workspace

### BusinessProfile

Relación 1:1 Workspace: identidad, vendedor, teléfono, domicilio, web, descripción, productos,
diferenciadores, firma aprobada y `profile_version`. La firma se agrega determinísticamente a los
mensajes fijos; la campaña copia texto y versión.

### IntegrationConfiguration y PromptConfiguration

Relación 1:1 Workspace. Integration guarda proveedores/parámetros no secretos, incluyendo
proveedor/modelo/dimensiones de embeddings; ciphertext write-only para LLM/Google y revisión. La
configuración OpenAI-compatible de embeddings usa la misma conexión OpenAI-compatible y credencial
del LLM, pero detrás de un contrato separado. Prompt conserva preferencias usadas sólo por
respuestas y comunicación con Contactos; no modifica campañas iniciales ni reglas de búsqueda. Root
keys, barreras live e infraestructura no se guardan.

### WorkspaceMessageTemplateRevision

`kind=INITIAL|REMINDER|REFERRED_PROPOSAL`, subject cuando corresponde, body de texto plano,
revision, `approved_at/by`, active y hash canónico. Sin placeholders. Una revisión aprobada no se
edita; otra fila la reemplaza para campañas futuras.

### KnowledgeFact y KnowledgeFactRevision

Fact agrupa una pregunta/hecho con categoría y estado. Revision contiene texto aprobado, fuentes
humanas, version, hash, `approved_at/by`, superseded flag y timestamps. Sólo revisiones aprobadas
entran al contexto. PDFs no se parsean automáticamente.

### WorkspaceKnowledgeContextRevision

Contexto global del Workspace: texto breve de background, fuente humana, version, hash,
`approved_at/by` y superseded flag. Una versión aprobada no se edita; aprobar una nueva reemplaza
la anterior para solicitudes futuras. Se inyecta siempre como orientación, pero no alcanza por sí
sola para fundamentar respuestas que requieran un dato puntual.

### KnowledgeFactEmbedding

Embedding cacheado por `KnowledgeFactRevision` aprobada, provider, model, dimensions e input hash.
Guarda vector normalizado cuando está `READY` o error redactado cuando está `FAILED`. Cambiar texto,
modelo, proveedor o dimensiones produce una fila nueva; no muta la revisión aprobada.

### SearchCategory y SearchCategoryRule

Categoría activa/archivada y reglas deterministas versionadas. Una regla admite código de
taxonomía y/o términos literales; AND dentro de regla, OR entre reglas. Campaña congela revisión,
orden y contenido.

### SearchZone

`workspace`, `official_code`, `name`, `normalized_name`, `level=COUNTRY|PROVINCE|DISTRICT|
NEIGHBORHOOD|CUSTOM`, `parent`, `province_code/name`, `selectable`, `label_plural`, GeoJSON WGS84,
bbox, boundary revision/hash, source/attribution, active/sort/archive. Unicidad por
`(workspace, source, parent, official_code)`; nombres repetidos en distintas provincias son válidos.
CABA conserva barrios como nivel seleccionable. Editar geometría crea revisión para futuro sin
mutar snapshots.

## 4. Overture particionado

### OvertureRelease

Identidad inmutable de release: release ID único, schema/taxonomy/importer versions, manifests,
metadata hash, sources/licenses/NOTICE, descubierto/creado. No significa cobertura completa ni
“activo” global.

### OvertureCoveragePartition

FK release y provincia: código/nombre, bbox, estado `IMPORTING|READY|FAILED|SUPERSEDED`, active,
boundary/import hashes, conteos, validaciones, source/license metadata y timestamps. Única por
`(release, province_code, import_revision)` y a lo sumo una active READY por provincia. Una falla
no desactiva otra partición READY.

### OvertureDatasetZone

Distrito congelado dentro de una partición: SearchZone origen, código/nombre/nivel, geometría,
bbox, hash/revisión/fuente/atribución. Única por partición y código/hash.

### OverturePlace, OverturePlaceZone y OvertureTaxonomyCode

Place pertenece a release/partición y es único por GERS ID dentro del release; conserva nombres,
dirección, punto, websites, emails, teléfonos, taxonomy/basic_category, confidence, estado y
proveniencia/licencia. La implementación puede normalizar una fila canónica por release y enlazarla
a varias particiones sin duplicar payload, siempre que preserve el límite de lectura provincial.
PlaceZone materializa point-in-polygon. TaxonomyCode fija jerarquía del release.

### CampaignCoverageSelection

Colección ordenada por Campaign con partition, release, provincia, district zone, geometry/hash y
labels congelados. Constraint rechaza releases mixtos; la creación toma sólo READY.

## 5. Organizaciones, emails y Contactos

### Organization

Empresa canónica del Workspace: nombre/dirección originales y normalizados, website, dominio,
teléfono, coordenadas, status y provenance. No pertenece a una campaña. Índices de búsqueda y
constraint de workspace.

### OrganizationIdentity

FK Organization, `kind=GERS_ID|BUSINESS_DOMAIN|NAME_ADDRESS`, `value_hash`, source, first/last seen.
Única `(workspace, kind, value_hash)`. Conflictos concurrentes resuelven a una Organization
existente o crean tarea de merge; no duplican.

### EmailAddress

FK Organization: original, normalized, domain, label, provenance/source inbound, validation/MX
status/date, `is_valid`, invalid reason/date, `is_preferred`, first/last seen. Única
`(workspace, normalized_email)`, a lo sumo una preferred por Organization. Transferir propiedad
entre Organizations requiere resolución admin auditada.

### Contact

OneToOne Organization: `created_reason=HUMAN_REPLY|MANUAL|MANUAL_RESTRICTION|UNSUBSCRIBE`, status
comprensible, display name/company override opcional, preferred_email, notes, automation state,
last_interaction_at y timestamps. Su mera existencia excluye toda Organization de campañas.

### CommunicationRestriction

Scope `CONTACT|EMAIL`, FK Contact/EmailAddress según scope, kind
`UNSUBSCRIBE|BOUNCE|MANUAL_NO_CONTACT`, reason/evidence/source message, active, irreversible,
created/lifted timestamps y actores. Check constraints alinean scope/FKs. UNSUBSCRIBE nunca se
desactiva; BOUNCE invalida email; sólo manual puede levantarse con motivo admin.

### Conversation

FK Contact, GmailConnection y `gmail_thread_id`, subject/display metadata, automation state
`ACTIVE|SUSPENDED_HUMAN|OFF`, suspension task, last_message_at. Única por conexión/thread. Un
Contacto puede tener varios hilos.

### ConversationMemory

FK Contact (opcional Conversation), version, structured summary, source message IDs, source hash,
created_at. Sólo contiene hechos/conclusiones con referencias; no reemplaza los mensajes. Una nueva
versión supersede sin editar historia.

## 6. Campañas, audiencia y mensajes

### Campaign

Workspace, name, state, discovery state/reason, delivery mode `REVIEW_ONLY|DRY_RUN|LIVE`, approval
mode `CAMPAIGN|PER_MESSAGE`, target/raw limit, daily/interval/business window/timezone, reminder
enabled/delay days (0/1 recordatorio; default tres), fixed content/signature snapshots, settings
snapshot, audience/content/attachment/schedule hashes, approval actor/date, pause/error/cancel
reasons, lifecycle timestamps and attribution.

Los estados nuevos son `DRAFT|DISCOVERING|AWAITING_APPROVAL|RUNNING|PAUSED|CANCELLED|COMPLETED|
STOPPED_ERROR`. Campañas legacy conservan valores/historia mediante mapping de migración o campos
read-only.

### CampaignCategorySelection y CampaignZoneSelection

Colecciones ordenadas congeladas con rubro/reglas y jerarquía/geometría/hashes. CoverageSelection
resuelve zona a partición exacta. Inmutables al salir de DRAFT.

### SearchQuery y SearchRun

Query pertenece a Campaign/CoverageSelection y guarda criterios/cursor hash. Run fija partition,
request, lote normalizado/hash, cursor/next, counts, state, idempotency y error redactado. No usa
red durante campaña.

### CampaignEnrollment

Campaign + Organization + selected EmailAddress, discovery provenance, eligibility state/reason,
prepared initial, human response timestamps, response polarity/source stage y timestamps. Única
`(campaign, organization)` y opcionalmente defensa unique `(campaign, selected_email)`. Se conserva
aunque luego exista Contact/restricción.

### Catalog, CampaignAttachment y OutboundAttachment

Catalog es versión PDF privada e inmutable: storage key, filename seguro, detected MIME, tamaño
<=15 MiB, SHA-256, uploader y active/missing. CampaignAttachment ordena catálogos y es única por
`(campaign, catalog)`/`(campaign, position)`.

OutboundAttachment copia a cada efecto posición, Catalog/version, storage key, nombre, size y hash.
No se edita tras aprobación/queue. La suma source <=17 MiB; el MIME final <=24 MiB; inicial y
referido exigen conjunto completo.

### OutboundMessage

`kind=INITIAL|CAMPAIGN_REMINDER|MANUAL_REPLY|AUTOMATIC_REPLY|REFERRED_PROPOSAL|REDIRECT_ACK|
SCHEDULED_CONTACT`, CampaignEnrollment/Contact/Conversation opcionales según kind, recipient
EmailAddress y normalized snapshot, subject/body/signature snapshots, delivery mode, state,
idempotency key, semantic action key, content revision/editor/approval/hash, deterministic RFC
Message-ID, Gmail message/thread ID, parent inbound, In-Reply-To/References, attempts/next attempt,
scheduled/sent/simulated timestamps, MIME hash/size and error.

Idempotency key, Message-ID y Gmail ID son únicos. `AUTOMATIC_REPLY|REDIRECT_ACK` tienen a lo sumo
una acción semántica por inbound. Enviar/reintentar modifica la misma fila.

### CampaignDeliveryReservation

`workspace`, normalized email snapshot, local date, message, campaign, status/timestamps. Única
`(workspace, normalized_email, local_date)`. Sólo INITIAL/REMINDER la crean. Cancelación previa al
efecto puede liberarse únicamente conforme al servicio y auditoría; incertidumbre Gmail exige
reconciliación antes de reutilizar.

## 7. Mailbox, decisiones y atención humana

### GmailConnection

OneToOne Workspace: account email, scopes, refresh-token ciphertext, cursor/history ID, state,
token version, sync timestamps/errors. Una conexión por Workspace; jamás contraseña.

### InboundMessage

Gmail/RFC IDs, thread, Conversation, linked outbound, headers permitidos, From/To snapshots,
subject, external/received dates, plain text, HTML sanitizado, authored text extraction,
classification, human/auto/bounce flags, read and processing state/error. Gmail ID único por
conexión; Message-ID único cuando existe. Persistencia precede a análisis.

### EmailCandidate

FK inbound: UUID/request-stable ID, literal original, normalized email, region
`NEW_CONTENT|SIGNATURE|QUOTED`, offsets/source `TEXT|MAILTO`, syntax/MX/restriction status,
ownership resolution y hash. Máximo diez por inbound y unique por inbound/normalized/region. No
almacena reconstrucciones.

### ReplyDecision

OneToOne o versión por inbound: mode `OFF|SHADOW|LIVE`, provider/model/schema/policy versions,
classification, allowlisted intent/action, confidence, candidate FK opcional, proposed body,
human reason enum, context manifest JSON/hash, selected fact revision M2M, state, reviewed result,
review actor/date and feedback. Check constraints validan action/candidate. Output crudo inválido no
se persiste; manifiesto contiene IDs/versiones, no cuerpos duplicados.

### HumanTask

Workspace, Contact, Conversation, inbound/decision, kind/reason/status
`OPEN|RESOLVED|DISMISSED`, friendly summary, opened/resolved timestamps and actors, resolution note.
Una unique condicional evita dos tasks abiertos por inbound/reason. OPEN suspende Conversation.

### NotificationDelivery

FK HumanTask/admin recipient, channel `GMAIL`, generic subject, secure URL, deterministic
idempotency/Message-ID, state `PENDING|SENDING|RECONCILING|SENT|FAILED`, provider IDs/attempts/error.
Única por task/admin/channel. Nunca guarda inbound body.

## 8. Comunicación programada

### ContactCommunicationPlan

OneToOne o colección activa acotada por Contact: enabled, purpose
`CHECK_IN|PRODUCT_FEEDBACK|ADMIN_GOAL`, goal text, preferred EmailAddress, cadence days default 30
con check >=7, mode `REVIEW_BEFORE_SEND|AUTOMATIC`, state `ACTIVE|PAUSED`, snoozed_until,
next_due_at, last_interaction/sent timestamps and attribution.

### ScheduledContactAttempt

FK plan, due_at, provider/model, manifest/hash acotado de contexto, IDs de revisiones aprobadas,
OutboundMessage opcional, state
`DUE|DRAFT_REVIEW|AUTHORIZED|SENT|HUMAN_REQUIRED|CANCELLED|INELIGIBLE`, idempotency key, reason and
timestamps. Única por plan/due cycle; impide doble trabajo de Beat.

La reserva automática admite exactamente una fuente: `ReplyDecision` con Conversation, o
`ScheduledContactAttempt` sin Conversation previa. Esta última consume un slot diario de Workspace;
el límite móvil por Conversation empieza a aplicar después de que Gmail crea el hilo nuevo.

## 9. Históricos, uso y auditoría

### Prospect, ProspectEmail, AIAnalysis, ContactLedger y ContactOverride

Durante expand/switch, Prospect y ProspectEmail conservan discovery/historia y se vinculan a
Organization/Enrollment. `AIAnalysis` existente es read-only; campañas nuevas no crean análisis de
relevancia/copy. ContactLedger/ContactOverride siguen como barrera legacy hasta validar backfill;
luego se retiran mediante migración forward. Nunca se borran mensajes/auditorías dependientes ni
se reescriben campañas completadas.

### ProviderUsage, AuditEvent y BackgroundJob

ProviderUsage mide operación/unidades/costo sin payload sensible. AuditEvent es append-only con
actor user/system, action, entity, before/after redactados y correlation ID. BackgroundJob conserva
task, entity, queue, idempotency, state, heartbeat/retry/error. Métricas de producto no son
contadores en estas filas; se derivan del dominio.

## 10. Invariantes y migración

- No hay initial/reminder elegible sin EmailAddress válida y selected.
- Contact excluye Organization completa de nuevas CampaignEnrollment elegibles.
- Toda restricción se comprueba en preparación, aprobación, queue y frontera Gmail.
- Unsubscribe es irreversible; no existe override.
- Sólo un INITIAL/REMINDER por email/día local entre campañas.
- Un reminder como máximo por initial y se cancela ante evento humano/restricción.
- Todos los PDFs requeridos deben verificar; no existe envío parcial.
- LLM sólo referencia candidate/fact IDs incluidos en su request; policy determina efecto.
- Embeddings sólo seleccionan facts candidatos para el contexto; nunca autorizan un envío.
- HumanTask OPEN suspende automation; limits/kill switches se leen justo antes de Gmail.
- Los modelos de history y auditoría no se eliminan desde UI.

El cutover valida, antes de contract: conteos de prospects->organizations/enrollments, emails,
respuestas humanas->contacts, threads->conversations, inbound/outbound y restricciones; hashes de
Gmail/RFC IDs, cuerpos/adjuntos/ciphertext; y ausencia de Organization/Email duplicados. El fallo de
cualquier validación detiene la migración de retiro sin destruir filas legacy.
