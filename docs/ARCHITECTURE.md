# Arquitectura

## 1. Estilo y límites

Se implementará un **monolito modular Django**. PostgreSQL es la única fuente de verdad; Redis se usa como broker/cache prescindible y Celery ejecuta trabajo asíncrono. Las integraciones externas se aíslan con protocolos tipados y adaptadores. Django templates y HTMX resuelven la UI sin SPA ni pipeline Node.

```text
Browser -> Django web -> PostgreSQL
                    |-> private catalog volume
                    |-> Redis -> Celery workers -> Outscraper / DNS / websites / LLM / Gmail
                              -> Celery Beat ----> scheduler / Gmail sync / recovery
```

Servicios Docker futuros: `web`, `worker`, `beat`, `postgres` y `redis`. `web` se publica en `127.0.0.1` por defecto. Worker y Beat usan la misma imagen y configuración. Sólo Beat dispara tareas periódicas; un lock PostgreSQL evita dos schedulers efectivos.

## 2. Organización futura

```text
src/contact_outreach/   # settings, URLs, ASGI/WSGI y Celery
src/apps/accounts/      # propietario, login y conexión OAuth
src/apps/configuration/ # perfil, rubros, zonas y settings operativos
src/apps/prospects/     # prospectos, emails, deduplicación y enriquecimiento
src/apps/campaigns/     # campañas, consultas, mensajes y orquestación
src/apps/catalogs/      # archivos PDF privados e inmutables
src/apps/mailbox/       # sync, hilos, clasificación y respuestas manuales
src/apps/integrations/  # contratos, adaptadores y fakes
src/apps/audit/         # eventos y jobs observables
templates/              # páginas y fragments HTMX
tests/                  # árbol espejo, factories y fixtures
```

La lógica de negocio vive en servicios/comandos de dominio. Models preservan invariantes locales; formularios validan input; views coordinan HTTP; tasks reciben IDs y llaman servicios idempotentes. No se pasan objetos serializados completos por Celery.

## 3. Flujos principales

### Inicio y extracción

1. Una transacción valida campaña, perfil, consultas, catálogo, límites y proveedor; congela snapshots y cambia `DRAFT -> RUNNING`.
2. Un orquestador toma la siguiente `SearchQuery` pendiente, reserva costo y crea `SearchRun` con idempotency key.
3. El extractor devuelve un lote; submit/poll y la persistencia cruda se serializan con el lock de
   campaña para que pausa/cancelación no puedan confirmar durante un efecto pago.
4. Cada registro produce o vincula un `Prospect` mediante identidades deduplicables. Se selecciona un email después de sintaxis, exclusiones y MX.
5. Se programan enriquecimiento web y análisis sólo para prospectos elegibles. Los slots de objetivo se reservan transaccionalmente para limitar sobreprocesamiento.

Objetivo, agotamiento de queries/raw/costo o fallo permanente cierran `discovery_state`. La campaña sigue `RUNNING` para drenar mensajes ya autorizados y pasa a `COMPLETED` al terminar la cola, conservando `discovery_stop_reason`; sólo un error que compromete la campaña completa usa `STOPPED_ERROR`.

### Enriquecimiento e IA

`WebsiteFetcher` devuelve un snapshot acotado o un fallo no fatal. El análisis recibe un objeto de hechos estructurado, distingue texto web no confiable y usa un único prompt versionado. Pydantic rechaza JSON inválido, scores fuera de rango o evidencia desconocida; el validador de dominio compone firma/BAJA y rechaza el cuerpo final si queda fuera de 70–130 palabras o contiene material prohibido verificable. Un resultado relevante crea un `OutboundMessage` preparado; uno bajo umbral termina como irrelevante.

### Envío

Un scheduler por minuto selecciona mensajes elegibles con `select_for_update(skip_locked)`. Revalida campaña, supresión, email, catálogo, ventana, cupo, intervalo, Gmail, `SEND_MODE` y kill switch. En dry-run persiste el MIME hash y `DRY_RUN_COMPLETED` sin llamar Gmail.

En live, el sender fija `SENDING` y una clave única antes del efecto externo. Tras éxito guarda IDs Gmail y `SENT`. Ante timeout ambiguo queda `RECONCILING`; nunca vuelve a enviar sin buscar primero el `Message-ID`. Esta estrategia reduce duplicados pero Gmail no ofrece exactamente-una-vez absoluto.

### Respuestas

Beat ejecuta una única sincronización por cuenta. `historyId` obtiene cambios; se consultan metadatos y sólo se persisten mensajes ligados a threads/cabeceras propios. Ante cursor expirado se captura primero un baseline y luego se usa búsqueda limitada, evitando perder llegadas concurrentes. Un cursor vacío de una conexión legacy sólo inicializa baseline y no importa históricos. Reglas detectan rebotes y BAJA antes de construir cualquier proveedor IA; la IA sólo clasifica lo restante. Las respuestas manuales pasan por un comando separado que exige POST, CSRF, usuario autenticado y clic explícito: el request persiste una autorización única y el worker revalida supresión/invalidez bajo el lock compartido antes de Gmail. Una ambigüedad sólo avanza mediante reconciliación por `Message-ID`.

## 4. Colas y recuperación

Colas futuras: `orchestration`, `extraction`, `enrichment`, `analysis`, `delivery`, `mailbox` y `maintenance`. Delivery tendrá concurrencia baja; extracción e IA respetarán límites por proveedor. Cada task tiene soft/hard timeout, reintentos declarados y `BackgroundJob` correlacionado.

Al iniciar worker o en un barrido periódico:

- `SENDING`/`RECONCILING` vencidos se reconcilian, no se reenvían a ciegas.
- jobs `RUNNING` sin heartbeat vuelven a pendiente sólo si su operación es interna o idempotente.
- campañas pausadas/canceladas no entregan nuevas tareas.
- campañas activas sin `SearchRun` recuperable reconstruyen su siguiente query desde PostgreSQL.
- mensajes preparados sobreviven porque la cola se reconstruye desde PostgreSQL.

## 5. Consistencia e idempotencia

- Restricciones únicas protegen emails normalizados, identidades de proveedor, hashes de negocio, resultados de IA, mensajes Gmail e idempotency keys.
- Locks por campaña serializan objetivo/costo y efectos del extractor; todas las identidades de email
  validado se bloquean antes de deduplicar; locks por prospecto impiden pipelines dobles; un
  `ContactLedger` único por email serializa la autorización de primer contacto; lock por conexión Gmail serializa sync.
- El contador diario se calcula sobre mensajes live confirmados en la zona configurada.
- Redis puede perderse sin perder estado de negocio. Cachés se reconstruyen y nunca autorizan un envío por sí solos.
- Audit y uso de proveedor se escriben en la misma transacción que el cambio local relevante.

## 6. Configuración y modos

Variables de entorno contienen secretos e infraestructura. La base guarda parámetros editables no secretos y snapshots de campaña. `SEND_MODE=dry-run|live` y `SEND_KILL_SWITCH=true|false` son controles de despliegue; live requiere `SEND_MODE=live` y kill switch falso. El dashboard los muestra como sólo lectura.

Los adaptadores activos se eligen por configuración: mocks por defecto, Outscraper opcional, LLM mock/Ollama/OpenAI-compatible y Gmail fake/API. Cambiar proveedor no cambia el dominio.

## 7. Observabilidad, backup y health

Logs estructurados incluyen correlation ID, campaña, job, proveedor, duración y código de error, nunca tokens/API keys ni cuerpos completos fuera de debug explícito y redactado. `ProviderUsage`, `BackgroundJob` y `AuditEvent` sustentan dashboard y diagnóstico.

El middleware HTTP genera un correlation ID no confiado al cliente, lo devuelve en la respuesta y
emite JSON por stdout sin query string ni body. Jobs proyecta checkpoints persistidos; el único
retry manual es `SEND_FAILED -> QUEUED` sobre la misma fila, después de revalidar owner, campaña,
email, supresión, catálogo y ausencia de confirmación Gmail.

Health checks separados:

- liveness del proceso;
- readiness con PostgreSQL y Redis;
- estado degradado para Gmail/proveedores, sin marcar la web como caída.

El backup consistente incluye PostgreSQL y volumen privado de catálogos; la clave de cifrado se respalda separadamente. Restore verifica migraciones, hashes de catálogo y capacidad de descifrar tokens antes de habilitar live.
Los scripts publican backups sólo tras dump/copia/checksums completos, exigen confirmación para
restore y rechazan live efectivo con kill switch desactivado. Health degradado informa margen de
disco y configuración local sin hacer llamadas remotas.

## 8. Riesgos arquitectónicos

- **SEGURIDAD:** `gmail.readonly` permite leer el buzón aunque la aplicación filtre lo persistido; requiere extremo cuidado y posiblemente verificación Google.
- **INTEGRIDAD:** un timeout posterior a aceptar un envío Gmail deja una ventana residual de duplicado; reconciliación y pausa ante incertidumbre son obligatorias.
- **ENTREGABILIDAD:** una cuenta personal y outreach frío pueden generar reputación negativa, rebotes o suspensión; los límites propios no garantizan aceptación de Google.
- **COSTO:** Outscraper y modelos remotos cobran con unidades diferentes; se debe reservar costo conservador y detener antes de exceder el cap.
- **OPERACIÓN:** Beat único y workers reiniciables requieren locks y barridos de recuperación probados.
