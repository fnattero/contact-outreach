import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Anything an attacker can influence (an id from a URL, a name, a filter) is replaced by this value.
const HOSTILE = "a/../b?admin=1&x=#frag";
const hostile = new Proxy(
  {},
  {
    get: (_target, key) => (key === Symbol.toPrimitive || key === "toString" ? () => HOSTILE : HOSTILE),
    ownKeys: () => [],
    getOwnPropertyDescriptor: () => undefined,
  },
);

type Call = { url: string; method: string; headers: Headers; credentials: RequestCredentials | undefined };
let calls: Call[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({
        url: String(url),
        method: (init.method ?? "GET").toUpperCase(),
        headers: new Headers(init.headers),
        credentials: init.credentials,
      });
      if (String(url) === "/api/v1/auth/csrf/") {
        return new Response(JSON.stringify({ data: { csrf_token: "token" } }), { status: 200 });
      }
      return new Response(JSON.stringify({ data: [], meta: { page: 1, page_size: 1, total: 0 } }), { status: 200 });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

type Endpoint = (...args: unknown[]) => unknown;

async function everyEndpoint(): Promise<Array<[string, Endpoint]>> {
  vi.resetModules();
  const api = (await import("@/lib/api")) as unknown as Record<string, unknown>;
  return Object.entries(api).filter(
    ([name, value]) => typeof value === "function" && !["can", "problemMessage", "getCsrfToken"].includes(name),
  ) as Array<[string, Endpoint]>;
}

describe("every API function, called with hostile input", () => {
  it("finds the whole API surface", async () => {
    expect((await everyEndpoint()).length).toBeGreaterThan(60);
  });

  it("never lets an argument escape its place in the URL, and always sends the session safely", async () => {
    const failures: string[] = [];
    for (const [name, fn] of await everyEndpoint()) {
      calls.length = 0;
      try {
        await fn(...Array.from({ length: Math.max(fn.length, 3) }, () => hostile));
      } catch {
        // A function may reject hostile input; what matters is what it put on the wire.
      }
      for (const call of calls.filter((item) => item.url !== "/api/v1/auth/csrf/")) {
        if (!call.url.startsWith("/api/v1/")) failures.push(`${name}: leaves the API prefix -> ${call.url}`);
        if (call.url.includes(HOSTILE)) failures.push(`${name}: raw hostile value in ${call.url}`);
        if (call.url.includes("/../")) failures.push(`${name}: path traversal in ${call.url}`);
        if (call.url.includes("#")) failures.push(`${name}: fragment in ${call.url}`);
        if (/[?&]admin=1/.test(call.url)) failures.push(`${name}: injected parameter in ${call.url}`);
        if (call.credentials !== "include") failures.push(`${name}: not sent with the session cookie`);
        if (!["GET", "HEAD", "OPTIONS"].includes(call.method) && !call.headers.get("X-CSRFToken")) {
          failures.push(`${name}: ${call.method} ${call.url} without a CSRF token`);
        }
      }
    }

    expect(failures).toEqual([]);
  });

  it("reaches several verbs, so the check above is not vacuous", async () => {
    const verbs = new Set<string>();
    for (const [, fn] of await everyEndpoint()) {
      calls.length = 0;
      try {
        await fn(hostile, hostile, hostile, hostile);
      } catch {
        // ignored
      }
      calls.forEach((call) => verbs.add(call.method));
    }

    expect([...verbs].sort()).toEqual(expect.arrayContaining(["GET", "PATCH", "POST"]));
  });
});
