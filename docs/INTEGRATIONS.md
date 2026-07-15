# Integraciones

## 1. Contratos comunes

Los contratos viven en dominio Python y no exponen SDKs externos:

```python
class ExtractorProvider(Protocol):
    def extract(self, request: SearchRequest) -> ExtractionBatch: ...

class WebsiteFetcher(Protocol):
    def fetch(self, request: WebsiteRequest) -> WebsiteResult: ...

class LLMProvider(Protocol):
    def analyze(self, request: AnalysisRequest) -> AIAnalysisResult: ...
    def classify_reply(self, request: ReplyClassificationRequest) -> ReplyClassification: ...

class GmailProvider(Protocol):
    def authorization_url(self, state: str, redirect_uri: str) -> str: ...
    def exchange_code(self, code: str, redirect_uri: str) -> GmailConnectionData: ...
    def revoke(self) -> None: ...
    def test_connection(self) -> GmailAccountInfo: ...
    def send(self, request: GmailSendRequest) -> GmailSendResult: ...
    def reply(self, request: GmailReplyRequest) -> GmailSendResult: ...
    def sync(self, cursor: GmailCursor | None) -> GmailSyncBatch: ...
```

Requests/results son dataclasses o modelos Pydantic inmutables. Todo adaptador acepta timeout, correlation/idempotency key y configuración explícita. Excepciones comunes: `RetryableProviderError`, `RateLimitError(retry_after)`, `AuthenticationError`, `ValidationProviderError`, `CostLimitError` y `PermanentProviderError`.

## 2. Outscraper

`OutscraperProvider` implementa `ExtractorProvider`; no existe cliente propio de Google Maps. Construye consultas con rubro, zona y ubicación snapshot, solicita email/contact enrichment y conserva la respuesta JSON exacta antes de mapearla. El parser versionado tolera campos faltantes y registra desconocidos sin incorporarlos automáticamente al dominio.

Si la API devuelve un request asíncrono, `SearchRun` persiste el request ID y el polling es idempotente. Un run no se repite con una nueva solicitud después de timeout si puede consultarse el request existente. Se respetan 429/403, `Retry-After`, estado de cuenta y errores permanentes.

**COSTO:** precios/unidades cambian y no se codifican. El adaptador recibe costo máximo por unidad configurado, reserva un upper bound antes de llamar y guarda costo real cuando esté disponible. No inicia un lote que pueda superar el cap restante. Referencia: [API oficial](https://docs.outscraper.com/) y [precios](https://outscraper.com/pricing/).

`MockExtractorProvider` devuelve fixtures determinísticos con registros válidos, sin email, duplicados y payload desconocido; permite paginación, 429 y error permanente programables.

## 3. Email y DNS MX

La sintaxis se valida con `email-validator`; se normaliza dominio IDNA y se conserva local part sin aplicar reglas específicas de Gmail (`+`, puntos). Se excluyen local parts configurados y patrones evidentemente no comerciales.

Un resolver inyectable basado en `dnspython` consulta MX con timeout. Null MX significa inválido; NXDOMAIN/sin MX es inválido después de considerar fallback A/AAAA conforme política documentada; timeout/SERVFAIL es transitorio y se reintenta. No se intenta handshake SMTP ni se contrata verificador externo.

Selección: email marcado principal por proveedor, luego mismo dominio empresarial, luego rol comercial (`ventas`, `info`, `contacto`), luego orden original. Candidatos excluidos o inválidos nunca se seleccionan. Gmail/Hotmail pueden aceptarse para pequeños negocios, pero su dominio no deduplica empresas.

## 4. WebsiteFetcher

`HttpWebsiteFetcher` usa un cliente HTTP sin JavaScript y resolver/transport inyectables. Aplica las reglas SSRF de `SECURITY.md`, permite sólo HTML/texto y procesa como máximo home más tres links internos relevantes. La selección puntúa paths/títulos como servicios, productos, nosotros, reparaciones y contacto, siempre dentro del mismo dominio registrable.

El resultado contiene páginas, final URLs, fechas, status, content hashes, extracto limpio y error parcial. Un fallo nunca bloquea IA: se crea `WebsiteResult` fallback sin texto web. `FakeWebsiteFetcher` no abre sockets y modela redirects, timeout, oversize y host prohibido.

## 5. Proveedores LLM

- `MockLLMProvider`: reglas determinísticas y outputs configurables para éxito/error.
- `OllamaProvider`: endpoint local configurable, modelo explícito y JSON schema cuando la versión lo soporte; no presupone GPU.
- `OpenAICompatibleProvider`: base URL, modelo y API key; usa `/v1/chat/completions` compatible y modo JSON/schema si está disponible, siempre con validación local.

El input contiene hechos con IDs estables, perfil snapshot, reglas y texto web rotulado no confiable. `evidence` sólo admite hechos del input. El prefijo `PUBLICIDAD -`, firma y BAJA se aplican/validan en código de dominio para no depender del modelo. El prompt descuenta del presupuesto las palabras del footer determinístico y el validador exige que el cuerpo final compuesto, no sólo el fragmento del modelo, tenga 70–130 palabras.

Una llamada lógica por prospecto puede tener hasta tres intentos técnicos con el mismo input hash. No se encadenan llamadas de corrección ni fallback genérico. El caché incluye input, prompt/schema, proveedor y modelo. Clasificación de respuestas es una operación distinta; BAJA y bounce se resuelven primero con reglas.

## 6. Gmail OAuth y envío

Flujo OAuth web-server con PKCE/state, redirect local configurado, acceso offline y scopes:

- `https://www.googleapis.com/auth/gmail.send`
- `https://www.googleapis.com/auth/gmail.readonly`

No se usa SMTP ni `mail.google.com`. La app valida que los scopes concedidos sean los esperados, cifra refresh token y obtiene el email de la cuenta mediante recursos Gmail permitidos. En modo Testing, Google puede expirar refresh tokens externos a los siete días: [OAuth 2.0](https://developers.google.com/identity/protocols/oauth2).

Al conectar por primera vez se guarda el `historyId` actual como baseline y no se importa correo histórico. Si se reconecta sin cursor confiable, se aplica el fallback acotado sólo contra threads/cabeceras de campañas conocidas.

MIME se construye con la librería estándar `email`: `text/plain; charset=utf-8`, PDF base64, un `To`, sin CC/BCC, Date, deterministic Message-ID y headers opacos `X-Contact-Outreach-Campaign`/`Message`. No incluyen email, categoría ni PII. Se codifica base64url y usa `users.messages.send`: [guía de envío](https://developers.google.com/workspace/gmail/api/guides/sending).

Para respuestas manuales `reply()` se invoca sólo desde el comando HTTP explícito y envía con `threadId`, `In-Reply-To`, `References` y asunto del hilo. Antes de cualquier retry ambiguo se busca el Message-ID; si la reconciliación no es concluyente, se pausa y requiere intervención en lugar de arriesgar duplicado.

`FakeGmailProvider` simula autorización, exchange, revocación y mailbox en memoria/base de prueba; deduplica por Message-ID, modela cuotas/historyId/404/rebotes y nunca abre red.

## 7. Sincronización Gmail

Beat solicita cambios con `history.list(startHistoryId)`, pagina, obtiene sólo metadata/raw necesarios y actualiza el cursor al confirmar toda la transacción. History expirado (HTTP 404) activa una búsqueda `newer_than:30d`, máximo 1000 candidatos, que filtra por Gmail thread IDs o headers propios antes de persistir. Gmail indica que history suele conservarse al menos una semana pero puede expirar antes: [sincronización oficial](https://developers.google.com/workspace/gmail/api/guides/sync).

Se guarda sólo el hilo relacionado y cuerpos sanitizados. IDs Gmail únicos hacen repetible cada sync. Una falla de clasificación no revierte importación ni cursor; deja `OTHER`/job pendiente según corresponda.

## 8. Política de reintentos

| Integración | Reintentos | Acción permanente |
| --- | --- | --- |
| Outscraper | 3, respeta Retry-After, backoff+jitter | detener extracción/campaña según error |
| DNS MX | 2 para timeout/SERVFAIL | prospecto ERROR si no concluye |
| Web | 1 por página | continuar con fallback |
| LLM | 3 intentos técnicos | prospecto ERROR, sin mensaje |
| Gmail send | no retry ciego; reconciliar primero | pausar ante ambigüedad/auth |
| Gmail sync | 3; fallback ante history 404 | degradar sync, nunca bloquear UI |

Cada llamada tiene timeout y presupuesto total. Los tests sustituyen todos los adaptadores y sockets; no existe opt-in accidental a live dentro de pytest.
