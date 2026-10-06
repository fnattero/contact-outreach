import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const DEFAULT_HSTS_SECONDS = 31_536_000;

function contentSecurityPolicy(nonce: string, development: boolean, upgradeRequests: boolean): string {
  const directives = [
    "default-src 'self'",
    // Scripts run only with this request's nonce; 'strict-dynamic' lets those load their chunks.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ""}`,
    // The component library injects <style> elements at runtime, so styles cannot be nonce-only.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    `connect-src 'self'${development ? " ws: wss:" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  if (upgradeRequests) {
    directives.push("upgrade-insecure-requests");
  }
  return directives.join("; ");
}

// HTTPS is only enforced when the public origin is HTTPS; a plain-HTTP local stack must stay usable.
function servedOverHttps(): boolean {
  return process.env.NODE_ENV === "production" && Boolean(process.env.PUBLIC_APP_ORIGIN?.startsWith("https://"));
}

function hstsMaxAge(): number {
  const configured = Number(process.env.HSTS_MAX_AGE_SECONDS);
  return Number.isInteger(configured) && configured >= 0 ? configured : DEFAULT_HSTS_SECONDS;
}

export function proxy(request: NextRequest): NextResponse {
  const development = process.env.NODE_ENV === "development";
  const nonce = btoa(crypto.randomUUID());
  const https = servedOverHttps();
  const policy = contentSecurityPolicy(nonce, development, https);

  // Next reads the nonce from the request's CSP header while rendering and applies it to the
  // framework scripts it emits.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", policy);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  if (https) {
    // Read at runtime so a first deployment can use a short value before raising it.
    response.headers.set("Strict-Transport-Security", `max-age=${hstsMaxAge()}`);
  }
  return response;
}

export const config = {
  matcher: [
    {
      // Pages only: the API proxy returns JSON, and static assets and the health route need no CSP.
      source: "/((?!api|_next/static|_next/image|favicon.ico|healthz).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
