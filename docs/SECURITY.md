# Seguridad

## 1. Modelo de amenazas

Activos principales: credenciales OAuth/API, base de prospectos, emails, mensajes, catálogo, perfil comercial y capacidad de enviar desde Gmail. Entradas hostiles: webs de terceros, respuestas email/HTML, JSON de proveedores, output LLM, archivos PDF, parámetros HTTP y CSV exportado.

Adversarios considerados: sitio que intenta SSRF o prompt injection, archivo malicioso, remitente con HTML/script, acceso local no autorizado, filtración por logs/backups, worker duplicado y abuso accidental de live. La v1 no se declara segura para Internet público sin proxy TLS y hardening adicional.

## 2. Autenticación y sesión

- Un único `User` propietario; sin registro, recuperación pública ni usuarios invitados.
- `OWNER_USERNAME` y `OWNER_PASSWORD` inicializan/rotan mediante comando explícito que no imprime valores. Django guarda únicamente hash Argon2.
- Login con throttling por IP/sesión, mensajes neutros y regeneración de sesión.
- CSRF obligatorio en todo POST/HTMX; ninguna acción mutante usa GET.
- Cookies `HttpOnly`, `SameSite=Lax`; `Secure` es obligatorio al exponer fuera de localhost. Timeout de sesión configurable y logout invalida sesión.
- El bind por defecto es `127.0.0.1`. Exposición externa exige allowlist de hosts/orígenes, proxy TLS, `SECURE_PROXY_SSL_HEADER`, HSTS y revisión de despliegue.

**SEGURIDAD:** HTTP local implica que `Secure` no puede activarse; sólo se acepta porque el servicio no sale de loopback. Docker no debe publicar PostgreSQL ni Redis.

## 3. Secretos y OAuth

- Secrets sólo en variables/archivos fuera del repositorio; `.env.example` contiene placeholders.
- API keys LLM/Outscraper no se editan ni persisten en claro desde el dashboard.
- Refresh token Gmail se cifra con una clave versionada externa (`FIELD_ENCRYPTION_KEY`); access tokens viven en memoria cuando sea posible.
- Nunca se loguean tokens, authorization codes, client secrets, API keys ni URLs con credenciales.
- Desconectar revoca cuando sea posible y elimina ciphertext/cursor local, conservando auditoría sin secreto.
- Scopes: `gmail.send` y `gmail.readonly`, nunca `mail.google.com`. `gmail.readonly` es restringido y su uso/almacenamiento puede requerir verificación y evaluación de Google: [documentación de scopes](https://developers.google.com/workspace/gmail/api/auth/scopes).

## 4. SSRF y fetch web

`WebsiteFetcher` aplica defensa por cada URL inicial y redirección:

1. Parseo estricto; sólo `http`/`https`, sin userinfo, fragmentos, hostname vacío ni puertos fuera de 80/443.
2. Canonicalización IDNA y rechazo de `localhost`, sufijos locales, metadata cloud y hostnames/IPs especiales.
3. Resolución A/AAAA mediante resolver inyectable. Se rechaza si **cualquier** respuesta es loopback, privada, link-local, multicast, reservada, no especificada o CGNAT.
4. Conexión al IP público validado y fijado para ese hop, preservando Host/SNI; no se permite una segunda resolución implícita. Se revalida cada redirect.
5. Máximo tres redirects, cuatro páginas, 2 MiB por respuesta, sólo HTML/texto, connect 5 s, read 10 s y presupuesto total 30 s.

No se ejecuta JavaScript, se descargan imágenes ni se aceptan `file:`, `ftp:`, `data:`. La validación de aplicación se complementa con egress restringido del contenedor cuando el entorno lo permita.

## 5. Contenido no confiable e IA

- Scripts, estilos, formularios, SVG, iframes y navegación repetida se eliminan; el dashboard prefiere texto.
- HTML email se sanitiza con allowlist mínima, sin scripts, estilos, eventos, formularios, objetos ni URLs activas peligrosas.
- El prompt separa instrucciones de hechos y rotula toda web/email como `UNTRUSTED_DATA`; instrucciones encontradas dentro se ignoran.
- Herramientas y acciones externas no están disponibles para el modelo. Output pasa por Pydantic y validadores de evidencia, longitud y reglas de copy.
- Instrucciones adicionales del usuario no pueden desactivar supresión, hechos permitidos, CTA, texto plano ni controles de seguridad.
- CSV antepone `'` a celdas que comienzan con `=`, `+`, `-`, `@`, tab o retorno para evitar formula injection.

## 6. Upload y almacenamiento

- Catálogo: nombre sanitizado, extensión `.pdf`, MIME detectado, magic bytes `%PDF-`, parseo estructural básico, máximo 15 MiB y SHA-256.
- El path lo genera el servidor; nunca usa rutas del cliente. Almacenamiento fuera de static/media público, permisos mínimos y descarga sólo autenticada.
- La versión es inmutable. Antes de cada envío se recalculan existencia, tamaño y hash.
- Un PDF válido puede seguir conteniendo contenido activo; nunca se renderiza en el servidor ni se abre automáticamente. Se documenta escaneo antivirus como hardening futuro.

## 7. Envío, abuso y cumplimiento

- Tres barreras independientes: `SEND_MODE=live`, kill switch desactivado y campaña live.
- Preflight exige Gmail conectado/probado, perfil con identidad/domicilio, BAJA, catálogo íntegro, supresión limpia, horario y cuotas.
- Un destinatario; sin CC/BCC, seguimiento, HTML, rotación ni evasión.
- Pausa automática por rebotes/errores, 403/429 persistentes o autenticación revocada.
- La lista de supresión se consulta en preparación, enqueue y send; `UNSUBSCRIBE` es irreversible desde UI.

**LEGAL/ENTREGABILIDAD:** el prefijo `PUBLICIDAD -`, identidad y BAJA implementan controles técnicos, pero no prueban licitud de cada base ni garantizan cumplimiento. La normativa argentina exige identificar publicidad y ofrecer retiro/bloqueo; se requiere revisión legal antes de live: [Disposición 4/2009](https://servicios.infoleg.gob.ar/infolegInternet/anexos/150000-154999/151221/norma.htm).

## 8. Logs, auditoría y privacidad

Logs normales guardan IDs, estados, latencias, contadores y errores redactados. No guardan cuerpos completos, HTML, raw JSON, prompts, recipients ni secretos. Debug explícito tiene duración acotada, redacción y aviso visible; los payloads completos permanecen en almacenamiento de dominio con acceso autenticado.

`AuditEvent` es append-only. Se auditan login, configuración live, conexión Gmail, campaña, overrides, supresiones, uploads, transiciones, envíos y respuestas manuales. No se registran ciphertext ni tokens.

Raw extractor y snapshots web tienen retención inicial de 180 días. Supresiones se conservan permanentemente con datos mínimos. Backups se cifran, tienen permisos restrictivos, rotación documentada y restore probado.

## 9. Checklist para live

- Bind/proxy/orígenes/cookies revisados y TLS si no es loopback.
- Secrets fuera del repo, clave de cifrado respaldada por separado y logs redactados.
- OAuth publicado/configurado; refresh token estable y scopes exactos confirmados.
- Identidad legal, domicilio, reply-to y BAJA completos; revisión legal registrada externamente.
- Catálogo hash válido, Gmail test exitoso, límites conservadores y supresión cargada.
- Backup y restore probados; kill switch comprobado antes de desactivarlo.
