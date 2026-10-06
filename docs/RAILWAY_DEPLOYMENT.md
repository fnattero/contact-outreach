# Despliegue seguro en Railway — frontend y backend separados

> **Nota.** Este documento es una guía de despliegue, no la descripción de un entorno en
> funcionamiento. Actualmente no hay ninguna instancia pública del sistema corriendo: los
> pasos de abajo describen cómo se desplegaría, y las variables, dominios y servicios que
> menciona son ejemplos a completar.

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
CLIENT_IP_HEADER=x-real-ip
HSTS_MAX_AGE_SECONDS=31536000
```

Ninguna variable de DB, Redis, S3, Gmail, LLM ni cifrado entra al servicio frontend. El token y URL
son server-only y nunca usan prefijo `NEXT_PUBLIC_`.

En producción el proxy responde 503 sin llamar al backend si falta `BACKEND_INTERNAL_URL` o
`INTERNAL_PROXY_TOKEN`, o si `PUBLIC_APP_ORIGIN` no es un origen exacto HTTPS. `CLIENT_IP_HEADER`
nombra el header donde el borde de Railway entrega la IP del navegador (`x-real-ip`); sin él, el
bloqueo de inicio de sesión y los límites de tasa se compartirían entre todos los usuarios.
`HSTS_MAX_AGE_SECONDS` se puede bajar (por ejemplo a 300) en el primer despliegue y subir después.

## 3. Variables backend

```text
APP_ENV=production
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<secreto aleatorio de al menos 50 caracteres>
FIELD_ENCRYPTION_KEY=<secreto aleatorio de al menos 32 caracteres, respaldado aparte>
INTERNAL_PROXY_TOKEN=<mismo secreto server-side del frontend, al menos 32 caracteres>

DATABASE_URL=<rol runtime en PostgreSQL privado>
MIGRATION_DATABASE_URL=<rol DDL usado sólo antes de Supervisor>
REDIS_URL=<Redis privado db 0; cache (db 1) y resultados (db 2) se derivan de esta URL>

S3_ENDPOINT_URL=<endpoint privado Railway Bucket>
S3_BUCKET_NAME=<bucket privado>
S3_ACCESS_KEY_ID=<secret>
S3_SECRET_ACCESS_KEY=<secret>
S3_REGION_NAME=<region>
S3_ADDRESSING_STYLE=path

DJANGO_ALLOWED_HOSTS=<dominio-publico-exacto>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<dominio-publico-exacto>
PUBLIC_BASE_URL=https://<dominio-publico-exacto>
DJANGO_SECURE_COOKIES=true
DJANGO_SSL_REDIRECT=true
DJANGO_PROXY_HTTPS=true
DJANGO_RAILWAY_PROXY=true

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
`INTERNAL_PROXY_TOKEN`; backend no confía headers forwarded de un request sin token. El backend
sólo acepta el host público (el proxy lo presenta como `Host`), no su nombre privado. Los endpoints
`/api/v1/health/live/` y `/api/v1/health/ready/` están exentos del token y del redireccionamiento a
HTTPS para que los health checks de Railway y del contenedor funcionen por HTTP privado.

Con `APP_ENV=production` el arranque falla si falta o es débil `DJANGO_SECRET_KEY`,
`FIELD_ENCRYPTION_KEY` o `INTERNAL_PROXY_TOKEN`, si falta `REDIS_URL`, o si `APP_ENV` no es uno de
`development`, `test`, `production`. El HSTS del backend es de un año por defecto
(`DJANGO_HSTS_SECONDS`). No fijar el `PORT` público: cada container consume el valor Railway y liga
`0.0.0.0` internamente.

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

Eliminar inmediatamente `OWNER_*`. El entrypoint se niega a arrancar con `APP_ENV=production` y
`RUN_OWNER_BOOTSTRAP_ON_STARTUP=true`, porque el bootstrap reescribe la contraseña del owner en
cada reinicio; `.env.example` lo deja en `true` sólo para el entorno local.

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

## 6. Primer despliegue

Antes de asignar el dominio público: backup de la DB verificado, `SEND_MODE=dry-run`, los tres kill
switches activos y los providers fake. Asignar el custom domain sólo al frontend, conectar Gmail
con el redirect exacto de ese dominio y ejecutar una campaña dry-run completa. Para el primer
despliegue conviene `HSTS_MAX_AGE_SECONDS=300` y `DJANGO_HSTS_SECONDS=300`; subirlos a un año una vez
validado el dominio. Una incidencia en producción activa los kill switches y se corrige hacia
adelante.

Railway health checks no sustituyen alertas, backup ni restore drill. Mantener 30 días de backup DB
cuando el plan lo soporte y respaldar `FIELD_ENCRYPTION_KEY` por canal cifrado separado.
