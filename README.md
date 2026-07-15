# Contact Outreach

Aplicación local Django para outreach B2B de un único propietario. El incremento actual incluye
autenticación, configuración comercial, catálogos privados, campañas, supresiones, auditoría,
extracción durable, enriquecimiento web seguro, generación personalizada, OAuth Gmail y entrega
controlada de primeros mensajes. Los proveedores mock
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

2. Reemplazar en `.env` `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `OWNER_PASSWORD` y
   `FIELD_ENCRYPTION_KEY`. Gmail e IA siguen fake; las integraciones de red sólo se habilitan
   explícitamente como se describe más abajo.

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
válido y sobre el umbral crea un mensaje `PREPARED`; no existe fallback de copy. Beat reconstruye
la cola desde PostgreSQL: en dry-run genera y hashea el MIME sin llamar Gmail; en live sólo entrega
si pasan modo, kill switch, conexión probada, cuota, intervalo, horario, catálogo, supresión y
ledger global. Desde el detalle se puede regenerar únicamente un candidato que todavía no entró
en entrega.

## Conectar Gmail

El proveedor fake permite probar todo el flujo sin red desde la pantalla Gmail. Para OAuth real:

1. Crear credenciales OAuth de aplicación web en Google Cloud.
2. Registrar exactamente `GMAIL_OAUTH_REDIRECT_URI` (por defecto,
   `http://127.0.0.1:8000/gmail/oauth/callback/`).
3. Configurar fuera del repositorio:

   ```bash
   GMAIL_PROVIDER=api
   GMAIL_OAUTH_CLIENT_ID=replace-with-client-id
   GMAIL_OAUTH_CLIENT_SECRET=replace-with-client-secret
   GMAIL_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/gmail/oauth/callback/
   FIELD_ENCRYPTION_KEY=replace-with-an-independent-random-secret
   ```

La autorización solicita sólo `gmail.send` y `gmail.readonly`, valida state y PKCE, y cifra el
refresh token. Después de conectar es obligatorio usar “Enviar prueba a mi Gmail”; el servidor
fija el destinatario a la misma cuenta conectada y no acepta uno enviado por el formulario. Como
esa prueba es un envío real, también exige `SEND_MODE=live` y `SEND_KILL_SWITCH=false`.

Para habilitar una campaña live deben configurarse además `SEND_MODE=live` y
`SEND_KILL_SWITCH=false`. El scheduler envía un destinatario por MIME, sin CC/BCC, HTML ni tracking,
y adjunta la versión PDF congelada. Un timeout ambiguo pasa a reconciliación por `Message-ID`; un
reinicio de web/worker reconstruye pendientes y reconciliaciones desde la base.

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
- sin API keys, OAuth real, SMTP, scraping ni llamadas de red de proveedores
