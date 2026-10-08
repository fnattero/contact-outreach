import { fireEvent, render, screen, within } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell, isForbiddenShellRoute } from "@/components/app-shell";
import { SectionTabs } from "@/components/design-system/section-tabs";
import type { Capability, UserSession } from "@/lib/api";
import { sessionFor } from "./session";

const nav = vi.hoisted(() => ({ path: "/dashboard", push: vi.fn() }));
const auth = vi.hoisted(() => ({ session: null as unknown }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useRouter: () => ({ push: nav.push }),
}));
vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  return { ...actual, useAuth: () => ({ session: auth.session, loading: false, refresh: vi.fn(), signOut: vi.fn() }) };
});

function session(role: "ADMIN" | "VENDEDOR", capabilities?: Capability[]): UserSession {
  return {
    ...sessionFor(role, capabilities),
    id: 1,
    username: "usuario",
    email: "usuario@example.com",
    workspace_id: "w",
    workspace_name: "Empresa",
    session_expires_at: "2099-01-01T00:00:00Z",
    reauthentication_active: false,
  };
}

function shell(userSession: UserSession) {
  auth.session = userSession;
  return render(
    <AppShell session={userSession} onSignOut={vi.fn()}>
      <p>contenido</p>
    </AppShell>,
  );
}

beforeEach(() => {
  nav.path = "/dashboard";
  nav.push.mockReset();
  window.localStorage.clear();
});

describe("sidebar groups", () => {
  it("gives an admin the three groups, with the advanced one folded", () => {
    shell(session("ADMIN"));

    expect(screen.getByText("Día a día")).toBeInTheDocument();
    expect(screen.getByText("Mi empresa")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Avanzado" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("link", { name: "Perfil comercial" })).toBeInTheDocument();
    // Folded: its pages are not reachable until it is opened.
    expect(screen.queryByRole("link", { name: "Integraciones" })).toBeNull();
  });

  it("opens and folds the advanced group, and remembers it", () => {
    const first = shell(session("ADMIN"));

    fireEvent.click(screen.getByRole("button", { name: "Avanzado" }));
    expect(screen.getByRole("link", { name: "Integraciones" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Correos bloqueados" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Actividad del sistema" })).toBeInTheDocument();
    first.unmount();

    shell(session("ADMIN"));
    expect(screen.getByRole("button", { name: "Avanzado" })).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps the advanced group open while one of its pages is showing", () => {
    nav.path = "/settings/integrations";

    shell(session("ADMIN"));

    expect(screen.getByRole("link", { name: "Integraciones" }).closest("li")).toHaveClass("nav-item--active");
    // Nothing to fold while the highlighted page lives inside it.
    expect(screen.queryByRole("button", { name: "Avanzado" })).toBeNull();
  });

  it("shows a seller only the day-to-day sections", () => {
    shell(session("VENDEDOR"));

    expect(screen.getByRole("link", { name: "Resumen" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Correos" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audiencia" })).toBeNull();
    expect(screen.queryByText("Mi empresa")).toBeNull();
    expect(screen.queryByText("Avanzado")).toBeNull();
  });

  it("sends Audiencia to the first tab the person may open", () => {
    shell(session("ADMIN", ["view_summary", "manage_configuration"]));

    expect(screen.getByRole("link", { name: "Audiencia" })).toHaveAttribute("href", "/settings/relevance");
  });

  it("highlights the section a related page belongs to", () => {
    for (const [path, label] of [
      ["/outbound", "Correos"],
      ["/attention", "Resumen"],
      ["/settings/prompts", "Respuestas automáticas"],
      ["/settings/relevance", "Audiencia"],
    ] as const) {
      nav.path = path;
      const view = shell(session("ADMIN"));
      const links = within(view.container).getAllByRole("link", { name: label });
      expect(links[0].closest("li"), path).toHaveClass("nav-item--active");
      view.unmount();
    }
  });
});

describe("route protection after the reorganization", () => {
  it("still protects every section reached through a tab", () => {
    const seller = sessionFor("VENDEDOR");
    for (const path of ["/prospects", "/settings/relevance", "/automation", "/automation/knowledge", "/settings/prompts"]) {
      expect(isForbiddenShellRoute(seller, path), path).toBe(true);
    }
    for (const path of ["/responses", "/outbound", "/attention"]) {
      expect(isForbiddenShellRoute(seller, path), path).toBe(false);
    }
  });
});

describe("section tabs", () => {
  const tabs = [
    { href: "/prospects", label: "Negocios", capability: "manage_campaigns" as const },
    { href: "/settings/relevance", label: "Filtro", capability: "manage_configuration" as const },
  ];

  it("marks the tab of the current page", () => {
    nav.path = "/settings/relevance";
    auth.session = session("ADMIN");

    render(createElement(SectionTabs, { label: "Audiencia", tabs }));

    expect(screen.getByRole("link", { name: "Filtro" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Negocios" })).not.toHaveAttribute("aria-current");
  });

  it("hides the tabs a person may not open, and everything when one is left", () => {
    auth.session = session("ADMIN", ["manage_configuration"]);

    const { container } = render(createElement(SectionTabs, { label: "Audiencia", tabs }));

    expect(container).toBeEmptyDOMElement();
  });

  it("asks before leaving a page with unsaved changes", () => {
    nav.path = "/prospects";
    auth.session = session("ADMIN");

    render(createElement(SectionTabs, { label: "Audiencia", tabs, dirty: true }));
    fireEvent.click(screen.getByRole("link", { name: "Filtro" }));

    expect(screen.getByText("Lo que editaste se perderá si cambiás de pestaña.")).toBeInTheDocument();
    expect(nav.push).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Descartar cambios" }));
    expect(nav.push).toHaveBeenCalledWith("/settings/relevance");
  });
});
