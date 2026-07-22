# Operación y recuperación

## 1. Señales operativas

PostgreSQL es la fuente de verdad. El dashboard calcula contadores desde `SearchRun`, `Prospect`,
`OutboundMessage` e `InboundMessage`; Redis no autoriza ni contabiliza envíos. Jobs expone estado,
intentos, heartbeat, próximo retry y error redactado para extracción, pipeline, delivery y sync.
Auditoría es append-only.

Los logs son JSON por stdout. `http.request_completed` incluye método, path sin query string,
status, duración y correlation ID. `storage.write_failed` identifica ENOSPC sin filename ni payload.
Los errores HTML muestran el correlation ID y no la excepción.

Health endpoints:

- `/health/live/`: proceso web vivo;
- `/health/ready/`: PostgreSQL y Redis disponibles;
- `/health/degraded/`: margen del volumen privado y estado/configuración local de Gmail,
  extractor e IA. No llama proveedores remotos.

## 2. Backup

`scripts/backup.sh [directorio]` ejecuta `verify_restore`, genera `pg_dump --format=custom`, copia
el volumen `/app/private`, crea manifiesto y checksums, y publica el directorio sólo al finalizar.
Usa `umask 077`. Un fallo, incluido disco lleno, conserva el backup anterior y elimina sólo el
temporal de esa ejecución.

El dump contiene ciphertext de Integraciones pero no `FIELD_ENCRYPTION_KEY`. Guardar esa raíz por
un canal cifrado separado. Un backup sin base, catálogos y clave no es restaurable; quien obtiene
sólo el dump no debe poder descifrar API keys, client secret ni refresh token.

## 3. Restore

1. Recuperar `.env` y la misma `FIELD_ENCRYPTION_KEY`.
2. Establecer `SEND_KILL_SWITCH=true`.
3. Ejecutar `scripts/restore.sh --confirm RUTA_BACKUP`.
4. El script verifica checksums, detiene web/worker/beat, restaura PostgreSQL, reemplaza el volumen
   privado y reasigna sus archivos al usuario no privilegiado `app`; después aplica migraciones y
   ejecuta `verify_restore`.
5. `verify_restore` rechaza migraciones pendientes, catálogos ausentes/hash inválido, refresh tokens
   o credenciales de integración no descifrables, poco disco o live efectivo sin kill switch. Nunca
   imprime valores ni ciphertext.
6. Revisar dashboard, Gmail y health; completar el checklist live antes de cambiar barreras.

La restauración es destructiva sólo después de `--confirm`. Probarla periódicamente sobre una
instalación separada y conservar evidencia del resultado.

## 4. Disco lleno

Las cargas verifican `MIN_FREE_DISK_BYTES` antes de escribir y capturan ENOSPC. Un archivo parcial
se elimina y la UI informa que debe liberarse espacio. Ante alerta degradada:

1. mantener o activar el kill switch;
2. pausar campañas live;
3. no borrar catálogos referenciados ni datos de PostgreSQL manualmente;
4. liberar espacio en logs/backups externos o ampliar el volumen;
5. verificar `/health/degraded/` y ejecutar `python src/manage.py verify_restore`;
6. reanudar sólo después del preflight.

Si PostgreSQL reporta ENOSPC, detener workers/beat, recuperar espacio y seguir el procedimiento del
motor; no reencolar efectos Gmail hasta reconciliar estados `SENDING|RECONCILING`.

## 5. Proveedor no disponible y errores parciales

- Outscraper/LLM transitorio: queda `RETRY_WAIT` con deadline persistido y backoff acotado. Beat lo
  recupera tras reinicio.
- Fallo permanente de extracción: termina discovery como `FAILED_PROVIDER`; la campaña drena sólo
  la cola ya autorizada.
- Web caída/rechazada: crea snapshot fallback auditable y continúa sin inventar hechos.
- Gmail auth/permanente: degrada conexión y pausa; timeout ambiguo pasa a reconciliación por
  Message-ID, nunca a reenvío ciego.
- Fallo final visible: Jobs ofrece retry sólo para `OutboundMessage.SEND_FAILED`, con causa corregida
  obligatoria y reutilizando fila/idempotencia. Campañas cerradas, suprimidos, emails inválidos,
  catálogo roto o confirmación Gmail bloquean la acción.

## 6. Reinicios

Los mensajes Celery son señales. Las tareas vuelven a leer estado y los barridos reconstruyen runs,
pipeline, mensajes preparados, reconciliaciones y sync desde PostgreSQL. Después de un reinicio:

1. comprobar readiness;
2. revisar Jobs `RUNNING|RETRY_WAIT|FAILED`;
3. revisar campañas pausadas/errores parciales;
4. confirmar que no haya `SENDING` vencido fuera de reconciliación;
5. ejecutar `make smoke-worker`.
