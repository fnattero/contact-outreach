# Operación y recuperación

## 1. Señales operativas

PostgreSQL es la fuente de verdad. El dashboard deriva métricas desde campañas, enrollments,
Contactos, mensajes, decisiones y tareas; Redis no autoriza ni contabiliza envíos. Jobs expone
estado, intentos, heartbeat, próximo retry y error redactado para extracción, delivery, sync y
automatizaciones. Auditoría es append-only.

Las campañas nuevas descubren primero toda la audiencia y luego esperan aprobación. En el modo
predeterminado una confirmación congela audiencia, mensaje fijo, firma, PDFs, horario y actor; el
modo opcional por mensaje exige revisar cada destinatario antes de iniciar. `DRY_RUN` valida sin
Gmail y `LIVE` sólo entrega después de esa aprobación. La IA no participa del contacto inicial; sí
puede generar costo al analizar respuestas o preparar seguimientos de Contactos.

Los logs son JSON por stdout. `http.request_completed` incluye método, path sin query string,
status, duración y correlation ID. `storage.write_failed` identifica ENOSPC sin filename ni payload.
Los errores HTML muestran el correlation ID y no la excepción.

Health endpoints:

- `/health/live/`: proceso web vivo;
- `/health/ready/`: PostgreSQL y Redis disponibles;
- `/health/degraded/`: margen del volumen privado y estado/configuración local de Gmail,
  extractor, snapshot/cobertura Overture e IA. No llama proveedores remotos.

El volumen `private_catalogs` debe estar montado con lectura/escritura en `web` y sólo lectura en
`worker`. Si el worker no lo ve, la preparación MIME rechaza el catálogo como no disponible y el
mensaje permanece preparado para que el scheduler lo recupere después de corregir el montaje.

## 2. Backup

`scripts/backup.sh [directorio]` ejecuta `verify_restore`, genera `pg_dump --format=custom`, copia
el volumen `/app/private`, crea manifiesto y checksums, y publica el directorio sólo al finalizar.
Usa `umask 077`. Un fallo, incluido disco lleno, conserva el backup anterior y elimina sólo el
temporal de esa ejecución.

El dump contiene el catálogo Overture, sus manifests/licencias/atribución y ciphertext de
Integraciones, pero no `FIELD_ENCRYPTION_KEY`. Guardar esa raíz por
un canal cifrado separado. Un backup sin base, catálogos y clave no es restaurable; quien obtiene
sólo el dump no debe poder descifrar API keys, client secret ni refresh token.

## 3. Restore

1. Recuperar `.env` y la misma `FIELD_ENCRYPTION_KEY`.
2. Establecer `SEND_KILL_SWITCH=true`.
3. Ejecutar `scripts/restore.sh --confirm RUTA_BACKUP`.
4. El script verifica checksums, detiene web/worker/maintenance/beat, restaura PostgreSQL, reemplaza
   el volumen privado y reasigna sus archivos al usuario no privilegiado `app`; después aplica
   migraciones y ejecuta `verify_restore`.
5. `verify_restore` rechaza migraciones pendientes, catálogos PDF ausentes/hash inválido, refresh
   tokens o credenciales de integración no descifrables, snapshots no `READY` usados por campañas
   activas, poco disco o live efectivo sin kill switch. Nunca imprime valores ni ciphertext.
6. Revisar manualmente en **Datos de búsqueda** los hashes de cobertura, conteos, fuentes, licencias,
   notices y atribución restaurados; después revisar Gmail y health y completar el checklist live.

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

- Consulta Overture local o LLM transitoria: queda `RETRY_WAIT` con deadline persistido y backoff
  acotado. Beat la recupera tras reinicio con el mismo snapshot, cursor e idempotency key.
- Import Overture fallido: la partición nueva queda `FAILED`, la cobertura anterior sigue disponible y
  sólo un nuevo POST explícito puede reintentar la sincronización.
- Fallo permanente de extracción: la campaña queda pausada o en error con una acción concreta;
  nunca autoriza una audiencia incompleta silenciosamente.
- Web caída/rechazada: conserva procedencia y continúa únicamente con emails validados; nunca
  inventa hechos.
- Gmail auth/permanente: degrada conexión y pausa; timeout ambiguo pasa a reconciliación por
  Message-ID, nunca a reenvío ciego.
- Fallo final visible: Jobs ofrece retry sólo para efectos recuperables, con causa corregida y la
  misma fila/idempotencia. Campañas cerradas, Contactos/restricciones, emails inválidos, PDFs rotos,
  tareas humanas o confirmación Gmail bloquean la acción.

## 6. Reinicios

Los mensajes Celery son señales. Las tareas vuelven a leer estado y los barridos reconstruyen runs,
pipeline, mensajes preparados, decisiones automáticas, seguimientos, notificaciones,
reconciliaciones y sync desde PostgreSQL. Después de un reinicio:

1. comprobar readiness;
2. revisar Jobs `RUNNING|RETRY_WAIT|FAILED`;
3. revisar campañas pausadas/errores parciales;
4. confirmar que no haya `SENDING` vencido fuera de reconciliación;
5. ejecutar `make smoke-worker`.

## 7. Sincronización y retención Overture

Beat comprueba diariamente metadata del catálogo STAC oficial y persiste el resultado; no descarga
lugares. El operador revisa provincias y espacio disponible, abre **Datos de búsqueda** y solicita
la actualización de cada cobertura necesaria. El servidor vuelve a validar el release y la
provincia. La tarea corre únicamente en `maintenance` con concurrencia uno y no acepta URL,
credencial ni bucket enviado por el navegador.

Durante el import, revisar release, provincia, progreso por lotes, cantidad transmitida/aceptada,
errores de validación y límite de 500.000 lugares. `FAILED` nunca reemplaza una partición READY.
Antes de descubrir una campaña, confirmar que todas sus provincias estén READY dentro del mismo
release, con hashes, fuentes, licencias, atribución y conteos. Se conserva toda cobertura fijada por
una campaña; la limpieza sólo elimina particiones históricas sin referencias.

Una edición de zona produce un hash nuevo. Las campañas ya iniciadas conservan su geometría; las
nuevas fallan el preflight hasta tener una partición READY que cubra esa provincia y geometría.

## 8. Cutover desde el proveedor retirado

1. Activar `SEND_KILL_SWITCH=true` y hacer un backup verificado de PostgreSQL/catálogos.
2. Revocar la clave del proveedor retirado; quitarla de `.env`, del gestor de secretos y de copias
   operativas, junto con todas sus variables históricas. Borrarla localmente sin revocarla no
   invalida la credencial.
3. Desplegar y ejecutar las migraciones forward. Borradores pasan a Overture; runs no terminados
   quedan terminales con motivo auditado `provider_retired`; resultados, costos y auditoría
   históricos completados permanecen sólo lectura.
4. Confirmar/subir límites GeoJSON y sincronizar la última versión Overture desde el dashboard.
5. Revisar cobertura/hashes, conteos, contactos, fuentes, licencias, notices, atribución y health.
6. Ejecutar una campaña pequeña `REVIEW_ONLY` e inspeccionar todos los emails antes de ampliar uso.
