import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement, type ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RelevanceSettingsPage from "@/app/settings/relevance/page";
import { isForbiddenShellRoute } from "@/components/app-shell";
import {
  getIntegrationStatus,
  getRelevanceFilter,
  updateRelevanceFilter,
  type IntegrationStatus,
  type RelevanceFilter,
} from "@/lib/api";
import { sessionFor } from "./session";

const auth = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/settings/relevance",
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  const { sessionFor: makeSession } = await import("./session");
  return {
    ...actual,
    useAuth: () => ({ session: makeSession(auth.role), loading: false, refresh: vi.fn(), signOut: vi.fn() }),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getRelevanceFilter: vi.fn(),
    updateRelevanceFilter: vi.fn(),
    getIntegrationStatus: vi.fn(),
  };
});

function filter(overrides: Partial<RelevanceFilter> = {}): RelevanceFilter {
  return {
    mode: "LENIENT",
    mode_label: "Prudente",
    criteria: "Nos sirven los talleres de motores.",
    default_criteria: "Texto sugerido de ejemplo.",
    criteria_limit: 1200,
    revision: 1,
    ...overrides,
  };
}

function inApp(element: ReactElement) {
  return render(createElement(App, null, element));
}

beforeEach(() => {
  auth.role = "ADMIN";
  vi.mocked(getRelevanceFilter).mockReset().mockResolvedValue(filter());
  vi.mocked(updateRelevanceFilter).mockReset();
  vi.mocked(getIntegrationStatus)
    .mockReset()
    .mockResolvedValue({ llm: { provider: "openai-compatible" } } as IntegrationStatus);
});

describe("audience filter settings page", () => {
  it("shows the four options with their explanations and preselects the saved one", async () => {
    inApp(createElement(RelevanceSettingsPage));

    expect(await screen.findByText("Desactivado")).toBeInTheDocument();
    expect(screen.getByText("Sólo marcar")).toBeInTheDocument();
    expect(screen.getByText(/Si queda duda, se conservan/)).toBeInTheDocument();
    expect(screen.getByText(/y también los dudosos/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Prudente/ })).toBeChecked();
    expect(screen.getByDisplayValue("Nos sirven los talleres de motores.")).toBeInTheDocument();
    // The audience filter is explained without any mention of how it works under the hood.
    expect(screen.queryByText(/prompt|LLM|inteligencia/i)).toBeNull();
  });

  it("refuses a seller and never asks the API for the filter", async () => {
    auth.role = "VENDEDOR";

    inApp(createElement(RelevanceSettingsPage));

    expect(screen.getByText("No tenés permisos para editar el filtro de audiencia.")).toBeInTheDocument();
    await waitFor(() => expect(getRelevanceFilter).not.toHaveBeenCalled());
    expect(isForbiddenShellRoute(sessionFor("VENDEDOR"), "/settings/relevance")).toBe(true);
  });

  it("saves a gentler mode without asking for a typed confirmation", async () => {
    vi.mocked(updateRelevanceFilter).mockResolvedValue(filter({ mode: "OBSERVE", mode_label: "Sólo marcar" }));

    inApp(createElement(RelevanceSettingsPage));
    fireEvent.click(await screen.findByRole("radio", { name: /Sólo marcar/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Guardar" }));

    await waitFor(() =>
      expect(updateRelevanceFilter).toHaveBeenCalledWith("OBSERVE", "Nos sirven los talleres de motores."),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("asks for a typed word before switching to Estricto and only saves after it", async () => {
    vi.mocked(updateRelevanceFilter).mockResolvedValue(filter({ mode: "STRICT", mode_label: "Estricto" }));

    inApp(createElement(RelevanceSettingsPage));
    fireEvent.click(await screen.findByRole("radio", { name: /Estricto/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Guardar" }));

    const dialog = await screen.findByRole("dialog");
    expect(updateRelevanceFilter).not.toHaveBeenCalled();
    const confirm = within(dialog).getByRole("button", { name: "Activar estricto" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/Escribí ESTRICTO/), { target: { value: "ESTRICTO" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Activar estricto" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Activar estricto" }));

    await waitFor(() =>
      expect(updateRelevanceFilter).toHaveBeenCalledWith("STRICT", "Nos sirven los talleres de motores."),
    );
  });

  it("warns that nothing is judged for real while no AI provider is connected", async () => {
    vi.mocked(getIntegrationStatus).mockResolvedValue({ llm: { provider: "fake" } } as IntegrationStatus);

    inApp(createElement(RelevanceSettingsPage));

    expect(await screen.findByText("Todavía no hay un proveedor de IA conectado")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Conectar un proveedor" })).toHaveAttribute("href", "/settings/integrations");
  });

  it("restores the suggested text and keeps no technical details on the page", async () => {
    inApp(createElement(RelevanceSettingsPage));
    const box = (await screen.findByLabelText("Describí a quién le vendés")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "Otro texto." } });
    fireEvent.click(screen.getByRole("button", { name: "Volver al texto sugerido" }));

    await waitFor(() => expect(box.value).toBe("Texto sugerido de ejemplo."));
    expect(screen.queryByText("Detalles técnicos")).toBeNull();
  });

  it("counts characters against the limit", async () => {
    inApp(createElement(RelevanceSettingsPage));
    await screen.findByDisplayValue("Nos sirven los talleres de motores.");

    expect(screen.getByText("35/1200")).toBeInTheDocument();
  });
});
