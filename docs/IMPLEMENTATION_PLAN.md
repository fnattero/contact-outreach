# Plan de implementación

## Reglas del plan

Cada fase debe entrar como cambio verificable y dejar `main` ejecutable. No se habilita live hasta completar la fase 12. Las migraciones se agregan en la fase que introduce el modelo y nunca se reescriben después de integrarse. Tests no acceden a Internet ni requieren credenciales.

## Estado del incremento de configuración

El dashboard implementa la fase 2, el audit log append-only de fase 1 y el corte vertical de
campaña borrador/supresión/máquinas de estado. El incremento de extracción aporta la fase 3 y la
parte de email/deduplicación de fase 4: `SearchRun`, payload crudo, uso/costo, polling durable,
Outscraper opt-in, mock determinístico, prospectos, identidades globales, sintaxis/MX y email
principal. Los incrementos de web e IA aportan las fases 5–6 para ese corte: fetch HTTP con IP
fijada y defensa SSRF, snapshots de hasta cuatro páginas, proveedores mock/Ollama/OpenAI
compatible, prompt y schema versionados, validación y retry acotado/durable, caché persistente,
`AIAnalysis`, `OutboundMessage` preparado, reserva idempotente por prospecto y regeneración manual
con generación monotónica. Los incrementos de fases 7–9 agregan `BackgroundJob`, recuperación
desde PostgreSQL, ledger/override global, OAuth Gmail con PKCE y token cifrado, MIME texto+PDF,
proveedor API/fake, scheduler de entrega, cuota/calendario/intervalo, pausa/cancelación/kill switch,
backoff acotado y reconciliación por Message-ID. Esto aporta evidencia
de FR-01, FR-02, FR-03, FR-04, FR-05, FR-06, FR-07, FR-08, FR-09, FR-10, FR-12, FR-14, FR-15,
FR-16 y FR-17. El incremento de fase 10 agrega sincronización Gmail incremental con fallback
acotado, asociación exclusiva por thread/cabeceras propias, sanitización, clasificación y efectos de
baja/rebote. También incorpora el hilo cronológico y la respuesta manual idempotente por POST, con
`threadId`, `In-Reply-To` y `References`; un worker dedicado sólo consume autorizaciones creadas por
POST, revalida elegibilidad bajo lock y reconcilia ambigüedades, mientras el scheduler general nunca
convierte mensajes entrantes en respuestas. Esto
completa evidencia de FR-11 y FR-13 y amplía FR-12, FR-14, FR-15 y FR-17.
El incremento de fases 11–12 completa métricas derivadas, filtros/búsqueda/paginación, CSV seguro,
progreso de jobs, retry manual sobre la misma entidad, logs JSON correlacionados/redactados,
pantallas de error, health degradado, ENOSPC, confirmaciones de acciones/live, simulación fake desde
UI, backup/restore con checksums y verificación de catálogos/tokens. La aceptación fake completa y
los runbooks están en `tests/e2e/`, `docs/OPERATIONS.md` y `docs/HARDENING_AUDIT.md`. Esto completa
evidencia de FR-14, FR-15, FR-16, FR-17, OPS-02 y QA-01 sin cambiar la arquitectura.
El incremento posterior de configuración segura extiende fases 1, 2, 6, 8, 11 y 12 con
`IntegrationConfiguration`, credenciales write-only cifradas por propósito, reautenticación,
fallback de entorno explícito, resolución dinámica en workers, URLs/redirects endurecidos y
verificación de restore. Actualiza evidencia de FR-05, FR-06, FR-10, FR-14, FR-16, FR-17, DM-01 y
QA-01. La raíz de cifrado, infraestructura y las dos barreras globales de envío siguen externas.
El objetivo
calificado corta sobre prospectos `QUEUED` creados por el análisis sobre umbral; nunca se cuenta
`EMAIL_FOUND` como calificado. `SEND_MODE` y el kill switch permanecen como controles de
despliegue de sólo lectura.

## Fase 0 - Scaffold reproducible y aplicación base

- **Objetivo:** crear proyecto Python/Django, quality gates y runtime Compose sin modelos de dominio. Incluye el shell mínimo solicitado para operar la instalación: login del propietario built-in, navegación y dashboard inicial; la auditoría y el hardening completo permanecen en fase 1.
- **Archivos/módulos:** `pyproject.toml`, `Makefile`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `src/contact_outreach/`, shell mínimo de `accounts`, `dashboard`, `health` e `integrations`, `tests/`, `.gitignore`.
- **Migraciones:** sólo migraciones built-in de Django en la base local; ninguna propia.
- **Pruebas:** settings, liveness/readiness, login/dashboard mínimo, Celery importable y tarea smoke, contratos con proveedores fake, red bloqueada en pytest.
- **Verificación:** `docker compose config`, `docker compose build`, `make lint typecheck test`, `docker compose up --wait`.
- **Terminado:** web/worker/beat/postgres/redis saludables; web ligada a 127.0.0.1; migración segura desde vacío; propietario y dashboard operativos; datos demo opt-in separados; dry-run y fakes por defecto.
- **Dependencias:** ninguna.
- **Riesgos:** permisos de volumen, imágenes no fijadas y health checks que oculten fallos.

## Fase 1 - Seguridad base, propietario y auditoría

- **Objetivo:** login local seguro, bootstrap de propietario, CSRF/sesiones, secretos y audit log append-only.
- **Archivos/módulos:** `accounts`, `audit`, settings de seguridad, middleware de correlación y redacción.
- **Migraciones:** `AuditEvent`; aplicar `auth` (incluido `User` built-in), `sessions` y `contenttypes` de Django.
- **Pruebas:** bootstrap/rotación sin secreto en logs, Argon2, login/logout, throttling, permisos, CSRF y AuditEvent inmutable.
- **Verificación:** `make check`; `python manage.py check --deploy` con settings externos de prueba.
- **Terminado:** no existe registro; anónimo no accede a negocio; acciones mutantes requieren sesión/CSRF y quedan auditadas.
- **Dependencias:** fase 0.
- **Riesgos:** cookies incompatibles con HTTP local y filtración de variables en errores.

## Fase 2 - Configuración, seeds y catálogos

- **Objetivo:** perfil comercial, rubros, barrios y catálogo privado versionado.
- **Archivos/módulos:** `configuration`, `catalogs`, forms/views/templates, comandos de seed.
- **Migraciones:** `BusinessProfile`, `IntegrationConfiguration`, `SearchCategory`, `SearchZone`,
  `Catalog`; constraints de credenciales/configuración, nombre/version/hash y seed data migration con
  23 rubros/48 barrios.
- **Pruebas:** CRUD/archivado, configuración cifrada write-only con reautenticación/CSRF,
  idempotencia de seeds, validación PDF/MIME/magic/size/path, hash/inmutabilidad y descarga
  autenticada.
- **Verificación:** `make check`; aplicar migraciones dos veces sobre base limpia; cargar fixtures PDF válidos/falsos.
- **Terminado:** configuración completa desde dashboard; archivos fuera del público; editar catálogo crea versión nueva.
- **Dependencias:** fase 1.
- **Riesgos:** falsos positivos MIME y pérdida de sincronía DB/volumen.

## Fase 3 - Campaña borrador, extracción y persistencia cruda

- **Objetivo:** crear campañas/consultas y ejecutar extracción fake/Outscraper bajo límites.
- **Archivos/módulos:** `campaigns`, `prospects`, contratos/adapter extractor, tasks `extraction` y dashboard inicial de progreso.
- **Migraciones:** `Campaign` con estado operativo/descubrimiento, `CampaignSelection`, `SearchQuery`, `SearchRun`, `Prospect`, `ProspectIdentity`, `ProspectEmail`, `ProviderUsage` e índices/uniques.
- **Pruebas:** snapshots, orden de queries, payload crudo antes de parser, async polling, recuperación
  de campaña sin mensaje Redis, serialización de cancelación, 429/403, máximo crudo, cap/costo y proveedor fake.
- **Verificación:** `make check`; E2E fake de campaña a registros `DISCOVERED`; revisar queries SQL críticas.
- **Terminado:** extracción sólo ocurre tras Iniciar y respeta queries, raw cap, costo y errores permanentes; el corte por objetivo calificado se integra en fase 7 cuando existe el pipeline completo.
- **Dependencias:** fase 2.
- **Riesgos:** esquema Outscraper variable, sobrecosto por lotes y payloads grandes.

## Fase 4 - Email, deduplicación y supresión

- **Objetivo:** producir un prospecto canónico con cero/un email principal elegible.
- **Archivos/módulos:** servicios de normalización, MX, selección, dedupe, elegibilidad y UI de supresión/override.
- **Migraciones:** `SuppressionEntry`, `ContactLedger` sin FKs salientes futuras, `ContactOverride`; constraints parciales de email primario e identidades.
- **Pruebas:** todos los casos de normalización/MX/exclusión/selección, cuatro claves de dedupe, creación concurrente del ledger, elegibilidad de recontacto, override y baja/bounce no anulables.
- **Verificación:** `make check`; tests concurrentes contra PostgreSQL; plan de índices con dataset demo.
- **Terminado:** duplicados convergen sin perder fuentes; ningún suprimido/inválido queda elegible; overrides quedan justificados y listos para consumo atómico durante live en fase 9.
- **Dependencias:** fase 3.
- **Riesgos:** merges falsos por dominio/nombre y DNS intermitente.

## Fase 5 - Enriquecimiento web seguro

- **Objetivo:** obtener snapshots útiles sin JavaScript y sin permitir SSRF.
- **Archivos/módulos:** contrato/adaptadores `WebsiteFetcher`, resolver/transport fijado con deadline DNS, Public Suffix List embebida, extracción/limpieza y tasks `enrichment`.
- **Migraciones:** `WebsiteSnapshot` con hashes, extractos, límites y error parcial.
- **Pruebas:** matriz SSRF IPv4/IPv6/DNS rebinding/redirects/metadata, timeouts, tamaños, content type, máximo páginas, limpieza y fake.
- **Verificación:** `make check`; suite SSRF con sockets externos bloqueados.
- **Terminado:** ningún caso prohibido abre conexión; un fallo web siempre deja fallback auditable y continúa.
- **Dependencias:** fase 4.
- **Riesgos:** TOCTOU DNS, parser HTML costoso y redirects engañosos.

## Fase 6 - Análisis y copy con IA

- **Objetivo:** calificar y generar un mensaje válido con una llamada lógica cacheada.
- **Archivos/módulos:** contratos/adaptadores mock/Ollama/OpenAI-compatible, schemas Pydantic, prompt versionado, facts builder y task `analysis`.
- **Migraciones:** `AIAnalysis` con retry diferido/generación, reserva durable de pipeline en `Prospect`, `OutboundMessage` base, `contact_sequence` condicional, cache unique, Message-ID/idempotency unique; agregar `reserved_message`/`last_sent_message` a `ContactLedger` ahora que existe outbound.
- **Pruebas:** JSON/bounds/evidence, 70–130 palabras sobre mensaje final, idioma/reglas/CTA, prompt injection, retries diferidos, caché, regeneración concurrente e indisponibilidad sin fallback.
- **Verificación:** `make check`; golden tests del prompt contra mock; prueba E2E con Ollama sustituido, nunca real.
- **Terminado:** sólo outputs completamente válidos crean mensaje candidato; bajo umbral no envía; error queda visible.
- **Dependencias:** fase 5.
- **Riesgos:** variabilidad de modelos, claims difíciles de validar y costo por retries.

## Fase 7 - Orquestación durable y máquinas de estados

- **Objetivo:** automatizar el pipeline, objetivo y pausa/reanudación/cancelación con recuperación.
- **Archivos/módulos:** state services, orchestrator, scheduler interno, locks y `BackgroundJob`.
- **Migraciones:** `BackgroundJob`, estados/check constraints, snapshots definitivos y reservas de objetivo/costo.
- **Pruebas:** transiciones inválidas, dos workers, reservas, pausa/cancelación en curso, agotamiento de descubrimiento con drenaje de cola, Redis vacío, worker reiniciado y job sin heartbeat.
- **Verificación:** `make check`; E2E fake con workers reales y reinicios de contenedor.
- **Terminado:** campaña alcanza objetivo o razón terminal exacta sin doble procesamiento y sobrevive reinicios.
- **Dependencias:** fases 3–6.
- **Riesgos:** deadlocks, starvation y tareas tardías tras cancelar.

## Fase 8 - OAuth Gmail y MIME

- **Objetivo:** conectar/probar/desconectar Gmail, cifrar token y construir mensajes correctos sin enviar automáticamente aún.
- **Archivos/módulos:** `mailbox`, `configuration`, `GmailProvider` API/fake, OAuth callback,
  credenciales de aplicación cifradas, token crypto, MIME builder y páginas Integraciones/Gmail.
- **Migraciones:** `GmailConnection`; ampliar `OutboundMessage` con MIME hash, IDs Gmail y thread. La FK a inbound se agrega en fase 10.
- **Pruebas:** state OAuth, scopes exactos, reautenticación, client secret/refresh token
  cifrados y no visibles, bloqueo de rotación con conexión activa, redacción/revocación, MIME
  texto+PDF, tamaño, headers y fake.
- **Verificación:** `make check`; inspección MIME con parser estándar; búsqueda de secretos en logs/diff.
- **Terminado:** fake funciona end-to-end; API real sólo se prueba manualmente con credenciales externas y no en CI.
- **Dependencias:** fases 1, 2 y 6.
- **Riesgos:** scope restringido/verificación Google, refresh token de siete días y adjuntos cerca del límite.

## Fase 9 - Scheduler de entrega e idempotencia

- **Objetivo:** dry-run y envío live controlado por cuota, calendario, kill switch y reconciliación.
- **Archivos/módulos:** delivery scheduler/sender/reconciler, elegibilidad final, safety monitor y vistas de preparados/enviados/errores.
- **Migraciones:** agregar attempts, next attempt, timestamps y reservas de entrega; constraints/checks finales de primer contacto live.
- **Pruebas:** daily limit/timezone/intervalo, dry-run sin consumo de ledger, asignación/override concurrente de `contact_sequence`, kill switch, catálogo cambiado, supresión tardía, 403/429, timeout ambiguo, restart y umbrales de pausa.
- **Verificación:** `make check`; E2E fake con doble worker/doble submit y reloj congelado.
- **Terminado:** ningún camino envía sin tres barreras live; doble ejecución produce como máximo un efecto fake y la incertidumbre real pausa.
- **Dependencias:** fases 4, 7 y 8.
- **Riesgos:** exactamente-una-vez imposible de garantizar en Gmail y reputación de cuenta personal.

## Fase 10 - Sync, clasificación y respuesta manual

- **Objetivo:** importar sólo hilos propios, clasificar y permitir respuesta humana explícita.
- **Archivos/módulos:** Gmail sync/fallback, parser/sanitizer, classifier, thread views y manual reply command/form.
- **Migraciones:** `InboundMessage`, parent/reference fields de `OutboundMessage`, cursor/fechas de sync e índices Gmail/RFC.
- **Pruebas:** history paginado/404, asociación por thread/headers, mensajes ajenos, HTML hostil, BAJA/bounce/auto-reply, clasificación fallida, CSRF y doble clic manual.
- **Verificación:** `make check`; E2E fake de seis clasificaciones y respuesta dentro del hilo.
- **Terminado:** respuestas aparecen sin importar inbox ajeno; supresión es inmediata; ninguna task puede contestar automáticamente.
- **Dependencias:** fases 8–9.
- **Riesgos:** acceso amplio del scope readonly, hilos mal asociados y HTML malicioso.

## Fase 11 - Dashboard, CSV y operación visible

- **Objetivo:** completar todas las vistas, filtros, contadores, logs y exportaciones HTMX.
- **Archivos/módulos:** dashboards de todos los apps, partials HTMX, query services, CSV seguro y navegación.
- **Migraciones:** sólo índices surgidos de planes reales de consulta; sin duplicar contadores derivados.
- **Pruebas:** permisos/CSRF, filtros combinados, paginación, contadores, empty/error states, accesibilidad básica y CSV formula injection.
- **Verificación:** `make check`; smoke responsive con Playwright; revisar queries N+1 y tiempos con datos demo.
- **Terminado:** cada pantalla/filtro/export requerido funciona y distingue enviado, simulado, error y engagement.
- **Dependencias:** fases 2–10.
- **Riesgos:** consultas costosas, contadores inconsistentes y HTMX sin fallback claro.

## Fase 12 - Operación, demo y hardening

- **Objetivo:** cerrar experiencia de desarrollo, backups/restores, documentación y aceptación E2E.
- **Archivos/módulos:** README, scripts de backup/restore, datos demo, health checks finales, runbooks y Compose endurecido.
- **Migraciones:** `makemigrations --check` debe estar limpio; sólo fixes encontrados por restore desde cero.
- **Pruebas:** E2E completo fake, restore con catálogo/token cifrado, fallo Redis/worker/Beat, retención y checklist live.
- **Verificación:** `make check`, build limpio, instalación desde cero, backup, destrucción controlada, restore y repetición E2E.
- **Terminado:** otra persona levanta dry-run siguiendo README; toda integración real permanece opt-in; matriz de requisitos y checklist live están completos.
- **Dependencias:** todas las fases.
- **Riesgos:** documentación divergente, backups no restaurables y activación live accidental.

## Matriz de trazabilidad

| Requisito | Fases | Evidencia principal |
| --- | --- | --- |
| FR-01 | 3, 4 | extractor, raw JSON, email y dedupe |
| FR-02 | 3, 7, 11 | límites, reservas, contadores |
| FR-03 | 2, 11 | seeds y CRUD |
| FR-04 | 5 | fetch seguro/fallback |
| FR-05 | 6 | schemas, prompt, cache y threshold |
| FR-06 | 1, 2, 6, 8, 11, 12 | perfil, parámetros y credenciales cifradas |
| FR-07 | 7 | lifecycle y recovery |
| FR-08 | 7, 9 | scheduler, cuotas y safety stop |
| FR-09 | 2, 9 | catálogo/version/hash |
| FR-10 | 1, 2, 8, 9, 12 | credenciales OAuth, conexión, MIME, IDs e idempotencia |
| FR-11 | 10 | history/fallback/hilos |
| FR-12 | 4, 10 | clasificación, invalidez y supresión |
| FR-13 | 10 | reply manual thread-safe |
| FR-14 | 2, 3, 9–11 | vistas, filtros y CSV |
| FR-15 | 1, 4, 7, 9, 10 | estados, locks, audit e idempotencia |
| FR-16 | 0, 1, 2, 5, 8, 11, 12 | auth, bind, secretos cifrados, SSRF y upload |
| FR-17 | 0, 3, 5, 6, 8, 12 | contratos/fakes/E2E |
| OPS-01 | 0 | stack y migraciones |
| OPS-02 | 0, 2, 12 | Compose, Makefile, seeds, ops y README |
| DM-01 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 | entidades, configuración cifrada y extensiones de integridad |
| QA-01 | 0–12 | suites incrementales y E2E final |

### Cobertura de las secciones originales

| Sección original | Requisitos | Fases |
| --- | --- | --- |
| 1. Búsqueda de prospectos | FR-01 | 3, 4 |
| 2. Objetivo de campaña | FR-02 | 3, 7, 11 |
| 3. Rubros y barrios | FR-03 | 2, 11 |
| 4. Enriquecimiento web | FR-04 | 5 |
| 5. IA y perfil comercial | FR-05, FR-06 | 1, 2, 6, 8, 11, 12 |
| 6. Campañas y envío | FR-07, FR-08 | 7, 9 |
| 7. Catálogo PDF | FR-09 | 2, 9 |
| 8. Gmail | FR-10 | 1, 2, 8, 9, 12 |
| 9. Sincronización/clasificación | FR-11, FR-12 | 4, 10 |
| 10. Respuesta manual | FR-13 | 10 |
| 11. Dashboard/CSV | FR-14 | 2, 3, 9, 10, 11 |
| 12. Estados/idempotencia | FR-15 | 1, 4, 7, 9, 10 |
| 13. Seguridad | FR-16 | 0, 1, 2, 5, 8, 12 |
| 14. Stack e interfaces | OPS-01, FR-17 | 0, 3, 5, 6, 8, 12 |
| 15. Entidades | DM-01 | 1–10 |
| 16. Calidad | QA-01 | 0–12 |
| 17. Experiencia de desarrollo | OPS-02 | 0, 2, 12 |

## Riesgos que bloquean live

1. Revisión legal ausente o identidad/BAJA incompletas.
2. OAuth Gmail no publicado/estable, scopes distintos o token no descifrable.
3. Backup/restore no probado o catálogo sin integridad.
4. Fallos en idempotencia, supresión, SSRF o límites diarios.
5. Kill switch no probado y logs con secretos/PII.

Ningún riesgo de esta lista se acepta silenciosamente; live continúa deshabilitado hasta resolverlo.
