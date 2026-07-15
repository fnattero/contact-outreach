# Modelo de datos

## 1. Convenciones

Todas las tablas usan UUID, `created_at` y `updated_at` en UTC. Emails y nombres comparables conservan valor original y normalizado. Dinero usa `Decimal` más moneda ISO; nunca `float`. JSON externo se guarda en `JSONField` con hash y metadatos de esquema. Fechas de negocio se muestran en la zona de la campaña.

Los snapshots usados por campañas, análisis y mensajes son inmutables. El borrado de configuración referenciada se convierte en archivado. Supresiones, mensajes, uso de proveedor y auditoría no se eliminan en la UI.

## 2. Propietario y configuración

### User

Usuario Django con email/username único, password hasheada, `is_active` y último acceso. No hay registro ni roles; sólo puede existir un propietario activo mediante servicio de bootstrap.

### BusinessProfile

Relación 1:1 con `User`: empresa, vendedor, teléfono, WhatsApp, descripción, productos, diferenciadores, domicilio, web, firma, instrucciones IA y umbral 0–100. `profile_version` aumenta al editar y permite construir snapshots.

### SearchCategory

`name`, `normalized_name`, `active`, `sort_order`, `archived_at`. Restricción única case-insensitive sobre nombre no archivado.

### SearchZone

`name`, `normalized_name`, `kind` (`NEIGHBORHOOD|CUSTOM`), `location_text`, `active`, `sort_order`, `archived_at`. Los barrios seed no son técnicamente especiales y pueden editarse.

## 3. Búsqueda y prospectos

### SearchQuery

Pertenece a campaña; guarda snapshots de rubro/zona/ubicación, texto final, orden, estado, cantidad de runs y último error. Única por `(campaign, normalized_query)`.

### SearchRun

Pertenece a query y campaña: proveedor, idempotency key, request JSON redactado, response JSON crudo, response hash, provider request ID, estado, cursor, cantidades, costo reservado/real, moneda, inicio/fin y error. Únicas `idempotency_key` y `(provider, provider_request_id)` cuando exista.

### Prospect

Entidad canónica: nombre original/normalizado, dirección original/normalizada, barrio, categoría, website, dominio registrable empresarial, teléfono, coordenadas, datos seleccionados del proveedor, estado del pipeline, `error_stage` y `last_error`. También conserva una reserva durable del trabajo (`pipeline_reservation_key`, fechas de reserva/claim) y un contador monotónico `analysis_generation`; ambos impiden que entregas duplicadas o resultados viejos reemplacen trabajo más nuevo. Índices por estado, dominio, nombre normalizado, campaña y barrio. `error_stage` permite reanudar exactamente la etapa fallida.

### ProspectIdentity

Clave de deduplicación con `kind` (`EMAIL|BUSINESS_DOMAIN|PROVIDER_ID|NAME_ADDRESS`) y `value_hash`; pertenece a un prospecto. Única global por `(kind, value_hash)`. La creación concurrente captura conflicto y vincula al prospecto ya existente. Los dominios gratuitos, plataformas compartidas y dominios de proveedores no generan identidad `BUSINESS_DOMAIN`.

### ProspectEmail

`prospect`, original, `normalized_email`, dominio, local part, fuente, orden del proveedor, sintaxis, estado MX, fecha MX, motivo de exclusión, `is_primary`, `is_invalid`, fecha/reason de invalidez. Email normalizado se indexa globalmente; sólo un primario por prospecto mediante restricción parcial. Un mismo email no puede pertenecer a dos prospectos canónicos.

### ContactOverride

Autoriza una nueva primera aproximación pese a contacto previo: ledger/email, campaña destino, motivo obligatorio, autor, fecha y consumo. Única activa por campaña/email. Nunca es aplicable si existe supresión o email inválido.

### ContactLedger

Una fila global por `normalized_email`, bloqueada antes de autorizar cualquier `FIRST_CONTACT` live: `reserved_message`, `reserved_at`, `last_sent_message`, `last_sent_at`, `next_sequence` y estado de reserva. El primer contacto usa secuencia 1; un recontacto sólo reserva la siguiente secuencia al consumir un `ContactOverride`. Dry-run no reserva ni incrementa secuencia. La restricción única sobre email convierte la política global en segura frente a dos campañas concurrentes.

## 4. Enriquecimiento e IA

### WebsiteSnapshot

`prospect`, URL solicitada/final, fecha, status HTTP, content type, hash, extracto limpio, páginas consultadas, bytes, estado y error. No guarda scripts ni HTML completo por defecto. Índice por prospecto/fecha y hash.

### AIAnalysis

`prospect`, `input_hash`, prompt/schema version, provider, model, fecha, estado, attempts, score, confidence, reason, evidence JSON, subject sin prefijo, body text, output JSON validado y error. Registra además la generación/nonce de regeneración, actor solicitante y `next_retry_at`; `RETRY_WAIT` representa un error transitorio diferido sin convertirlo en output válido ni borrar el candidato vigente. Clave de caché única por `(input_hash, prompt_version, schema_version, provider, model)`.

## 5. Campañas y mensajes

### Campaign

Nombre, estado operativo, `discovery_state`, `discovery_stop_reason`, modo (`DRY_RUN|LIVE`), motivo de pausa/cierre, objetivo, máximo crudo, límite/costo/moneda, límite diario, intervalo, calendario, timezone, umbral, provider snapshots, perfil/prompt snapshots, catálogo, contadores derivados de referencia, fechas y autor. `settings_snapshot` es JSON inmutable desde el inicio. Índices por estado y fecha.

### CampaignSelection

Tablas intermedias para categorías y zonas seleccionadas, con nombre, orden y criterio congelados. Se crean antes de iniciar y no dependen de ediciones posteriores.

### Catalog

Cada fila es una versión inmutable: nombre visible, versión, storage key privada, nombre original sanitizado, MIME detectado, tamaño, SHA-256, fecha, autor, active y missing flag. Únicas `(name, version)` y SHA-256 opcionalmente reutilizable. Reemplazar archivo crea una fila nueva.

### OutboundMessage

`kind` (`FIRST_CONTACT|MANUAL_REPLY`), campaña, prospecto/email, recipient original/normalizado, `contact_sequence`, subject, body, catálogo/version, estado, delivery mode, idempotency key, Message-ID RFC, MIME hash, Gmail message/thread ID, parent inbound, attempts, next attempt, sent/simulated timestamps y error. `contact_sequence` es nulo para dry-run y respuestas manuales; se asigna al reservar un envío live. Son únicas idempotency key, Message-ID, Gmail message ID y `(recipient_normalized, contact_sequence)` para primeros contactos live. El primer contacto se reintenta actualizando la misma fila; nunca se crea otra para el mismo intento lógico.

`ContactLedger` impide crear otro `FIRST_CONTACT` para un email con envío confirmado salvo `ContactOverride` válido. Las respuestas manuales no consumen secuencia.

### InboundMessage

Cuenta Gmail, Gmail message/thread ID, Message-ID RFC, `In-Reply-To`, References normalizadas, from/to, subject, fecha externa/recibida, text body, HTML sanitizado, clasificación, confianza, `is_human`, `is_read`, outbound relacionado y error. Únicas Gmail message ID y Message-ID cuando esté presente.

### GmailConnection

Relación 1:1 con propietario: email conectado, scopes concedidos, refresh token cifrado, access metadata no persistente salvo expiración, último `history_id`, fechas de sync, estado, error y token version. Nunca contiene contraseña.

## 6. Cumplimiento, uso y auditoría

### SuppressionEntry

Email normalizado único, motivo (`UNSUBSCRIBE|BOUNCE|MANUAL`), fuente, mensaje relacionado, fecha, autor y evidencia mínima. `UNSUBSCRIBE` es permanente y no se elimina ni se anula. Bounce además marca `ProspectEmail.is_invalid`.

### ProviderUsage

Proveedor, operación, campaña/run/análisis/job relacionados, unidades, costo estimado/real, moneda, request ID, fecha y metadata no secreta. Índices por proveedor, campaña y fecha.

### AuditEvent

Append-only: actor (`USER|SYSTEM`), acción, tipo/ID de entidad, before/after redactados, correlation ID, IP local cuando corresponda y fecha. La aplicación no ofrece update/delete.

### BackgroundJob

Nombre de task, Celery task ID, idempotency key, entidad, cola, estado, attempts, heartbeat, timestamps, next retry y error redactado. Única por idempotency key. No reemplaza el resultado de dominio.

## 7. Integridad y retención

- PostgreSQL aplica check constraints a scores, confianza, tamaños, límites y costos no negativos.
- Toda transición y todo envío se ejecutan en `transaction.atomic`; el efecto Gmail se rodea con estado durable y reconciliación.
- La respuesta cruda de extractor y snapshots web se retienen 180 días por defecto; una tarea elimina contenido pero conserva hashes, métricas y auditoría. Mensajes relacionados se conservan hasta eliminación administrativa futura.
- Supresiones, hashes de emails suprimidos y evidencias mínimas se conservan permanentemente para evitar recontacto.
- Un backup necesita base, volumen privado y clave de cifrado; sin los tres no se considera restaurable.
