# Máquinas de estados

## 1. Regla general

Los estados se separan por agregado para no perder información. Un prospecto puede conservar pipeline `ANALYZED`, tener mensaje `SENT` y engagement `INTERESTED` simultáneamente. El dashboard proyecta las etiquetas solicitadas desde estas máquinas.

Sólo servicios de dominio ejecutan transiciones dentro de una transacción, bloquean la fila, validan precondiciones y crean `AuditEvent`. Toda transición no enumerada es inválida.

## 2. Campaña

Estados operativos: `DRAFT`, `RUNNING`, `PAUSED`, `CANCELLED`, `COMPLETED`, `STOPPED_ERROR`.

| Desde | Hacia | Disparador y condiciones |
| --- | --- | --- |
| DRAFT | RUNNING | Usuario inicia; snapshots, catálogo, queries y configuración válidos |
| RUNNING | PAUSED | Usuario, kill switch, Gmail/catálogo inválido o safety threshold |
| PAUSED | RUNNING | Usuario reanuda y todas las precondiciones vuelven a ser válidas |
| DRAFT/RUNNING/PAUSED | CANCELLED | Usuario cancela; terminal, sin nuevos efectos externos |
| RUNNING | COMPLETED | Descubrimiento terminó por cualquier razón prevista y la cola autorizada fue procesada |
| RUNNING | STOPPED_ERROR | Error de integridad/seguridad que impide continuar cualquier etapa |

Pausar es reversible; `CANCELLED`, `COMPLETED` y `STOPPED_ERROR` son terminales. Los mensajes ya aceptados por Gmail no se revierten.

La etapa de descubrimiento tiene estado independiente: `PENDING`, `RUNNING`, `TARGET_REACHED`, `EXHAUSTED_QUERIES`, `EXHAUSTED_RAW_LIMIT`, `EXHAUSTED_COST`, `FAILED_PROVIDER`. Los últimos cinco son terminales para extracción. La campaña permanece `RUNNING` mientras drena mensajes ya calificados y registra ese estado como `discovery_stop_reason`; `FAILED_PROVIDER` no invalida resultados ya persistidos.

## 3. Pipeline del prospecto

Estados: `DISCOVERED`, `EMAIL_FOUND`, `ENRICHED`, `ANALYZED`, `SKIPPED_NO_EMAIL`, `SKIPPED_DUPLICATE`, `SKIPPED_IRRELEVANT`, `QUEUED`, `ERROR`.

| Desde | Hacia | Condición |
| --- | --- | --- |
| DISCOVERED | EMAIL_FOUND | Email principal pasa sintaxis, exclusiones y MX |
| DISCOVERED | SKIPPED_NO_EMAIL | No queda candidato utilizable |
| DISCOVERED/EMAIL_FOUND | SKIPPED_DUPLICATE | Identidad resuelve a canónico ya procesado |
| EMAIL_FOUND | ENRICHED | Snapshot web exitoso o fallback registrado |
| ENRICHED | ANALYZED | Output IA válido y persistido |
| ANALYZED | SKIPPED_IRRELEVANT | Score menor al umbral snapshot |
| ANALYZED | QUEUED | Score suficiente, slot de objetivo y elegibilidad de contacto |
| DISCOVERED/EMAIL_FOUND/ENRICHED | ERROR | Reintentos de la etapa actual agotados; se persiste `error_stage` |
| ERROR | DISCOVERED/EMAIL_FOUND/ENRICHED | Reintento manual explícito vuelve a `error_stage`, con causa corregida y auditada |

Una falla web no lleva a `ERROR`: produce un snapshot fallback y avanza a `ENRICHED`. `QUEUED` significa que existe un `OutboundMessage` único.

## 4. Mensaje saliente

Estados: `PREPARED`, `QUEUED`, `SENDING`, `RECONCILING`, `SENT`, `DRY_RUN_COMPLETED`, `SEND_FAILED`, `CANCELLED`.

| Desde | Hacia | Condición |
| --- | --- | --- |
| PREPARED | QUEUED | Campaña activa, no suprimido, catálogo íntegro |
| QUEUED | SENDING | Scheduler obtiene lock, cupo, intervalo y ventana válidos |
| QUEUED | DRY_RUN_COMPLETED | Modo dry-run; MIME construido/validado sin Gmail |
| SENDING | SENT | Gmail confirma y se persisten IDs |
| SENDING | RECONCILING | Resultado externo ambiguo; queda prohibido reenviar |
| RECONCILING | SENT | Gmail encuentra el Message-ID ya aceptado |
| RECONCILING | QUEUED | Búsqueda concluyente confirma ausencia y quedan intentos |
| QUEUED/SENDING/RECONCILING | SEND_FAILED | Error permanente o reintentos agotados |
| PREPARED/QUEUED | CANCELLED | Campaña cancelada antes de iniciar efecto externo |
| SEND_FAILED | QUEUED | Reintento manual con causa resuelta y misma fila/idempotency key |

`SENDING` vencido siempre pasa primero por `RECONCILING`. `SENT` y `DRY_RUN_COMPLETED` son terminales para ese mensaje. Una respuesta manual usa la misma máquina pero sólo puede originarse por POST explícito del usuario.

## 5. Engagement

Estado derivado por prospecto/campaña: `NONE`, `REPLIED`, `INTERESTED`, `NOT_INTERESTED`, `UNSUBSCRIBED`, `BOUNCED`.

| Evento clasificado | Resultado |
| --- | --- |
| INTERESTED | INTERESTED, `responded=true` |
| NOT_INTERESTED | NOT_INTERESTED, `responded=true` |
| OTHER humano | REPLIED, `responded=true` |
| UNSUBSCRIBE | UNSUBSCRIBED, `responded=true`, supresión permanente |
| BOUNCE | BOUNCED, `responded=false`, email inválido |
| AUTO_REPLY | Sin cambio de engagement, `responded=false` |

`UNSUBSCRIBED` prevalece sobre cualquier clasificación posterior. `BOUNCED` bloquea envíos al email, aunque una respuesta humana previa permanezca visible en el hilo.

## 6. SearchRun y jobs

`SearchRun`: `PENDING -> RUNNING -> SUCCEEDED|RETRY_WAIT|FAILED_PERMANENT|CANCELLED`. `RETRY_WAIT -> RUNNING` conserva request ID e idempotency key.

`BackgroundJob`: `PENDING -> RUNNING -> SUCCEEDED|RETRY_WAIT|FAILED|CANCELLED`. Un heartbeat vencido lleva a recuperación; no autoriza repetir un efecto externo sin consultar el agregado correspondiente.

## 7. Invariantes de concurrencia

- Una campaña reserva objetivo y costo bajo lock antes de crear trabajo.
- Sólo un worker puede procesar un prospecto/etapa o mensaje a la vez.
- Un `ContactLedger` único se bloquea justo antes de live para asignar `contact_sequence` y consumir un override; dry-run no lo modifica.
- Sólo un sync Gmail corre por conexión.
- Supresión e invalidez se verifican al preparar, encolar y justo antes de enviar.
- Catálogo se verifica por storage key, tamaño y SHA-256 justo antes de construir MIME.
- El límite diario se reserva transaccionalmente; una reserva expirada se libera sólo tras reconciliar Gmail.
- Las tareas reciben IDs, vuelven a leer estado y terminan sin efecto si la transición ya ocurrió.

## 8. Proyecciones del dashboard

- `crudos`: registros almacenados en `SearchRun.response_json`/métricas.
- `con email`: prospectos que alcanzaron `EMAIL_FOUND`.
- `duplicados`, `irrelevantes`: estados `SKIPPED_*` correspondientes.
- `calificados`: análisis sobre umbral con slot aceptado.
- `en cola`: mensajes `PREPARED|QUEUED|SENDING|RECONCILING`.
- `enviados`: sólo `SENT`; `DRY_RUN_COMPLETED` se muestra como simulado.
- `fallidos`: prospectos `ERROR` y mensajes `SEND_FAILED`, separados por filtro.
- `respondidos`, `interesados`: engagement derivado, nunca contadores mutables independientes.
