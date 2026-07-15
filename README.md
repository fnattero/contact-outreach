# Contact Outreach

Aplicación local Django para outreach B2B de un único propietario. El incremento actual incluye
autenticación, configuración comercial, catálogos privados, campañas, supresiones, auditoría,
extracción durable, enriquecimiento web seguro y generación personalizada. Los proveedores mock
son el default sin red; Outscraper, el fetch HTTP y los proveedores IA quedan aislados por
contratos internos y son opt-in.

## Requisitos

- Docker Engine con Docker Compose.
- Opcional para validar fuera de Docker: Python 3.12+ y `make`.

## Iniciar el entorno

1. Crear la configuración local:

   ```bash
   cp .env.example .env
   ```

2. Reemplazar en `.env` `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD` y `OWNER_PASSWORD`. Gmail e IA
   siguen fake; Outscraper sólo se habilita explícitamente como se describe más abajo.

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
lo ejecuta automáticamente. Los seeds de las migraciones crean 23 rubros y los 48 barrios oficiales
de CABA; el comando demo continúa creando únicamente el propietario.

## Configurar una campaña

Después de iniciar sesión:

1. Completar el perfil comercial.
2. Revisar o editar rubros y zonas.
3. Cargar un PDF válido de hasta 15 MiB en Catálogos.
4. Crear una campaña, seleccionando al menos un rubro, una zona y una versión de catálogo.
5. Ajustar objetivo, máximo crudo, costo, límite diario, intervalo, horario, zona horaria y umbral.
6. Iniciar el borrador para congelar perfil, configuración, selecciones, catálogo y consultas.

El objetivo inicial es 300. `SEND_MODE` y `SEND_KILL_SWITCH` se muestran en el dashboard pero sólo
se configuran por entorno. Con los defaults, cualquier campaña es dry-run, el kill switch está
activo y web/IA usan fakes sin sockets. La extracción mock usa también un resolver MX
determinístico; la extracción Outscraper usa DNS MX real desde el worker.

Cada prospecto con email obtiene un snapshot de la home y hasta tres páginas internas. Luego una
única llamada lógica evalúa relevancia y redacta JSON estructurado. Sólo un resultado enteramente
válido y sobre el umbral crea un mensaje `PREPARED`; no existe fallback de copy. Desde el detalle
de campaña se puede regenerar un candidato preparado. Esa acción no lo aprueba ni lo envía.

## Habilitar enriquecimiento web e IA

El fetch HTTP real se activa globalmente con `WEBSITE_FETCHER=http`. Sólo acepta HTTP/HTTPS por
80/443, valida todas las respuestas DNS y cada redirect, conecta a la IP pública validada y aplica
límites de páginas, bytes, redirects y tiempo. El contenido resultante siempre se trata como dato
no confiable.

Al crear una campaña se puede elegir `Ollama` u `OpenAI compatible`, indicando URL base y modelo.
Los valores externos se configuran sólo por entorno:

```bash
# Ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434

# API compatible con /v1/chat/completions
OPENAI_COMPATIBLE_BASE_URL=https://proveedor.example
LLM_API_KEY=replace-with-your-key
```

La URL/modelo se congelan en la campaña. `LLM_API_KEY` nunca se persiste. Ambos adaptadores exigen
salida JSON schema y vuelven a validarla localmente; los tests sustituyen sus transportes y
mantienen todos los sockets bloqueados.

## Habilitar Outscraper

Revisar primero el precio vigente y ajustar la reserva conservadora. Luego definir sólo por entorno:

```bash
EXTRACTOR_PROVIDER=outscraper
OUTSCRAPER_API_KEY=replace-with-your-key
OUTSCRAPER_MAX_COST_PER_RESULT=0.010000
OUTSCRAPER_BATCH_SIZE=20
OUTSCRAPER_POLL_SECONDS=30
```

Al crear la campaña, seleccionar `Outscraper`. Iniciar crea un `SearchRun` durable; el worker envía
la consulta asíncrona con enrichment de contactos, guarda el ID y el JSON crudo, y Beat reanuda el
polling después de reinicios. Los errores y el uso/costo estimado aparecen en el detalle de campaña.
La API key no se guarda en base, no se incluye en la URL y no aparece en auditoría. No existe
fallback de scraping directo.

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
- catálogos fuera de static/media público, bajo `PRIVATE_STORAGE_ROOT`
- sin API keys, OAuth, SMTP, scraping ni llamadas de red de proveedores
