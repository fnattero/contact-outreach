# Despliegue seguro en Railway — frontend y backend separados

Railway aloja dos deployables de aplicación y tres servicios de infraestructura. Sólo el frontend
tiene dominio público. Backend, PostgreSQL, Redis y Bucket usan private networking; no se crean TCP
proxies ni dominios públicos para ellos. La instalación v2 usa DB/Bucket nuevos y no copia datos.

## 1. Servicios

| Servicio | Imagen/comando | Público | Credenciales de datos |
| --- | --- | --- | --- |
| `frontend` | `frontend/Dockerfile` | Sí, único custom domain | Sólo backend URL y proxy token server-side |
| `backend` | `backend/Dockerfile` / `backend` | No | DB runtime/migration, Redis, S3, providers |
| `postgres` | PostgreSQL 17 patched | No | Sólo backend |
| `redis` | Redis 7.4 patched | No | Sólo backend |
| `bucket` | Railway private Bucket | No | Sólo backend |

El container backend ejecuta primero `migrate_safe` y después Supervisor con Uvicorn, worker
general, maintenance concurrency uno y Beat. Se configura exactamente una réplica. API/workers no
se despliegan por separado y no se llaman por HTTP entre sí.

## 2. Variables frontend

```text
NODE_ENV=production
PUBLIC_APP_ORIGIN=https://<dominio-publico-exacto>
BACKEND_INTERNAL_URL=http://backend.railway.internal:<puerto-privado>
INTERNAL_PROXY_TOKEN=<secreto aleatorio distinto por entorno>
```

Ninguna variable de DB, Redis, S3, Gmail, LLM ni cifrado entra al servicio frontend. El token y URL
son server-only y nunca usan prefijo `NEXT_PUBLIC_`.

## 3. Variables backend

```text
APP_ENV=production
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<secreto aleatorio largo>
LOGIN_THROTTLE_HMAC_KEY=<secreto aleatorio independiente>
FIELD_ENCRYPTION_KEY=<secreto aleatorio respaldado aparte>
INTERNAL_PROXY_TOKEN=<mismo secreto server-side del frontend>

DATABASE_URL=<rol runtime en PostgreSQL privado>
MIGRATION_DATABASE_URL=<rol DDL usado sólo antes de Supervisor>
REDIS_BROKER_URL=<Redis privado db 0>
REDIS_CACHE_URL=<Redis privado db 1>
REDIS_RESULT_URL=<Redis privado db 2>

S3_ENDPOINT_URL=<endpoint privado Railway Bucket>
S3_BUCKET_NAME=<bucket privado>
S3_ACCESS_KEY_ID=<secret>
S3_SECRET_ACCESS_KEY=<secret>
S3_REGION=<region>
S3_ADDRESSING_STYLE=path

DJANGO_ALLOWED_HOSTS=<backend-internal-host>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<dominio-publico-exacto>
PUBLIC_BASE_URL=https://<dominio-publico-exacto>
DJANGO_SECURE_COOKIES=true
DJANGO_SSL_REDIRECT=false
DJANGO_PROXY_HTTPS=true

SEND_MODE=dry-run
SEND_KILL_SWITCH=true
AUTO_REPLY_KILL_SWITCH=true
RELATIONSHIP_KILL_SWITCH=true
WEBSITE_FETCHER=fake
LLM_PROVIDER=fake
EMBEDDING_PROVIDER=fake
GMAIL_PROVIDER=fake
```

Railway termina TLS en frontend. El proxy Next normaliza metadata y prueba identidad con
`INTERNAL_PROXY_TOKEN`; backend no confía headers forwarded de un request sin token. No fijar el
`PORT` público: cada container consume el valor Railway y liga `0.0.0.0` internamente.

## 4. Migraciones y primer administrador

El entrypoint backend:

1. valida variables;
2. espera DB/Redis con timeout;
3. usa `MIGRATION_DATABASE_URL` para `python src/manage.py migrate_safe`;
4. elimina esa variable del entorno hijo;
5. inicia Supervisor con `DATABASE_URL` runtime.

Un fallo de migración impide servir API/schema parcial. `MIGRATION_DATABASE_URL` no llega a
Uvicorn/workers/Beat.

El primer admin se crea una sola vez desde un shell/job privado:

```text
OWNER_USERNAME=<valor temporal>
OWNER_EMAIL=<valor temporal>
OWNER_PASSWORD=<valor temporal fuerte>
python src/manage.py bootstrap_owner
```

Eliminar inmediatamente `OWNER_*`. Nunca habilitar bootstrap automático en producción.

## 5. Staging y validación

1. Crear frontend/backend/PostgreSQL/Redis/Bucket nuevos.
2. Mantener backend/data sin dominio/TCP público.
3. Desplegar con providers fake, dry-run y kill switches activos.
4. Verificar frontend `/healthz` y backend private `/api/v1/health/live|ready`.
5. Ejecutar backend/frontend/security checks y fake E2E desktop/mobile.
6. Confirmar cookie `__Host-`, sesión 12 h, CSRF-in-session, exact origin, CSP y no-store.
7. Confirmar que spoofed forwarded/internal headers fallan.
8. Probar upload/download S3 por API; el browser nunca recibe una URL pública.
9. Ejecutar smoke de ambos workers/Beat y recovery tras reinicio Redis/backend.
10. Ejecutar backup/restore con todos los kill switches activos.
11. Conectar Gmail sólo después de mover el dominio y actualizar el redirect exacto.

## 6. Cutover sin rollback productivo

Antes de mover dominio: backup viejo verificado, kill switches viejos activos, `SEND_MODE=dry-run`,
workers/Beat viejos detenidos y refresh token Gmail viejo revocado. Mover el custom domain sólo al
frontend v2, conectar Gmail nuevo y ejecutar una campaña dry-run. El servicio viejo queda privado,
sin procesos de efectos y con DB/PDF propios. Una incidencia v2 activa kill switches y se corrige
forward; nunca se reactivan Gmail/workers viejos.

Railway health checks no sustituyen alertas, backup ni restore drill. Mantener 30 días de backup DB
cuando el plan lo soporte y respaldar `FIELD_ENCRYPTION_KEY` por canal cifrado separado.
