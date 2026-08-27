# Contact Outreach

Aplicación privada de una empresa para outreach B2B, seguimiento de contactos y automatización
controlada. La arquitectura v2 separa una interfaz Next.js pública de un backend Django privado y
mantiene PostgreSQL, Redis y almacenamiento S3-compatible como servicios de infraestructura
independientes.

La migración se desarrolla sin modificar el despliegue anterior. La base v2 empieza vacía, todos
los proveedores siguen fake y los tres kill switches permanecen activos. Las reglas de selección,
supresión, aprobación, idempotencia, reconciliación Gmail y automatización documentadas continúan
siendo invariantes obligatorias.

## Arquitectura

```text
Navegador -> frontend Next.js :3000 -> /api/v1 proxy -> backend privado :8000
                                                       |-> PostgreSQL
                                                       |-> Redis
                                                       |-> MinIO/Railway Bucket
                                                       |-> Gmail/Overture/LLM
```

El contenedor backend supervisa Uvicorn, el worker general, el worker de mantenimiento y Celery
Beat. Los workers son procesos internos del mismo backend y acceden directamente a PostgreSQL a
través de los mismos servicios de dominio que la API. Sólo el frontend recibe un dominio público
en producción.

## Estructura

```text
backend/   Django, DRF, workers, migraciones y tests Python
frontend/  Next.js, React, TypeScript y Ant Design
infra/     Docker Compose y servicios locales
docs/      especificaciones, seguridad, operaciones y plan de implementación
```

## Desarrollo local

Requisitos: Docker Engine con Compose, Git y `make`. Python 3.12+ y Node.js 24 LTS son opcionales
si se desea ejecutar los checks fuera de Docker.

```bash
cp .env.example .env
make build
make up
```

El navegador usa solamente <http://127.0.0.1:3000>. El backend de diagnóstico queda en
<http://127.0.0.1:8001>, MinIO en `127.0.0.1:9000` y su consola en
<http://127.0.0.1:9001>. PostgreSQL y Redis no necesitan publicarse al host.

Generar valores locales independientes antes del primer arranque:

```bash
openssl rand -hex 32  # DJANGO_SECRET_KEY
openssl rand -hex 32  # FIELD_ENCRYPTION_KEY
openssl rand -hex 32  # INTERNAL_PROXY_TOKEN
openssl rand -hex 32  # POSTGRES_PASSWORD
```

No versionar `.env`, credenciales, catálogos, exportaciones, backups ni datos personales.

Comandos principales:

```bash
make logs
make backend-check
make frontend-check
make test-e2e
make security-check
make check
```

`make backend-check` ejecuta Ruff, formato, mypy estricto, pytest sin red, chequeo de migraciones
y checks de Django. `make frontend-check` ejecuta ESLint, TypeScript estricto, Vitest y un build de
producción. Los tests nunca llaman HTTP, DNS, Gmail, Overture ni LLM reales.

## Seguridad predeterminada

- No hay registro público ni recuperación pública de contraseña.
- La v2 usa sesiones opacas, cookie `__Host-`, CSRF en sesión y un único origen.
- MFA está diferido en v2; no se afirma protección equivalente contra toma de cuentas.
- El frontend no recibe secretos ni guarda tokens en almacenamiento del navegador.
- El backend, PostgreSQL, Redis y el Bucket no tienen exposición pública.
- `SEND_MODE=dry-run` y todos los kill switches quedan cerrados hasta una habilitación explícita.
- Los PDFs son privados, se verifican por tamaño, tipo y SHA-256 antes de cada efecto Gmail.

## Despliegue y operación

La guía de Railway está en [docs/RAILWAY_DEPLOYMENT.md](docs/RAILWAY_DEPLOYMENT.md), los incidentes
y restores en [docs/OPERATIONS.md](docs/OPERATIONS.md), y la secuencia completa de migración en
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

No se copian datos del sistema anterior. Durante el cutover se deshabilitan sus workers, Beat y
Gmail, se retira su dominio y se conecta Gmail sólo a la instalación nueva. Después del corte se
repara hacia adelante; el despliegue anterior queda privado únicamente para consulta histórica.

## Antes de habilitar efectos reales

Como mínimo deben pasar `make check`, `make test-e2e`, auditorías de dependencias/imágenes/secretos,
la matriz de seguridad, una prueba de backup/restore y una campaña completa en dry-run. La conexión
Gmail, el envío live, las respuestas automáticas y la automatización de relaciones se habilitan en
revisiones independientes. Ningún paso de arquitectura elimina esos controles de producto.
