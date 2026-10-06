import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import JobsPage from "@/app/jobs/page";
import { canSeeAdministration, isForbiddenShellRoute } from "@/components/app-shell";
import { can, getBackgroundJobs } from "@/lib/api";
import { sessionFor } from "./session";

const auth = vi.hoisted(() => ({ session: null as unknown }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/jobs",
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  return { ...actual, useAuth: () => ({ session: auth.session, loading: false, refresh: vi.fn(), signOut: vi.fn() }) };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getBackgroundJobs: vi.fn() };
});

describe("can", () => {
  it("answers from the session's capabilities, never from its role", () => {
    expect(can(sessionFor("ADMIN"), "manage_users")).toBe(true);
    expect(can(sessionFor("VENDEDOR"), "manage_users")).toBe(false);
    expect(can(sessionFor("VENDEDOR"), "view_contacts")).toBe(true);
    // An ADMIN session that lacks a capability is refused: the role is not consulted.
    expect(can(sessionFor("ADMIN", ["view_summary"]), "manage_users")).toBe(false);
  });

  it("treats a missing session or capability list as no access", () => {
    expect(can(null, "view_summary")).toBe(false);
    expect(can(undefined, "view_summary")).toBe(false);
    expect(can({ capabilities: undefined } as never, "view_summary")).toBe(false);
  });
});

describe("shell routes follow capabilities", () => {
  it("blocks a seller from administration and creation routes only", () => {
    const seller = sessionFor("VENDEDOR");
    expect(canSeeAdministration(seller)).toBe(false);
    for (const path of ["/audit", "/jobs", "/campaigns/new", "/contacts/new", "/settings/users", "/settings/integrations/x"]) {
      expect(isForbiddenShellRoute(seller, path), path).toBe(true);
    }
    for (const path of ["/dashboard", "/campaigns", "/campaigns/123", "/contacts", "/responses", "/outbound"]) {
      expect(isForbiddenShellRoute(seller, path), path).toBe(false);
    }
  });

  it("lets an admin reach everything", () => {
    const admin = sessionFor("ADMIN");
    expect(canSeeAdministration(admin)).toBe(true);
    for (const path of ["/audit", "/jobs", "/campaigns/new", "/settings/users"]) {
      expect(isForbiddenShellRoute(admin, path), path).toBe(false);
    }
  });

  it("opens only the sections a narrower grant covers", () => {
    const integrationsOnly = sessionFor("VENDEDOR", ["view_summary", "manage_integrations"]);
    expect(canSeeAdministration(integrationsOnly)).toBe(true);
    expect(isForbiddenShellRoute(integrationsOnly, "/settings/integrations")).toBe(false);
    expect(isForbiddenShellRoute(integrationsOnly, "/audit")).toBe(true);
    expect(isForbiddenShellRoute(integrationsOnly, "/campaigns/new")).toBe(true);
  });

  it("denies everything restricted when the session has no capability list", () => {
    expect(isForbiddenShellRoute(null, "/audit")).toBe(true);
    expect(isForbiddenShellRoute({ capabilities: [] }, "/campaigns/new")).toBe(true);
  });
});

describe("jobs page", () => {
  beforeEach(() => {
    vi.mocked(getBackgroundJobs).mockReset().mockResolvedValue({
      data: [{
        id: "job-1", created_at: "2026-10-01T10:00:00Z", task_name: "mailbox.deliver_message", entity_type: "OutboundMessage",
        entity_id: "m-1", queue: "default", state: "FAILED", state_label: "Falló", attempts: 3, heartbeat_at: null,
        started_at: null, finished_at: "2026-10-01T10:00:09Z", next_retry_at: null, error: "timeout",
      }],
      meta: { page: 1, page_size: 25, total: 1 },
    } as never);
  });

  async function expandFirstRow() {
    const { container } = render(createElement(App, null, createElement(JobsPage)));
    await screen.findByText("Entregar correo");
    fireEvent.click(container.querySelector(".ant-table-row-expand-icon") as Element);
  }

  it("offers the retry only to someone who may retry sends", async () => {
    auth.session = sessionFor("ADMIN");
    await expandFirstRow();
    expect(await screen.findByText("Reintentar")).toBeInTheDocument();
  }, 20_000);

  it("shows a user who can only view jobs the failure but no retry control", async () => {
    auth.session = sessionFor("VENDEDOR", ["view_summary", "view_jobs"]);
    await expandFirstRow();
    await waitFor(() => expect(screen.getByText(/Detalles técnicos del error/)).toBeInTheDocument());
    expect(screen.queryByText("Reintentar")).not.toBeInTheDocument();
  }, 20_000);

  it("refuses a user without view_jobs", async () => {
    auth.session = sessionFor("VENDEDOR");
    render(createElement(App, null, createElement(JobsPage)));
    expect(await screen.findByText("No tenés permisos para ver tareas.")).toBeInTheDocument();
  }, 20_000);
});
