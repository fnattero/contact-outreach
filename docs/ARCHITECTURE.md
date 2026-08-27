# Arquitectura

## 1. Decisión principal

Se adopta un **frontend Next.js/React independiente y un backend modular Django REST unificado**.
PostgreSQL, Redis y object storage son servicios privados separados. La frontera de negocio sigue
en los servicios de dominio Django; la separación frontend/backend es una frontera de despliegue y
seguridad del navegador, no una división de las transacciones de dominio.

API, worker general, worker Overture/maintenance y Beat comparten un único backend container,
release y credenciales. Un runner aplica migraciones bajo advisory lock antes de iniciar un
Supervisor que mantiene esos procesos. Los workers acceden al ORM directamente y nunca llaman
endpoints HTTP internos. Sólo frontend y backend se despliegan de forma independiente; los procesos
internos del backend se reinician juntos y existe una sola réplica inicial.

```text
Browser -> HTTPS -> Next.js frontend (único público)
                     | / -> React UI
                     | /api/v1/* -> proxy privado autenticado
                                      -> backend container
                                         |-> Uvicorn/Django REST
                                         |-> Celery general
                                         |-> Celery maintenance (concurrency=1)
                                         |-> Celery Beat
                                         |-> migration runner antes de startup
                                         |-> PostgreSQL privado
                                         |-> Redis privado
                                         |-> S3-compatible privado
                                         |-> DNS/web/LLM/Gmail/Overture
```

En local el browser usa sólo `localhost:3000`; el backend diagnóstico liga `127.0.0.1:8001` y los
servicios de datos no necesitan publicación. En producción el custom domain pertenece sólo al
frontend. El backend exige un token de proxy server-side, hosts/orígenes exactos y nunca confía
headers forwarded enviados por el browser.

## 2. Módulos y dependencias

```text
backend/src/contact_outreach/ settings, API URLs, ASGI, Celery y middleware
backend/src/apps/accounts/    Workspace, Membership, sesión y usuarios/capacidades
backend/src/apps/*/           dominio, provider boundaries, tasks y DRF APIs
backend/tests/                tests de dominio/API, migraciones y fakes sin red
frontend/app/                 rutas Next.js y proxy `/api/v1/*`
frontend/features/            UI por capacidad con cliente OpenAPI generado
frontend/tests/               unit, accesibilidad y Playwright desktop/mobile
infra/                        Compose de frontend/backend/PostgreSQL/Redis/MinIO
```

Si el repositorio conserva temporalmente entidades en `prospects` o `configuration`, el cutover
puede implementar los modelos nuevos allí antes de extraer un módulo. La frontera lógica importa
más que un refactor de paths. No se mueve código sólo por estética durante la migración.

La dirección de dependencias es API serializers/views y tasks -> servicios de dominio ->
models/protocolos.
Adaptadores externos dependen de los protocolos, nunca al revés. Views y tasks no asignan estados.
Tasks reciben UUIDs, reabren filas y revalidan permisos/precondiciones.

El frontend consume exclusivamente `/api/v1`; oculta controles según capabilities sólo para UX.
DRF vuelve a autorizar y scopear cada objeto. Mutaciones largas crean `BackgroundJob` durable en la
misma transacción y publican Celery con `transaction.on_commit`.

## 3. Autorización y privacidad

Un middleware resuelve el único Workspace y la `Membership` activa. Un servicio central de
capacidades decide `manage_users`, `manage_campaigns`, `approve_campaign`, `send_reply`,
`manage_contacts`, `manage_knowledge`, `manage_automation` y lecturas permitidas. Los filtros por
`created_by` desaparecen como autorización; esos FKs quedan para atribución.

La matriz se aplica en navegación, views y servicios para impedir bypass por POST/task:

| Capacidad | ADMIN | VENDEDOR |
| --- | --- | --- |
| Resumen, campañas no borrador, mensajes enviados | leer/escribir según acción | sólo leer |
| Contactos, conversaciones y tareas | administrar | sólo leer |
| Usuarios, configuración, integraciones, conocimiento | administrar | sin acceso |
| Borradores, audiencia/prospectos, exportaciones, PDFs, jobs, auditoría | administrar | sin acceso |

Todas las respuestas con cuerpos de mensajes/contactos usan `Cache-Control: private, no-store`.
Provider/model/confidence/IDs/hashes/manifiestos se exponen sólo a admins bajo detalles técnicos.

## 4. Flujos principales

### 4.1 Base nueva y preservación del servicio anterior

La arquitectura nueva aplica toda la historia de migraciones sobre PostgreSQL vacío y luego las
migraciones forward de API/storage/remoción de MFA. No hay ETL, backfill entre bases, dual-write ni
CDC. El servicio anterior conserva su DB/volumen y queda privado, sin workers, Beat ni Gmail después
del cutover. Nunca se editan las migraciones Overture existentes.

### 4.2 Geografía y Overture

La UI selecciona provincias y distritos jerárquicos; cada provincia carga un mapa SVG local de sus
distritos seleccionables y conserva lista/búsqueda como respaldo. Al iniciar discovery, una
transacción congela zonas/rubros y resuelve una partición `READY` por provincia, todas del mismo
`OvertureRelease`. Si falta una, aborta con el nombre y el camino “Datos de búsqueda”.

Maintenance ejecuta un `record_batch_reader` por bbox provincial. El import transmite lotes,
deduplica GERS ID dentro del release, valida schema/taxonomía/proveniencia/licencias y aplica
point-in-polygon exacto a cada distrito. Una partición fallida no afecta las READY de otras
provincias. Una `SearchQuery` sólo consulta la partición de su distrito con orden/cursor estable.

### 4.3 Campaña determinista y aprobación

1. `DRAFT`: el admin elige rubros, provincias/distritos, PDFs, ventana, modo de entrega/aprobación y
   recordatorio. Se copian contenido fijo y firma aprobados.
2. `DISCOVERING`: busca organizaciones y crea enrollments elegibles. No llama LLM.
3. `AWAITING_APPROVAL`: audiencia final y resumen exacto quedan visibles.
4. En `CAMPAIGN`, una confirmación congela audience/content/attachment/schedule hashes. En
   `PER_MESSAGE`, sólo quedan autorizadas las filas aprobadas y un POST adicional inicia.
5. `RUNNING`: el scheduler revalida y reserva `(email, fecha local)` antes de cada inicial o
   recordatorio. Un conflicto se agenda al próximo día permitido.
6. Gmail confirma/reconcilia envíos. El inicial confirmado crea como máximo un recordatorio
   pendiente; cualquier respuesta humana/Contacto/restricción lo cancela.
7. `COMPLETED` espera el resultado terminal de todos los recordatorios posibles.

Los PDFs se fijan en dos niveles: `CampaignAttachment` ordena las versiones aprobadas y
`OutboundAttachment` copia de forma inmutable storage key/hash/tamaño/orden para el efecto. MIME se
construye únicamente tras verificar el conjunto completo y sus límites.

Los emails cargados manualmente se persisten y excluyen de campañas dentro del request, pero su
validación MX se despacha con `transaction.on_commit` a Celery. El worker reabre la fila por UUID y
marca `VALID`, `INVALID` o `TRANSIENT`; ninguna vista espera DNS.

### 4.4 Sincronización y promoción de Contacto

Un lock por conexión serializa sync. La transacción importa únicamente threads/cabeceras propias o
mails directos cuyo remitente coincide con un EmailAddress válido de un Contacto existente,
actualiza cursor y ejecuta primero baja, bounce y auto-reply determinísticos. Una respuesta humana
de campaña promueve Organization a Contact, crea/vincula Conversation y cancela recordatorios. Un
mail directo vincula el Contacto existente y queda sin campaña/outbound padre. Los mensajes Gmail con
marca `SENT` que responden a un inbound conocido se persisten como `MANUAL_REPLY` confirmado, se
agregan a la misma Conversation, resuelven la tarea `REPLY_REVIEW` y cancelan efectos automáticos
que todavía estén en cola. Al commit se publica una task de decisión sólo si no existe esa respuesta
manual. El lock ya está liberado antes de LLM.

El análisis trabaja sobre un snapshot de contexto acotado. Extrae emails literales antes del LLM,
inyecta el contexto general vigente, usa `EmbeddingProvider` para elegir hasta tres facts
puntuales aprobados por similitud y construye el manifiesto/schema dinámico con candidate IDs/fact
revision IDs permitidos. Si la búsqueda semántica es pobre o ambigua, los facts mejor rankeados
pueden entrar como candidatos con `may_be_irrelevant=true`; el LLM debe ignorar los que no apliquen
y el policy deriva a HumanTask cuando no hay fundamento suficiente. Si falla o falta contexto
obligatorio, crea HumanTask. Las instrucciones admin de redacción entran como
`ADMIN_WRITING_INSTRUCTIONS` y sólo gobiernan estilo; nunca reconstruye prompts desde logs.
La policy también detecta señales claras de coordinación humana (canal de llamada/reunión más
coordinación o disponibilidad) si el LLM las clasifica erróneamente; no convierte menciones
informativas de horarios o teléfonos en tareas por sí solas.

### 4.5 Decisión y efecto automático

El LLM sólo devuelve una propuesta estructurada; no tiene tool calling. Un policy engine
determinista toma `ReplyDecision` y decide una de estas salidas:

```text
persist inbound -> deterministic effects -> build bounded context -> decide_reply
                                                           |
                 +-----------------------------------------+------------------+
                 |                                            |               |
              no action                                  HumanTask       eligible action
                                                                           |
                                                              lock + policy recheck
                                                                           |
                                                                    durable OutboundMessage
                                                                           |
                                                               Gmail send/reply/reconcile
```

En SHADOW, la última rama sólo persiste qué habría hecho. En LIVE, un executor común revalida
modo, kill switch, headers, restricción, límite y suspensión inmediatamente antes de Gmail. Para
`REPLY`, el executor conserva y envía el `proposed_body` validado de `ReplyDecision`; los facts
seleccionados sólo fundamentan la decisión y no reemplazan su redacción.

La redirección es una saga explícita:

1. validar/bloquear candidate y Contact;
2. agregar EmailAddress al mismo Contact;
3. crear `REFERRED_PROPOSAL` en hilo nuevo con PDFs del origen;
4. confirmar/reconciliar Gmail;
5. recién entonces crear `REDIRECT_ACK` en el hilo original.

Cada paso tiene idempotency key distinta derivada del inbound Gmail ID. Si el paso 3 falla no se
crea un ACK falso y se abre HumanTask.

### 4.6 Tareas y notificaciones

Abrir `HumanTask` suspende automation de la Conversation en la misma transacción. El dashboard es
la fuente durable de la alerta. Después del commit se crean `NotificationDelivery` por admin
activo; cada una usa Gmail con asunto genérico y link generado desde `PUBLIC_BASE_URL`. Fallar el
canal secundario nunca cambia el task.

Cuando una respuesta manual `MANUAL_REPLY` llega a `SENT`, ya sea desde la aplicación o detectada en
Gmail, el servicio de dominio resuelve las tareas `REPLY_REVIEW` abiertas para ese inbound y reactiva
la Conversation sólo si no quedan otras tareas abiertas. Una autorización manual en cola o fallida
no limpia la alerta.

### 4.7 Comunicación programada

Beat selecciona aprobaciones `ContactCommunicationPlan` vencidas bajo lock. La fecha y cadencia viven
en `FollowUpTopic` globales; cada aprobación sólo cachea el próximo vencimiento derivado por tema,
historial y snooze. El LLM sólo propone contenido para el tema aprobado. `REVIEW_BEFORE_SEND` crea
draft durable. `AUTOMATIC` todavía atraviesa el mismo policy engine, contexto y executor. Un envío
confirmado o interacción genuina recalcula `next_due_at` desde el timestamp confirmado. Una
aprobación no interactúa con elegibilidad de campaña.
Un Contacto manual sin hilo previo puede recibir una `HumanTask` a nivel Contacto; al abrirla se
suspende su automatización hasta que un administrador la resuelva o descarte.

## 5. Jobs, consistencia y recuperación

Colas: `orchestration`, `extraction`, `enrichment`, `analysis`, `delivery`, `mailbox`,
`notifications` y `maintenance`. Beat es el único productor periódico; un lock PostgreSQL impide
dos schedulers efectivos.

- Redis puede borrarse: jobs pendientes se reconstruyen desde PostgreSQL.
- Locks por campaña serializan lifecycle/aprobación; locks por Organization/EmailAddress protegen
  dedupe/restricciones; lock por Conversation protege automatización; lock por GmailConnection
  protege cursor; locks/advisory globales protegen imports.
- `CampaignDeliveryReservation` aplica same-day de forma transaccional.
- `SENDING` o `RECONCILING` vencido busca Message-ID antes de volver a `QUEUED`.
- Una acción automática se identifica por `(inbound_gmail_id, semantic_action)`; doble task no crea
  un segundo efecto.
- Audit y cambios locales relevantes se escriben juntos. Publicación Celery usa
  `transaction.on_commit`.
- Pausa, cancelación, restricción o kill switch se vuelven a leer inmediatamente antes del efecto.

## 6. Configuración y secretos

Workspace posee `BusinessProfile`, `IntegrationConfiguration`, `PromptConfiguration`, defaults de
mensajes y knowledge. `IntegrationConfiguration` define también proveedor/modelo/dimensiones de
embeddings; la variante OpenAI-compatible reutiliza la conexión OpenAI-compatible configurada.
`PromptConfiguration` separa preferencias de campañas de las instrucciones de respuestas
automáticas. Las credenciales se cifran con subclaves por propósito derivadas de
`FIELD_ENCRYPTION_KEY`, son write-only y se resuelven al construir adaptador. Campañas guardan sólo
snapshots no secretos.

Controles externos de despliegue:

- `SEND_MODE=dry-run|live` y `SEND_KILL_SWITCH=true|false`.
- `AUTO_REPLY_KILL_SWITCH=true|false` independiente.
- `RELATIONSHIP_KILL_SWITCH=true|false` independiente.
- `PUBLIC_BASE_URL` obligatorio sólo para alertas por email.
- hosts/orígenes/proxy/HSTS/cookies/CSP desde entorno, no desde dashboard.

## 7. Observabilidad, health y backup

Logs estructurados incluyen correlation ID, entidad, operación, duración y código de error; no
incluyen cuerpos, recipients sin redacción, prompts, tokens, ciphertext ni credenciales.
`ProviderUsage`, `BackgroundJob`, `ReplyDecision`, `NotificationDelivery` y `AuditEvent` sostienen
diagnóstico sin contadores mutables.

Liveness sólo indica proceso. Readiness comprueba servicios locales. Health detallado de
Gmail/proveedores/configuración es admin-only y `no-store`. El backup consistente incluye DB,
volumen PDF y clave de cifrado respaldada aparte. Restore verifica migraciones, ciphertext,
integridad de cada PDF y particiones Overture activas antes de habilitar live.

## 8. Riesgos explícitos

- Gmail readonly puede requerir verificación Google y concede más visibilidad que lo persistido.
- Gmail no ofrece exactly-once absoluto; reconciliación reduce, pero no elimina, una ventana
  residual tras timeout.
- Outreach frío, aunque limitado, puede afectar reputación o incumplir obligaciones legales; live
  exige revisión externa.
- Una decisión LLM puede equivocarse; allowlists, hechos versionados, SHADOW, límites y kill switch
  contienen el daño.
- La aplicación aún no está desplegada de forma pública: readiness no reemplaza proxy TLS,
  certificados, monitoreo ni revisión de infraestructura.
