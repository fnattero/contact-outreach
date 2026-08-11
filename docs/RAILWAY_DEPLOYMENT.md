# Despliegue seguro en Railway

Este procedimiento prepara un entorno de staging privado y luego un entorno público controlado.
Railway ejecuta el Dockerfile del repositorio. El mismo contenedor se usa como web, worker,
maintenance worker, beat y migración; cada servicio debe tener un comando distinto.

## 1. Servicios del proyecto

Crear en el mismo proyecto y entorno:

| Servicio | Comando del contenedor | Público | Volumen `/app/private` |
| --- | --- | --- | --- |
| `web` | `web` | Sí, sólo el dominio de la aplicación | lectura/escritura |
| `worker` | `worker` | No | sólo lectura |
| `maintenance` | `maintenance-worker` | No | sólo lectura |
| `beat` | `beat` | No | no necesita |
| `migrate` | `migrate` o pre-deploy `python src/manage.py migrate_safe` | No | no necesita |

Agregar PostgreSQL y Redis como servicios internos. No crear TCP proxies públicos para ellos.
Referenciar sus variables con Railway, por ejemplo:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
```

Railway recomienda estas referencias entre servicios y sus redes privadas para evitar credenciales
duplicadas y exposición pública: <https://docs.railway.com/guides/docker-compose>.

## 2. Variables obligatorias del servicio web

Configurar las mismas variables sensibles en `web`, `worker`, `maintenance`, `beat` y `migrate`.
Usar Railway Variables/Secrets; nunca subirlas al repositorio.

```text
APP_ENV=production
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<secreto aleatorio largo, distinto por entorno>
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
FIELD_ENCRYPTION_KEY=<secreto aleatorio largo, respaldado por separado>

DJANGO_ALLOWED_HOSTS=<dominio-railway-exacto>,<dominio-personalizado-exacto>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<dominio-publico-exacto>
PUBLIC_BASE_URL=https://<dominio-publico-exacto>
DJANGO_SECURE_COOKIES=true
DJANGO_SSL_REDIRECT=true
DJANGO_PROXY_HTTPS=true
DJANGO_RAILWAY_PROXY=true

PRIVATE_STORAGE_ROOT=/app/private
RUN_MIGRATIONS_ON_STARTUP=false
RUN_OWNER_BOOTSTRAP_ON_STARTUP=false

SEND_MODE=dry-run
SEND_KILL_SWITCH=true
AUTO_REPLY_KILL_SWITCH=true
RELATIONSHIP_KILL_SWITCH=true
WEBSITE_FETCHER=fake
LLM_PROVIDER=fake
EMBEDDING_PROVIDER=fake
GMAIL_PROVIDER=fake
```

El contenedor toma automáticamente el `PORT` que Railway inyecta y enlaza en `0.0.0.0`.
No fijar `PORT=8000` en producción. Si se usa el dominio Railway, `RAILWAY_PUBLIC_DOMAIN` se
incorpora a los hosts permitidos cuando la plataforma lo proporciona; verificarlo en la vista de
variables. El health check de Railway usa `healthcheck.railway.app`, que el modo
`DJANGO_RAILWAY_PROXY=true` agrega como host exacto.

`DJANGO_RAILWAY_PROXY=true` sólo debe usarse en el servicio web público de Railway. El middleware
acepta HTTPS reenviado únicamente si llega el marcador `X-Railway-Request-Id` junto con
`X-Forwarded-Proto=https`; no acepta por esa vía el host ni la IP del cliente. Si se despliega detrás
de otro proxy, usar `DJANGO_TRUSTED_PROXY_IPS` con CIDR exactos y mantener el modo Railway apagado.

Comenzar con el HSTS de 300 segundos que trae la aplicación. Aumentarlo gradualmente sólo después
de comprobar el dominio, certificados y todos sus subdominios. No habilitar
`DJANGO_HSTS_INCLUDE_SUBDOMAINS` ni `DJANGO_HSTS_PRELOAD` sin controlar cada subdominio HTTPS; por
esa razón `check --deploy` puede informar esas dos advertencias durante el rollout inicial.

## 3. Volumen privado

Adjuntar un volumen al servicio `web` en `/app/private` y el mismo volumen a `worker` y
`maintenance` en `/app/private`. El web necesita escritura; los workers sólo lectura. No servir ese
directorio como static/media y no montar el volumen en beat.

Probar que el volumen sobrevive a un redeploy y definir retención/backup antes de importar PDFs o
catálogos. El volumen no reemplaza el backup de PostgreSQL ni el backup separado de
`FIELD_ENCRYPTION_KEY`.

## 4. Migración y propietario inicial

Usar el pre-deploy command de Railway:

```text
python src/manage.py migrate_safe
```

Esto evita que varias réplicas web intenten cambiar el esquema durante el arranque. Si Railway no
ofrece un pre-deploy command para el servicio elegido, desplegar un servicio privado `migrate` con
comando `migrate`; debe completarse antes de escalar `web`.

Para el primer administrador, ejecutar una vez desde un shell/one-off job privado:

```text
python src/manage.py bootstrap_owner
```

Definir `OWNER_USERNAME`, `OWNER_EMAIL` y `OWNER_PASSWORD` sólo para esa ejecución; después
eliminarlos o rotar inmediatamente `OWNER_PASSWORD`. No habilitar el bootstrap automático en web.

## 5. Orden de validación

1. Desplegar staging sin dominio público o con acceso restringido.
2. Confirmar `/health/live/` y `/health/ready/`; el segundo debe comprobar PostgreSQL y Redis.
3. Ejecutar `python src/manage.py check --deploy` con las variables reales de staging.
4. Verificar login, MFA, CSRF, cookies, redirección HTTPS, descarga privada y logs redactados.
5. Ejecutar `make smoke-worker` y comprobar que los cuatro procesos Celery estén activos.
6. Crear un backup/restauración de prueba con `SEND_KILL_SWITCH=true`.
7. Conectar Gmail sólo con los scopes aprobados y mantener `GMAIL_PROVIDER=fake` hasta completar
   la prueba OAuth controlada.
8. Para el primer rollout, mantener dry-run y todos los kill switches. Habilitar LIVE únicamente
   después de revisar manualmente una campaña pequeña y su reconciliación.

Railway health checks sólo controlan la disponibilidad durante el despliegue; agregar monitoreo
externo, alertas de logs y una política de backup. Referencias oficiales: [health checks](https://docs.railway.com/deployments/healthchecks),
[PostgreSQL](https://docs.railway.com/databases/postgresql) y [red privada](https://docs.railway.com/private-networking).
