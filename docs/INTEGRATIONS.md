# Integraciones

## 0. Frontera frontend/backend y secretos

El browser consume exclusivamente `/api/v1/*` en el mismo origin que Next.js. Next actúa como
proxy streaming hacia el backend privado, elimina headers forwarded/control suministrados por el
cliente y agrega un token interno server-side. No hay CORS, JWT browser ni acceso directo a
PostgreSQL, Redis, S3, Gmail o proveedores.

API keys, Google client secret, Redis/DB/S3 y root keys provienen del entorno del backend. La API
sólo expone `configured|not_configured` y metadatos no sensibles. El refresh token Gmail es la única
credencial producida en runtime: se cifra por propósito en PostgreSQL. Workers pertenecen al mismo
backend release y llaman servicios/ORM directamente; nunca endpoints HTTP internos.

`PrivateObjectStorage` abstrae MinIO local y Railway Bucket productivo. Acepta streams, genera key
server-side, calcula metadata/hashes y nunca entrega URL pública. Catálogos se descargan por un
endpoint backend autenticado que transmite el objeto completo con `private, no-store`.

## 1. Contratos y frontera de efectos

El dominio define protocolos inmutables; ningún model/view/task consume schemas de SDK:

```python
class ExtractorProvider(Protocol):
    def search(self, request: SearchRequest) -> ExtractionBatch: ...

class WebsiteFetcher(Protocol):
    def fetch(self, request: WebsiteRequest) -> WebsiteResult: ...

class LLMProvider(Protocol):
    def analyze(self, request: AnalysisRequest) -> AIAnalysisResult: ...  # sólo legacy
    def classify_reply(self, request: ReplyClassificationRequest) -> ReplyClassification: ...  # compatibilidad
    def decide_reply(self, request: ReplyDecisionRequest) -> ReplyDecisionResult: ...
    def draft_scheduled_contact(
        self, request: ScheduledContactDraftRequest
    ) -> ScheduledContactDraftResult: ...

class EmbeddingProvider(Protocol):
    def embed(self, request: EmbeddingRequest) -> EmbeddingResult: ...

class GmailProvider(Protocol):
    def authorization_url(self, state: str, redirect_uri: str) -> str: ...
    def exchange_code(self, code: str, redirect_uri: str) -> GmailConnectionData: ...
    def revoke(self) -> None: ...
    def test_connection(self) -> GmailAccountInfo: ...
    def send(self, request: GmailSendRequest) -> GmailSendResult: ...
    def reply(self, request: GmailReplyRequest) -> GmailSendResult: ...
    def find_by_message_id(self, message_id: str) -> GmailMessageMatch: ...
    def sync(self, cursor: GmailCursor | None) -> GmailSyncBatch: ...
```

Requests/results son dataclasses o Pydantic tipados, sin ORM ni secretos serializados. Adaptadores
reciben timeout, correlation/idempotency y configuración explícitos. Excepciones comunes:
`RetryableProviderError`, `RateLimitError(retry_after)`, `AuthenticationError`,
`ValidationProviderError`, `AmbiguousProviderResult` y `PermanentProviderError`.

El LLM nunca tiene GmailProvider ni tool calling. `decide_reply` sólo propone una salida. Los
servicios de dominio validan IDs/policy, crean OutboundMessage durable y recién un worker separado
ejecuta `send`/`reply`. No se incorpora LangGraph: estado, retries, compensación e idempotencia
viven explícitamente en PostgreSQL.
`draft_scheduled_contact` también sólo propone asunto/cuerpo y cita IDs de revisiones activas:
el scheduler fija fecha, Contacto y destinatario, y el executor conserva toda autoridad de envío.

Tests sólo construyen fakes e impiden sockets/HTTP/DNS/Gmail/Overture/LLM reales.

## 2. Overture Maps Places por provincia

`OverturePlacesProvider.search()` consulta sólo PostgreSQL. `SearchRequest` fija release,
partition ID provincial, district hash, reglas, confidence, cursor, limit e idempotency key. El
resultado conserva orden/replay, release/partition/versiones/proveniencia y costo cero.

Maintenance es la única integración de red Overture. Para cada provincia ejecuta una llamada
`overturemaps.record_batch_reader("place", ..., stac=True)` con su bbox; después filtra cada punto
por polígonos exactos. Dos provincias lejanas implican dos reads acotados. El cliente sólo acepta
STAC/S3 oficiales definidos en código, batches streaming, máximo configurable de places, dedupe por
GERS ID, exclusión `permanently_closed` y validación fail-closed de schema, taxonomy,
`basic_category`, provenance, licencias y NOTICE.

`OvertureRelease` separa identidad/metadatos de `OvertureCoveragePartition`. Cada provincia puede
estar IMPORTING/READY/FAILED/SUPERSEDED independientemente. Activar una nueva partición no cambia
otras; campañas sólo combinan READY del mismo release. Beat consulta metadata una vez por día; sólo
un POST admin+CSRF inicia import en cola `maintenance` concurrency=1.

El snapshot legacy activo se backfillea como CABA cuando coincide con cobertura esperada;
cobertura inesperada queda histórica. No se reescriben migraciones previas. La retención protege
particiones usadas por campañas, activa y anterior provincial.

`MockExtractorProvider` pagina fixtures determinísticos con direct email, fallback web, duplicados,
sin email y errores; nunca abre red.

## 3. DNS/email y WebsiteFetcher

Email usa `email-validator`, dominio IDNA, local part conservado y sin canonicalizaciones
específicas de Gmail. MX usa resolver inyectable; null MX/NXDOMAIN inválidos, timeout/SERVFAIL
transitorios y nunca SMTP handshake.

Los canales agregados manualmente se guardan primero como `UNKNOWN` y una task separada ejecuta MX
fuera del request. Resultado válido habilita el canal; inválido lo bloquea; transitorio queda como
“Validación pendiente” y reintenta hasta tres veces. Tests fuerzan `mock` y bloquean DNS real.

`HttpWebsiteFetcher` aplica la matriz SSRF de `SECURITY.md`, transport con IP fijado, misma PSL
embebida, máximo home + tres páginas, contenido/bytes/redirects/timeouts acotados y sin JavaScript.
Devuelve texto limpio y emails **literales** visibles/`mailto:` con URL/offset/hash; no infiere
direcciones ni guarda HTML completo.

Esta extracción de prospecting es distinta de `EmailCandidate`: al importar un inbound, la
aplicación analiza texto plano y mailto ya sanitizado, no WebsiteFetcher, y conserva hasta diez
literales por regiones NEW_CONTENT/SIGNATURE/QUOTED.

## 4. Proveedores LLM y embeddings

Implementaciones:

- `MockLLMProvider`: decisiones y fallos determinísticos, incluyendo schema violations.
- `OllamaProvider`: endpoint/modelo explícitos y JSON schema cuando soporte.
- `OpenAICompatibleProvider`: base URL/modelo/API key, endpoint compatible y schema/JSON mode con
  validación local siempre.

URLs rechazan userinfo/query/fragment; HTTP sólo para destinos locales/privados explícitamente
permitidos y no siguen redirects con Authorization. API key se resuelve desde ciphertext/fallback
de entorno al construir el adaptador.

Embeddings:

- `FakeEmbeddingProvider`: embeddings léxicos determinísticos para desarrollo y tests, sin red.
- `OpenAICompatibleEmbeddingProvider`: endpoint `/embeddings`, `encoding_format=float`,
  modelo configurable —por defecto `text-embedding-3-small`— y dimensiones configurables.

La app usa embeddings sólo para buscar facts puntuales aprobados antes de construir el request LLM.
El proveedor de embeddings no recibe Gmail ni puede ejecutar acciones; si falla, devuelve dimensión
inválida o la selección queda baja/ambigua, la automatización no inventa una respuesta. En selección
baja/ambigua los tres facts mejor rankeados pueden pasar como contexto sugerido con
`may_be_irrelevant=true`, y el LLM debe ignorarlos si no coinciden claro. A escala mayor se puede
reemplazar el cache relacional por un vector store sin cambiar el contrato de dominio.

### 4.1 Campañas nuevas y compatibilidad

Nuevas campañas iniciales realizan **cero** llamadas `analyze`: rubro/zonas/email determinan
audiencia y el mensaje fijo se compone en dominio. `analyze` y `classify_reply` permanecen
temporalmente para leer/reprocesar únicamente flujos legacy permitidos; `AIAnalysis` histórico es
read-only y nunca se regenera para enviar una campaña completada.

### 4.2 ReplyDecisionRequest

La aplicación construye, en este orden, hasta 24.000 caracteres:

1. instrucciones admin de redacción como `ADMIN_WRITING_INSTRUCTIONS`;
2. completos y obligatorios: texto recién escrito del inbound, mensaje INITIAL o
   REFERRED_PROPOSAL original, padre directo cuando existen y contexto general vigente;
3. hasta seis mensajes recientes relevantes del Contacto, incluso de otros threads;
4. `ConversationMemory` estructurada con IDs fuente para historia anterior;
5. hasta tres `KnowledgeFactRevision` activas, elegidas por embeddings según la consulta.

PDFs y HTML crudo quedan fuera. Si el bloque obligatorio no entra no se invoca proveedor y se abre
HumanTask. Si la consulta no encuentra facts claros —similitud baja o empate ambiguo— los mejores
matches pueden agregarse como sugerencias (`may_be_irrelevant=true`); la política debe pedir humano
cuando el LLM no puede vincularlos claramente con la consulta. El contexto cruzado no es “todo el
hilo”: el selector preserva causalidad sin saturar.

El request incluye:

- inbound/contact/conversation IDs opacos y versión de policy;
- bloques rotulados por rol/proveniencia: `NEW_INBOUND` entra como `UNTRUSTED_DATA` y el contexto
  general vigente entra como `APPROVED_GLOBAL_CONTEXT`;
- lista de `EmailCandidateRef(id, normalized, region, validation_state)`;
- lista de `FactRevisionRef(id, version, text)` aprobada y seleccionada para esta consulta;
- enums dinámicos de candidate/fact IDs y actions/intents;
- instrucciones fijas de que reuniones, precios y demás categorías de riesgo deben pedir humano.
  La instrucción distingue una coordinación explícita de una consulta informativa general; además,
  el dominio aplica un fallback estrecho para llamada/reunión más coordinación o disponibilidad.

El hash canónico cubre IDs/versiones/orden/textos efectivos. DB persiste sólo manifest con IDs,
versiones, char counts, estado de retrieval, scores redondeados y hashes; el cuerpo ya vive en sus
tablas y no se duplica en logs/prompts.

### 4.3 ReplyDecisionResult

Schema estricto (`extra=forbid`):

```text
classification
intent
action
confidence 0..1
candidate_id | null
fact_revision_ids[]
proposed_body | null
human_reason | null
```

El servicio rechaza candidate/fact IDs no presentes, duplicados, region no permitida, body cuando
la acción no lo admite, ausencia de fact para respuestas fundamentadas, intent/action
incompatibles y confidence fuera de rango. Para `REPLY`, el `proposed_body` validado se conserva y
se usa como cuerpo final; los `fact_revision_ids` prueban el fundamento y no se renderizan como
texto final. El raw inválido no se almacena como decisión válida; el error se redacta. Reintentos
técnicos son acotados y no convierten un fallo en respuesta genérica.

Allowlist auto: `APPROVED_PRODUCT_INFORMATION`, `APPROVED_COMPANY_FACT`,
`GROUNDED_SIMPLE_CLARIFICATION`, `EXPLICIT_PROPOSAL_REDIRECTION`. `POLITE_ACKNOWLEDGEMENT` y
`NOT_INTERESTED` deben producir `NO_ACTION`. Meeting/date, quote/pricing, negotiation, complaint,
legal/privacy, unsupported technical advice, multiple intent, ambiguity, conflict e insufficient
context deben devolver `HUMAN` con reason enum; el policy engine impone lo mismo aunque el modelo no
lo haga.

## 5. Extracción de candidatos inbound

Un parser determinista opera antes de `decide_reply`:

- usa el texto plain/sanitized y href `mailto:`; regex/parser sólo acepta literales RFC plausibles;
- recorta puntuación envolvente, normaliza dominio IDNA y valida sintaxis;
- divide new authored content, firma y quoted history mediante MIME/markers conservadores;
- deduplica sin perder regiones/proveniencia y persiste máximo diez;
- no transforma “nombre arroba dominio” ni otras ofuscaciones;
- MX/restricción/ownership se resuelven fuera del modelo.

El modelo elige candidate ID sólo en una redirección explícita. Únicamente `NEW_CONTENT` es
auto-elegible. Varios candidatos plausibles, candidato de firma/cita, ownership de otra
Organization, MX inválido/transitorio inconcluso o restricción crean HumanTask.

## 6. Gmail OAuth, MIME y efectos

OAuth web-server usa PKCE/state, offline access, redirect exacto y scopes `gmail.send` y
`gmail.readonly`. Guardar credenciales exige reauth; refresh token/client secret se cifran.
Conectar establece history baseline sin importar inbox completo.

MIME usa `email` estándar: text/plain UTF-8, un To, sin CC/BCC, Message-ID determinístico y headers
opacos. INITIAL/REFERRED_PROPOSAL adjuntan el orden exacto de OutboundAttachment; reminder/replies
no adjuntan por default. Antes de codificar se validan cada PDF y suma <=17 MiB; tras serializar se
exige <=24 MiB. Un fallo aborta todo el efecto.

`send` inicia hilo para INITIAL, REFERRED_PROPOSAL y SCHEDULED_CONTACT. `reply` conserva threadId,
subject, In-Reply-To y References para REMINDER, MANUAL_REPLY, AUTOMATIC_REPLY y REDIRECT_ACK.
El contexto visto por LLM no se confunde con headers: parent/original se resuelven y guardan antes
de formar reply.
Cuando un `MANUAL_REPLY` queda `SENT`, se resuelven las tareas `REPLY_REVIEW` abiertas para el
inbound padre; estados fallidos o ambiguos no cierran la revisión.

Toda llamada parte de OutboundMessage `SENDING`. Éxito persiste gmail IDs/`SENT`. Timeout queda
`RECONCILING`; `find_by_message_id` debe concluir aceptación/ausencia antes de retry. Proposal y ACK
de redirect son mensajes/idempotency keys distintas; ACK sólo se autoriza tras proposal SENT.

`FakeGmailProvider` deduplica Message-ID, conserva threads/headers/MIME, simula history, quotas,
auth, timeout antes/después de aceptación y reconciliation sin sockets.

## 7. Sincronización Gmail

Beat encola cada minuto por default. Un lock por GmailConnection rodea history pagination,
persistencia y cursor, no clasificación/LLM. History 404 captura baseline y aplica fallback
`newer_than:30d`/1000 candidatos, filtrando por thread IDs o headers propios, o por remitentes que
coincidan con un `EmailAddress` válido de un Contacto existente. Cursor legacy vacío inicializa
baseline sin importar histórico indiscriminado.

Se persisten sólo metadata/parts necesarios de hilos asociados o mails directos de Contactos
preexistentes. Un mail directo queda sin `related_outbound`, pero se liga al Contacto/Organization
y a una Conversation; remitentes desconocidos no crean Contactos. Texto detached se recupera; no
se importan adjuntos no textuales. Gmail/Message IDs hacen sync repetible. En la transacción:

1. upsert inbound y asociación;
2. sanitizar/separar texto authored;
3. aplicar unsubscribe/bounce/auto-reply determinísticos;
4. promover Contact humano, Conversation y cancelar reminders;
5. actualizar cursor.

Los adaptadores marcan `GmailInboundMessage.is_sent` desde la etiqueta Gmail `SENT`. Un mensaje
enviado por la cuenta se ignora si ya coincide con un `OutboundMessage` propio; si sus headers o
thread responden a un inbound conocido de un Contacto, se persiste como `MANUAL_REPLY` `SENT`, se
resuelve la tarea `REPLY_REVIEW` del inbound y se cancelan respuestas automáticas aún en cola. Sólo
`transaction.on_commit` encola candidate extraction/decision para inbounds sin una respuesta
manual confirmada. Fallar LLM no revierte inbound ni cursor.

## 8. Alertas por Gmail

Al abrir HumanTask se crea una NotificationDelivery por email de admin activo. Requiere
`PUBLIC_BASE_URL`; request contiene subject fijo `Hay una conversación que necesita revisión` y
cuerpo fijo con link a la ruta task. No contiene cuerpo/asunto inbound, contacto, empresa ni
dirección. Usa Message-ID determinístico, `send`, reconciliation y límites propios. Fallo sólo
marca delivery; HumanTask permanece.

## 9. Política de retries

| Integración | Retry | Resultado terminal |
| --- | --- | --- |
| Overture local | DB transitorio con cursor/idempotency | cerrar query; conservar datos |
| Import provincial | retry manual/idempotente en partition no activa | FAILED; anterior sigue READY |
| DNS MX | hasta 3 diferidos | invalid/inconclusive; retry visible en Contactos; humano si redirect |
| Website | uno por página dentro de presupuesto | fallback auditable |
| Embeddings | máximo 3 técnicos; no fallback a facts no seleccionados | HumanTask/provider failure cuando hacen falta facts |
| LLM decide | máximo 3 técnicos; rate limits diferidos | HumanTask/provider failure; nunca auto fallback |
| Gmail send/reply | no retry ciego | RECONCILING o FAILED/HumanTask |
| Gmail sync | 3; fallback history 404 | conexión degradada, UI disponible |
| Notification | retry + reconciliation por Message-ID | FAILED; task durable |

Cada llamada tiene timeout y presupuesto total. Tests no ofrecen un flag que habilite red real.
