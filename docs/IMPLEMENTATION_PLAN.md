# Plan de implementación — frontend/backend seguro

## Reglas

La migración crea una aplicación v2 en una rama, Railway environment, PostgreSQL, Redis y Bucket
nuevos. El servicio anterior continúa durante desarrollo y después queda privado, sin Gmail,
workers ni Beat. No hay data migration, dual-write, traffic split ni rollback productivo.

- Baseline preservado: tag `pre-service-split-a030c7d` sobre commit `a030c7d`.
- Toda fase actualiza docs/trazabilidad y conserva defaults dry-run/kill switches/SHADOW.
- Sólo migraciones forward; no editar ni renumerar migraciones Overture.
- Dominio permanece en servicios; API serializers/views y tasks nunca asignan estado directamente.
- Tests bloquean red y usan provider fakes.
- Backend es una sola réplica/container con Uvicorn, worker general, maintenance y Beat
  supervisados; migrations corren antes del runtime.
- Sólo frontend es público; backend/PostgreSQL/Redis/S3 permanecen privados.

## Fase 0 — Baseline, decisión y documentación

**Objetivo:** congelar el servicio previo y sustituir decisiones HTMX/MFA/data-backfill.

- Crear tag/branch; baseline `make check`, fake E2E y secret scan.
- Actualizar los documentos gobernantes, deployment, operations y hardening audit.
- Fijar frontend Next/React/TS/AntD, backend Django/DRF/Uvicorn, sesión/CSRF, no MFA, DB vacía,
  Redis único y storage S3-compatible.
- Patch de seguridad Django 5.2.17 y dependency policy con lockfiles/audits.

**Terminado:** baseline recuperable, docs no contradictorias y hardening v2 marcado pendiente.

## Fase 1 — Monorepo y scaffold reproducible

**Objetivo:** separar deployables sin cambiar dominio.

- Mover Python mecánicamente a `backend/`; conservar packages/migration labels e historia.
- Crear `frontend/` Next App Router strict TS/AntD y `infra/docker-compose.yml`.
- Root Makefile: backend/frontend/E2E/security/full gates.
- Lockfiles exactos, imágenes non-root digest-pinned y builds frozen.

**Terminado:** backend previo y tests pasan desde nuevo path; frontend shell compila; Compose usa
volúmenes/ports v2 aislados.

## Fase 2 — Backend unificado e infraestructura privada

**Objetivo:** una frontera backend con todos los procesos y servicios de datos separados.

- Uvicorn ASGI; Supervisor para API, general worker, maintenance concurrency uno y Beat.
- Entrypoint valida entorno, espera DB/Redis, ejecuta `migrate_safe` con
  `MIGRATION_DATABASE_URL`, elimina privilegio DDL y arranca Supervisor.
- Redis db 0 broker, db 1 cache/throttling, db 2 results/health; PostgreSQL sigue source of truth.
- `PrivateObjectStorage`: MinIO local y Railway Bucket; backend-only streaming upload/download.
- Health agregado y graceful shutdown/recovery.

**Terminado:** un container procesa API/colas/schedule/migrations sin duplicar efectos; sólo backend
posee credenciales.

## Fase 3 — DRF, sesión/CSRF y autorización

**Objetivo:** contrato `/api/v1` seguro y browser same-origin.

- DRF/OpenAPI, envelopes, Problem Details, pagination 25/max100, correlation ID, ETag/If-Match,
  `Idempotency-Key` y respuestas async con `BackgroundJob`.
- CSRF-in-session, cookie `__Host-contact_outreach_session` 12 h, login/logout/activation/session
  y password reauth 10 min.
- Remover django-otp/middleware/forms/routes/templates/RecoveryCode mediante migración forward.
- Portar capabilities ADMIN/VENDEDOR a permissions, object scoping y service checks.
- Proxy token interno, forwarded-header normalization, exact origin, no CORS, no-store, throttles.
- OpenAPI genera client/types TypeScript; CI detecta drift.

**Terminado:** matriz anonymous/admin/vendor y pruebas de CSRF/session/proxy/reauth pasan.

## Fase 4 — API completa por slices

**Objetivo:** exponer cada workflow existente sin mover lógica a HTTP.

1. Workspace, perfil, templates, categories/rules/zones e integration status no secreto.
2. Contactos, emails, MX jobs, restrictions, timeline, tasks y communication plans.
3. Catálogos S3 upload/download e integridad MIME.
4. Overture releases/partitions/sync/progress en maintenance.
5. Campaign draft/discovery/audience/approval/actions y outbound review.
6. Prospects, exports neutralizados, dashboard metrics.
7. Gmail OAuth/sync/conversations/manual replies/reconciliation.
8. Knowledge/context/decisions/SHADOW-LIVE/redirect/notifications/scheduled contacts.
9. Audit, jobs/retry y degraded health.

Cada acción larga crea state/job/audit en una transacción y publica `on_commit`. Fake inbound nunca
existe en producción. Secrets estáticos sólo entorno; Gmail refresh token cifrado es la excepción.

**Terminado:** todos los workflows tienen path REST documentado y tests de success/failure/
permission/idempotency/provider error.

## Fase 5 — Frontend responsive y accesible

**Objetivo:** reemplazar UI Django por Next/React sin convertir frontend en autoridad.

- Proxy Node streaming `/api/v1/*`, sólo server env, strip headers y neutral 502.
- Auth/session provider con CSRF sólo memoria y handling 401/403/409/412/429/5xx.
- Rutas: login/activate, dashboard, contacts/attention, campaigns, outbound, responses, catalogs,
  automation, settings profile/templates/prompts/integrations/categories/overture/users, audit/jobs.
- ADMIN muta; VENDEDOR sólo ve proyecciones permitidas.
- Ant Design responsive desde 320 px: drawer, cards/tables, full-screen mobile forms, touch 44 px,
  focus/keyboard/screen-reader/color-independent states.
- Plain text email default; HTML sólo en sandbox; CSP nonce y no cache/service worker.

**Terminado:** feature parity desktop/mobile y WCAG 2.2 AA sobre componentes propios.

## Fase 6 — Contract de presentación vieja

**Objetivo:** backend v2 sólo API tras probar paridad.

- Mover validación reusable de forms a serializers/domain antes de retirar forms/views.
- Remover routes/templates/static HTMX y fake UI del runtime nuevo.
- Mantener management commands, migrations, provider fakes y neutral JSON errors.

**Terminado:** no template participa de behavior v2 y el image viejo sigue independiente.

## Fase 7 — Hardening y release

**Objetivo:** revalidar seguridad/operación completa.

- Backend/frontend checks, OpenAPI drift, network-blocked fake E2E, Playwright desktop/mobile.
- Dependency/image/SBOM/secret scans; CSRF/CORS/cookie/proxy/IDOR/XSS/CSP/SSRF/OAuth/PDF/CSV.
- Gmail ambiguity/duplicate, Beat/restart/Redis loss, Overture import bajo carga.
- Fresh/upgrade migrations futuras, backup/restore DB+Bucket+key y Railway staging.
- Actualizar HARDENING_AUDIT con evidencia nueva.

**Terminado:** cero high/critical sin resolver, restore probado, frontend saludable durante import y
todos los kill switches/rechecks aprobados.

## Fase 8 — Cutover fresco y fix-forward

**Objetivo:** cambiar arquitectura de una vez sin dos emisores Gmail.

- Inicializar DB/Redis/Bucket, bootstrap admin, profile/templates/PDFs/Overture/secrets desde cero.
- Backup viejo; activar kill switches/dry-run, detener workers/Beat, revocar Gmail y quitar dominio.
- Mover dominio sólo a frontend v2; verificar TLS/cookies/CSRF/CSP/proxy y conectar Gmail nuevo.
- Ejecutar dry-run; live sólo por checklist. Auto/relationship permanecen independientes.
- Mantener viejo privado/reference-only. Incidentes v2 cierran effects y se corrigen forward.

**Terminado:** sólo frontend público; backend/data privados; viejo incapaz de efectos; docs/runbooks
coinciden con Railway real.

## Trazabilidad requisito -> fase

| Requisito | Fases | Evidencia |
| --- | --- | --- |
| FR-01 Workspace/roles/auth | 0, 3, 5, 7 | sesión/CSRF/lockout/reauth/permission matrix; MFA diferido |
| FR-02 Contactos/restricciones | 4, 5, 7 | API/UI y rechecks de dominio |
| FR-03/04 Geografía/Overture | 2, 4, 5, 7 | maintenance, partitions y mapa/lista |
| FR-05–10 Campaign/Gmail | 2, 4, 5, 7 | durable jobs, delivery/reconciliation y E2E |
| FR-11–15 IA/automation/relaciones | 4, 5, 7 | manifests/policy/tasks/limits/kill switches |
| FR-16/17 Contact UI/métricas | 4, 5, 7 | REST projections y responsive frontend |
| FR-18 Internet readiness | 2, 3, 5, 7, 8 | only-public frontend, private networking y staging |
| FR-19 Estado/idempotencia/audit | 2–7 | services/constraints/jobs/OpenAPI/audit |
| FR-20 UX/accesibilidad | 5, 7 | Playwright/mobile/axe/keyboard |
| OPS-01/02 Stack/operación | 0–8 | monorepo, Supervisor, Compose, Railway y runbooks |
| DM-01 Data | 2–4, 7 | migrations forward, DB nueva y private S3 |
| QA-01 Tests | 0–8 | backend/frontend/security/E2E gates |

## Bloqueos de go-live

1. Backend/data accesibles públicamente o proxy spoofable.
2. Bypass de roles, sesión, CSRF, reauth, restrictions o last-admin.
3. Duplicate Gmail effect, missing reconciliation, same-day/reminder/idempotency failure.
4. PDF parcial/tamper, secret/body leak o unsafe provider input.
5. Kill switch/rate/task/policy bypass.
6. Supervisor/Beat duplicate, migration parcial o restore no probado.
7. Frontend sin paridad mobile/accesible o schema/client drift.
8. Old y new Gmail/workers activos simultáneamente.

Ningún bloqueo se acepta silenciosamente. La revisión legal/deliverability sigue externa antes de
activar outreach live.
