// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { config, proxy } from "@/proxy";

function request(path = "/dashboard") {
  return new NextRequest(`https://app.example.com${path}`);
}

function directives(csp: string): Map<string, string> {
  return new Map(
    csp.split(";").map((part) => {
      const [name, ...value] = part.trim().split(" ");
      return [name, value.join(" ")] as const;
    }),
  );
}

function production(origin = "https://app.example.com") {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("PUBLIC_APP_ORIGIN", origin);
}

afterEach(() => vi.unstubAllEnvs());

describe("content security policy", () => {
  it("allows scripts only through a per-request nonce in production", () => {
    production();
    const response = proxy(request());
    const csp = directives(response.headers.get("Content-Security-Policy") ?? "");

    const script = csp.get("script-src") ?? "";
    expect(script).toMatch(/^'self' 'nonce-[A-Za-z0-9+/=]+' 'strict-dynamic'$/);
    expect(script).not.toContain("unsafe-inline");
    expect(script).not.toContain("unsafe-eval");
    expect(csp.get("default-src")).toBe("'self'");
    expect(csp.get("object-src")).toBe("'none'");
    expect(csp.get("frame-ancestors")).toBe("'none'");
    expect(csp.get("form-action")).toBe("'self'");
    expect(csp.get("base-uri")).toBe("'self'");
    expect(csp.get("connect-src")).toBe("'self'");
    expect(csp.has("upgrade-insecure-requests")).toBe(true);
  });

  it("uses a fresh nonce for every request and hands it to the renderer", () => {
    production();
    const nonceOf = (response: Response) =>
      /'nonce-([^']+)'/.exec(response.headers.get("Content-Security-Policy") ?? "")?.[1];

    const first = proxy(request());
    const second = proxy(request());

    expect(nonceOf(first)).toBeTruthy();
    expect(nonceOf(first)).not.toBe(nonceOf(second));
    expect(first.headers.get("x-middleware-request-x-nonce")).toBe(nonceOf(first));
    expect(first.headers.get("x-middleware-request-content-security-policy")).toBe(
      first.headers.get("Content-Security-Policy"),
    );
  });

  it("keeps styles same-origin plus inline, which the component library injects at runtime", () => {
    production();
    const csp = directives(proxy(request()).headers.get("Content-Security-Policy") ?? "");
    expect(csp.get("style-src")).toBe("'self' 'unsafe-inline'");
  });

  it("only relaxes the policy for the development tooling", () => {
    vi.stubEnv("NODE_ENV", "development");
    const csp = directives(proxy(request()).headers.get("Content-Security-Policy") ?? "");
    expect(csp.get("script-src")).toContain("'unsafe-eval'");
    expect(csp.get("connect-src")).toContain("ws:");
    expect(csp.has("upgrade-insecure-requests")).toBe(false);
  });
});

describe("transport security", () => {
  it("sends HSTS in production, for one year unless configured", () => {
    production();
    expect(proxy(request()).headers.get("Strict-Transport-Security")).toBe("max-age=31536000");

    vi.stubEnv("HSTS_MAX_AGE_SECONDS", "300");
    expect(proxy(request()).headers.get("Strict-Transport-Security")).toBe("max-age=300");

    vi.stubEnv("HSTS_MAX_AGE_SECONDS", "not-a-number");
    expect(proxy(request()).headers.get("Strict-Transport-Security")).toBe("max-age=31536000");
  });

  it("does not force HTTPS for a plain-HTTP local stack, which would make it unreachable", () => {
    production("http://localhost:3000");
    const response = proxy(request());

    expect(response.headers.get("Strict-Transport-Security")).toBeNull();
    expect(response.headers.get("Content-Security-Policy")).not.toContain(
      "upgrade-insecure-requests",
    );
    expect(response.headers.get("Content-Security-Policy")).toContain("'strict-dynamic'");
  });

  it("does not send HSTS outside production", () => {
    vi.stubEnv("NODE_ENV", "development");
    expect(proxy(request()).headers.get("Strict-Transport-Security")).toBeNull();
  });
});

describe("matcher", () => {
  const source = config.matcher[0].source;
  const matches = (path: string) => new RegExp(`^${source}$`).test(path);

  it("covers pages and skips the API proxy, static assets and the health route", () => {
    expect(matches("/dashboard")).toBe(true);
    expect(matches("/login")).toBe(true);
    expect(matches("/")).toBe(true);
    expect(matches("/api/v1/auth/session")).toBe(false);
    expect(matches("/_next/static/chunks/app.js")).toBe(false);
    expect(matches("/healthz")).toBe(false);
  });
});
