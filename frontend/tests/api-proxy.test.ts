// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "@/app/api/v1/[...path]/route";

const upstream = vi.fn();

function call(headers: Record<string, string> = {}) {
  const request = new Request("https://app.example.com/api/v1/auth/login/", {
    method: "POST",
    headers,
    body: "{}",
  });
  return POST(request, { params: Promise.resolve({ path: ["auth", "login"] }) });
}

function forwarded(): Headers {
  expect(upstream).toHaveBeenCalledTimes(1);
  return (upstream.mock.calls[0][1] as RequestInit).headers as Headers;
}

describe("backend proxy", () => {
  beforeEach(() => {
    upstream.mockReset().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", upstream);
    vi.stubEnv("BACKEND_INTERNAL_URL", "http://backend.internal:8000");
    vi.stubEnv("PUBLIC_APP_ORIGIN", "https://app.example.com");
    vi.stubEnv("INTERNAL_PROXY_TOKEN", "t".repeat(40));
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("authenticates to the backend and presents the public origin", async () => {
    await call();
    const headers = forwarded();
    expect(headers.get("X-Internal-Proxy-Token")).toBe("t".repeat(40));
    expect(headers.get("Host")).toBe("app.example.com");
    expect(headers.get("X-Forwarded-Proto")).toBe("https");
  });

  it("reports the browser address from the edge header", async () => {
    await call({ "x-real-ip": "203.0.113.7" });
    expect(forwarded().get("X-Internal-Client-IP")).toBe("203.0.113.7");
  });

  it("never lets a browser spoof the forwarded or internal headers", async () => {
    await call({
      "x-internal-client-ip": "198.51.100.1",
      "x-internal-proxy-token": "guess",
      "x-forwarded-for": "198.51.100.2",
      "x-forwarded-host": "evil.example",
      "x-real-ip": "203.0.113.7",
    });
    const headers = forwarded();
    expect(headers.get("X-Internal-Client-IP")).toBe("203.0.113.7");
    expect(headers.get("X-Internal-Proxy-Token")).toBe("t".repeat(40));
    expect(headers.get("X-Forwarded-For")).toBeNull();
    expect(headers.get("X-Forwarded-Host")).toBe("app.example.com");
  });

  it("sends no client address when the edge header is missing", async () => {
    await call({ "x-internal-client-ip": "198.51.100.1" });
    expect(forwarded().get("X-Internal-Client-IP")).toBeNull();
  });

  it("reads the address from the header named in CLIENT_IP_HEADER", async () => {
    vi.stubEnv("CLIENT_IP_HEADER", "cf-connecting-ip");
    await call({ "cf-connecting-ip": "203.0.113.9", "x-real-ip": "198.51.100.1" });
    expect(forwarded().get("X-Internal-Client-IP")).toBe("203.0.113.9");
  });
});

describe("backend proxy in production", () => {
  beforeEach(() => {
    upstream.mockReset().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", upstream);
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("BACKEND_INTERNAL_URL", "http://backend.internal:8000");
    vi.stubEnv("PUBLIC_APP_ORIGIN", "https://app.example.com");
    vi.stubEnv("INTERNAL_PROXY_TOKEN", "t".repeat(40));
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("works when fully configured", async () => {
    expect((await call()).status).toBe(200);
  });

  it.each(["BACKEND_INTERNAL_URL", "PUBLIC_APP_ORIGIN", "INTERNAL_PROXY_TOKEN"])(
    "refuses to call the backend when %s is missing",
    async (name) => {
      vi.stubEnv(name, "");
      const response = await call();

      expect(response.status).toBe(503);
      expect(upstream).not.toHaveBeenCalled();
      expect(response.headers.get("Cache-Control")).toBe("private, no-store");
      const body = JSON.stringify(await response.json());
      expect(body).not.toContain(name);
      expect(body).not.toContain("t".repeat(40));
    },
  );

  it("accepts a plain-HTTP origin only for a local stack", async () => {
    vi.stubEnv("PUBLIC_APP_ORIGIN", "http://localhost:3000");
    expect((await call()).status).toBe(200);
    upstream.mockClear();

    vi.stubEnv("PUBLIC_APP_ORIGIN", "http://app.example.com");
    expect((await call()).status).toBe(503);
  });

  it("refuses a public origin that is not an exact https origin", async () => {
    vi.stubEnv("PUBLIC_APP_ORIGIN", "http://app.example.com/path");
    expect((await call()).status).toBe(503);
    expect(upstream).not.toHaveBeenCalled();
  });
});
