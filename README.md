# Contact Outreach

Aplicación local Django para outreach B2B de un único propietario. El incremento actual incluye
autenticación, configuración comercial, catálogos privados, campañas, supresiones, auditoría,
extracción durable, enriquecimiento web seguro, generación personalizada, OAuth Gmail y entrega
controlada de primeros mensajes. También incluye métricas derivadas, filtros, búsqueda, paginación,
CSV seguro, progreso/reintento de jobs, logs JSON correlacionados, errores amigables y
backup/restore verificado. Los proveedores mock
son el default sin red; Outscraper, el fetch HTTP y los proveedores IA quedan aislados por
contratos internos y son opt-in.

## Requisitos

- Una distribución Linux de 64 bits con Git, `make`, OpenSSL y al menos 4 GiB libres.
- Docker Engine con el plugin Docker Compose. En una máquina limpia, instalar ambos desde la
  [guía oficial de Docker](https://docs.docker.com/engine/install/) para la distribución.
- Opcional para validar fuera de Docker: Python 3.12+ y `make`.

Comprobar la instalación antes de continuar:

```bash
git --version
docker version
docker compose version
make --version
```

## Iniciar el entorno

1. Crear la configuración local:

   ```bash
   cp .env.example .env
   ```

2. Generar valores independientes y reemplazarlos en `.env`:

   ```bash
   openssl rand -hex 32  # DJANGO_SECRET_KEY
   openssl rand -hex 32  # POSTGRES_PASSWORD
   openssl rand -hex 32  # FIELD_ENCRYPTION_KEY
   ```

   Definir además una contraseña fuerte en `OWNER_PASSWORD`. Gmail e IA siguen fake; las
   integraciones de red sólo se habilitan explícitamente. No guardar `.env`, la clave de cifrado ni
   backups dentro del repositorio.

3. Construir e iniciar todos los servicios:

   ```bash
   make build
   make up
   ```

La web queda disponible en <http://127.0.0.1:8000/>. El arranque aplica las migraciones built-in
de Django bajo un advisory lock de PostgreSQL y crea o rota el propietario configurado sin imprimir
la contraseña. La imagen recolecta los archivos estáticos y Gunicorn los sirve mediante WhiteNoise;
no se necesita un servidor Node ni un CDN. PostgreSQL y Redis no publican puertos al host.

Para seguir los logs:

```bash
make logs
```

Cada línea de aplicación es JSON e incluye evento, nivel, correlation ID, duración, ruta y código
de estado cuando corresponde. No incluye query strings, cuerpos, destinatarios ni secretos.

## Comprobar el entorno

- Liveness: <http://127.0.0.1:8000/health/live/>
- Readiness de PostgreSQL y Redis: <http://127.0.0.1:8000/health/ready/>
- Estado degradado de storage/proveedores: <http://127.0.0.1:8000/health/degraded/>
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

Campañas, Prospectos, Envíos, Respuestas, Jobs y Auditoría ofrecen búsqueda, filtros y paginación.
Prospectos, Envíos y Respuestas exportan el conjunto filtrado a CSV con neutralización de fórmulas.
Jobs muestra heartbeat, intentos, próximo retry y error redactado. Un fallo final sólo puede
reintentarse con motivo explícito, sobre la misma fila, Message-ID e idempotency key; un estado
ambiguo se reconcilia y no ofrece ese botón.

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

## Backup y restore

El backup incluye un dump consistente de PostgreSQL y el volumen privado de catálogos, con hashes
SHA-256 y permisos restrictivos. No incluye `FIELD_ENCRYPTION_KEY`: respaldarla separadamente es
obligatorio para recuperar Gmail OAuth.

```bash
make backup
# o: ./scripts/backup.sh /ruta/cifrada/backups
```

Para restaurar, recuperar la misma `FIELD_ENCRYPTION_KEY`, dejar `SEND_KILL_SWITCH=true` y confirmar
el reemplazo de la instalación actual:

```bash
make restore BACKUP=backups/20260716T120000Z
```

Restore verifica checksums, restaura base/catálogos con propiedad del usuario no privilegiado
`app`, aplica migraciones, recalcula hashes y prueba que los refresh tokens se puedan descifrar
antes de reiniciar workers. Ver incidentes de disco, proveedores y reinicios en
[docs/OPERATIONS.md](docs/OPERATIONS.md).

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

La aceptación automatizada
`tests/e2e/test_base_application.py::test_full_fake_acceptance_flow_from_ui` recorre desde la UI
perfil, PDF, OAuth/prueba fake, campaña, pipeline, entrega, respuesta entrante, clasificación,
respuesta manual y exportaciones.

## Checklist obligatorio antes de SEND_MODE=live

No cambiar `SEND_MODE=live` ni desactivar el kill switch hasta completar y registrar externamente:

- [ ] `make check` y `make test-e2e` pasan sobre el commit a desplegar.
- [ ] `.env`, backups, catálogos/exportaciones y claves no están versionados; el escaneo de secretos
  no encuentra valores reales.
- [ ] Bind/proxy/orígenes/cookies fueron revisados; fuera de loopback hay TLS, cookies secure y HSTS.
- [ ] `FIELD_ENCRYPTION_KEY` tiene backup separado y un backup+restore reciente pasó
  `verify_restore` con el kill switch activo.
- [ ] Gmail OAuth usa exactamente `gmail.send` y `gmail.readonly`, la prueba propia pasó y se revisó
  la política de expiración del refresh token.
- [ ] Identidad legal, vendedor, domicilio, firma, asunto `PUBLICIDAD -` y BAJA están completos; la
  campaña tuvo revisión legal y de entregabilidad.
- [ ] Catálogo conserva tamaño/hash y el disco supera `MIN_FREE_DISK_BYTES`.
- [ ] Supresiones fueron revisadas y se probaron baja, bounce e invalidez tardía.
- [ ] Límite diario, intervalo, días, horario y `America/Argentina/Buenos_Aires` son conservadores.
- [ ] Se probaron pausa, cancelación, kill switch, proveedor caído, retry/reconciliación y reinicio.
- [ ] `/health/ready/` está `ok` y `/health/degraded/` no tiene componentes críticos degradados.
- [ ] Primero se cambia `SEND_MODE=live` manteniendo `SEND_KILL_SWITCH=true`; recién después del
  preflight se desactiva el kill switch para una campaña LIVE confirmada.

## Controles seguros por defecto

- `HOST_BIND=127.0.0.1`
- `SEND_MODE=dry-run`
- `SEND_KILL_SWITCH=true`
- extractor, web, IA y Gmail en `fake`
- catálogos fuera de static/media público, bajo `PRIVATE_STORAGE_ROOT`
- sin API keys, OAuth real, SMTP, scraping ni llamadas de red de proveedores
