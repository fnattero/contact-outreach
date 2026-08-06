# Máquinas de estados

## 1. Regla general

Cada agregado conserva su propia máquina para no perder historia. Sólo servicios de dominio
ejecutan transiciones dentro de `transaction.atomic`, bloquean las filas, revalidan invariantes y
crean auditoría. Views/tasks nunca asignan estados. Toda transición no enumerada falla cerrada.

Los estados legacy se preservan para lectura/migración, pero campañas nuevas y acciones nuevas usan
las máquinas de este documento.

## 2. Campaña

Estados: `DRAFT`, `DISCOVERING`, `AWAITING_APPROVAL`, `RUNNING`, `PAUSED`, `CANCELLED`,
`COMPLETED`, `STOPPED_ERROR`.

| Desde | Hacia | Condición |
| --- | --- | --- |
| DRAFT | DISCOVERING | Admin inicia; contenido/firma/PDFs/reglas/zonas y particiones READY válidos |
| DISCOVERING | AWAITING_APPROVAL | Todas las queries terminaron y audiencia final quedó persistida |
| DISCOVERING | PAUSED | Admin o fallo recuperable de cobertura/integración/seguridad |
| AWAITING_APPROVAL | RUNNING | Aprobación campaign-level válida o inicio explícito de per-message con filas aprobadas |
| AWAITING_APPROVAL | PAUSED | Admin o preflight recuperable |
| RUNNING | PAUSED | Admin, kill switch, Gmail/PDF inválido, cuotas de seguridad o error recuperable |
| PAUSED | estado_anterior | Admin reanuda y todas las precondiciones vuelven a ser válidas |
| DRAFT/DISCOVERING/AWAITING_APPROVAL/RUNNING/PAUSED | CANCELLED | Admin cancela; no empiezan nuevos efectos |
| RUNNING | COMPLETED | Discovery terminó y cada initial/recordatorio posible quedó terminal |
| DISCOVERING/AWAITING_APPROVAL/RUNNING/PAUSED | STOPPED_ERROR | Integridad irrecuperable impide continuar |

`CANCELLED`, `COMPLETED` y `STOPPED_ERROR` son terminales. Pausa guarda `resume_state`; nunca salta
aprobación. Un mensaje ya aceptado por Gmail no se revierte.

Discovery mantiene subestado `PENDING|RUNNING|TARGET_REACHED|EXHAUSTED_QUERIES|
EXHAUSTED_RAW_LIMIT|FAILED_PROVIDER`. Los últimos cuatro cierran búsqueda; sólo los resultados
persistidos pasan a audiencia. Si falta una provincia READY, el inicio no sale de DRAFT.

## 3. Enrollment

Estados de elegibilidad: `DISCOVERED`, `EMAIL_SELECTED`, `PREPARED`, `EXCLUDED_DUPLICATE`,
`EXCLUDED_CONTACT`, `EXCLUDED_RESTRICTED`, `EXCLUDED_NO_EMAIL`, `APPROVED`, `QUEUED`, `CONTACTED`,
`RESPONDED`, `CANCELLED`, `ERROR`.

| Desde | Hacia | Condición |
| --- | --- | --- |
| DISCOVERED | EMAIL_SELECTED | Dirección validada resuelta hacia Organization |
| DISCOVERED/EMAIL_SELECTED | EXCLUDED_* | Dedupe, Contact, restricción o falta de email |
| EMAIL_SELECTED | PREPARED | Mensaje fijo y adjuntos exactos congelados; cero LLM |
| PREPARED | APPROVED | Aprobación campaign-level o per-message incluye la fila y sigue elegible |
| PREPARED | CANCELLED | Per-message no aprobado al iniciar o campaña cancelada |
| APPROVED | QUEUED | Recheck + reserva de fecha/ventana válida |
| QUEUED | CONTACTED | Initial confirmado/reconciliado SENT |
| CONTACTED | RESPONDED | Primera respuesta humana asociada |
| cualquier no terminal | EXCLUDED_CONTACT/EXCLUDED_RESTRICTED/CANCELLED/ERROR | Cambio de elegibilidad o error correspondiente |

Crear Contact cancela cualquier trabajo de campaña aún no enviado para la Organization.

## 4. Mensaje saliente

Estados: `DRAFT`, `REVIEW_READY`, `AUTHORIZED`, `QUEUED`, `SENDING`, `RECONCILING`, `SENT`,
`DRY_RUN_COMPLETED`, `SEND_FAILED`, `CANCELLED`, `INELIGIBLE`.

| Desde | Hacia | Condición |
| --- | --- | --- |
| DRAFT | AUTHORIZED | Aprobación CAMPAIGN congela fila/hash |
| DRAFT | REVIEW_READY | PER_MESSAGE o comunicación programada requiere revisión |
| REVIEW_READY | REVIEW_READY | Edición admin válida incrementa revisión |
| REVIEW_READY | AUTHORIZED | Aprobación explícita válida |
| AUTHORIZED | QUEUED | Recheck final previo, agenda y reserva aplicable |
| QUEUED | SENDING | Scheduler obtiene lock, ventana/cupo/kill switches válidos |
| QUEUED | DRY_RUN_COMPLETED | Modo dry-run; MIME válido local, cero Gmail |
| SENDING | SENT | Gmail confirma IDs |
| SENDING | RECONCILING | Resultado ambiguo; prohibido reenviar |
| RECONCILING | SENT | Message-ID encontrado |
| RECONCILING | QUEUED | Ausencia concluyente y retry permitido |
| QUEUED/SENDING/RECONCILING | SEND_FAILED | Error permanente o presupuesto agotado |
| DRAFT/REVIEW_READY/AUTHORIZED/QUEUED | CANCELLED | Campaña/task cancelada antes del efecto |
| DRAFT/REVIEW_READY/AUTHORIZED/QUEUED | INELIGIBLE | Contact/restricción/header/context/PDF vuelve inválido el efecto |
| SEND_FAILED | QUEUED | Retry admin sobre misma fila tras resolver causa y reconciliar |

`SENT` y `DRY_RUN_COMPLETED` son terminales. Todos los kinds usan la misma frontera durable, pero:

- INITIAL y CAMPAIGN_REMINDER requieren reserva same-day.
- INITIAL y REFERRED_PROPOSAL requieren todos los PDFs congelados.
- MANUAL_REPLY sólo nace por POST admin.
- AUTOMATIC_REPLY, REFERRED_PROPOSAL y REDIRECT_ACK sólo nacen de una ReplyDecision LIVE que el
  policy engine autorizó, con semantic action key única.
- SCHEDULED_CONTACT nace de plan vencido y modo review/automatic.

## 5. Recordatorio de campaña

Estados: `NOT_APPLICABLE`, `PENDING`, `SCHEDULED`, `QUEUED`, `SENT`, `CANCELLED`, `INELIGIBLE`,
`FAILED`.

| Desde | Hacia | Condición |
| --- | --- | --- |
| NOT_APPLICABLE | PENDING | Initial confirmado y campaña habilita recordatorio |
| PENDING | SCHEDULED | due = sent_at + delay, desplazado a ventana laboral |
| PENDING/SCHEDULED | CANCELLED | Respuesta humana, Contact manual, unsubscribe o bounce |
| SCHEDULED | QUEUED | Vence, sigue elegible y reserva email/fecha local |
| SCHEDULED | SCHEDULED | Conflicto same-day mueve al próximo día permitido |
| QUEUED | SENT | Reply Gmail en hilo original confirmado |
| PENDING/SCHEDULED/QUEUED | INELIGIBLE | Restricción/Contact/campaña impide envío |
| QUEUED | FAILED | Error terminal después de reconciliación/retries |

AUTO_REPLY no cancela. Única fila/OutboundMessage por initial; nunca se crea un segundo reminder.

## 6. Mensaje entrante y Contacto

Procesamiento inbound: `PERSISTED -> DETERMINISTIC_APPLIED -> DECISION_PENDING -> DECIDED|
HUMAN_REQUIRED|FAILED`.

| Evento | Efecto determinístico |
| --- | --- |
| Respuesta humana genuina | Crear/promover Contact, vincular Conversation, cancelar reminders |
| Mail directo de Contacto existente | Vincular Contact/Organization/Conversation; sin campaña ni outbound padre |
| UNSUBSCRIBE humano | Lo anterior + restricción irreversible y estado “Baja solicitada” |
| BOUNCE | Invalidar sólo EmailAddress; cancelar reminder; no crear Contact por sí solo |
| AUTO_REPLY | Persistir evento; no Contact, no respuesta humana, no cancelar reminder |

La transacción termina antes de publicar `DECISION_PENDING` con `on_commit`. Un reintento sobre el
mismo Gmail ID reutiliza mensaje, candidatos, Contact y task/decision existentes.

Contacto no es una máquina de campaña: `ACTIVE|NO_CONTACT|UNSUBSCRIBED|INCOMPLETE`. Puede cambiar
sus datos/preferencias, pero nunca deja de excluir la Organization de campañas. `UNSUBSCRIBED` no
retrocede. Una restricción manual reversible no elimina Contact.

## 7. ReplyDecision y automatización

Estados: `PENDING`, `SHADOW_RECORDED`, `NO_ACTION`, `AUTO_ELIGIBLE`, `AUTHORIZED`, `EXECUTING`,
`COMPLETED`, `HUMAN_REQUIRED`, `REJECTED_POLICY`, `FAILED`.

| Desde | Hacia | Condición |
| --- | --- | --- |
| PENDING | SHADOW_RECORDED | Output válido en modo SHADOW; cero Gmail |
| PENDING | NO_ACTION | POLITE_ACKNOWLEDGEMENT, NOT_INTERESTED o acción NONE |
| PENDING | HUMAN_REQUIRED | Intent/riesgo/contexto/provider/schema exige admin |
| PENDING | AUTO_ELIGIBLE | Intent allowlisted, facts/candidate/confidence válidos |
| AUTO_ELIGIBLE | REJECTED_POLICY | OFF/SHADOW, gate, kill switch, límite o recheck falla |
| AUTO_ELIGIBLE | AUTHORIZED | LIVE calificado y policy/rechecks completos |
| AUTHORIZED | EXECUTING | Outbound durable creado y worker reclama lock |
| EXECUTING | COMPLETED | Efecto(s) Gmail confirmados/reconciliados |
| EXECUTING | HUMAN_REQUIRED | Fallo o ambigüedad requiere intervención |
| PENDING/AUTO_ELIGIBLE/AUTHORIZED/EXECUTING | FAILED | Error persistido; sin reclamo de éxito |

Confianza >=0,90 es necesaria, nunca suficiente. IDs no incluidos o campos extra producen
`HUMAN_REQUIRED/FAILED`, no fallback. El contexto válido incluye contexto global vigente y, para
respuestas fundamentadas, facts puntuales activos seleccionados por embeddings; si llegan por
similitud baja o selección ambigua se marcan como posibles y no autorizan una respuesta por sí
solos. El recheck de una decisión previa no considera respuestas automáticas posteriores de otros
hilos del mismo Contacto como contexto humano nuevo. Una Conversation `SUSPENDED_HUMAN` impide
nuevas autorizaciones automáticas.

### Saga de redirección

`VALIDATING -> PROPOSAL_AUTHORIZED -> PROPOSAL_SENDING -> PROPOSAL_CONFIRMED -> ACK_AUTHORIZED ->
ACK_SENDING -> COMPLETED`.

Desde cualquier paso previo a confirmación puede ir a `HUMAN_REQUIRED`; no se crea ACK. Tras
`PROPOSAL_CONFIRMED`, recovery siempre reconcilia/continúa ACK con su propia key y no repite la
propuesta. Un semantic action por inbound impide dos sagas.

## 8. HumanTask y NotificationDelivery

HumanTask: `OPEN -> RESOLVED|DISMISSED`. Abrir pone Conversation en `SUSPENDED_HUMAN`. Resolver o
descartar puede restaurar `ACTIVE` sólo si no queda otro task abierto; no dispara envío salvo una
acción admin separada y explícita.

Una respuesta manual confirmada (`MANUAL_REPLY -> SENT`) resuelve automáticamente las tareas
`REPLY_REVIEW` abiertas para ese inbound, usando como actor al usuario que autorizó la respuesta.
Si el envío manual falla o queda `RECONCILING`, la tarea sigue `OPEN`.

NotificationDelivery: `PENDING -> SENDING -> SENT|RECONCILING|FAILED`; `RECONCILING -> SENT|PENDING|
FAILED`. El task sigue OPEN aunque todas las notificaciones fallen.

## 9. Plan de comunicación

Tema global: `active|inactive`. Aprobación por Contacto: `DISABLED|ACTIVE|PAUSED`; snooze conserva
ACTIVE pero no es due. Attempt:
`DUE -> DRAFT_REVIEW|AUTHORIZED|HUMAN_REQUIRED|INELIGIBLE|CANCELLED`; `DRAFT_REVIEW -> AUTHORIZED|
CANCELLED`; `AUTHORIZED -> SENT|HUMAN_REQUIRED|INELIGIBLE` mediante OutboundMessage.

Interacción genuina recalcula `next_due_at >= interaction_at + cadence` usando la cadencia del tema
global; envío confirmado usa `sent_at + cadence`. Tema inactivo, restricción, task abierto,
suspensión, contexto insuficiente o kill switch impiden autorización.

## 10. Overture y jobs

`OvertureCoveragePartition`: `IMPORTING -> READY|FAILED`; una READY anterior sólo pasa a
`SUPERSEDED` después de activar atómicamente la nueva de esa provincia. Releases no mezclan filas
ejecutables de distintas versiones.

`SearchRun`: `PENDING -> RUNNING -> SUCCEEDED|RETRY_WAIT|FAILED_PERMANENT|CANCELLED`.
`BackgroundJob`: `PENDING -> RUNNING -> SUCCEEDED|RETRY_WAIT|FAILED|CANCELLED`. Recovery de
heartbeat sólo repite operaciones internas/idempotentes; efectos externos consultan su agregado.

## 11. Invariantes de concurrencia

- Un Workspace singleton y un último admin activo se protegen bajo lock.
- Lockout fijo no desliza al recibir intentos bloqueados.
- OrganizationIdentity y EmailAddress únicas convergen bajo conflicto concurrente.
- Una campaña congela audiencia/configuración bajo lock; filas posteriores no entran solas.
- CampaignDeliveryReservation permite un INITIAL/REMINDER por email/fecha local.
- Un initial posee como máximo un reminder.
- Cada Gmail Message-ID ambiguo se reconcilia antes de requeue.
- Un inbound Gmail ID posee una sola acción semántica; propuesta y ACK tienen keys separadas.
- Una Conversation con HumanTask OPEN no autoriza automatización.
- Los límites de tres respuestas/Conversation/24 h y veinte/Workspace/día se reservan
  transaccionalmente y se revalidan antes de Gmail.
- Tareas reciben IDs, reabren estado y no producen efecto si la transición ya ocurrió.

## 12. Proyecciones del dashboard

Los estados técnicos se traducen a resultados: `SENT` = “Enviado”; `DRY_RUN_COMPLETED` = “Modo de
prueba: no se envió”; decisión automática completa = “Respondido automáticamente”; HumanTask OPEN
= “Necesita que lo revises”; reminder rescheduled = “Se pasó al próximo día permitido para evitar
correos duplicados”. Detalles técnicos quedan colapsados y sólo para admin.
