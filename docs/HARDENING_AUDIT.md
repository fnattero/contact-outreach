# Auditoría específica de hardening

Fecha: 2026-07-16. Alcance: implementación local, proveedores fake y adaptadores aislados.
Resultado: no se identificó un camino conocido que eluda las barreras live, supresión o
reconciliación. Gmail no ofrece exactly-once absoluto y la estabilidad del refresh token depende de
Google; ambos siguen siendo riesgos operativos explícitos.

| Área | Control implementado y evidencia | Resultado |
| --- | --- | --- |
| Envíos duplicados | `ContactLedger` por email, uniques de secuencia/idempotency/Message-ID/Gmail ID, misma fila por retry y reconciliación antes de repetir. Regresiones de delivery/manual y aceptación fake. | Cubierto; riesgo residual externo documentado. |
| Transiciones inválidas | Servicios de campaña, discovery, pipeline y delivery validan el grafo bajo transacción; views/tasks coordinan. | Cubierto. |
| Pérdida de refresh token | Ciphertext Fernet con clave externa; disconnect elimina local; backup excluye la clave y `verify_restore` prueba descifrado. README advierte expiración en OAuth Testing. | Cubierto localmente; depende de backup separado y Google. |
| Exposición de secretos | Entorno, redacción de audit/logs, sin query/body/recipient en log y errores sin excepción. `.env`, backups y private ignorados. | Cubierto; escaneo obligatorio antes de live. |
| SSRF | HTTP/S 80/443, DNS validado, IP fijada, redirects revalidados y límites de bytes/tiempo/páginas. | Cubierto por matriz SSRF. |
| Prompt injection | Web `UNTRUSTED_DATA`, sin tools, evidencia limitada a fact IDs y output/copy validado localmente. | Cubierto. |
| Archivos maliciosos | Nombre generado, extensión/MIME/magic/EOF/tamaño/hash, storage privado, sin render, integridad antes de send y ENOSPC atómico. | Cubierto para v1; antivirus fuera de alcance documentado. |
| CSRF | Middleware Django, mutantes POST, tokens, logout POST y pantalla 403 neutra. | Cubierto. |
| Concurrencia | Transacciones, row/advisory locks, `skip_locked`, generaciones, reservas y uniques. | Cubierto por servicios; validar carga PostgreSQL Compose antes de live. |
| Reintentos | Backoff, `Retry-After`, deadlines, máximo técnico, sin fallback IA ni retry ciego Gmail; retry manual revalida elegibilidad. | Cubierto. |
| Supresiones | Consulta en preparación/cola/efecto, lock compartido, BAJA irreversible, bounce invalida y override no aplica. | Cubierto. |
| Límites diarios | Cálculo desde live confirmado/reservado bajo lock, intervalo, ventana, días y safety thresholds. | Cubierto. |
| Zona horaria | UTC persistido; límites/calendario con `ZoneInfo` del snapshot; default Buenos Aires. | Cubierto. |
| Reinicios | Checkpoints PostgreSQL y recovery de extracción/pipeline/delivery/reconciliación; fake outbox durable. | Cubierto. |
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
