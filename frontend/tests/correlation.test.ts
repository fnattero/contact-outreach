import { describe, expect, it } from "vitest";
import { newCorrelationId } from "@/lib/correlation";

describe("newCorrelationId", () => {
  it("creates a UUID correlation identifier", () => {
    expect(newCorrelationId()).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });
});
