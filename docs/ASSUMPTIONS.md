# Supuestos y decisiones

Este registro fija defaults e invariantes de la revisión contact-centric. Los controles de
seguridad no se relajan desde el dashboard.

| ID | Decisión | Impacto |
| --- | --- | --- |
| A-001 | Monorepo: frontend Next.js/React/TypeScript/Ant Design y backend modular Django 5.2 LTS/DRF/Uvicorn; PostgreSQL, Redis y S3-compatible separados. | Arquitectura |
| A-002 | Una instalación contiene un solo Workspace/empresa; hay múltiples usuarios, no tenants ni registro público. | Alcance |
| A-003 | Roles: ADMIN completo y VENDEDOR de lectura acotada. Los campos creator/uploader son atribución, no autorización. | Seguridad |
| A-004 | Admin crea cuentas mediante link de un solo uso mostrado una vez y válido 24 h; no hay recovery público. | Seguridad |
| A-005 | Quinto fallo de username+IP bloquea 30 min; 20 fallos/IP en 30 min bloquean IP; un intento bloqueado no desliza la espera. | Seguridad |
| A-006 | MFA queda diferido en v2; se elimina django-otp/recovery y se compensa temporalmente con sesión 12 h, Argon2, lockout, CSRF y reauth sensible. | Seguridad |
| A-007 | No se borra un User con historia ni se desactiva/degrada al último ADMIN activo; rol/deactivación invalida sesiones. | Integridad |
| A-008 | Organization es global dentro del Workspace; EmailAddress es única en Workspace y pertenece a una Organization. | Datos |
| A-009 | Una respuesta humana, carga manual o restricción manual crea Contact; Contact excluye toda Organization de campañas. | Producto |
| A-010 | AUTO_REPLY y BOUNCE no crean Contact por sí solos; UNSUBSCRIBE humano sí y queda “Baja solicitada”. | Datos |
| A-011 | Manual Contact exige email; empresa/nombre son opcionales. Un Contact puede tener varios emails y Conversations. | UX |
| A-012 | UNSUBSCRIBE es irreversible. Bounce invalida sólo el email. Restricción manual requiere motivo y sólo admin puede revertirla. | Legal / integridad |
| A-013 | No existe override para contactar una Organization que ya es Contact ni una dirección dada de baja. | Seguridad |
| A-014 | SearchZone usa jerarquía provincia/distrito; CABA conserva barrios seleccionables y custom zones quedan sólo como compatibilidad legacy no visible. | Geografía |
| A-015 | Se seedéan límites oficiales versionados de todas las provincias y sus partidos/departamentos/comunas; fuente/atribución se persisten. | Legal / datos |
| A-016 | Overture separa Release de particiones provinciales; una campaña usa sólo particiones READY del mismo release. | Integridad |
| A-017 | El import hace una lectura bbox por provincia y filtro exacto de distritos. La campaña nunca usa un bbox combinado nacional. | Costo / operación |
| A-018 | El snapshot activo legacy se preserva/backfillea como CABA; cobertura inesperada queda histórica y migraciones Overture existentes no se editan. | Migración |
| A-019 | Nuevas campañas no llaman LLM para redactar copy inicial ni para puntuar relevancia numéricamente; sí aplican un filtro de audiencia por IA sólo veto, desactivable desde el dashboard (A-060). El objetivo cuenta enrollments elegibles con initial preparado. | Producto / costo |
| A-020 | Contenido inicial/reminder/referido usa exactamente los seeds de PRODUCT_SPEC, sin placeholders, más firma BusinessProfile determinista. | Copy |
| A-021 | Ciclo nuevo: DRAFT, DISCOVERING, AWAITING_APPROVAL, RUNNING, COMPLETED con PAUSED/CANCELLED/STOPPED_ERROR. | Estado |
| A-022 | Discovery termina antes de approval. `CAMPAIGN` es default; `PER_MESSAGE` exige aprobación individual y start separado. | Control humano |
| A-023 | Campaña snapshottea audiencia, contenido/firma, adjuntos, agenda, actor/fecha y hashes al aprobar. | Trazabilidad |
| A-024 | Cada initial exige al menos un PDF. Se permiten varios ordenados: 15 MiB/archivo, 17 MiB fuentes totales, 24 MiB MIME final. | Entregabilidad |
| A-025 | Initial y referred proposal adjuntan PDFs aprobados; reminder y replies no adjuntan por default; nunca se envía conjunto parcial. | Integridad |
| A-026 | INITIAL/REMINDER reservan un email por fecha local America/Argentina/Buenos_Aires; conflicto mueve al próximo día permitido. | Anti-spam |
| A-027 | No hay cooldown cross-campaign adicional. Un email sin respuesta puede entrar después en otra campaña, pero no recibir dos mensajes de campaña el mismo día. | Alcance |
| A-028 | Cada campaña tiene cero o un reminder, default tres días calendario desde sent_at confirmado y siguiente ventana laboral. | Seguimiento |
| A-029 | Respuesta humana, Contact manual, unsubscribe o bounce cancelan reminder; auto-reply no. | Estado |
| A-030 | Gmail sync corre cada minuto por default y nunca sostiene su lock durante LLM; publica análisis con transaction.on_commit. | Operación |
| A-056 | Gmail sync importa mails directos sólo si el remitente coincide con un EmailAddress válido de un Contacto existente; no crea contactos desde desconocidos. | Seguridad / producto |
| A-057 | Gmail sync identifica la etiqueta `SENT`; una respuesta enviada directamente en Gmail sólo se proyecta como `MANUAL_REPLY` si responde a un inbound conocido, resuelve su `REPLY_REVIEW` y bloquea automáticos en cola. | Integridad / seguridad |
| A-031 | Se extraen máximo diez emails literales inbound de plain text/mailto, con NEW_CONTENT/SIGNATURE/QUOTED; no se reconstruyen ofuscados. | Seguridad |
| A-032 | Input LLM máximo 24.000 caracteres; instrucciones admin, inbound authored, original/padre cuando existen y contexto general vigente son obligatorios; luego hasta seis recientes, memoria con fuentes y tres facts activos elegidos por embeddings. | Costo / seguridad |
| A-033 | No entran PDFs ni raw HTML al LLM; sólo se persiste manifest de IDs/versiones/retrieval/hash, no prompts gigantes duplicados. | Privacidad |
| A-034 | El conocimiento sólo usa revisiones guardadas/activas por administradores; no hay extracción automática desde catálogos. Embeddings selecciona facts, no autoriza respuestas. | Integridad |
| A-058 | En `LIVE`, una `REPLY` envía el `proposed_body` validado por el LLM; los facts seleccionados son evidencia autorizada y nunca reemplazan la redacción final. | Seguridad / UX |
| A-055 | Embeddings default `fake`; OpenAI-compatible usa modelo `text-embedding-3-small` y dimensiones configurables desde Integraciones. Similitud baja o selección ambigua inyecta hasta tres facts como sugerencias `may_be_irrelevant=true`, no como autorización automática. | Integración IA |
| A-035 | No se usa LangGraph. `decide_reply` produce JSON estructurado; policy/domain services ejecutan efectos sin tools para el modelo. | Arquitectura |
| A-036 | Modos de respuesta: OFF, SHADOW default y LIVE. SHADOW produce cero autorizaciones Gmail. | Seguridad |
| A-037 | LIVE se habilita por decisión explícita de un administrador y requiere reauth admin. | Seguridad |
| A-038 | Confianza automática mínima 0,90 es necesaria pero no suficiente. | Seguridad |
| A-039 | Sólo product info/company facts/simple clarification fundamentados y redirect explícito son auto-elegibles. ACK cortés/not interested no reciben reply. | Política IA |
| A-040 | Meeting/dates, pricing/quotes, negotiation, complaints, legal/privacy, unsupported technical, multi-intent, ambiguity, ownership conflict, insufficient context y provider/schema failure son humanos. | Política IA |
| A-041 | HumanTask OPEN suspende automation de Conversation hasta resolución/dismiss admin; una respuesta manual confirmada resuelve la tarea del inbound respondido. | Seguridad |
| A-042 | Redirect agrega el email explícito al mismo Contact, envía proposal en hilo nuevo y ACK exacto sólo tras confirmación; fallo crea task sin false ACK. | Integridad |
| A-043 | Proposal/ACK tienen keys separadas y sólo hay una semantic action por inbound Gmail ID. | Idempotencia |
| A-044 | Máximo tres automatic replies por Conversation/24 h y veinte por Workspace/día. | Anti-abuso |
| A-045 | Alertas: badge durable + email genérico a admins activos con link seguro, sin inbound. PUBLIC_BASE_URL es obligatorio para ese canal. | Privacidad |
| A-046 | Seguimiento usa temas globales opt-in por contacto: cadencia default 30 días/mínimo 7, REVIEW_BEFORE_SEND default y AUTOMATIC opcional. | Relaciones |
| A-047 | SEND_MODE dry-run y SEND_KILL_SWITCH true por default; AUTO_REPLY_KILL_SWITCH y RELATIONSHIP_KILL_SWITCH también true por default. | Seguridad |
| A-048 | Conversational/scheduled replies no usan same-day campaign guard; scheduled Contact nunca vuelve elegible a Contact. | Alcance |
| A-049 | Métricas se derivan de filas, no counters; cero denominador muestra `—`; global y filtro campaign, sin rango arbitrario inicial. | Analítica |
| A-050 | UI en español simple, progressive disclosure, técnico sólo admin, consecuencias de toggles, errores accionables, foco/teclado/responsive y color no exclusivo. | UX / accesibilidad |
| A-051 | Sólo Next.js es público; `/api/v1` se proxya al backend privado con token interno. PostgreSQL, Redis y storage nunca reciben endpoints públicos. | Operación |
| A-052 | Gmail usa gmail.send + gmail.readonly y reconciliación Message-ID; exactly-once absoluto no existe. | Integridad |
| A-053 | No Google Maps scraping, SMTP password, tracking, HTML outbound, account rotation ni evasión de cuotas. | Legal / seguridad |
| A-054 | Campañas/AIAnalysis históricos siguen legibles y nunca se regeneran o reenvían retroactivamente. | Migración |
| A-059 | La IA nunca redacta el cuerpo del contacto inicial. Se eliminó el camino `analyze_prospect`, que puntuaba relevancia y redactaba copy en una sola llamada. El scoring se perdió con él y se acepta esa pérdida; el modelo `AIAnalysis` se conserva a propósito como punto de partida para reconstruirlo sin redacción. | Producto / seguridad |
| A-060 | El filtro de audiencia por IA es sólo veto: puede quitar un prospecto descubierto, nunca agregarlo, ordenarlo ni reinstalarlo. Modo default `LENIENT`; `OFF` lo desactiva; `OBSERVE` evalúa sin descartar. El modelo devuelve `FIT|UNCLEAR|UNFIT` y el código decide el veto; nunca se le pide "ser más estricto". El criterio es texto abierto que guía cómo juzgar los casos dudosos pero no puede cambiar el formato ni los veredictos. No devuelve evidencia por `fact_id`: sólo un motivo de una línea. | Producto / seguridad |
| A-061 | La campaña congela modo y criterio (texto y hash) en `prompt_snapshot.prospect_screening` al iniciar; editar el criterio no altera una campaña en curso. Una campaña sin esa clave es anterior al filtro y se trata como `OFF`. El literal `llm_calls: 0` pasó a `initial_outreach_llm_calls: 0`. | Trazabilidad |
| A-062 | Entrada del filtro ≤ 4.000 caracteres: criterio ≤ 1.200 y nombre/rubro/zona/dominio más un extracto de la primera página del sitio ≤ 1.200, rotulado `UNTRUSTED_DATA`. Una llamada por prospecto, `temperature=0`, sin retry y sin batch; el veredicto se guarda por prospecto con un hash que incluye el digest del criterio y excluye el modo, así que cambiar de modo no cuesta una llamada. Un prospecto que la elegibilidad ya rechaza no se envía al modelo. El modelo puede ser distinto del de las respuestas (`relevance_llm_model`). | Costo / seguridad |
| A-063 | "Recuperar" es durable por negocio y permanente: el filtro no vuelve a descartarlo aunque cambie el criterio, y no existe "des-recuperar". Es posible mientras la campaña está en `DISCOVERING` o `AWAITING_APPROVAL`; el negocio se suma a la audiencia preparada antes de aprobar. Con la campaña ya enviando, la UI lo deshabilita explicando el motivo. | Control humano |
| A-064 | El contexto general aprobado ya no se envía al modelo: sólo orientaba tono y alcance y no se podía citar, y las instrucciones de escritura ya lo cubren. Lo que una respuesta puede afirmar sale únicamente de los datos aprobados. Las revisiones existentes quedan legibles y sin uso. | Producto / costo |
| A-065 | La firma del perfil comercial la agrega el sistema al final de las respuestas automáticas, igual que en las propuestas, recordatorios y mensajes programados. El modelo recibe la orden de no firmar. La firma se congela en el mensaje y se vuelve a comparar con la del perfil antes de enviar. | Producto / seguridad |
| A-066 | El envío real necesita dos llaves: el servidor lo permite (`SEND_MODE=live` y bloqueo de envíos apagado) y un admin lo enciende en la app (`SendModeSetting`, con reautenticación, palabra de confirmación y auditoría). Sin fila, la app está en simulación. `SEND_REQUIRES_APP_ENABLE=false` devuelve la decisión al servidor, como antes. Apagarlo no requiere confirmación. | Seguridad |
| A-067 | Las palabras de un rubro encuentran también el plural de su última palabra (bobinado y bobinados), así quien las escribe no necesita comodines. Los ejemplos por defecto de rubros se reemplazaron por negocios que usan motores; sólo se reemplazan los originales sin editar (los usados por una campaña se archivan, los demás se borran) y nunca los que el equipo creó o modificó. | Producto |
| A-068 | Un usuario sin actividad registrada se puede eliminar (con palabra de confirmación, auditado). Quien creó campañas, catálogos o configuración queda referenciado por ese historial y sólo se puede desactivar. | Integridad |
| A-069 | La pantalla de zonas con datos lista todas las provincias con su estado y permite cargar una por vez; sin versión explícita usa la última verificada por mantenimiento. | Operación |
| A-070 | El filtro de audiencia puede usar un servicio de IA distinto del de las respuestas, con dirección y clave propias. La clave de las respuestas solo se comparte si es exactamente el mismo servicio y dirección; si no, hace falta una clave propia. Volver a la conexión compartida borra la clave propia. | Costo / seguridad |
| A-071 | El modo automático (envío real) de una campaña solo se ofrece si los envíos reales están habilitados (servidor y aplicación); si no, se muestra deshabilitado con el motivo. Exige una confirmación explícita y la campaña igual requiere aprobación de una persona. El borrador de una campaña a medio crear se guarda en el navegador (sessionStorage) y nunca incluye esa confirmación. | Seguridad / UX |

## Calidad de audiencia y scoring pendiente

Esta sección registra una capacidad que existía y se perdió como efecto secundario. El filtro de
audiencia categórico y sólo veto (A-060 a A-063) la reconstruye en parte; el scoring numérico con
evidencia sigue pendiente.

**Qué existía.** `apps/prospects/analysis.py::analyze_prospect` llamaba al `LLMProvider` una sola vez
por prospecto y obtenía, en un único objeto JSON, dos cosas distintas:

- *scoring de relevancia*: `relevance_score` (escala 0 a 100), `confidence`, `relevance_reason` y
  `evidence` como lista de `fact_id` literales; se persistía en `AIAnalysis` y un prospecto por
  debajo de `campaign.relevance_threshold` quedaba `SKIPPED_IRRELEVANT`;
- *redacción del contacto inicial*: `subject` y `body_text`, que se componían con la firma aprobada
  y se guardaban como `OutboundMessage`.

**Por qué se perdió.** Ambas salidas compartían una llamada, un JSON schema y una validación. Al
mover las campañas al mensaje fijo aprobado por una persona y eliminar la redacción automática, el
scoring se fue con ella. No fue una decisión sobre el scoring: fue un efecto colateral, y se
registra como tal.

**Qué filtra un prospecto hoy.** `enrollment_eligibility` en `apps/contacts/services.py` verifica
cinco condiciones de higiene y cumplimiento (dirección elegida y válida, la Organization no es ya un
Contact, no existe otra inscripción activa y la dirección no está suprimida); es el único filtro
que puede **admitir** un prospecto. Encima, el filtro de audiencia por IA puede **quitar** un
prospecto que no encaja con el criterio escrito por el operador (A-060).

**Dónde queda la calidad de audiencia.** En la selección de rubros y zonas que arma la consulta
Overture y, además, en el filtro de audiencia, que sólo puede descartar y es configurable desde el
dashboard. Con el filtro en `OFF`, la calidad depende sólo de rubros y zonas.

**Consecuencias registradas.**

- `Campaign.relevance_threshold` y `BusinessProfile.relevance_threshold` **siguen sin leerlos
  nadie**: el filtro de audiencia es categórico (tres veredictos y cuatro modos) y no usa
  umbrales numéricos.
  Las columnas, las constraints `0..100` y los valores guardados se conservan a propósito, y los
  snapshots de campaña y de perfil los siguen escribiendo, para que el scoring nuevo pueda
  retomarlos sin migración destructiva ni pérdida de configuración histórica. El control se
  ocultó de la UI (perfil comercial y alta de campaña) y de la API (`BusinessProfileSerializer`,
  payload y campos escribibles de campañas) hasta que el scoring vuelva: un control visible que
  no hace nada es peor que ninguno, porque alguien lo sube creyendo que filtra más.
- `AIAnalysis` conserva sus filas y migraciones y sigue siendo legible (A-054), pero ya no se
  escriben filas nuevas.
- `build_analysis_facts` se conserva: arma hechos literales y atribuibles desde `Prospect` y
  `WebsiteSnapshot` sin llamar a ningún proveedor.
- `recover_prospect_pipeline` sólo reencola prospectos de campañas en `DISCOVERING`. Antes incluía
  `RUNNING`, donde el pipeline avanzaba gracias al análisis; sin él, incluir `RUNNING` reencolaría
  para siempre los mismos prospectos. Un prospecto de una campaña pausada durante el descubrimiento
  y reanudada a `RUNNING` queda sin avanzar.

**Ítem abierto.** Un scoring numérico de relevancia como capacidad independiente, sin redacción:
entrada de hechos versionados y salida numérica con evidencia. El estado `ANALYZED` queda reservado
para él y hoy nadie lo alcanza. El veto categórico ya resuelve la decisión sobre si filtra
automáticamente: sólo quita, y una persona puede recuperar lo quitado.

## Rubros seed

Se conservan los 23 rubros actuales: Bobinados de motores; Reparación de motores eléctricos;
Talleres electromecánicos; Mantenimiento industrial; Service de herramientas eléctricas;
Reparación de bombas eléctricas; Reparación de bombas de agua; Autoelectricidad; Alternadores y
arranques; Reparación de autoelevadores; Reparación de grupos electrógenos; Mantenimiento y
reparación de ascensores; Service de aspiradoras; Reparación de lavarropas; Reparación de
electrodomésticos; Reparación de máquinas industriales; Ferreterías industriales; Venta y
reparación de herramientas eléctricas; Repuestos para herramientas eléctricas; Maquinaria de
limpieza industrial; Reparación de portones automáticos; Máquinas de coser industriales; y
Reparación de motores de corriente continua. Cada uno conserva reglas versionadas.

## Geografía seed

Argentina tiene 24 jurisdicciones de primer nivel (23 provincias y CABA). Se cargan los niveles
oficiales pertinentes bajo cada una. En CABA se preservan los 48 barrios actuales con sus códigos,
geometría y atribución; en provincias se presentan partidos, departamentos o comunas según la
denominación local. Un nombre repetido bajo otra provincia no se deduplica por nombre.

## Contradicciones resueltas

1. **Público vs. servicios privados:** Railway termina TLS sólo frente al frontend; el backend y
   datos permanecen en private networking y confían proxy metadata sólo con token interno.
2. **IA “agente” vs. efectos:** el LLM analiza texto y selecciona IDs/acciones permitidos; un
   servicio determinista con estado durable ejecuta Gmail. No necesita tools ni LangGraph.
3. **Contacto con varios emails:** Organization agrupa identidades y Contact agrupa Conversations;
   EmailAddress nuevo preserva la fuente inbound sin crear otra persona/empresa automáticamente.
4. **Campañas futuras vs. no spam:** Contact excluye la organización; un no respondedor puede
   reaparecer, pero la reserva por email/día evita dos campañas simultáneas.
5. **Contexto completo vs. saturación:** inbound/original/padre y contexto global siempre completos;
   historia reciente, memoria source-linked y RAG de hasta tres facts entran por presupuesto fijo.
6. **Automático vs. humano:** policy por intención domina confidence; reuniones, precios y riesgo
   siempre crean tarea.
7. **Coordinación explícita:** el prompt pide `HUMAN` para coordinar llamadas/reuniones y un
   fallback determinístico estrecho corrige sólo esa combinación clara; consultas informativas
   sobre horarios o teléfono no se bloquean automáticamente.
