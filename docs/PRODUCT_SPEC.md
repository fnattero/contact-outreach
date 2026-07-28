# Especificación de producto

## 1. Propósito y alcance

Contact Outreach es una aplicación web de una sola empresa para encontrar compradores B2B de
carbones para motores, enviar campañas desde Gmail y concentrar las relaciones comerciales en
`Contactos`. Es un monolito modular Django/HTMX preparado para publicarse detrás de HTTPS y para
varios usuarios de la misma empresa; no es un SaaS multiempresa y no ofrece registro público.

Una campaña busca organizaciones por rubros y zonas de una o más provincias, prepara para toda la
audiencia un mensaje fijo previamente aprobado, adjunta uno o más catálogos PDF y puede enviar un
único recordatorio si no hubo respuesta. Una respuesta humana convierte a la organización en
Contacto y la excluye de campañas futuras. Desde allí se muestra toda la conversación, incluso si
usa varias direcciones o hilos.

La IA no redacta ni decide la audiencia del contacto inicial. Sólo analiza respuestas ya
persistidas o genera comunicaciones programadas con Contactos. Puede contestar en modo live
únicamente intenciones expresamente permitidas y fundamentadas; no tiene herramientas ni acceso a
Gmail. Los servicios de dominio validan su decisión y ejecutan efectos idempotentes. Todo lo que
requiere criterio comercial o contiene riesgo crea una tarea humana.

## 2. Usuarios y principios

- Existe un único `Workspace` (empresa) con varios `User` de Django vinculados mediante
  `Membership`.
- `ADMIN` administra usuarios, configuración, campañas, aprobaciones, respuestas, Contactos,
  conocimiento y automatización.
- `VENDEDOR` sólo lee Resumen, campañas, emails enviados, Contactos y conversaciones. No ve
  borradores, prospectos, exportaciones, PDFs, configuración, integraciones, jobs, auditoría ni
  detalles técnicos; no puede mutar nada salvo cerrar su propia sesión.
- No hay registro ni recuperación pública. Un admin crea la cuenta y entrega una única vez un link
  de activación/reset válido por 24 horas.
- Instalaciones nuevas usan dry-run, kill switches activos y decisiones de respuesta en `SHADOW`.
- El lenguaje visible es español simple. La pantalla explica consecuencias, errores y próximos
  pasos; color nunca es la única señal.
- PostgreSQL es la fuente de verdad. Redis y Celery sólo transportan trabajo recuperable.
- No se raspa Google Maps ni se evaden cuotas, controles antiabuso o restricciones de Gmail.

## 3. Requisitos funcionales

### FR-01 Workspace, roles y autenticación

La instalación crea un `Workspace` y migra al propietario existente como primer `ADMIN`. Perfil,
integraciones, prompts, conexión Gmail, campañas y catálogos pertenecen al Workspace; los campos
de creador, editor, aprobador o uploader sólo atribuyen acciones y nunca delimitan autorización.
Un segundo admin puede operar campañas creadas por otro.

Las capacidades se validan en vistas y servicios centrales. No se elimina un usuario con historia
ni se puede desactivar o degradar al último admin activo. Cambiar rol o desactivar invalida sus
sesiones activas.

El login aplica un lockout fijo. Los intentos 1–4 devuelven error neutro; el quinto fija un bloqueo
de 30 minutos. La clave es HMAC de username normalizado más IP de cliente confiable. Además, una IP
queda bloqueada tras 20 fallos sobre cualquier username dentro de 30 minutos. Un intento mientras
está bloqueado devuelve HTTP 429 y `Retry-After` sin extender la espera; un login correcto limpia
el par. Un admin puede desbloquear desde UI y existe un comando de emergencia.

Los administradores deben enrolar TOTP con `django-otp`; vendedores pueden hacerlo. Se generan diez
códigos de recuperación de un solo uso, guardados únicamente como hashes. El alta, reset, cambio de
rol, desactivación, desbloqueo y uso de recuperación quedan auditados sin secretos.

### FR-02 Organizaciones, direcciones y Contactos

`Organization` representa una empresa deduplicada dentro del Workspace. Conserva identidades
globales mediante `OrganizationIdentity` (GERS ID, dominio y hashes de nombre/dirección).
`EmailAddress` permite varias direcciones validadas por organización, con etiqueta, preferencia,
proveniencia, estado de validez y evidencia.

`CampaignEnrollment` vincula organización, campaña y dirección elegida. Un `Contact` uno-a-uno se
crea al recibir una respuesta humana genuina, al cargarlo manualmente o al aplicar una restricción
manual. La carga manual sólo exige email; nombre y organización pueden completarse después. Un
Contacto excluye a toda la organización de nuevas campañas, sin importar qué dirección respondió.
Los emails agregados manualmente quedan excluidos de campañas desde el primer momento y se validan
por MX en segundo plano. La UI muestra “Validación pendiente” y permite reintentar sin bloquear la
pantalla; sólo un resultado válido habilita comunicaciones programadas.

`CommunicationRestriction` reúne baja, rebote y “No contactar”. “No contactar este contacto”
bloquea todas sus direcciones; “No usar este email” bloquea sólo ese canal. `UNSUBSCRIBE` es
irreversible. Un bounce invalida sólo la dirección. Una restricción manual sólo puede revertirla un
admin con motivo auditado. La antigua página Supresiones desaparece, pero las barreras y su
historial permanecen dentro de Contactos.

`Conversation` representa un Contacto más un hilo Gmail. Un Contacto puede tener varios hilos y
direcciones; una propuesta redirigida abre un hilo nuevo y ambos aparecen en una misma cronología.

### FR-03 Provincias, partidos/departamentos y zonas

`SearchZone` forma una jerarquía con código oficial, nivel, padre, provincia, fuente, atribución y
flag seleccionable. Los nombres sólo son únicos bajo el mismo padre/código, porque pueden repetirse
entre provincias. Se cargan geometrías oficiales versionadas para todas las provincias y sus
partidos, departamentos o comunas. En CABA el nivel elegible sigue siendo Barrio. Las zonas custom
se conservan.

Al crear campaña el admin puede elegir una o más provincias, buscar y expandir sus distritos,
seleccionar todo o limpiar por provincia. La UI usa las etiquetas “Partidos”, “Departamentos”,
“Comunas” o “Barrios” y oculta jerga geométrica. Una campaña congela nombres, códigos, geometrías,
hashes, reglas y orden.

### FR-04 Cobertura Overture particionada

`OvertureRelease` conserva la identidad y metadatos inmutables de un release. Cada
`OvertureCoveragePartition` contiene la cobertura de una provincia y evoluciona de forma
independiente hasta `READY`. Una campaña sólo combina particiones del mismo release y cada
`SearchQuery` usa la partición que contiene su distrito.

El importador oficial ejecuta una lectura acotada al bbox de una provincia y luego filtra
exactamente por polígonos de distrito. Nunca realiza una lectura gigante para provincias lejanas.
El snapshot activo actual se migra como cobertura CABA; cualquier cobertura legacy inesperada se
preserva como histórica. Si falta cobertura READY, la campaña falla antes de descubrir con una
explicación accionable, por ejemplo: “Faltan los datos de Mendoza. Actualizalos desde Datos de
búsqueda.”

### FR-05 Descubrimiento y elegibilidad de audiencia

Los rubros mantienen reglas deterministas versionadas. La búsqueda consulta sólo los catálogos
Overture locales fijados, página con orden/cursor estable y persistencia idempotente. Los sitios
oficiales se consultan con `WebsiteFetcher` SSRF-safe sólo para encontrar emails publicados cuando
Overture no ofrece uno útil. No se infieren direcciones.

Las identidades descubiertas se resuelven hacia `Organization` y `EmailAddress`; los datos
históricos de `Prospect` y `AIAnalysis` permanecen legibles mientras dura el cutover. Nuevas
campañas no invocan LLM para relevancia ni redacción. Su objetivo cuenta enrollments únicos con una
dirección elegible y un mensaje inicial preparado. Se revalida elegibilidad al preparar, aprobar,
encolar y justo antes de Gmail: dirección válida, sin Contacto de la organización, sin
restricciones, campaña/mode correctos, barreras Gmail y adjuntos íntegros.

### FR-06 Contenido fijo, ciclo y aprobación de campaña

El Workspace conserva revisiones versionadas del contenido por defecto, sin placeholders de
cliente. La revisión inicial seed es:

- Asunto inicial: `Propuesta comercial`
- Cuerpo inicial:

  ```text
  Buen día:

  Nos ponemos en contacto para acercarle nuestra propuesta de carbones para motores y compartir nuestros catálogos. Trabajamos con distintas medidas y aplicaciones para motores y herramientas eléctricas. Si le resulta de interés, puede responder este correo y con gusto ampliaremos la información.

  Saludos.
  ```

- Cuerpo de recordatorio:

  ```text
  Buen día:

  Retomamos nuestro correo anterior para saber si pudo revisar la propuesta y los catálogos. Si necesita información sobre alguna medida o aplicación, puede responder este mensaje.

  Saludos.
  ```

- Asunto de propuesta referida: `Propuesta comercial`
- Cuerpo de propuesta referida:

  ```text
  Buen día:

  Nos indicaron que esta es la dirección adecuada para enviar nuestra propuesta comercial. Adjuntamos la información y los catálogos correspondientes. Quedamos a disposición ante cualquier consulta.

  Saludos.
  ```

La firma aprobada de `BusinessProfile` se agrega en código de forma determinista e idéntica para
todos. Crear/iniciar una campaña congela cuerpos, firma y revisiones.

El ciclo nuevo es `DRAFT -> DISCOVERING -> AWAITING_APPROVAL -> RUNNING -> COMPLETED`, con
`PAUSED`, `CANCELLED` y `STOPPED_ERROR`. El descubrimiento concluye antes de aprobar para que el
admin vea la audiencia final.

`CAMPAIGN` es el modo de aprobación por defecto: una confirmación congela hash de audiencia,
contenido/firma exactos, adjuntos, agenda, actor y fecha, y encola cada mensaje que siga elegible.
`PER_MESSAGE` permite revisar/editar mensajes individuales y luego exige iniciar la entrega; los
no aprobados se excluyen. Campañas históricas y sus análisis IA nunca se regeneran ni reenvían.

### FR-07 Múltiples catálogos PDF

Una campaña selecciona una colección ordenada de `CampaignAttachment`; cada mensaje conserva una
colección inmutable `OutboundAttachment`. El inicial y la propuesta referida requieren los PDFs de
la campaña. Recordatorios y respuestas conversacionales no adjuntan por defecto.

Cada PDF mantiene el límite de 15 MiB. La suma de fuentes no puede superar 17 MiB y el MIME final
serializado no puede superar 24 MiB. Falta, tamaño/hash cambiado o cualquier archivo inconsistente
pausa el envío; jamás se envía un subconjunto parcial.

### FR-08 Envío y protección contra duplicados diarios

`OutboundMessage.kind` admite `INITIAL`, `CAMPAIGN_REMINDER`, `MANUAL_REPLY`, `AUTOMATIC_REPLY`,
`REFERRED_PROPOSAL`, `REDIRECT_ACK` y `SCHEDULED_CONTACT`. Los servicios Gmail mantienen
Message-ID determinista, reconciliación antes de reintento y los métodos ordinarios `send`/`reply`.

`CampaignDeliveryReservation(email, local_date)` es única por dirección y fecha en
`America/Argentina/Buenos_Aires`. Los iniciales y recordatorios de campañas reservan
transaccionalmente. Ante conflicto el mensaje pasa al siguiente día/ventana permitidos y la UI
muestra: “Se pasó al próximo día permitido para evitar correos duplicados.” No existe cooldown
adicional entre campañas y una dirección sin respuesta puede participar en una campaña posterior.
Las respuestas y comunicaciones de Contactos no usan esta reserva de campaña.

Ningún efecto live ocurre salvo `SEND_MODE=live`, kill switch de envío desactivado, campaña
autorizada, cuotas/horario válidos y Gmail conectado. Una ambigüedad se reconcilia por Message-ID;
nunca se reenvía a ciegas.

### FR-09 Un recordatorio sin respuesta

Cada campaña puede habilitar un único recordatorio, con demora default de tres días calendario
desde `sent_at` confirmado. El vencimiento se mueve a la siguiente ventana laboral de la campaña.
Se responde en el hilo original, preservando Subject, References e In-Reply-To.

Respuesta humana genuina, Contacto manual, baja o bounce cancelan el recordatorio; auto-reply no.
La campaña sólo completa cuando cada recordatorio posible fue enviado, cancelado o quedó
inelegible. Preparación, reserva, ejecución y cancelación son idempotentes.

### FR-10 Gmail, respuestas y promoción a Contacto

Beat sincroniza por `historyId` cada minuto por defecto y usa el fallback acotado ya documentado
si expiró. Primero persiste y asocia el mensaje; aplica bounce, baja y auto-reply determinísticos;
luego, con `transaction.on_commit`, encola el análisis IA. Nunca sostiene el lock de sincronización
durante una llamada al LLM.

Una respuesta humana genuina crea/promueve el Contacto y cancela recordatorios pendientes. Una
baja humana crea el Contacto con estado “Baja solicitada”. Bounce y auto-reply se guardan como
eventos pero no crean Contacto por sí solos. El hilo conserva mensaje original, padres, Gmail/RFC
IDs y relación con campaña sin modificar historia.

### FR-11 Conocimiento, candidatos y contexto acotado

El admin gestiona dos tipos de información desde “Información para responder consultas”:

- contexto general aprobado, que se agrega siempre y sirve como background estable de la empresa,
  tono y límites;
- datos puntuales/FAQ aprobados, que son tarjetas cortas usadas para responder consultas
  concretas.

Sólo una revisión explícitamente aprobada puede fundamentar una respuesta; no se extraen hechos de
PDFs automáticamente.

Antes del LLM se extraen como máximo diez emails literales de texto plano y `mailto:`. Se
normalizan/validan y marcan `NEW_CONTENT`, `SIGNATURE` o `QUOTED`; no se reconstruyen direcciones
ofuscadas. El modelo sólo puede seleccionar IDs entregados en esa solicitud.

Cada solicitud tiene máximo 24.000 caracteres de entrada. Siempre incluye completo: nuevo texto
escrito por el remitente, mensaje inicial/referido original, padre directo y contexto general
aprobado vigente. Después agrega hasta seis mensajes recientes relevantes del Contacto entre hilos,
memoria estructurada con fuentes para historia antigua y hasta tres revisiones aprobadas elegidas
por búsqueda semántica con embeddings. Si la similitud es baja o varias tarjetas compiten de forma
ambigua, no se agregan datos puntuales y la decisión debe escalar a humano cuando necesita esos
datos para responder. Nunca incluye PDFs completos, HTML crudo ni un historial ilimitado. Si lo
obligatorio no entra, se crea tarea humana. Sólo se persiste el manifiesto con IDs/versiones/hash,
estado de recuperación y hashes de consulta, no una copia gigante del prompt.

### FR-12 Decisiones IA, SHADOW y habilitación live

`LLMProvider.decide_reply(request)` devuelve salida estructurada: clasificación, intención/acción
allowlisted, confianza, candidate ID opcional, IDs de revisiones de hechos, cuerpo propuesto y
motivo humano. La aplicación rechaza campos o IDs desconocidos. El LLM jamás invoca Gmail ni decide
qué información cargar en la base: sólo puede usar el contexto global y las tarjetas puntuales que
la aplicación ya seleccionó para esa solicitud.

Modos:

- `OFF`: sólo efectos determinísticos.
- `SHADOW` (default): muestra “Qué habría hecho el sistema”, borrador, hechos usados y feedback;
  no autoriza Gmail.
- `LIVE`: permite pasar a las políticas de envío automático.

`LIVE` no se puede habilitar hasta revisar al menos 30 decisiones, tener al menos diez
auto-elegibles, alcanzar 90% de exactitud de intención/acción y no registrar ningún caso que habría
enviado automáticamente pero fue marcado “Necesitaba una persona”. Habilitarlo exige
reautenticación admin. El mínimo de confianza es 0,90, pero nunca reemplaza una regla de política.

### FR-13 Acciones automáticas, redirección y atención humana

Sólo son auto-elegibles: información aprobada de producto, hechos aprobados de empresa,
aclaración simple fundamentada y redirección explícita de propuesta. `POLITE_ACKNOWLEDGEMENT` y
`NOT_INTERESTED` no reciben respuesta automática. Baja, bounce y auto-reply siguen reglas
deterministas.

Siempre requieren persona: reuniones/fechas, precios/cotizaciones, negociación, quejas,
legal/privacidad, consejo técnico no soportado, intenciones múltiples, candidatos ambiguos,
conflicto de organización, contexto insuficiente o fallo de proveedor/schema. Una `HumanTask`
suspende automatización de la Conversation hasta que un admin resuelva o descarte; vendedores sólo
leen.

Una respuesta segura usa el hilo original y recibe siempre inbound actual, original y padre
directo. Para redirección:

1. El LLM elige un único candidate ID `NEW_CONTENT` de un pedido explícito.
2. El dominio valida sintaxis, MX y restricciones y bloquea Contacto/candidato.
3. Si pertenece a otra organización, es ambiguo o inválido, crea `HumanTask`.
4. Si es válido, lo agrega al mismo Contacto como “Dirección indicada para propuestas”, con
   proveniencia del inbound.
5. Envía la propuesta fija y todos los PDFs aprobados de la campaña origen en un hilo nuevo.
6. Sólo luego de éxito confirmado/reconciliado responde en el hilo original:
   `Perfecto, muchas gracias. La propuesta fue enviada a la dirección indicada.`
7. Si falla el nuevo envío, no confirma y abre una tarea humana.

Propuesta y confirmación usan claves idempotentes distintas y sólo hay una acción semántica por
Gmail ID entrante. Justo antes de ejecutar se revalidan headers automáticos, restricciones,
contexto, modo y `AUTO_REPLY_KILL_SWITCH`. Límites: tres respuestas automáticas por Conversation en
24 horas móviles y veinte por Workspace/día.

### FR-14 Alertas humanas

Toda tarea queda como badge persistente en “Necesita atención”. Además se envía una notificación
genérica a cada email de admin activo mediante Gmail conectado, asunto `Hay una conversación que
necesita revisión`, y sólo un link seguro al dashboard: nunca copia el inbound. Requiere
`PUBLIC_BASE_URL`. `NotificationDelivery` hace envío/reconciliación idempotente; un fallo de email
no elimina ni cierra la tarea visible.

### FR-15 Comunicación programada con Contactos

Cada Contacto puede optar, desactivado por defecto, por un `ContactCommunicationPlan` con propósito
check-in, feedback de producto o meta escrita por admin; requiere email preferido. Cadencia default
30 días, mínimo siete. El modo default es `REVIEW_BEFORE_SEND`; un admin puede elegir `AUTOMATIC`,
pausar, posponer y definir próximo vencimiento.

El scheduler determina la fecha y el LLM propone contenido con el mismo contexto acotado y hechos
aprobados. El vencimiento crea borrador para revisión, mensaje autorizado en hilo nuevo o tarea
humana. Nunca envía con Contacto/email restringido, tarea abierta, automatización suspendida,
contexto insuficiente o kill switch de relaciones activo. Una interacción humana mueve el próximo
contacto al menos una cadencia; un envío confirmado calcula desde `sent_at`. No es recordatorio de
campaña ni vuelve elegible a un Contacto.

### FR-16 UI Contactos y conversaciones

La navegación principal reemplaza Prospectos/Supresiones/Respuestas por `Contactos` y `Necesita
atención`; la audiencia prospectiva queda dentro de cada campaña. La lista muestra empresa/nombre,
email preferido, estado comprensible, última interacción, próximo contacto y badge de atención.

El detalle presenta cronología agrupada por hilo, canales y proveniencia, campañas, restricciones,
notas, automatización, tareas y próximo contacto. Vendedor ve la misma cronología legible sin
controles ni detalles técnicos. IDs, hashes, proveedor, modelo, confidence y manifiestos sólo
aparecen para admin bajo “Detalles técnicos”. Todo cuerpo de contacto/mensaje responde
`Cache-Control: private, no-store`.

### FR-17 Métricas de Resumen

Las métricas se derivan de filas persistidas: destinatarios iniciales únicos; iniciales,
recordatorios, respuestas automáticas y comunicaciones programadas enviadas; respondedores
humanos únicos; tasa de respuesta; tasa positiva; Contactos creados; tasas de rebote y baja; decisiones
resueltas automáticamente; tareas humanas totales/abiertas; medianas de primera respuesta y de
intervención humana; respuestas posteriores al inicial versus al recordatorio.

La tasa de respuesta es enrollments con al menos una respuesta humana divididos por enrollments
con inicial confirmado. Varias respuestas no duplican personas. Denominador cero muestra `—` y una
explicación breve. Hay vista global del Workspace y filtro por campaña; cohortes por rango arbitrario
se difieren.

### FR-18 Preparación para Internet

Settings de producción exigen hosts/orígenes exactos, cookies seguras, awareness de HTTPS detrás
de proxy, redirect SSL, HSTS escalonado, CSP, referrer/content-type/frame protections y health
detallado protegido. Scripts inline se mueven a static. Se prueban `check --deploy`, matriz de
permisos y proxy confiable.

La app sigue ligada a loopback por defecto. Provisión real de proxy inverso/TLS está diferida por
decisión explícita: no se declara Internet go-live hasta aprobar un plan HTTPS posterior.

### FR-19 Estados, idempotencia, auditoría e interfaces

Las vistas/tasks no asignan estados directamente; llaman servicios de transición. Jobs reciben IDs,
son idempotentes y usan constraints, transacciones y locks. `AuditEvent` append-only registra actor,
acción, entidad, before/after redactado y correlación. No guarda secretos ni cuerpos completos.

Servicios externos sólo se acceden mediante `ExtractorProvider`, `WebsiteFetcher`,
`EmbeddingProvider`, `LLMProvider` y `GmailProvider`. Tests usan fakes y bloquean HTTP, DNS, Gmail,
Overture, embeddings y LLM reales. Los efectos de Gmail siempre permanecen fuera del modelo.

### FR-20 Usabilidad y accesibilidad

La interfaz asume personas sin conocimiento técnico. Las etiquetas describen resultados, por
ejemplo “Respondido automáticamente”, “Necesita que lo revises”, “Modo de prueba: no se envió” y
“Próximo contacto”. Cada toggle explica en una frase su consecuencia; cada error indica qué pasó y
qué hacer. Se usa progressive disclosure, fieldsets semánticos, foco visible, teclado y diseño
responsive. El color nunca comunica un estado por sí solo.

## 4. Requisitos operativos y de datos

- **OPS-01:** Python 3.12+, Django 5.2 LTS, PostgreSQL, Redis, Celery worker/Beat, Docker Compose,
  HTMX/templates, Ruff, mypy con `django-stubs`, pytest y migraciones versionadas; sin Node salvo
  necesidad documentada.
- **OPS-02:** `make lint`, `make typecheck`, `make test`, `make test-e2e`, `make check`, backups,
  restore, health, seeds y runbooks reproducibles.
- **DM-01:** persistir como mínimo las entidades detalladas en `DATA_MODEL.md`, incluidas Workspace,
  Membership, Organization, OrganizationIdentity, EmailAddress, Contact, restriction, enrollment,
  Conversation, mensajes/adjuntos, decisiones/tareas/memoria/conocimiento, planes programados,
  notificaciones y particiones Overture.
- **QA-01:** cubrir fresh/upgrade migrations, permisos, auth/TOTP, estados, concurrencia,
  idempotencia, supresión, SSRF, contexto/IA, MIME, Gmail, recordatorios, redirección, métricas, UX y
  E2E fake con red bloqueada.

## 5. Fuera de alcance y trabajo diferido

- Multiempresa/multitenancy, registro público y recuperación pública.
- LangGraph u otro framework de agentes; el flujo es una máquina de estados con salida LLM
  estructurada.
- Personalización/placeholders por cliente en campañas iniciales.
- Más de un recordatorio de campaña.
- Extracción automática de hechos desde PDF, precios/cotizaciones, calendario de reuniones y
  herramientas externas para el modelo.
- Mutaciones por vendedores.
- HTML saliente, tracking, scraping directo, SMTP con contraseña, rotación de cuentas o evasión.
- Provisión real de reverse proxy, certificados TLS y go-live público; sólo readiness de app.
- Cohortes métricas por rangos arbitrarios y borrado administrativo de historia.

## 6. Criterios de aceptación

Un upgrade conserva UUIDs, Gmail/RFC IDs, mensajes, adjuntos, restricciones, ciphertext y auditoría;
las migraciones Overture existentes no se reescriben. Una instalación limpia arranca con envío y
auto-respuesta bloqueados, login protegido, admin TOTP, contenido seed y decisiones SHADOW.

Un E2E fake puede seleccionar distritos de dos provincias, descubrir y aprobar audiencia, adjuntar
varios PDFs, enviar iniciales sin LLM, posponer un conflicto de mismo día, enviar/cancelar un único
recordatorio, importar una respuesta, promover Contacto, producir decisión SHADOW, ejecutar una
respuesta segura o redirección live bajo política y crear alerta humana en los casos prohibidos.
Vendedor puede leer las conversaciones y no ejecutar mutaciones. Ningún test accede a red.
