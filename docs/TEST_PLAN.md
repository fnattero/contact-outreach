# Plan de pruebas

## 1. Estrategia

pytest y pytest-django ejecutan tests unitarios, de integración con PostgreSQL y E2E HTTP/HTMX. Todos los proveedores tienen fakes; `pytest-socket` bloquea red excepto la base de test explícitamente permitida. HTTP se simula con `respx`, DNS mediante resolver fake y Celery corre eager o con worker de test según escenario.

Objetivo inicial: 85% de cobertura de líneas total y 95% en elegibilidad, supresión, estados, SSRF, MIME/idempotencia y scheduler. Cobertura no sustituye casos negativos ni revisión de invariantes.

Comandos futuros:

```bash
make lint       # Ruff check + format --check
make typecheck  # mypy con django-stubs
make test       # suite completa sin red
make test-e2e   # flujo con proveedores fake
make check      # lint + typecheck + test + migraciones pendientes
```

## 2. Unitarias de dominio

- Normalización de emails Unicode/IDNA, case, espacios y preservación de local part.
- Exclusión de noreply/no-reply/abuse/privacy y patrones configurados.
- MX válido, null MX, NXDOMAIN, fallback A/AAAA, timeout y SERVFAIL.
- Selección principal por flag, dominio, rol y orden; cero o un primario.
- Claves de dedupe: todos los emails validados, provider ID, dominio empresarial y
  nombre+dirección; excluir dominios gratuitos/compartidos y cubrir emails secundarios compartidos.
- Recovery reconstruye desde campaña/query la siguiente extracción si se perdió el mensaje Redis;
  pausa/cancelación serializan submit/poll y no permiten efectos posteriores.
- Reglas de recontacto: contacto previo, override consumible, baja y bounce no anulables.
- Todas las transiciones válidas e inválidas de las cuatro máquinas.
- Contadores derivados y clasificación de respuesta humana/automática.

## 3. Integraciones aisladas

### Extractor

Payload completo, campos faltantes, JSON desconocido, paginación/async, mismo request ID, 429/403, timeout, error permanente, persistencia cruda antes del parseo y cap de registros/costo.

### WebsiteFetcher y prompt injection

Bloquear localhost, IPv4/IPv6 privadas, link-local, CGNAT, metadata cloud, userinfo, puertos, esquemas y redirects público-a-privado. Probar DNS rebinding con IP fijado, cadena/loop de redirects, timeout, contenido no HTML y oversize. Verificar máximo cuatro páginas y limpieza.

Inyectar instrucciones web como “ignore previous instructions”, JSON falso y pedidos de envío; demostrar que quedan como texto, no cambian prompt, herramientas ni campos permitidos.

### IA

Validar score/confidence bounds, JSON inválido, evidence inexistente, cuerpo final compuesto de 69/70/130/131 palabras, emoji, claims prohibidos, CTA, prefijo/firma/BAJA, caché por hash y cambio de prompt/modelo. Confirmar un llamado lógico, retries limitados y ausencia de plantilla fallback.

### Gmail

Parsear MIME con librería estándar: texto plano UTF-8, un To, sin CC/BCC, PDF correcto, headers, Message-ID estable y tamaño. Probar respuesta con threadId/References/In-Reply-To, 403/429, auth revocada, timeout ambiguo y reconciliación sin segundo envío.

## 4. Integración con base y workers

- Conflictos concurrentes creando la misma identidad/email producen un prospecto canónico; dos campañas sobre el mismo `ContactLedger` asignan una sola autorización.
- Dos workers no reservan el mismo slot de objetivo, costo, mensaje o cupo diario.
- Límite diario respeta timezone y cambio de día; intervalo y ventana laboral bloquean correctamente.
- Pausar/cancelar durante tasks impide efectos posteriores; reanudar continúa desde checkpoints. Agotar queries/raw/costo cierra descubrimiento pero permite drenar la cola ya autorizada.
- Reinicio con `SENDING`, `RECONCILING`, job sin heartbeat y Redis vacío reconstruye trabajo sin duplicar.
- Catálogo borrado/alterado pausa campaña; versión anterior sigue referenciada.
- Kill switch y dry-run prevalecen incluso con campaña live.
- Umbrales de bounce/failure pausan exactamente al cruzar el límite.

## 5. Respuestas, supresión y seguridad

- Conexión inicial fija baseline sin importar históricos; history incremental paginado, cursor sólo tras commit, mensajes repetidos e history 404 con fallback limitado.
- Cursor legacy vacío inicializa baseline sin importar; fallback captura baseline antes de listar para no perder llegadas concurrentes; fallos auth/permanentes degradan la conexión.
- Asociación por Gmail thread ID, Message-ID, References e In-Reply-To; ignorar mensajes ajenos.
- BAJA en mayúsculas/minúsculas y frases equivalentes crea supresión permanente antes de construir o invocar IA, incluso si el proveedor está mal configurado.
- Bounce invalida email; auto-reply no marca respuesta humana; clasificación IA fallida queda OTHER.
- Respuesta manual no sale con GET, sin login, sin CSRF ni sin clic; doble POST conserva un envío. El worker revalida una supresión tardía y una ambigüedad se reconcilia por Message-ID sin permitir otra fila para el mismo inbound.
- Headers RFC sobredimensionados no envenenan el cursor y cuerpos de texto detached se recuperan sin importar archivos adjuntos.
- Sanitización de HTML/email, filenames/path traversal, PDF falso/oversize y CSV formula injection.
- Tokens/API keys/cuerpos no aparecen en logs, errores, auditoría ni snapshots mostrados.
- Permisos: usuario anónimo sólo accede login/health permitido; archivos privados requieren sesión.

## 6. E2E fake

Escenario principal:

1. Bootstrap de propietario y seeds; login.
2. Configurar perfil, cargar PDF fixture y crear campaña objetivo pequeño.
3. Fake extractor devuelve válidos, sin email, duplicado e irrelevante.
4. Fake web/LLM produce calificados y un error reintentable.
5. Ejecutar dry-run, validar contadores y cero llamadas Gmail reales.
6. Habilitar live sólo dentro del fake, enviar respetando cuota y simular reinicio.
7. Inyectar interesado, BAJA, auto-reply y bounce; sincronizar y verificar dashboard.
8. Enviar una respuesta manual fake en el hilo y exportar CSV seguro.

## 7. Quality gates

- `ruff check`, `ruff format --check` y mypy sin errores en código propio.
- `makemigrations --check --dry-run` no detecta migraciones faltantes.
- Suite con red bloqueada y sin credenciales externas.
- Tests críticos no pueden marcarse xfail/skip salvo issue y vencimiento explícitos.
- Toda corrección de idempotencia, supresión o seguridad agrega regresión específica.
