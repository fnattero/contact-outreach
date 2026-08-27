import { newCorrelationId } from "@/lib/correlation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

const REQUEST_TIMEOUT_MS = 30_000;

type RouteContext = { params: Promise<{ path: string[] }> };

function backendUrl(path: string[], search: string): URL {
  const base = process.env.BACKEND_INTERNAL_URL;
  if (!base) {
    throw new Error("BACKEND_INTERNAL_URL is not configured");
  }
  const encodedPath = path.map(encodeURIComponent).join("/");
  const url = new URL(`/api/v1/${encodedPath}/`, base);
  url.search = search;
  return url;
}

function forwardedRequestHeaders(request: Request, correlationId: string): Headers {
  const headers = new Headers();
  for (const [name, value] of request.headers.entries()) {
    const lowerName = name.toLowerCase();
    if (
      HOP_BY_HOP_HEADERS.has(lowerName) ||
      lowerName === "host" ||
      lowerName === "forwarded" ||
      lowerName.startsWith("x-forwarded-") ||
      lowerName.startsWith("x-internal-")
    ) {
      continue;
    }
    headers.append(name, value);
  }

  const proxyToken = process.env.INTERNAL_PROXY_TOKEN;
  if (proxyToken) {
    headers.set("X-Internal-Proxy-Token", proxyToken);
  }
  const publicOrigin = new URL(
    process.env.PUBLIC_APP_ORIGIN ?? request.url,
  );
  // The upstream URL is private, but Django must validate the public browser
  // origin as its Host. Never let the internal service name become the
  // application host seen by Django.
  headers.set("Host", publicOrigin.host);
  headers.set("X-Forwarded-Host", publicOrigin.host);
  headers.set("X-Forwarded-Proto", publicOrigin.protocol.replace(":", ""));
  headers.set("X-Correlation-ID", correlationId);
  return headers;
}

function forwardedResponseHeaders(upstream: Response): Headers {
  const headers = new Headers();
  for (const [name, value] of upstream.headers.entries()) {
    if (!HOP_BY_HOP_HEADERS.has(name.toLowerCase()) && name.toLowerCase() !== "set-cookie") {
      headers.append(name, value);
    }
  }
  const cookieHeaders = upstream.headers as Headers & { getSetCookie?: () => string[] };
  for (const cookie of cookieHeaders.getSetCookie?.() ?? []) {
    headers.append("Set-Cookie", cookie);
  }
  headers.set("Cache-Control", "private, no-store");
  return headers;
}

async function proxyRequest(request: Request, context: RouteContext): Promise<Response> {
  const correlationId = request.headers.get("X-Correlation-ID") ?? newCorrelationId();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const { path } = await context.params;
    const upstream = await fetch(backendUrl(path, new URL(request.url).search), {
      method: request.method,
      headers: forwardedRequestHeaders(request, correlationId),
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      cache: "no-store",
      redirect: "manual",
      signal: controller.signal,
      // Required by Node's fetch implementation when streaming a request body.
      duplex: "half",
    } as RequestInit & { duplex: "half" });

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: forwardedResponseHeaders(upstream),
    });
  } catch {
    return Response.json(
      {
        type: "about:blank",
        title: "Servicio temporalmente no disponible",
        status: 502,
        code: "backend_unavailable",
        detail: "No fue posible completar la solicitud.",
        correlation_id: correlationId,
      },
      {
        status: 502,
        headers: {
          "Cache-Control": "private, no-store",
          "X-Correlation-ID": correlationId,
        },
      },
    );
  } finally {
    clearTimeout(timeout);
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const PUT = proxyRequest;
export const PATCH = proxyRequest;
export const DELETE = proxyRequest;
export const OPTIONS = proxyRequest;
