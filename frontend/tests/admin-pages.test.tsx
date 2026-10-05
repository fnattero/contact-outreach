import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement, type ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CatalogsPage from "@/app/catalogs/page";
import CategoriesPage from "@/app/settings/categories/page";
import IntegrationsSettingsPage from "@/app/settings/integrations/page";
import ProfileSettingsPage from "@/app/settings/profile/page";
import SuppressionsPage from "@/app/settings/suppressions/page";
import { isForbiddenShellRoute } from "@/components/app-shell";
import { sessionFor } from "./session";
import {
  createSuppression,
  deleteSearchCategory,
  getSearchCategories,
  getSuppressions,
  toggleSearchCategory,
  type SearchCategory,
  type Suppression,
} from "@/lib/api";

const auth = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  const { sessionFor } = await import("./session");
  return {
    ...actual,
    useAuth: () => ({ session: sessionFor(auth.role), loading: false, refresh: vi.fn(), signOut: vi.fn() }),
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    createSuppression: vi.fn(),
    deleteSearchCategory: vi.fn(),
    getBusinessProfileVersioned: vi.fn().mockResolvedValue({ data: null, etag: null }),
    getCatalogs: vi.fn().mockResolvedValue([]),
    getGmailConnection: vi.fn().mockResolvedValue({ connected: false, email: null, status: "DISCONNECTED" }),
    getIntegrationStatus: vi.fn().mockResolvedValue(new Promise(() => undefined)),
    getSearchCategories: vi.fn(),
    getSuppressions: vi.fn(),
    toggleSearchCategory: vi.fn(),
  };
});

function inApp(element: ReactElement) {
  // antd's message/modal helpers come from <App>, which the real providers mount once.
  return render(createElement(App, null, element));
}

function suppression(overrides: Partial<Suppression> = {}): Suppression {
  return {
    id: "supp-1",
    email: "baja@cliente.example",
    reason: "UNSUBSCRIBE",
    reason_label: "Baja",
    source: "dashboard",
    evidence: "Lo pidió por correo",
    created_at: "2026-10-01T12:00:00Z",
    ...overrides,
  };
}

function category(overrides: Partial<SearchCategory> = {}): SearchCategory {
  return {
    id: "cat-1",
    name: "Talleres",
    active: true,
    sort_order: 0,
    rules_revision: 1,
    rules: [{ id: "r1", taxonomy_code: "", name_terms: ["taller mecanico"], active: true, sort_order: 0 }],
    ...overrides,
  };
}

beforeEach(() => {
  auth.role = "ADMIN";
  vi.mocked(getSuppressions).mockReset();
  vi.mocked(createSuppression).mockReset();
  vi.mocked(getSearchCategories).mockReset();
  vi.mocked(toggleSearchCategory).mockReset();
  vi.mocked(deleteSearchCategory).mockReset();
});

describe("suppressions page", () => {
  it("lists blocked addresses with their reason and evidence", async () => {
    vi.mocked(getSuppressions).mockResolvedValue({
      data: [suppression(), suppression({ id: "supp-2", email: "manual@cliente.example", reason: "MANUAL", reason_label: "Manual", evidence: "" })],
      meta: { page: 1, page_size: 25, total: 2 },
    });

    inApp(createElement(SuppressionsPage));

    expect(await screen.findByText("baja@cliente.example")).toBeInTheDocument();
    expect(screen.getByText("Lo pidió por correo")).toBeInTheDocument();
    expect(screen.getByText("Baja")).toBeInTheDocument();
    expect(getSuppressions).toHaveBeenCalledWith({ q: "", page: 1, page_size: 25 });
  });

  it("explains an empty list", async () => {
    vi.mocked(getSuppressions).mockResolvedValue({ data: [], meta: { page: 1, page_size: 25, total: 0 } });

    inApp(createElement(SuppressionsPage));

    expect(await screen.findByText("La lista está vacía")).toBeInTheDocument();
  });

  it("refuses a seller and never asks the API for the list", async () => {
    auth.role = "VENDEDOR";

    inApp(createElement(SuppressionsPage));

    expect(screen.getByText("No tenés permisos para administrar supresiones.")).toBeInTheDocument();
    await waitFor(() => expect(getSuppressions).not.toHaveBeenCalled());
  });

  it("sends the new block to the API with a manual reason by default", async () => {
    vi.mocked(getSuppressions).mockResolvedValue({ data: [], meta: { page: 1, page_size: 25, total: 0 } });
    vi.mocked(createSuppression).mockResolvedValue(suppression());
    inApp(createElement(SuppressionsPage));
    fireEvent.click(await screen.findByRole("button", { name: "Agregar correo" }));

    fireEvent.change(await screen.findByLabelText("Correo electrónico"), { target: { value: "nuevo@cliente.example" } });
    fireEvent.click(await screen.findByRole("button", { name: "Guardar" }));

    await waitFor(() =>
      expect(createSuppression).toHaveBeenCalledWith({ email: "nuevo@cliente.example", reason: "MANUAL", evidence: "" }),
    );
  });

  it("shows a server validation error on the field it belongs to", async () => {
    vi.mocked(getSuppressions).mockResolvedValue({ data: [], meta: { page: 1, page_size: 25, total: 0 } });
    vi.mocked(createSuppression).mockRejectedValue({
      status: 400,
      code: "validation_error",
      field_errors: { email: ["Este dominio no recibe correo."] },
    });
    inApp(createElement(SuppressionsPage));
    fireEvent.click(await screen.findByRole("button", { name: "Agregar correo" }));

    fireEvent.change(await screen.findByLabelText("Correo electrónico"), { target: { value: "dudoso@cliente.example" } });
    fireEvent.click(await screen.findByRole("button", { name: "Guardar" }));

    expect(await screen.findByText("Este dominio no recibe correo.")).toBeInTheDocument();
  });
});

describe("category management", () => {
  async function openPanel(name: string) {
    fireEvent.click(await screen.findByText(name));
  }

  it("shows inactive categories as such so they can be switched back on", async () => {
    vi.mocked(getSearchCategories).mockResolvedValue([category(), category({ id: "cat-2", name: "Ferreterías", active: false })]);

    inApp(createElement(CategoriesPage));

    expect(await screen.findByText("Ferreterías")).toBeInTheDocument();
    expect(screen.getByText("Inactivo")).toBeInTheDocument();
    expect(getSearchCategories).toHaveBeenCalledWith(true);
  });

  it("toggles a category through the API and reflects the new state", async () => {
    vi.mocked(getSearchCategories).mockResolvedValue([category()]);
    vi.mocked(toggleSearchCategory).mockResolvedValue(category({ active: false }));
    inApp(createElement(CategoriesPage));
    await openPanel("Talleres");

    fireEvent.click(await screen.findByRole("button", { name: "Desactivar rubro" }));

    await waitFor(() => expect(toggleSearchCategory).toHaveBeenCalledWith("cat-1"));
    expect(await screen.findByText("Inactivo")).toBeInTheDocument();
    // A button that is still loading carries an extra icon label, so match the name loosely.
    expect(await screen.findByRole("button", { name: /Activar rubro/ })).toBeInTheDocument();
  });

  it("deletes only after the confirmation word is typed", async () => {
    vi.mocked(getSearchCategories).mockResolvedValue([category()]);
    vi.mocked(deleteSearchCategory).mockResolvedValue({ outcome: "deleted" });
    inApp(createElement(CategoriesPage));
    await openPanel("Talleres");

    fireEvent.click(await screen.findByRole("button", { name: "Eliminar rubro" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Eliminar rubro" });
    expect(confirm).toBeDisabled();
    expect(deleteSearchCategory).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText(/Escribí ELIMINAR/), { target: { value: "ELIMINAR" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Eliminar rubro" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Eliminar rubro" }));

    await waitFor(() => expect(deleteSearchCategory).toHaveBeenCalledWith("cat-1"));
    await waitFor(() => expect(screen.queryByText("Talleres")).not.toBeInTheDocument());
  });
});

describe("direct-route guards", () => {
  it.each([
    ["catálogos", CatalogsPage, "No tenés permisos para administrar catálogos."],
    ["perfil comercial", ProfileSettingsPage, "No tenés permisos para editar el perfil comercial."],
    ["integraciones", IntegrationsSettingsPage, "No tenés permisos para ver integraciones."],
  ])("a seller who reaches %s directly is refused by the page itself", async (_name, Page, message) => {
    auth.role = "VENDEDOR";

    inApp(createElement(Page));

    expect(await screen.findByText(message)).toBeInTheDocument();
  });
});

describe("shell routing for the new administration pages", () => {
  it.each(["/prospects", "/settings/suppressions"])("keeps a seller away from %s", (path) => {
    expect(isForbiddenShellRoute(sessionFor("VENDEDOR"), path)).toBe(true);
    expect(isForbiddenShellRoute(sessionFor("ADMIN"), path)).toBe(false);
  });
});
