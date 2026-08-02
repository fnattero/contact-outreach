# Supuestos y decisiones

Este registro fija defaults e invariantes de la revisión contact-centric. Los controles de
seguridad no se relajan desde el dashboard.

| ID | Decisión | Impacto |
| --- | --- | --- |
| A-001 | Python 3.12+, Django 5.2 LTS, monolito modular Django/HTMX, PostgreSQL, Redis y Celery; sin SPA/Node salvo necesidad demostrada. | Arquitectura |
| A-002 | Una instalación contiene un solo Workspace/empresa; hay múltiples usuarios, no tenants ni registro público. | Alcance |
| A-003 | Roles: ADMIN completo y VENDEDOR de lectura acotada. Los campos creator/uploader son atribución, no autorización. | Seguridad |
| A-004 | Admin crea cuentas mediante link de un solo uso mostrado una vez y válido 24 h; no hay recovery público. | Seguridad |
| A-005 | Quinto fallo de username+IP bloquea 30 min; 20 fallos/IP en 30 min bloquean IP; un intento bloqueado no desliza la espera. | Seguridad |
| A-006 | TOTP es obligatorio para ADMIN y opcional para VENDEDOR; cada set tiene diez recovery codes hasheados de un uso. | Seguridad |
| A-007 | No se borra un User con historia ni se desactiva/degrada al último ADMIN activo; rol/deactivación invalida sesiones. | Integridad |
| A-008 | Organization es global dentro del Workspace; EmailAddress es única en Workspace y pertenece a una Organization. | Datos |
| A-009 | Una respuesta humana, carga manual o restricción manual crea Contact; Contact excluye toda Organization de campañas. | Producto |
| A-010 | AUTO_REPLY y BOUNCE no crean Contact por sí solos; UNSUBSCRIBE humano sí y queda “Baja solicitada”. | Datos |
| A-011 | Manual Contact exige email; empresa/nombre son opcionales. Un Contact puede tener varios emails y Conversations. | UX |
| A-012 | UNSUBSCRIBE es irreversible. Bounce invalida sólo el email. Restricción manual requiere motivo y sólo admin puede revertirla. | Legal / integridad |
| A-013 | No existe override para contactar una Organization que ya es Contact ni una dirección dada de baja. | Seguridad |
| A-014 | SearchZone usa jerarquía provincia/distrito; CABA conserva barrios seleccionables y custom zones siguen soportadas. | Geografía |
| A-015 | Se seedéan límites oficiales versionados de todas las provincias y sus partidos/departamentos/comunas; fuente/atribución se persisten. | Legal / datos |
| A-016 | Overture separa Release de particiones provinciales; una campaña usa sólo particiones READY del mismo release. | Integridad |
| A-017 | El import hace una lectura bbox por provincia y filtro exacto de distritos. La campaña nunca usa un bbox combinado nacional. | Costo / operación |
| A-018 | El snapshot activo legacy se preserva/backfillea como CABA; cobertura inesperada queda histórica y migraciones Overture existentes no se editan. | Migración |
| A-019 | Nuevas campañas no llaman LLM para relevancia ni copy inicial; el objetivo cuenta enrollments elegibles con initial preparado. | Producto / costo |
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
| A-031 | Se extraen máximo diez emails literales inbound de plain text/mailto, con NEW_CONTENT/SIGNATURE/QUOTED; no se reconstruyen ofuscados. | Seguridad |
| A-032 | Contexto LLM máximo 24.000 caracteres; inbound authored, original, padre y contexto general aprobado son obligatorios; luego hasta seis recientes, memoria con fuentes y tres facts aprobados elegidos por embeddings. | Costo / seguridad |
| A-033 | No entran PDFs ni raw HTML al LLM; sólo se persiste manifest de IDs/versiones/retrieval/hash, no prompts gigantes duplicados. | Privacidad |
| A-034 | El conocimiento sólo usa revisiones explícitamente aprobadas; no hay extracción automática desde catálogos. Embeddings selecciona facts, no autoriza respuestas. | Integridad |
| A-055 | Embeddings default `fake`; OpenAI-compatible usa modelo `text-embedding-3-small` y dimensiones configurables desde Integraciones. Similitud baja o selección ambigua no inyecta facts puntuales. | Integración IA |
| A-035 | No se usa LangGraph. `decide_reply` produce JSON estructurado; policy/domain services ejecutan efectos sin tools para el modelo. | Arquitectura |
| A-036 | Modos de respuesta: OFF, SHADOW default y LIVE. SHADOW produce cero autorizaciones Gmail. | Seguridad |
| A-037 | LIVE exige >=30 decisiones revisadas, >=10 auto-elegibles, >=90% accuracy y cero unsafe auto; habilitar requiere reauth admin. | Seguridad |
| A-038 | Confianza automática mínima 0,90 es necesaria pero no suficiente. | Seguridad |
| A-039 | Sólo product info/company facts/simple clarification fundamentados y redirect explícito son auto-elegibles. ACK cortés/not interested no reciben reply. | Política IA |
| A-040 | Meeting/dates, pricing/quotes, negotiation, complaints, legal/privacy, unsupported technical, multi-intent, ambiguity, ownership conflict, insufficient context y provider/schema failure son humanos. | Política IA |
| A-041 | HumanTask OPEN suspende automation de Conversation hasta resolución/dismiss admin. | Seguridad |
| A-042 | Redirect agrega el email explícito al mismo Contact, envía proposal en hilo nuevo y ACK exacto sólo tras confirmación; fallo crea task sin false ACK. | Integridad |
| A-043 | Proposal/ACK tienen keys separadas y sólo hay una semantic action por inbound Gmail ID. | Idempotencia |
| A-044 | Máximo tres automatic replies por Conversation/24 h y veinte por Workspace/día. | Anti-abuso |
| A-045 | Alertas: badge durable + email genérico a admins activos con link seguro, sin inbound. PUBLIC_BASE_URL es obligatorio para ese canal. | Privacidad |
| A-046 | Seguimiento usa temas globales opt-in por contacto: cadencia default 30 días/mínimo 7, REVIEW_BEFORE_SEND default y AUTOMATIC opcional. | Relaciones |
| A-047 | SEND_MODE dry-run y SEND_KILL_SWITCH true por default; AUTO_REPLY_KILL_SWITCH y RELATIONSHIP_KILL_SWITCH también true por default. | Seguridad |
| A-048 | Conversational/scheduled replies no usan same-day campaign guard; scheduled Contact nunca vuelve elegible a Contact. | Alcance |
| A-049 | Métricas se derivan de filas, no counters; cero denominador muestra `—`; global y filtro campaign, sin rango arbitrario inicial. | Analítica |
| A-050 | UI en español simple, progressive disclosure, técnico sólo admin, consecuencias de toggles, errores accionables, foco/teclado/responsive y color no exclusivo. | UX / accesibilidad |
| A-051 | App-side public readiness se implementa, pero proxy inverso/TLS real queda diferido; bind loopback default y no se declara go-live. | Operación |
| A-052 | Gmail usa gmail.send + gmail.readonly y reconciliación Message-ID; exactly-once absoluto no existe. | Integridad |
| A-053 | No Google Maps scraping, SMTP password, tracking, HTML outbound, account rotation ni evasión de cuotas. | Legal / seguridad |
| A-054 | Campañas/AIAnalysis históricos siguen legibles y nunca se regeneran o reenvían retroactivamente. | Migración |

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

1. **Público vs. despliegue actual:** la aplicación queda endurecida/configurable para Internet;
   TLS/proxy real permanece una fase externa y loopback es el default seguro.
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
