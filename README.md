# Contact Outreach

Aplicación Django de una sola empresa y varios usuarios para outreach B2B y seguimiento de
Contactos. Los administradores configuran campañas, aprobaciones, Gmail, información autorizada y
automatización; los vendedores pueden leer campañas, correos y conversaciones sin modificar nada.

Las campañas buscan empresas por rubro, provincia y distrito, envían un mensaje fijo con uno o más
PDF, y pueden enviar un único recordatorio si no hubo respuesta. Una respuesta humana convierte a
toda la empresa en Contacto y la excluye de nuevas campañas. El flujo de IA empieza recién allí:
en modo observación propone una decisión; en modo automático sólo responde consultas sustentadas o
redirige una propuesta a una dirección literal validada. Reuniones, precios, negociación,
reclamos, ambigüedades y fallos siempre crean una tarea para una persona.

PostgreSQL, Redis, Celery, Gmail, Overture y los proveedores de IA quedan detrás de servicios
idempotentes y contratos internos. Los proveedores fake y los kill switches activos son los
valores predeterminados. La aplicación incluye métricas derivadas, auditoría, bloqueo de login,
TOTP para administradores, archivos privados, logs redactados y backup/restore verificado.

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

El dashboard requiere iniciar sesión con `OWNER_USERNAME` y `OWNER_PASSWORD` de `.env`. Ese primer
usuario se crea como administrador. No existe registro público: luego un administrador crea cada
usuario y entrega una sola vez su enlace de activación de 24 horas. Los administradores deben
configurar TOTP y reciben diez códigos de recuperación de un solo uso. El login se bloquea durante
30 minutos al quinto intento fallido; también hay protección contra intentos repartidos entre
muchos nombres de usuario. El logout es una acción POST protegida por CSRF.

## Exposición a Internet

La aplicación incluye configuración de producción para hosts y orígenes exactos, cookies seguras,
redirección HTTPS, proxy confiable, HSTS gradual, CSP sin scripts inline, protección de frame,
referrer y tipos de contenido. Los detalles de integraciones y salud degradada son sólo para
administradores; liveness y readiness públicas no revelan diagnósticos.

Esto no constituye por sí solo un despliegue público: Compose mantiene `HOST_BIND=127.0.0.1` por
defecto y este alcance no aprovisiona DNS, certificado TLS ni reverse proxy. Antes de publicar debe
aprobarse un plan HTTPS separado. Como mínimo, ese despliegue deberá definir `APP_ENV=production`,
`PUBLIC_BASE_URL=https://…`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, cookies y
redirección seguras; `DJANGO_PROXY_HTTPS=true` sólo se admite junto con la IP o red CIDR exacta del
proxy en `DJANGO_TRUSTED_PROXY_IPS`. Validar luego con `python src/manage.py check --deploy`.

## Datos de demostración

Los datos demo están separados del arranque normal y se rechazan fuera de `APP_ENV=development`.
Para crear el propietario demo, definir `DEMO_OWNER_PASSWORD` en `.env` y ejecutar:

```bash
make demo
```

El comando requiere además la habilitación explícita que agrega el target `demo`; producción nunca
lo ejecuta automáticamente. Los seeds de las migraciones crean 23 rubros con reglas deterministas,
las 24 jurisdicciones provinciales, 529 partidos/departamentos/comunas de GeoRef y conservan los 48
barrios de CABA como nivel seleccionable. Las fuentes oficiales quedan versionadas y atribuidas
dentro del repositorio; el comando demo continúa creando únicamente el propietario.

## Configurar una campaña

Después de iniciar sesión:

1. Completar el perfil comercial y su firma aprobada.
2. Revisar Integraciones y seleccionar Overture si se usarán datos reales.
3. Abrir **Datos de búsqueda** y preparar la partición de cada provincia que se quiera usar.
4. Revisar los rubros y cargar al menos un PDF válido en Catálogos.
5. Crear una campaña y elegir una o más provincias; dentro de cada una, marcar sus partidos,
   departamentos, comunas o barrios.
6. Elegir uno o más PDF, el calendario, el ritmo, el modo de entrega y si habrá un recordatorio.
7. Iniciar la búsqueda. Todavía no se envía ningún correo.
8. Revisar la audiencia completa, el texto fijo exacto, la firma, los PDF y el calendario.
9. Aprobar toda la campaña —modo recomendado— o aprobar mensajes individuales y luego iniciar sólo
   los aprobados.

El objetivo inicial es 300. `SEND_MODE` y `SEND_KILL_SWITCH` se muestran en el dashboard pero sólo
se configuran por entorno y controlan exclusivamente Gmail live. Con los defaults, las campañas
nuevas siguen siendo dry-run y los proveedores permanecen fake. **Solo revisión** es un modo de
campaña distinto: deja los correos disponibles para leer, pero nunca entra al flujo Gmail. La
búsqueda inicial no usa IA para relevancia ni redacción y no personaliza el mensaje por empresa.

Antes de guardar la primera credencial, definir una única `FIELD_ENCRYPTION_KEY` aleatoria en
`.env` y respaldarla por un canal cifrado separado. Puede generarse sin reutilizar ninguna
contraseña existente con `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Perderla
impide recuperar tokens y credenciales; copiarla junto con la base anula la separación de seguridad.

Cada empresa se deduplica globalmente dentro del espacio de trabajo y puede tener varios emails. Si
Overture no aporta uno válido y existe un sitio oficial, el lector busca únicamente direcciones
visibles y enlaces `mailto:`; nunca inventa direcciones. Antes de preparar, aprobar, encolar y enviar
se vuelve a comprobar email, Contacto, baja, rebote, restricción, modo, Gmail e integridad de todos
los PDF. Una reserva transaccional impide que dos campañas escriban al mismo email el mismo día;
el segundo envío se mueve al próximo día permitido.

**Contactos** reúne todos los hilos de una empresa, sus distintas direcciones, procedencia,
campañas, restricciones, notas, tareas y próximo contacto. **Necesita atención** concentra las
conversaciones que requieren una persona. Las listas normales omiten cuerpos e identificadores
técnicos; los detalles con contenido usan `private, no-store`. Jobs conserva intentos y errores
redactados. Todo reintento reutiliza la fila, el Message-ID y la clave de idempotencia; un estado
Gmail ambiguo se reconcilia antes de repetir.

El recordatorio se envía como máximo una vez, en el hilo original y sin adjuntos. Una respuesta
humana, una carga manual en Contactos, una baja o un rebote lo cancela; una respuesta automática de
ausencia no lo cancela. Los seguimientos periódicos de Contactos son independientes, opt-in,
desactivados por defecto y tienen su propio kill switch.

## Conectar Gmail

Este paso es opcional para **Solo revisión** y dry-run. Google OAuth sólo es necesario para probar
un envío real o ejecutar campañas `LIVE`; conectarlo no vuelve enviables campañas de revisión.

El proveedor fake permite probar todo el flujo sin red desde la pantalla Gmail. Para OAuth real:

1. Crear credenciales OAuth de aplicación web en Google Cloud.
2. Abrir **Integraciones**, seleccionar `Google Gmail` y copiar la redirect URI mostrada.
3. Registrar exactamente esa URI en Google Cloud.
4. Ingresar client ID, un nuevo client secret y la contraseña actual; guardar.
5. Abrir **Gmail** y pulsar **Conectar con Google**.

La autorización solicita sólo `gmail.send` y `gmail.readonly`, valida state y PKCE, y cifra el
refresh token. El client secret también se cifra y nunca se vuelve a mostrar; cambiarlo exige
desconectar primero. Después de conectar es obligatorio usar “Enviar prueba a mi Gmail”; el servidor
fija el destinatario a la misma cuenta conectada y no acepta uno enviado por el formulario. Como
esa prueba es un envío real, también exige `SEND_MODE=live` y `SEND_KILL_SWITCH=false`.

Para habilitar una campaña live deben configurarse además `SEND_MODE=live` y
`SEND_KILL_SWITCH=false`. El scheduler envía un destinatario por MIME, sin CC/BCC, HTML ni tracking,
y adjunta en orden todas las versiones PDF congeladas. Los archivos fuente no pueden superar 17
MiB combinados ni el MIME final 24 MiB; si falta uno, no se envía ninguno. Un timeout ambiguo pasa a
reconciliación por `Message-ID`; un reinicio de web/worker reconstruye pendientes y
reconciliaciones desde la base.

## Configurar la IA para respuestas

El fetch HTTP real se selecciona en **Integraciones → Sitios web**. Sólo acepta HTTP/HTTPS por
80/443, valida todas las respuestas DNS y cada redirect, conecta a la IP pública validada y aplica
límites de páginas, bytes, redirects y tiempo. El contenido resultante siempre se trata como dato
no confiable. `WEBSITE_FETCHER` queda como fallback para instalaciones todavía no configuradas
desde el dashboard.

En **Integraciones** se puede elegir `Ollama` u `OpenAI compatible`, indicar URL base/modelo y, para
el proveedor remoto, ingresar una API key. Guardar exige la contraseña actual. La key queda como
ciphertext write-only; la página sólo informa si está configurada y su origen. Dejar el campo vacío
conserva el valor; marcar eliminar lo borra y desactiva el fallback de entorno.

El proveedor analiza sólo respuestas recibidas y seguimientos opt-in; no interviene en el mensaje
inicial fijo. El servidor ignora overrides de campaña, de modo que redirigir una API key siempre
exige reautenticarse en Integraciones. HTTP sólo se acepta para servicios locales/privados; URLs
con credenciales/query/fragment y redirects se rechazan para evitar reenviar el header de
autorización. Ambos adaptadores exigen salida JSON schema y vuelven a validarla localmente; los
tests sustituyen sus transports y mantienen todos los sockets bloqueados.

La pantalla **Respuesta automática** empieza en **Sólo observar**. Allí se cargan y aprueban
versiones de hechos o preguntas frecuentes; los PDF nunca se convierten solos en conocimiento. Por
cada respuesta, el sistema conserva sólo un manifiesto con IDs, versiones y hashes de un contexto
acotado a 24.000 caracteres: texto nuevo completo, propuesta original, padre directo, hasta seis
mensajes recientes entre hilos, memoria estructurada y hasta ocho datos aprobados.

El modo automático queda bloqueado hasta revisar al menos 30 decisiones —diez aptas para
automatización—, alcanzar 90% de precisión y no marcar ninguna respuesta que habría sido automática
como “Necesitaba una persona”. Activarlo vuelve a pedir la contraseña. Incluso entonces mandan
`AUTO_REPLY_KILL_SWITCH`, las restricciones, los límites de tres respuestas por conversación en 24
horas y veinte por espacio de trabajo al día.

## Sincronizar Overture Maps Places

Overture no requiere API key. En **Integraciones**, seleccionar `Overture Maps Places`, fijar la
confianza mínima de existencia y confirmar con la contraseña actual. Después:

1. Elegir la provincia cuya cobertura se quiere preparar.
2. Abrir **Datos de búsqueda** y revisar la última versión detectada por el chequeo diario
   exitoso de metadata.
3. Revisar el release detectado y pulsar **Sincronizar última versión**. El servidor acepta sólo el
   `latest` del catálogo oficial ya persistido y delega la importación al worker `maintenance` de
   concurrencia uno; el request del navegador no puede elegir una URL, bucket ni release oculto.
4. Esperar el estado `READY` de esa provincia; un import fallido deja intactas las particiones
   anteriores y las demás provincias del mismo release.
5. Repetir sólo para las provincias necesarias y revisar lugares, distritos, fuentes, licencias y
   atribución.

El import usa el cliente Python oficial con modo STAC y el release exacto: hace una lectura acotada
por el bounding box de una provincia, transmite lotes PyArrow, descarta cualquier GERS ID repetido,
filtra cada punto contra los polígonos exactos de sus distritos, excluye `permanently_closed` y
guarda la partición inmutable en PostgreSQL. El catálogo STAC raíz y el catálogo del release están
fijados al host oficial; la fuente Places queda fijada al bucket S3 oficial.

Antes de activar se comprueban `taxonomy`, `basic_category`, IDs, geometrías, hashes, conteos,
procedencia y licencias. Como el catálogo STAC actual publica `schema:version` nulo, cada release
admitido se fija a una versión del schema oficial previamente revisada; cualquier release futuro o
versión declarada distinta falla cerrado hasta actualizar el importador. La taxonomía persistida es
el release exacto porque STAC no publica otra versión semántica independiente. El campo histórico
`categories` no participa en las búsquedas. Un cambio incompatible deja el nuevo snapshot en
`FAILED` y nunca reemplaza la cobertura vigente. Una campaña puede combinar varias provincias,
pero todas sus particiones deben pertenecer al mismo release. Cada consulta usa únicamente la
partición que contiene su distrito y conserva release, reglas, límites y paginación estable.

Places es un dataset multilicencia: se conservan las licencias de cada fuente y los NOTICE
aplicables; si el snapshot incluye Foursquare, el dashboard muestra su aviso Apache-2.0. Revisar
siempre fuentes, licencias, notices y atribución del snapshot antes de usar o redistribuir los
datos. Se conservan el activo, el anterior y todos los snapshots referenciados por campañas. Nunca
se scrapea Google Maps ni se acepta una URL, bucket o credencial de dataset escrita por el usuario.

Referencias oficiales: [Places](https://docs.overturemaps.org/guides/places/),
[cliente Python](https://docs.overturemaps.org/getting-data/overturemaps-py/),
[taxonomía](https://docs.overturemaps.org/guides/places/taxonomy/),
[calendario de releases](https://docs.overturemaps.org/release-calendar/) y
[atribución/licencias](https://docs.overturemaps.org/attribution/).

Las zonas custom se suben como GeoJSON WGS84 `Polygon` o `MultiPolygon`, máximo 1 MiB, 100 partes,
200 anillos y 20.000 coordenadas. Se rechazan CRS custom, valores no finitos/fuera de rango y
geometrías inválidas o autointersectadas. Una edición cambia revisión/hash: campañas iniciadas
conservan el límite anterior y las nuevas esperan una sincronización que cubra el nuevo.

Si todavía no existe una fila de configuración en PostgreSQL, el extractor falla seguro a `fake`.
La selección `fake|overture` vive en el dashboard; `.env` no usa `EXTRACTOR_PROVIDER`, claves
Overture, credenciales de dataset ni URLs alternativas.

## Actualizar una instalación que usaba el proveedor retirado

1. Dejar `SEND_KILL_SWITCH=true` y crear un backup verificado de PostgreSQL y catálogos.
2. Revocar la clave del proveedor retirado. Borrarla de `.env`, del gestor de secretos y de
   cualquier copia operativa; retirar además sus variables históricas. Borrar el valor local sin
   revocarlo no invalida la credencial.
3. Desplegar esta versión y aplicar las migraciones forward. Los borradores pasan a Overture; los
   runs no terminados quedan cerrados con `provider_retired`; historial terminado y costos quedan
   disponibles sólo lectura.
4. Confirmar los distritos y preparar las provincias necesarias desde **Datos de búsqueda**.
5. Revisar cobertura y hashes, conteos, contactos, fuentes, licencias, notices, atribución y health.
6. Ejecutar una campaña pequeña **Solo revisión**, leer cada email y recién después ampliar uso.

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
obligatorio para recuperar Gmail OAuth y las credenciales cifradas de Integraciones. Los releases
y sus particiones Overture viven en PostgreSQL y quedan incluidos en el dump.

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
`app`, aplica migraciones y ejecuta `verify_restore`: comprueba migraciones, hashes de catálogos
PDF, espacio, descifrado de refresh tokens/credenciales y que las particiones Overture usadas por
campañas activas estén `READY`. Después del restore hay que revisar manualmente en **Datos de
búsqueda** los hashes de cobertura, conteos, licencias y atribución antes de iniciar una campaña o
habilitar live. Ver incidentes de disco, proveedores y reinicios en
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
bloquea sockets externos y usa proveedores fake o fixtures Overture locales; nunca descarga un
dataset ni llama Gmail/LLM reales.

Las pruebas de aceptación fake recorren perfil, varios PDF, campaña, aprobación, entrega,
respuesta entrante, promoción a Contacto, decisiones estructuradas, redirección, recordatorio,
restricciones, conversaciones y permisos. Las pruebas bloquean HTTP, DNS, Gmail, Overture e IA
reales.

## Checklist obligatorio antes de SEND_MODE=live

No cambiar `SEND_MODE=live` ni desactivar el kill switch hasta completar y registrar externamente:

- [ ] `make check` y `make test-e2e` pasan sobre el commit a desplegar.
- [ ] `.env`, backups, catálogos/exportaciones y claves no están versionados; el escaneo de secretos
  no encuentra valores reales.
- [ ] Existe un plan de despliegue HTTPS aprobado; bind, proxy confiable, hosts, orígenes, cookies,
  CSP y HSTS pasaron `check --deploy` en una configuración equivalente a producción.
- [ ] `FIELD_ENCRYPTION_KEY` tiene backup separado y un backup+restore reciente pasó
  `verify_restore` con el kill switch activo.
- [ ] Gmail OAuth usa exactamente `gmail.send` y `gmail.readonly`, la prueba propia pasó y se revisó
  la política de expiración del refresh token.
- [ ] Identidad, vendedor, domicilio, firma y los tres mensajes fijos están completos y aprobados.
- [ ] Todos los catálogos conservan tamaño/hash, el MIME final respeta 24 MiB y el disco supera
  `MIN_FREE_DISK_BYTES`.
- [ ] Las restricciones dentro de Contactos fueron revisadas y se probaron baja irreversible,
  rebote, “No contactar”, “No usar este email” e invalidez tardía.
- [ ] Límite diario, intervalo, días, horario y `America/Argentina/Buenos_Aires` son conservadores.
- [ ] Se probaron pausa, cancelación, kill switch, proveedor caído, retry/reconciliación y reinicio.
- [ ] La audiencia, texto, firma, PDF y calendario quedaron congelados por una aprobación de
  campaña, o el modo individual excluyó de forma explícita los mensajes no aprobados.
- [ ] Se probaron protección de mismo día y recordatorio en hilo con cancelación ante respuesta.
- [ ] `AUTO_REPLY_KILL_SWITCH` y `RELATIONSHIP_KILL_SWITCH` siguen activos durante el rollout;
  **Sólo observar** acumuló y superó la evaluación antes de considerar respuestas reales.
- [ ] `/health/ready/` está `ok` y `/health/degraded/` no tiene componentes críticos degradados.
- [ ] Primero se cambia `SEND_MODE=live` manteniendo `SEND_KILL_SWITCH=true`; recién después del
  preflight se desactiva el kill switch para una campaña LIVE confirmada.

## Controles seguros por defecto

- `HOST_BIND=127.0.0.1`
- `SEND_MODE=dry-run`
- `SEND_KILL_SWITCH=true`
- `AUTO_REPLY_KILL_SWITCH=true`
- `RELATIONSHIP_KILL_SWITCH=true`
- extractor, web, IA y Gmail en `fake`
- catálogos fuera de static/media público, bajo `PRIVATE_STORAGE_ROOT`
- sin API keys, OAuth real, SMTP, scraping ni llamadas de red de proveedores
