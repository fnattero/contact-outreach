import { readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";
import { isForbiddenShellRoute } from "@/components/app-shell";
import { sessionFor } from "./session";

/** Every page route on disk, e.g. app/campaigns/[id]/page.tsx -> /campaigns/123. */
function pageRoutes(): string[] {
  const root = join(process.cwd(), "app");
  const found: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (entry === "page.tsx") {
        const route = "/" + relative(root, dir).split(sep).join("/");
        found.push(route === "/." || route === "/" ? "/" : route.replace(/\[[^\]]+\]/g, "123"));
      }
    }
  };
  walk(root);
  return found.sort();
}

// The pages a seller may open: summary, campaigns, sent mail, contacts and conversations. Anything
// not listed is administrative and must be refused by the shell. A new page is therefore closed to
// sellers until someone consciously adds it here.
const OPEN_TO_EVERYONE = ["/", "/login", "/activate"];
const OPEN_TO_SELLERS = [
  "/dashboard",
  "/attention",
  "/campaigns",
  "/campaigns/123",
  "/responses",
  "/responses/123",
  "/outbound",
  "/outbound/123",
  "/contacts",
  "/contacts/123",
];

describe("page routes follow default-deny for sellers", () => {
  const routes = pageRoutes();
  const seller = sessionFor("VENDEDOR");

  it("finds the pages on disk", () => {
    expect(routes.length).toBeGreaterThan(25);
    for (const known of [...OPEN_TO_EVERYONE, ...OPEN_TO_SELLERS]) expect(routes).toContain(known);
  });

  it.each(routes.filter((r) => ![...OPEN_TO_EVERYONE, ...OPEN_TO_SELLERS].includes(r)))(
    "refuses a seller on the administrative page %s",
    (route) => {
      expect(isForbiddenShellRoute(seller, route)).toBe(true);
    },
  );

  it.each(OPEN_TO_SELLERS)("lets a seller read %s", (route) => {
    expect(isForbiddenShellRoute(seller, route)).toBe(false);
  });

  it.each(routes)("lets an administrator open %s", (route) => {
    expect(isForbiddenShellRoute(sessionFor("ADMIN"), route)).toBe(false);
  });

  it.each(routes.filter((r) => !OPEN_TO_EVERYONE.includes(r) && !OPEN_TO_SELLERS.includes(r)))(
    "refuses someone with no capabilities on %s",
    (route) => {
      expect(isForbiddenShellRoute(sessionFor("ADMIN", []), route)).toBe(true);
      expect(isForbiddenShellRoute(null, route)).toBe(true);
    },
  );

  it("does not let a look-alike path slip past the prefix rules", () => {
    for (const route of ["/settings/users/../users", "/settings/users/", "/audit/x/y"]) {
      expect(isForbiddenShellRoute(seller, route), route).toBe(true);
    }
  });
});
