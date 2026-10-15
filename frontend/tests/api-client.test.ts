import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Call = { url: string; init: RequestInit; headers: Headers };

/** Fresh module per test: the client keeps its CSRF token in module state. */
async function client() {
  vi.resetModules();
  return await import("@/lib/api");
}

function json(data: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

let calls: Call[];
let counter: number;

function stubFetch(handler?: (call: Call) => Response | undefined) {
  calls = [];
  counter = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const call = { url, init, headers: new Headers(init.headers) };
      calls.push(call);
      const custom = handler?.(call);
      if (custom) return custom;
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: `token-${++counter}` });
      return json({ ok: true });
    }),
  );
}

const csrfFetches = () => calls.filter((call) => call.url === "/api/v1/auth/csrf/").length;

beforeEach(() => stubFetch());
afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("requests", () => {
  it("sends reads with the session cookie, no caching and no CSRF header", async () => {
    const api = await client();

    await api.getSession();

    const [call] = calls;
    expect(call.init.credentials).toBe("include");
    expect(call.init.cache).toBe("no-store");
    expect(call.headers.get("X-CSRFToken")).toBeNull();
    expect(call.headers.get("Accept")).toBe("application/json");
    expect(call.headers.get("X-Correlation-ID")).toMatch(/\S+/);
    expect(csrfFetches()).toBe(0);
  });

  it("asks for a CSRF token before the first mutation and reuses it afterwards", async () => {
    const api = await client();

    await api.logout();
    await api.updateOutboundDraft("m1", "Asunto", "Cuerpo");

    const mutations = calls.filter((call) => call.url !== "/api/v1/auth/csrf/");
    expect(mutations[0].headers.get("X-CSRFToken")).toBe("token-1");
    expect(mutations[0].init.method).toBe("POST");
    expect(mutations[1].init.method).toBe("PATCH");
    expect(mutations[1].headers.get("Content-Type")).toBe("application/json");
  });

  it("never keeps the CSRF token in browser storage or cookies", async () => {
    const api = await client();

    await api.getCsrfToken();

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("sends a PDF upload as multipart, with the CSRF token, never as JSON", async () => {
    const api = await client();

    await api.uploadCatalog("Catálogo", new File(["%PDF-1.4"], "catalogo.pdf", { type: "application/pdf" }));

    const upload = calls.find((call) => call.url === "/api/v1/catalogs/");
    expect(upload?.init.body).toBeInstanceOf(FormData);
    expect(upload?.headers.get("Content-Type")).toBeNull();
    expect(upload?.headers.get("X-CSRFToken")).toBe("token-1");
  });

  it("returns nothing for an empty 204 response", async () => {
    stubFetch((call) => (call.url.includes("/logout/") ? new Response(null, { status: 204 }) : undefined));
    const api = await client();

    await expect(api.logout()).resolves.toBeUndefined();
  });

  it("encodes identifiers so a hostile id cannot change the path or query", async () => {
    const api = await client();

    await api.getCampaign("../../admin/users?x=1#y");
    await api.getContact("a/b");

    expect(calls[0].url).toBe("/api/v1/campaigns/..%2F..%2Fadmin%2Fusers%3Fx%3D1%23y/");
    expect(calls[1].url).toBe("/api/v1/contacts/a%2Fb/");
  });

  it("encodes filter values and leaves out the empty ones", async () => {
    const api = await client();

    await api.getOutboundMessages({ state: "SENT&admin=1", campaign: "", date_from: "2026-01-01" });

    expect(calls[0].url).toBe("/api/v1/outbound-messages/?state=SENT%26admin%3D1&date_from=2026-01-01");
  });

  it("gives every consequential action its own idempotency key", async () => {
    const api = await client();

    await api.runCampaignAction("c1", "approve");
    await api.runCampaignAction("c1", "approve");
    await api.authorizeOutboundMessage("m1");

    const keys = calls.map((call) => call.headers.get("Idempotency-Key")).filter(Boolean);
    expect(keys).toHaveLength(3);
    expect(new Set(keys).size).toBe(3);
  });
});

describe("CSRF token lifecycle", () => {
  it("fetches a fresh token after logging in, because the server rotates it", async () => {
    const api = await client();
    await api.login("admin", "secret");
    await api.updateOutboundDraft("m1", "s", "b");

    const draft = calls.find((call) => call.init.method === "PATCH");
    expect(draft?.headers.get("X-CSRFToken")).toBe("token-2");
  });

  it("fetches a fresh token after activating an account", async () => {
    const api = await client();
    await api.activate("t", "pw", "pw");
    await api.logout();

    expect(calls.filter((call) => call.init.method === "POST").at(-1)?.headers.get("X-CSRFToken")).toBe(
      "token-2",
    );
  });

  it("fetches a fresh token for the next login after logging out", async () => {
    const api = await client();
    await api.login("admin", "secret");
    await api.logout();
    await api.login("admin", "secret");

    const posts = calls.filter((call) => call.init.method === "POST");
    expect(posts.map((call) => call.headers.get("X-CSRFToken"))).toEqual([
      "token-1",
      "token-2",
      "token-3",
    ]);
  });

  it("drops the token even when the logout request itself fails", async () => {
    stubFetch((call) =>
      call.url.includes("/logout/") ? json({}, 500) : undefined,
    );
    const api = await client();
    await api.getCsrfToken();

    await expect(api.logout()).rejects.toBeDefined();
    await api.login("admin", "secret");

    expect(calls.at(-1)?.headers.get("X-CSRFToken")).toBe("token-2");
  });

  it("drops the token when the session is no longer valid", async () => {
    stubFetch((call) => (call.url.includes("/dashboard/") ? json({}, 401) : undefined));
    const api = await client();
    await api.getCsrfToken();

    await expect(api.getDashboardSummary()).rejects.toBeDefined();
    await api.login("admin", "secret");

    expect(csrfFetches()).toBe(2);
  });

  it("refuses to continue when the CSRF endpoint fails", async () => {
    stubFetch((call) => (call.url === "/api/v1/auth/csrf/" ? json({}, 429) : undefined));
    const api = await client();

    await expect(api.logout()).rejects.toBeDefined();
    expect(calls.filter((call) => call.init.method === "POST")).toHaveLength(0);
  });
});

describe("errors", () => {
  it("throws the server's problem document", async () => {
    stubFetch((call) =>
      call.url.includes("/session/")
        ? new Response(JSON.stringify({ status: 403, code: "permission_denied", detail: "No." }), {
            status: 403,
            headers: { "Content-Type": "application/problem+json" },
          })
        : undefined,
    );
    const api = await client();

    await expect(api.getSession()).rejects.toMatchObject({ status: 403, code: "permission_denied" });
  });

  it("turns an HTML or empty error page into a neutral problem instead of leaking it", async () => {
    stubFetch(() => new Response("<html>Traceback (most recent call last) secret.py</html>", { status: 502 }));
    const api = await client();

    const problem = await api.getSession().catch((error: unknown) => error);

    expect(problem).toMatchObject({ status: 502, code: "invalid_response" });
    expect(JSON.stringify(problem)).not.toContain("Traceback");
    expect(api.problemMessage(problem as never)).toBe(
      "No fue posible interpretar la respuesta del servidor.",
    );
  });

  it("uses a generic message when the server gives no detail", async () => {
    const api = await client();

    expect(api.problemMessage({})).toBe("No fue posible completar la solicitud.");
    expect(api.problemMessage({ detail: "Detalle claro." })).toBe("Detalle claro.");
  });
});

describe("capabilities", () => {
  it("answers only from what the server granted", async () => {
    const api = await client();

    expect(api.can(null, "view_summary")).toBe(false);
    expect(api.can(undefined, "view_summary")).toBe(false);
    expect(api.can({}, "view_summary")).toBe(false);
    expect(api.can({ capabilities: [] }, "view_summary")).toBe(false);
    expect(api.can({ capabilities: ["view_summary"] }, "view_summary")).toBe(true);
    expect(api.can({ capabilities: ["view_summary"] }, "manage_users")).toBe(false);
  });

  it("does not infer anything from the role name", async () => {
    const api = await client();

    expect(api.can({ role: "ADMIN", capabilities: [] } as never, "manage_users")).toBe(false);
  });
});
