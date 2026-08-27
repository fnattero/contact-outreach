# Especificación de producto

## 1. Propósito y alcance

Contact Outreach es una aplicación web de una sola empresa para encontrar compradores B2B de
carbones para motores, enviar campañas desde Gmail y concentrar las relaciones comerciales en
`Contactos`. La aplicación se entrega como un workspace único con frontend Next.js/React y un
backend modular Django REST desplegables de forma independiente. PostgreSQL, Redis y almacenamiento
S3-compatible son servicios privados separados. No es un SaaS multiempresa y no ofrece registro
público.

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

La instalación nueva crea un `Workspace` vacío y el primer `ADMIN` mediante un comando de
bootstrap de una sola ejecución. No se copian usuarios ni datos desde la instalación anterior. Perfil,
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

MFA queda diferido en esta versión. La autenticación usa sesiones opacas server-side, cookie
`__Host-` Secure/HttpOnly/SameSite=Lax, CSRF guardado en sesión, Argon2, contraseña mínima de 14
caracteres, sesión absoluta de 12 horas, reautenticación por contraseña para acciones sensibles y
el lockout durable anterior. El alta, reset, cambio de rol, desactivación y desbloqueo quedan
auditados sin secretos. La ausencia temporal de MFA se declara como riesgo y no como protección
equivalente.

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

`CommunicationRestriction` reúne baja, rebote y “No contactar”. El bloqueo completo del contacto se
gestiona desde la lista de Contactos con un checkbox auditable; “No usar este email” bloquea sólo ese
canal. `UNSUBSCRIBE` es irreversible. Un bounce invalida sólo la dirección. Una restricción manual
sólo puede revertirla un admin con motivo auditado. La antigua página Supresiones desaparece, pero
las barreras y su historial permanecen visibles dentro de Contactos.

`Conversation` representa un Contacto más un hilo Gmail. Un Contacto puede tener varios hilos y
direcciones; una propuesta redirigida abre un hilo nuevo y ambos aparecen en una misma cronología.

### FR-03 Provincias, partidos/departamentos y zonas

`SearchZone` forma una jerarquía con código oficial, nivel, padre, provincia, fuente, atribución y
flag seleccionable. Los nombres sólo son únicos bajo el mismo padre/código, porque pueden repetirse
entre provincias. Se cargan geometrías oficiales versionadas para todas las provincias y sus
partidos, departamentos o comunas. En CABA el nivel elegible sigue siendo Barrio. La UI de campañas
usa sólo zonas oficiales; las zonas custom quedan como datos legacy no seleccionables para nuevas
campañas.

Al crear campaña el admin puede elegir una o más provincias y seleccionar distritos desde un mapa
clickeable con respaldo de búsqueda/lista, selección total o limpieza por provincia. La UI usa las
etiquetas “Partidos”, “Departamentos”, “Comunas” o “Barrios” y oculta jerga geométrica. Una campaña
congela nombres, códigos, geometrías, hashes, reglas y orden.

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

Gmail también importa mensajes nuevos que no responden a un envío de la app cuando el remitente
coincide exactamente con un `EmailAddress` válido de un Contacto existente en el Workspace. En ese
caso el inbound queda ligado a ese Contacto/Organization y a una Conversation nueva o existente,
pero sin campaña ni outbound padre. Remitentes desconocidos o emails inválidos se descartan y no
crean Contactos automáticamente.

Si un inbound quedó como tarea de revisión y un administrador lo responde manualmente, la tarea se
resuelve automáticamente sólo cuando Gmail confirma la respuesta manual. La sincronización también
reconoce una respuesta escrita directamente en Gmail: la guarda en el hilo de Contactos como
`MANUAL_REPLY`, resuelve la tarea asociada y bloquea cualquier respuesta automática pendiente. Si el
envío manual de la aplicación falla o queda en reconciliación, la tarea permanece abierta.

### FR-11 Conocimiento, candidatos y contexto acotado

El admin gestiona dos tipos de información desde “Información para responder consultas”:

- contexto general de la empresa, editable en el mismo lugar por administradores; el último texto
  guardado queda activo y conserva el usuario que lo guardó;
- datos puntuales/FAQ activos, que se guardan con título e información y se usan para responder
  consultas concretas sin aprobación manual adicional.

Sólo una tarjeta puntual guardada por un administrador puede fundamentar una respuesta concreta; no
se extraen hechos de PDFs automáticamente.

En “Respuesta automática” el admin también puede editar instrucciones de redacción para el agente.
Estas instrucciones explican tono, estructura y estilo deseado; no pueden relajar policy, permitir
hechos no aprobados ni evitar revisión humana.

Antes del LLM se extraen como máximo diez emails literales de texto plano y `mailto:`. Se
normalizan/validan y marcan `NEW_CONTENT`, `SIGNATURE` o `QUOTED`; no se reconstruyen direcciones
ofuscadas. El modelo sólo puede seleccionar IDs entregados en esa solicitud.

Cada solicitud tiene máximo 24.000 caracteres de entrada. Siempre incluye completo: nuevo texto
escrito por el remitente y contexto general vigente. Si el mail responde a un envío de la app,
también incluye mensaje inicial/referido original y padre directo. Si es un mail directo de un
Contacto preexistente, incluye en su lugar un bloque obligatorio de perfil del Contacto y marca que
no hubo correo previo enviado por la app. Después agrega hasta seis mensajes recientes relevantes
del Contacto entre hilos, memoria estructurada con fuentes para historia antigua y hasta tres
tarjetas puntuales activas elegidas por búsqueda semántica con embeddings. Si la similitud es baja
o varias tarjetas compiten de forma ambigua, esas tarjetas pueden entrar como contexto sugerido con
marca `may_be_irrelevant=true`; el LLM debe ignorarlas si no coinciden claramente y escalar a humano
cuando ninguna alcanza para responder. Nunca incluye PDFs completos, HTML crudo ni un historial
ilimitado. Si lo obligatorio no entra, se crea tarea humana. Sólo se persiste el
manifiesto con IDs/versiones/hash, estado de recuperación y hashes de consulta, no una copia gigante
del prompt.

### FR-12 Decisiones IA, SHADOW y habilitación live

`LLMProvider.decide_reply(request)` devuelve salida estructurada: clasificación, intención/acción
allowlisted, confianza, candidate ID opcional, IDs de revisiones de hechos, cuerpo propuesto y
motivo humano. La aplicación rechaza campos o IDs desconocidos. El LLM jamás invoca Gmail ni decide
qué información cargar en la base: sólo puede usar el contexto global y las tarjetas puntuales que
la aplicación ya seleccionó para esa solicitud. El system prompt fijo contiene las reglas
inmutables; el request agrega `ADMIN_WRITING_INSTRUCTIONS` como guía editable de escritura. Para una
acción `REPLY` autorizada, `proposed_body` es el cuerpo final que se envía: los facts seleccionados
son evidencia autorizada y no se pegan como párrafos en lugar de la redacción del LLM.

Modos:

- `OFF`: sólo efectos determinísticos.
- `SHADOW` (default): registra qué habría hecho el sistema, el borrador y los hechos usados;
  no autoriza Gmail ni ofrece una aprobación posterior desde la configuración.
- `LIVE`: permite pasar a las políticas de envío automático.

`LIVE` se habilita por decisión explícita de un administrador y exige reautenticación admin. El
mínimo de confianza es 0,90, pero nunca reemplaza una regla de política.

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

La instrucción al LLM refuerza esta clasificación y el policy agrega una protección acotada para
pedidos explícitos de coordinar/agendar una llamada o reunión junto con día, horario o
disponibilidad. No se bloquean por sí solas consultas informativas como horario de atención,
teléfono, zonas de envío o retiros.

Una respuesta segura usa el hilo original y recibe inbound actual más contexto obligatorio. En
mails directos de Contactos preexistentes puede autorizar una respuesta normal si supera policy,
kill switches y límites. La redirección de propuesta exige campaña/outbound padre porque reutiliza
el contenido y PDFs aprobados de esa campaña. Para redirección:

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
contexto, modo y `AUTO_REPLY_KILL_SWITCH`. El recheck conserva estabilidad frente a respuestas
automáticas posteriores de otros hilos del mismo Contacto; esos efectos no son contexto humano
nuevo y no deben invalidar una decisión previa. Sí cancelan el efecto los mensajes humanos,
respuestas manuales, cambios de contexto obligatorio/facts/política o cambios dentro del mismo
hilo. Límites: tres respuestas automáticas por Conversation en 24 horas móviles y veinte por
Workspace/día.

### FR-14 Alertas humanas

Toda tarea queda como badge persistente en “Necesita atención”. Además se envía una notificación
genérica a cada email de admin activo mediante Gmail conectado, asunto `Hay una conversación que
necesita revisión`, y sólo un link seguro al dashboard: nunca copia el inbound. Requiere
`PUBLIC_BASE_URL`. `NotificationDelivery` hace envío/reconciliación idempotente; un fallo de email
no elimina ni cierra la tarea visible.

### FR-15 Comunicación programada con Contactos

Un admin configura `FollowUpTopic` globales con nombre, objetivo, instrucciones, cadencia global,
modo y próxima fecha global. En cada Contacto sólo se aprueban o pausan los temas aplicables mediante
`ContactCommunicationPlan`; requiere email preferido validado. Cadencia default 30 días, mínimo
siete. El modo default es `REVIEW_BEFORE_SEND`; `AUTOMATIC` sigue bloqueado por las mismas barreras.

El scheduler deriva el próximo vencimiento por aprobación desde el tema global, historial y snooze. El
LLM propone contenido con el mismo contexto acotado y hechos aprobados para el tema aprobado. El
vencimiento crea borrador para revisión, mensaje autorizado en hilo nuevo o tarea humana. Nunca envía
con Contacto/email restringido, tarea abierta, automatización suspendida, contexto insuficiente o kill
switch de relaciones activo. Una interacción humana mueve el próximo
contacto al menos una cadencia; un envío confirmado calcula desde `sent_at`. No es recordatorio de
campaña ni vuelve elegible a un Contacto.

### FR-16 UI Contactos y conversaciones

La navegación principal reemplaza Prospectos/Supresiones/Respuestas por `Contactos` y `Necesita
atención`; la audiencia prospectiva queda dentro de cada campaña. La lista muestra empresa/nombre,
email preferido, checkbox “No contactar”, temas aprobados, última interacción y badge de atención.

El detalle presenta cronología agrupada por hilo, canales y proveniencia, campañas, restricciones,
notas, automatización, tareas y temas de seguimiento aprobables. Vendedor ve la misma cronología legible sin
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

Sólo el servicio frontend recibe dominio público y TLS. El frontend enruta `/api/v1/*` por la red
privada al backend usando un token interno server-side; elimina headers forwarded suministrados por
el navegador. Backend, PostgreSQL, Redis y storage no reciben dominio ni proxy TCP público. El
go-live exige verificar en staging certificados, proxy, cookies, CSRF, CSP, health y monitoreo.

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

- **OPS-01:** monorepo con Python 3.12+, Django 5.2 LTS/DRF/Uvicorn y procesos Celery supervisados
  dentro de un backend; Next.js/React/TypeScript/Ant Design en un frontend independiente;
  PostgreSQL, Redis y S3-compatible privados; Docker Compose local con MinIO.
- **OPS-02:** `make backend-check`, `make frontend-check`, `make test-e2e`, `make security-check` y
  `make check`, lockfiles exactos, OpenAPI/cliente generado, backups, restore, health, seeds y
  runbooks reproducibles.
- **DM-01:** persistir como mínimo las entidades detalladas en `DATA_MODEL.md`, incluidas Workspace,
  Membership, Organization, OrganizationIdentity, EmailAddress, Contact, restriction, enrollment,
  Conversation, mensajes/adjuntos, decisiones/tareas/memoria/conocimiento, temas y aprobaciones de
  seguimiento, notificaciones y particiones Overture.
- **QA-01:** cubrir fresh/upgrade migrations, permisos, sesión/CSRF/lockout/reauth, proxy, estados,
  concurrencia, idempotencia, supresión, SSRF, contexto/IA, MIME, Gmail, recordatorios,
  redirección, OpenAPI, UI responsive/accesible y E2E fake con red bloqueada.

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
- MFA/TOTP/WebAuthn inicial; debe reintroducirse antes de ampliar significativamente el acceso.
- JWT/API keys para terceros, backend horizontal, workers desplegados por separado, Kubernetes,
  Sentry, antivirus y acceso directo browser-to-S3.
- Migración de datos, dual-write, traffic splitting gradual o rollback productivo al servicio viejo.
- Cohortes métricas por rangos arbitrarios y borrado administrativo de historia.

## 6. Criterios de aceptación

La migración arquitectónica crea una base nueva y no transporta UUIDs, Gmail/RFC IDs, mensajes,
adjuntos, restricciones, ciphertext ni auditoría. La instalación vieja queda preservada y privada.
Las migraciones Overture existentes no se reescriben. La instalación nueva arranca con envío y
auto-respuesta bloqueados, login protegido, contenido seed y decisiones SHADOW.

Un E2E fake puede seleccionar distritos de dos provincias, descubrir y aprobar audiencia, adjuntar
varios PDFs, enviar iniciales sin LLM, posponer un conflicto de mismo día, enviar/cancelar un único
recordatorio, importar una respuesta, promover Contacto, producir decisión SHADOW, ejecutar una
respuesta segura o redirección live bajo política y crear alerta humana en los casos prohibidos.
Vendedor puede leer las conversaciones y no ejecutar mutaciones. Ningún test accede a red.
