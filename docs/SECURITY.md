# Seguridad

## 1. Modelo de amenazas

Activos: cuentas/sesiones y roles, secretos OAuth/API, capacidad de envío Gmail, contactos y
conversaciones, PDFs privados, datos Overture, geometrías, conocimiento aprobado y auditoría.
Entradas hostiles: Internet público, login, proxy headers, sitios/DNS, emails/HTML/headers,
GeoParquet/GeoJSON, PDFs, outputs LLM, CSV y parámetros HTTP.

Adversarios: credential stuffing y spray de usernames, usuario autenticado fuera de su rol,
remitente que intenta prompt injection o redirección maliciosa, sitio SSRF, archivo hostil, worker
duplicado, timeout Gmail ambiguo y habilitación accidental de automatización/live.

La app implementa readiness para Internet, pero el despliegue actual no se declara público hasta
aprobar proxy inverso, TLS, certificados, monitoreo y runbook de incidente.

## 2. Autenticación, lockout y MFA

- Sin registro ni recuperación pública. El admin crea usuarios y recibe una sola vez un token
  aleatorio de activación/reset válido 24 h; DB guarda sólo digest y consumo.
- Passwords usan Argon2. Login y MFA regeneran sesión; logout la invalida.
- Attempts 1–4 dan mensaje neutro. El quinto fallo bloquea username normalizado + IP confiable por
  30 minutos. Veinte fallos agregados por IP en 30 minutos bloquean esa IP.
- Identificadores se guardan como HMAC con una clave de propósito, nunca username/IP crudos. Las
  actualizaciones toman locks transaccionales.
- Durante bloqueo se responde 429 y `Retry-After`; el intento no incrementa ni extiende
  `locked_until`. Un éxito limpia sólo el par correspondiente. Admin unlock y comando de emergencia
  auditan sin revelar digest.
- La IP sólo proviene de `REMOTE_ADDR` o del header del proxy cuando su origen pertenece a una
  allowlist exacta de proxies. Un cliente directo no puede falsear `X-Forwarded-For`.
- ADMIN requiere TOTP confirmado con `django-otp`; VENDEDOR puede habilitarlo. Diez recovery codes
  se muestran una vez y quedan hasheados/consumibles individualmente. Regenerar invalida el set.
- Cambio de rol, desactivación, reset sensible o pérdida de Membership incrementan una versión de
  seguridad y eliminan sesiones activas.
- No se borra un User con historia ni se desactiva/degrada el último admin activo.

## 3. Autorización

`created_by == request.user` nunca es una regla de acceso. Middleware resuelve Workspace y
Membership; servicios centrales verifican capacidades además de decorators/views. Un task sólo
opera IDs del único Workspace y revalida que la acción durable fue creada por actor autorizado.

VENDEDOR puede hacer GET de Resumen, campañas visibles/list/detail, mensajes `SENT` y
Contactos/conversaciones. Recibe 403/404 seguro para borradores, audiencia/prospectos, exports,
descargas PDF, reply, approvals, configuración, integraciones, usuarios, jobs, auditoría y detalles
técnicos. No puede POST salvo logout. Ocultar navegación no sustituye enforcement.

CSRF es obligatorio en mutaciones y HTMX. Acciones críticas (habilitar LIVE IA, cambiar
credenciales/rol, recovery reset) exigen reautenticación admin. GET nunca muta.

## 4. Sesiones, headers e Internet readiness

Producción define listas exactas de `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS`; no wildcards. Cookies
de sesión/CSRF `Secure`, `HttpOnly` cuando aplica y `SameSite=Lax`; SSL redirect; proxy HTTPS
awareness sólo para proxy confiable. HSTS se escala primero con duración corta y sin
includeSubDomains/preload hasta verificar despliegue.

Headers: CSP restrictiva con scripts/styles propios static y nonce sólo si fuera imprescindible,
`Referrer-Policy`, `X-Content-Type-Options`, frame-ancestors/DENY, permissions policy y no sniff.
Inline scripts se retiran. Mensajes, contactos, auth, health detallado y configuraciones sensibles
usan `Cache-Control: private, no-store`.

Liveness puede ser público y mínimo. Readiness externo sólo expone disponible/no disponible sin
topología. Estado detallado de DB/Redis/Gmail/providers/configuración es admin-only. La web sigue
ligada a `127.0.0.1` por defecto; no abrir host ni desactivar cookies secure para “hacerlo andar”.

## 5. Secretos y OAuth

- `FIELD_ENCRYPTION_KEY`, `DJANGO_SECRET_KEY`, DB/Redis, barreras live, claves HMAC, bootstrap y
  proxy config viven fuera del repositorio/dashboard.
- LLM API key, Google client secret y refresh token Gmail se cifran con Fernet autenticado y
  subclaves separadas por propósito. Son write-only; formularios, errores, audit y logs sólo muestran
  configured/origin.
- Rotar client credentials exige desconectar Gmail. OAuth usa state/PKCE, redirect exacto y scopes
  `gmail.send` + `gmail.readonly`, nunca SMTP password ni `mail.google.com`.
- No se loguean tokens, codes, secrets, ciphertext, Authorization headers ni URLs con credenciales.
- `PUBLIC_BASE_URL` se valida como HTTPS público en despliegue público y sólo genera links a rutas
  internas conocidas; nunca acepta path/body desde el inbound.
- Overture sólo usa hosts/catálogos oficiales definidos en código; no admite bucket/URL/credencial
  del dashboard.

## 6. Contenido hostil, LLM y prompt injection

- HTML web/email se limpia con allowlist; scripts, styles, events, forms, SVG, iframes, objects y
  URLs activas peligrosas se eliminan. Raw HTML no entra al modelo.
- Texto de web, email, firmas y citas se rotula como datos no confiables. No puede cambiar sistema,
  policy ni listas permitidas.
- El LLM no recibe Gmail, HTTP, calendario, filesystem ni tool calling. Sólo devuelve JSON.
- El schema enumera en cada request candidate IDs, fact revision IDs, intents y actions exactos.
  Campos/IDs extra, conflicto, multi-intent o output inválido fallan hacia HumanTask.
- Los hechos sólo provienen de revisiones aprobadas. El contexto global aprobado se inyecta siempre
  como orientación, y los facts puntuales se recuperan con embeddings hasta un máximo pequeño; si
  la búsqueda es baja o ambigua, no se usan como fundamento automático. No se extraen PDFs ni se
  inventan claims.
- Contexto máximo 24.000 caracteres: mandatory completo o HumanTask. Se limita historia y no se
  persiste prompt gigante/cuerpos duplicados en logs.
- Emails candidatos se extraen literalmente, máximo diez, con región. No se reconstruyen
  ofuscaciones. Firma/cita nunca es target automático y múltiples NEW_CONTENT ambiguos exigen
  persona.
- Confianza >=0,90 no permite eludir policy. SHADOW jamás produce autorización Gmail.

## 7. Barreras de automatización y envío

Envío inicial/live requiere simultáneamente `SEND_MODE=live`, `SEND_KILL_SWITCH=false`, campaign
mode/approval, Gmail, ventana/cupo, recipient elegible y adjuntos íntegros. Respuesta automática
además exige `AUTO_REPLY_KILL_SWITCH=false`, mode LIVE calificado, Conversation activa, intent
allowlisted, contexto/facts válidos y reservas de rate limit. La recuperación por embeddings sólo
decide qué facts llegan al request; no autoriza Gmail ni puede saltarse política, kill switches o
tareas humanas. Comunicación programada usa además `RELATIONSHIP_KILL_SWITCH=false`.

Estas barreras se comprueban al preparar/autorizar/encolar y **otra vez inmediatamente antes de
Gmail**. DB authorization nunca reemplaza la barrera externa. Headers de auto submitted/bulk/list,
unsubscribe, bounce, restricción, HumanTask abierto o cambio de contexto cancelan el efecto.

Límites automáticos: tres replies por Conversation en 24 h móviles y veinte por Workspace/día,
reservados transaccionalmente. Meeting/dates, price/quote, negotiation, complaints, legal/privacy,
unsupported technical, multiple intent, ambiguity y conflicts son siempre humanos.

La saga redirect verifica candidato/ownership/MX/restricciones bajo lock. Nunca dice “enviada” hasta
confirmación/reconciliación de la propuesta. Proposal y ACK tienen Message-ID/idempotency separados.
No hay override de unsubscribe ni Contact exclusion.

## 8. Gmail, abuso y privacidad de alertas

- Un destinatario por efecto; sin CC/BCC en campañas, tracking, HTML, account rotation o evasión.
- INITIAL/REMINDER reservan email/fecha local para evitar dos campañas el mismo día.
- Timeout ambiguo pasa a RECONCILING y busca Message-ID antes de retry.
- Sync sólo persiste mensajes ligados a threads/headers propios. `gmail.readonly` conserva riesgo
  potencial de acceso amplio y puede exigir verificación Google.
- Un email de alerta humana usa asunto genérico y link seguro; nunca inbound body, subject, contact
  name o dirección. Dashboard task persiste aunque notification falle.
- Unsubscribe se aplica antes de IA, es irreversible y bloquea todo efecto al scope aplicable.

Por decisión de producto, el copy seed no añade `PUBLICIDAD` ni pie `BAJA`. Eso puede ser
insuficiente legalmente para outreach no consentido; la revisión legal y de deliverability sigue
siendo requisito externo antes de live.

## 9. SSRF, DNS y red

`WebsiteFetcher` acepta sólo HTTP/HTTPS, hostname IDNA sin userinfo, puertos 80/443 y revalida cada
redirect. Rechaza localhost, metadata, privadas, loopback, link-local, multicast, reservadas,
unspecified y CGNAT si **cualquier** A/AAAA cae allí. Fija IP validada por hop manteniendo Host/SNI,
evita segunda resolución implícita y limita redirects/páginas/bytes/timeouts.

MX usa resolver inyectable con timeout; no hace SMTP handshake. Tests bloquean sockets. Contenedores
aplican egress/segmentación cuando infraestructura lo permita; PostgreSQL y Redis no se publican.

## 10. Uploads y almacenamiento privado

- PDF: `.pdf`, MIME detectado, magic `%PDF-`, parseo básico, <=15 MiB/archivo, SHA-256, storage key
  del servidor. Conjunto de campaña <=17 MiB y MIME final <=24 MiB.
- El conjunto exacto se verifica justo antes del MIME; un faltante/tamper pausa, nunca se adjunta
  parcial.
- GeoJSON: límites existentes (<=1 MiB, WGS84 Polygon/MultiPolygon, partes/anillos/coordenadas
  acotados, números finitos, sin CRS custom ni geometría inválida).
- Archivos están fuera de static/media público. Web escribe; worker monta read-only; Beat no monta.
  Descarga admin autenticada; VENDEDOR no descarga.
- Filenames nunca forman paths. PDFs no se renderizan/abren en servidor; antivirus queda como
  hardening futuro documentado.

## 11. Logs, auditoría y retención

Logs guardan IDs opacos, estados, duración, counts y error redactado; no cuerpos, HTML, prompts,
recipients, headers sensibles, candidatos, facts completos, secrets o ciphertext. Context manifest
persiste IDs/versiones/hash. `AuditEvent` es append-only y tampoco guarda cuerpos.

Mensajes/contactos se muestran sólo según capacidad y con no-store; no entran a exports de
VENDEDOR. CSV admin neutraliza celdas que empiezan con `=`, `+`, `-`, `@`, tab o CR. Supresiones y
evidencia mínima se conservan para no recontactar. Backups cifrados incluyen DB/PDF y clave por
separado; restore prueba descifrado e integridad sin imprimir valores.

## 12. Checklist antes de exposición/live

- Matriz anonymous/ADMIN/VENDEDOR y service-level authorization aprobada.
- Lockout quinto intento/IP spray, TOTP/recovery, last-admin y session invalidation aprobados.
- Hosts/orígenes/proxy confiable/cookies/redirect/HSTS/CSP/headers y `check --deploy` aprobados.
- Proxy TLS/certificados/monitoreo/runbook externos implementados; hasta entonces loopback.
- Secrets scan, backup/restore y health privado aprobados.
- Gmail scopes/app/refresh token, reconciliación y límites aprobados.
- PDFs/contenido/firma/cobertura Overture y same-day guard aprobados.
- SEND/AUTO_REPLY/RELATIONSHIP kill switches probados.
- SHADOW gate: >=30 reviews, >=10 auto-eligible, accuracy >=90%, cero unsafe auto; reauth para LIVE.
- Revisión legal/deliverability registrada externamente.
