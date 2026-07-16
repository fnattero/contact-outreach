# Especificación de producto

## 1. Propósito

Contact Outreach es una aplicación web local, de un solo usuario, para prospectar compradores B2B de carbones para motores. El operador define e inicia una campaña desde un dashboard; el sistema descubre negocios, valida un email principal, enriquece datos públicos, evalúa relevancia con IA, prepara un email de texto plano con un catálogo PDF y lo envía desde una única cuenta de Gmail. Las respuestas se sincronizan y clasifican, pero cualquier contestación posterior requiere una acción humana explícita.

La primera operación objetivo es CABA, Argentina, con 300 prospectos únicos, relevantes y con email. Ubicación, rubros, zonas, objetivo y límites operativos son configurables.

## 2. Usuario y principios

- **Usuario:** propietario u operador comercial único, autenticado localmente.
- **Automatización acotada:** iniciar una campaña autoriza el pipeline y el primer email, no respuestas futuras.
- **Dry-run primero:** ninguna instalación nueva envía correo real hasta habilitar `SEND_MODE=live`, desactivar el kill switch y conectar Gmail.
- **No evasión:** no se realiza scraping directo de Google Maps, CAPTCHA, proxies, rotación de cuentas ni elusión de cuotas.
- **Trazabilidad:** decisiones, cambios de estado, consumo de proveedores y mensajes quedan auditados.

## 3. Requisitos funcionales

### FR-01 Descubrimiento y extracción

El operador selecciona ubicación, rubros y zonas al crear la campaña. Al iniciarla se generan consultas determinísticas y se invoca `ExtractorProvider`, inicialmente Outscraper. La extracción no se ejecuta en segundo plano fuera de una campaña activa. Se conserva por ejecución la solicitud, respuesta JSON cruda, proveedor, identificadores, fechas, cantidades, costo estimado/real y error sanitizado. `MockExtractorProvider` permite desarrollo sin red.

El pipeline ignora registros sin email. Normaliza y valida sintaxis, consulta MX mediante DNS y elige como máximo un email principal. Excluye direcciones `noreply`, `no-reply`, `abuse`, `privacy` y patrones no comerciales documentados. Deduplica por email, dominio empresarial, identificador del proveedor y hash de nombre más dirección. Dominios gratuitos o compartidos no se usan solos para fusionar negocios.

### FR-02 Objetivo y límites de campaña

El objetivo cuenta prospectos únicos con email válido y `relevance_score` igual o superior al umbral, no resultados crudos. Se recorren consultas hasta alcanzarlo o agotar consultas, máximo crudo, límite de costo o error permanente del extractor. No se despachan nuevas consultas cuando reservas e items ya calificados pueden completar el objetivo.

Alcanzar un límite termina la etapa de descubrimiento, no descarta los mensajes ya calificados: la campaña permanece operativa hasta drenar o cancelar su cola autorizada y conserva el motivo exacto de cierre de extracción.

El dashboard muestra por separado: crudos, con email, duplicados, irrelevantes, calificados, en cola, enviados, fallidos, respondidos, interesados y, en dry-run, simulados. Los contadores son consultas derivadas de datos persistidos, no incrementos en Redis.

### FR-03 Rubros y zonas

`SearchCategory` y `SearchZone` se pueden crear, editar, activar, desactivar y eliminar cuando no exista una referencia protegida; en caso contrario se archivan. Los seeds incluyen los 23 rubros solicitados y los 48 barrios oficiales de CABA, enumerados en `ASSUMPTIONS.md`. Una campaña conserva un snapshot de nombres y criterios aunque luego cambie la configuración.

### FR-04 Enriquecimiento web

Si existe sitio, `WebsiteFetcher` obtiene la home y hasta tres páginas internas relevantes sin ejecutar JavaScript. Sólo admite HTTP/HTTPS, valida DNS y cada redirección contra SSRF, limita puertos, tiempo, tamaño, redirecciones y páginas. Extrae contenido textual tras eliminar scripts, estilos, navegación repetitiva y ruido.

El contenido se trata como dato hostil: nunca puede modificar instrucciones del sistema ni activar herramientas. Se guarda URL solicitada/final, fecha, estado, hash y extracto exacto usado por IA. Un fallo deja continuar con datos del extractor.

### FR-05 Análisis y generación con IA

Una llamada lógica por prospecto, con reintentos limitados, produce JSON validado por Pydantic: `relevance_score` 0–100, `confidence` 0–1, `relevance_reason`, `evidence[]`, `subject` y `body_text`. Las evidencias deben corresponder a hechos suministrados. Existen `MockLLMProvider`, `OllamaProvider` y `OpenAICompatibleProvider` con base URL, modelo y API key externa al repositorio.

El mensaje final compuesto usa español argentino, tono profesional y directo, 70–130 palabras incluyendo firma y BAJA, texto plano, sin emojis, tracking, afirmaciones inventadas, descuentos falsos ni marketing vacío. Sólo menciona contexto explícito; no dice haber visto una web sin evidencia. Explica una relación posible con carbones para motores sin asumir compra y pregunta únicamente qué día conviene que pase el vendedor. Agrega identidad, firma e instrucción clara para responder `BAJA`; el prefijo inicial de asunto es `PUBLICIDAD -`.

Por debajo del umbral se marca irrelevante y no se envía. Tras agotar reintentos se marca `ERROR`; no existe fallback genérico. El caché se identifica por hash canónico de input, versión de prompt/esquema, proveedor y modelo. Se persisten input hash, output validado, versión, proveedor, modelo y fecha.

### FR-06 Perfil comercial y configuración

El dashboard edita empresa, vendedor, teléfono, WhatsApp, descripción, productos, diferenciadores, dirección, web, firma, instrucciones adicionales y umbral. También configura ubicación, límites, proveedor/modelo y parámetros no secretos. API keys permanecen en variables de entorno y sólo se muestra su estado redactado.

### FR-07 Ciclo de campaña

Una campaña nace borrador y sólo procesa al pulsar Iniciar. Puede pausarse, reanudarse o cancelarse. El trabajo sobrevive reinicios porque PostgreSQL conserva checkpoints y Celery sólo transporta señales. Pausar o cancelar impide tomar nuevas tareas; las tareas en curso verifican el estado antes de producir efectos externos. No hay aprobación individual ni seguimientos automáticos.

La campaña guarda snapshots de objetivo, consultas, perfil, prompt, límites, modo y versión del catálogo. Una anulación manual, justificada y auditada, puede permitir recontactar un email previamente enviado, pero nunca uno dado de baja o marcado inválido.

### FR-08 Política de envío

Sólo se envía un primer mensaje por autorización, con un destinatario y sin CC/BCC. Se respetan límite diario, intervalo, zona horaria y ventana laboral. Los 403/429 y errores transitorios aplican backoff exponencial acotado; autenticación inválida, catálogo inconsistente y tasas anormales pausan la campaña con motivo visible. Un kill switch global prevalece sobre toda configuración.

### FR-09 Catálogos PDF

El usuario carga y selecciona versiones inmutables de catálogo. Se validan extensión, MIME detectado, encabezado `%PDF-`, tamaño y SHA-256; el archivo vive en almacenamiento privado. Se muestran nombre, tamaño, fecha, versión y hash. El máximo inicial es 15 MiB. Cada campaña referencia una versión exacta y se pausa si falta o su hash ya no coincide.

### FR-10 Gmail y primer envío

Una página permite conectar, inspeccionar, probar y desconectar una sola cuenta personal Gmail mediante OAuth 2.0. No se solicita ni almacena contraseña. Se generan MIME RFC válidos con cuerpo texto plano y PDF, `Message-ID` determinístico y cabeceras internas opacas. Se guardan recipient, subject, fechas, `gmail_message_id` y `gmail_thread_id`.

Antes de reintentar un envío ambiguo se reconcilia por `Message-ID` y datos persistidos. No se rotan cuentas ni se evaden límites de Gmail.

### FR-11 Sincronización de respuestas

Mientras la aplicación funciona, Celery Beat sincroniza incrementalmente mediante `historyId`. Si expiró, ejecuta un fallback limitado por fecha y cantidad. Sólo persiste mensajes cuyos thread IDs o cabeceras RFC correspondan a mensajes propios. Guarda texto, HTML sanitizado, headers permitidos y metadatos necesarios; no importa toda la bandeja ni adjuntos ajenos. El dashboard muestra el hilo completo relacionado.

### FR-12 Clasificación y supresión

Las clases son `INTERESTED`, `NOT_INTERESTED`, `UNSUBSCRIBE`, `AUTO_REPLY`, `BOUNCE` y `OTHER`. Reglas determinísticas detectan BAJA y rebotes antes de IA; la IA puede clasificar lo restante, nunca responder. Un fallo de clasificación queda `OTHER` para revisión.

`UNSUBSCRIBE` crea una supresión permanente global. `BOUNCE` invalida el email. Una respuesta humana (`INTERESTED`, `NOT_INTERESTED`, `UNSUBSCRIBE` u `OTHER`) marca respondido; `AUTO_REPLY` y `BOUNCE` no. Toda elegibilidad de envío consulta primero supresiones e invalidez.

### FR-13 Respuesta manual

Desde el hilo se muestra destinatario, asunto, historial y editor de texto. Sólo se autoriza al pulsar `Enviar respuesta`; el POST persiste texto, usuario e idempotency key y un worker ejecuta exclusivamente ese efecto durable dentro del `gmail_thread_id`, conservando asunto e incluyendo `References` e `In-Reply-To`. Los resultados ambiguos se reconcilian por `Message-ID` antes de permitir otro intento. Ninguna tarea genera texto, crea autorizaciones ni responde sin esa acción humana explícita.

### FR-14 Dashboard y exportaciones

El dashboard Django/HTMX incluye resumen, campañas, creación/progreso, prospectos, preparados, enviados, respuestas, interesados, no interesados, bajas, errores, rubros, zonas, extracción, IA, Gmail, perfil, catálogos, supresiones y auditoría. Filtra por campaña, estado, rubro, barrio, fecha, relevancia, respuesta e interés. Exporta CSV seguro de prospectos, enviados y respuestas, neutralizando fórmulas de planilla.

### FR-15 Estados, idempotencia y auditoría

Servicios de dominio validan transiciones; las vistas y tareas no asignan estados directamente. Se usan restricciones únicas, claves idempotentes, transacciones y `SELECT ... FOR UPDATE`/advisory locks para impedir concurrencia sobre prospecto, mensaje o sincronización. `AuditEvent` es append-only y registra actor, acción, entidad, antes/después redactado, fecha y correlación.

### FR-16 Seguridad local

No hay registro público. Un usuario propietario se inicializa desde variables de entorno y la contraseña sólo se guarda hasheada. Django provee sesiones, CSRF y hashing Argon2. La web escucha por defecto en `127.0.0.1`; exposición externa exige configuración explícita, TLS en proxy y cookies seguras. Se validan uploads y contenido externo, se cifran tokens, se redactan secretos y se aplican límites a todas las integraciones.

### FR-17 Abstracciones y modo fake

Extractor, IA, Gmail y fetch web viven detrás de interfaces, con implementaciones fake determinísticas. Ningún test requiere credenciales ni red real. Un flujo E2E fake recorre descubrimiento, análisis, dry-run, respuesta simulada, clasificación y respuesta manual simulada.

## 4. Requisitos operativos y de calidad

- **OPS-01:** Python 3.12+, Django 5.2 LTS, PostgreSQL, Celery, Redis, Docker Compose, pytest, Ruff, mypy con `django-stubs` y migraciones versionadas; sin Node salvo necesidad demostrada.
- **OPS-02:** entregar en fases posteriores Dockerfile, Compose, `.env.example`, Makefile, README, backups, restore, health checks, seeds, demo y comandos `make lint`, `make test`, `make typecheck`.
- **DM-01:** persistir y relacionar como mínimo `User`, `BusinessProfile`, `SearchCategory`, `SearchZone`, `SearchQuery`, `SearchRun`, `Prospect`, `ProspectEmail`, `WebsiteSnapshot`, `AIAnalysis`, `Campaign`, `Catalog`, `OutboundMessage`, `InboundMessage`, `SuppressionEntry`, `ProviderUsage`, `AuditEvent` y `BackgroundJob`, con las extensiones de integridad documentadas en `DATA_MODEL.md`.
- **QA-01:** cubrir normalización, deduplicación, email/MX, IA, prompt injection, SSRF, MIME, cuotas, reinicios, estados, idempotencia, bajas, hilos, respuesta manual, permisos, CSRF y E2E fake.

## 5. Fuera de alcance

SPA, multiusuario, CRM completo, seguimiento automático, aprobación individual, respuestas automáticas, HTML saliente, tracking, verificación de email paga, scraping directo, browser headless, SMTP con contraseña y exposición pública lista para Internet.

## 6. Criterios de aceptación del producto

Una instalación limpia arranca en dry-run, permite configurar y ejecutar una campaña fake hasta el objetivo, sobrevive un reinicio sin repetir efectos, muestra trazabilidad completa y procesa una respuesta simulada sin enviarla automáticamente. El modo live no arranca una entrega si faltan Gmail, identidad legal, catálogo íntegro o controles de seguridad.
