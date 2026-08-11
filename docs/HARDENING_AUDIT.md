# Auditoría específica de hardening

Fecha: 2026-07-26. Alcance: aplicación preparada para exposición detrás de HTTPS, autenticación
multiusuario, configuración cifrada, campañas fijas, Contactos, automatización controlada,
catálogo Overture particionado, proveedores fake y adaptadores aislados.
Resultado: no se identificó un camino conocido que eluda las barreras live, supresión o
reconciliación. Gmail no ofrece exactly-once absoluto y la estabilidad del refresh token depende de
Google; ambos siguen siendo riesgos operativos explícitos.

| Área | Control implementado y evidencia | Resultado |
| --- | --- | --- |
| Envíos duplicados | Reserva única `CampaignDeliveryReservation(email, fecha local)` para inicial/recordatorio; idempotencia, Message-ID determinista y reconciliación para campañas, respuestas y seguimientos. | Cubierto; Gmail no ofrece exactly-once absoluto y ese riesgo residual sigue documentado. |
| Revisión y aprobación | Aprobación de campaña congela audiencia, contenido, firma, PDFs, horario y actor; el modo opcional por mensaje conserva edición y autorización explícitas. Ambos revalidan antes de Gmail. | Cubierto por regresiones de permisos, hash, elegibilidad y conexión Gmail. |
| Acceso público y roles | Hosts/orígenes exactos, cookies seguras, proxy confiable, SSL redirect, HSTS gradual, CSP y `private, no-store`; matriz anónimo/ADMIN/VENDEDOR y autorización central por Workspace. Railway usa un modo explícito para `X-Forwarded-Proto` marcado por `X-Railway-Request-Id`. | Aplicación y configuración Railway cubiertas; certificados, dominio, health check y proxy real aún requieren validación en staging. |
| Inicio de sesión | Quinto fallo bloquea 30 minutos sin extender el plazo; hash HMAC usuario+IP, límite agregado por IP, TOTP obligatorio para ADMIN, recuperación de un uso y sesiones invalidadas al cambiar acceso. | Cubierto por pruebas de lockout, proxy y segundo factor. |
| Transiciones inválidas | Servicios de campaña, discovery, pipeline y delivery validan el grafo bajo transacción; views/tasks coordinan. | Cubierto. |
| Pérdida de refresh token | Ciphertext Fernet con clave externa; disconnect elimina local; backup excluye la clave y `verify_restore` prueba descifrado. README advierte expiración en OAuth Testing. | Cubierto localmente; depende de backup separado y Google. |
| Exposición de secretos | Root key externa; API keys/client secret como Fernet write-only ligado a propósito; reautenticación+CSRF; sin secretos/ciphertext en HTML, redirect, task, snapshot, audit, log o error; restore prueba descifrado. | Cubierto frente a DB/backup/UI/log aislados; compromiso conjunto de host+root key sigue siendo riesgo residual inevitable. |
| Cambio malicioso de proveedor | Reingreso de contraseña con throttling, campaña sin overrides POST, Overture sin URL/credencial configurable y limitado en código a STAC/S3 oficiales, LLM sin redirects con Authorization. Gmail exige disconnect antes de rotar OAuth. | Cubierto para sesión robada sin contraseña; revisar TLS si deja loopback. |
| SSRF | HTTP/S 80/443, DNS validado, IP fijada, redirects revalidados y límites de bytes/tiempo/páginas. | Cubierto por matriz SSRF. |
| Prompt injection | Mensajes entrantes son datos no confiables; el LLM no recibe tools ni credenciales, sólo puede devolver acciones/IDs allowlisted y los servicios vuelven a validar facts, candidatos, contexto y efectos. La búsqueda usa taxonomía/términos literales. | Cubierto. |
| GeoJSON hostil | Máximo 1 MiB/100 partes/200 anillos/20.000 coordenadas; WGS84 poligonal, rangos finitos, CRS custom y geometrías inválidas/autointersectadas rechazados; canonicalización y hash server-side. | Cubierto por matriz de geometría y CSRF/upload. |
| Integridad Overture | Hosts oficiales fijos, streaming acotado por bounding box provincial, GERS ID único, schema/taxonomía fail-closed, filtro poligonal exacto y particiones READY independientes dentro de un release. | Cubierto; releases nuevos requieren validación antes de activación. |
| Reproducción de campañas | Release, coberturas ordenadas, reglas, geometría y hashes quedan congelados; todas las provincias de una campaña pertenecen al mismo release y la retención protege referencias. | Cubierto por replay, particiones, paginación y upgrade. |
| Procedencia/licencias | Release, manifests, hashes, fuentes por campo, licencias, NOTICE, atribución y conteos quedan persistidos y visibles; Places se trata como multilicencia. | Cubierto; revisar fuentes/licencias/notices al sincronizar cada release. |
| Exposición de cuerpos | Listas públicas inexistentes, logs/auditoría sin cuerpos, detalle autenticado por rol y Workspace, escape de template y `private, no-store` en mensajes y Contactos. Alertas por email contienen sólo un enlace seguro. | Cubierto en la aplicación; una sesión autorizada puede leer conversaciones como requiere el producto. |
| Archivos maliciosos | Nombre generado, extensión/MIME/magic/EOF, 15 MiB por PDF, 17 MiB de fuentes y 24 MiB MIME, hashes y storage privado. El set ordenado se verifica completo: nunca hay envío parcial. | Cubierto para v1; antivirus fuera de alcance documentado. |
| CSRF | Middleware Django, mutantes POST, tokens, logout POST y pantalla 403 neutra. | Cubierto. |
| Concurrencia | Transacciones, row/advisory locks, `skip_locked`, generaciones, reservas y uniques. | Cubierto por servicios; validar carga PostgreSQL Compose antes de live. |
| Reintentos | Backoff, `Retry-After`, deadlines, máximo técnico, sin fallback IA ni retry ciego Gmail; retry manual revalida elegibilidad. | Cubierto. |
| Restricciones y Contactos | Consulta en preparación/aprobación/cola/efecto; Contact excluye toda la Organization, BAJA es irreversible, bounce invalida sólo el email y “No contactar” queda auditado dentro de Contactos. | Cubierto. |
| Automatización de respuestas | `SHADOW` por defecto y LIVE sujeto a reautenticación, confianza, contexto íntegro, facts activos, candidato literal, kill switch, suspensión por tarea y límites 3/conversación y 20/Workspace. | Cubierto; habilitar LIVE requiere decisión explícita admin. |
| Redirección de propuestas | Candidato literal `NEW_CONTENT`, pertenencia bajo lock, propuesta fija con PDFs en hilo nuevo y acuse únicamente después de confirmación/reconciliación. Una acción semántica por Gmail ID. | Cubierto por flujo feliz, fallos y reintentos. |
| Límites diarios | Campañas calculan cupos y fechas bajo lock; acciones automáticas reservan capacidad por conversación/Workspace; horarios y safety thresholds se revalidan justo antes del efecto. | Cubierto. |
| Zona horaria | UTC persistido; límites/calendario con `ZoneInfo` del snapshot; default Buenos Aires. | Cubierto. |
| Reinicios | Checkpoints PostgreSQL y recovery de extracción, pipeline, campañas, decisiones LIVE, notificaciones, seguimientos y reconciliación; fake outbox durable. | Cubierto. |
| Errores parciales | Discovery separado; fallo proveedor drena cola autorizada; web fallback; errores por prospecto/mensaje visibles. | Cubierto. |

## Comandos antes de live

```bash
make check
make test-e2e
git diff --check
rg -n --hidden -g '!.git/**' -g '!.env' '(BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z]{20,})' .
rg -n 'FR-[0-9]+|DM-[0-9]+|SEC|OPS|QA' docs/
```

Revisar además `git status --short`, `.env.example`, logs y el backup cifrado. Los placeholders de
documentación no son credenciales.
