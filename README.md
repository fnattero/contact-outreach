# Contact Outreach

Aplicación local Django para outreach B2B de un único propietario. Esta primera fase deja la
infraestructura ejecutable, autenticación, dashboard, health checks, Celery y proveedores fake.
No incluye integraciones reales ni permite seleccionarlas.

## Requisitos

- Docker Engine con Docker Compose.
- Opcional para validar fuera de Docker: Python 3.12+ y `make`.

## Iniciar el entorno

1. Crear la configuración local:

   ```bash
   cp .env.example .env
   ```

2. Reemplazar en `.env` `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD` y `OWNER_PASSWORD`. No usar
   credenciales reales de Gmail, Outscraper o IA; esta fase sólo acepta proveedores `fake`.

3. Construir e iniciar todos los servicios:

   ```bash
   make build
   make up
   ```

La web queda disponible en <http://127.0.0.1:8000/>. El arranque aplica las migraciones built-in
de Django bajo un advisory lock de PostgreSQL y crea o rota el propietario configurado sin imprimir
la contraseña. PostgreSQL y Redis no publican puertos al host.

Para seguir los logs:

```bash
make logs
```

## Comprobar el entorno

- Liveness: <http://127.0.0.1:8000/health/live/>
- Readiness de PostgreSQL y Redis: <http://127.0.0.1:8000/health/ready/>
- Procesamiento real de una tarea por el worker:

  ```bash
  make smoke-worker
  ```

El dashboard requiere iniciar sesión con `OWNER_USERNAME` y `OWNER_PASSWORD` de `.env`. No existe
registro público. El logout es una acción POST protegida por CSRF.

## Datos de demostración

Los datos demo están separados del arranque normal y se rechazan fuera de `APP_ENV=development`.
Para crear el propietario demo, definir `DEMO_OWNER_PASSWORD` en `.env` y ejecutar:

```bash
make demo
```

El comando requiere además la habilitación explícita que agrega el target `demo`; producción nunca
lo ejecuta automáticamente. En esta fase sin modelos de dominio, el propietario es el único dato demo.

## Detener y limpiar

Detener los contenedores preservando base, Redis y almacenamiento privado:

```bash
make down
```

Para probar migraciones desde una base completamente vacía, eliminar también los volúmenes locales.
Esto borra todos los datos del entorno:

```bash
docker compose down --volumes
make up
```

## Quality gates

Crear un entorno Python local y ejecutar todos los checks:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
make check
make test-e2e
```

También están disponibles por separado `make lint`, `make typecheck` y `make test`.
`make check` valida Ruff, formato, mypy, pytest, migraciones pendientes y checks de Django. Pytest
bloquea sockets y usa exclusivamente los proveedores fake.

## Controles seguros por defecto

- `HOST_BIND=127.0.0.1`
- `SEND_MODE=dry-run`
- `SEND_KILL_SWITCH=true`
- extractor, web, IA y Gmail en `fake`
- sin API keys, OAuth, SMTP, scraping ni llamadas de red de proveedores
