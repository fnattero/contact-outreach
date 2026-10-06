// @vitest-environment node
import { describe, expect, it } from "vitest";
import { GET } from "@/app/healthz/route";

describe("healthz", () => {
  it("reports ok without exposing configuration and is never cached", async () => {
    const response = await GET();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  });
});
