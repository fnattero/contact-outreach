import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AutomationPage from "@/app/automation/page";
import {
  approveKnowledgeContext,
  approveKnowledgeFact,
  getAutomationConfiguration,
  getKnowledgeContexts,
  getKnowledgeFacts,
  reauthenticate,
  setAutomationLive,
  type KnowledgeContextRevision,
  type KnowledgeFactRevision,
} from "@/lib/api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/automation",
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  return {
    ...actual,
    useAuth: () => ({ session: { role: "ADMIN" }, loading: false, refresh: vi.fn(), signOut: vi.fn() }),
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    approveKnowledgeContext: vi.fn(),
    approveKnowledgeFact: vi.fn(),
    getAutomationConfiguration: vi.fn().mockResolvedValue({
      mode: "SHADOW",
      mode_label: "Sólo observar",
      policy_version: "2026-07",
      live_enabled_at: null,
      live_enabled_by: null,
    }),
    getDashboardSummary: vi.fn().mockResolvedValue({
      safety: { send_mode: "dry-run", send_kill_switch: true, auto_reply_kill_switch: true, relationship_kill_switch: true },
    }),
    getKnowledgeContexts: vi.fn(),
    getKnowledgeFacts: vi.fn(),
    reauthenticate: vi.fn(),
    setAutomationLive: vi.fn(),
  };
});

function fact(overrides: Partial<KnowledgeFactRevision> = {}): KnowledgeFactRevision {
  return {
    id: "rev-1",
    fact_id: "fact-1",
    title: "Garantía",
    category: "Producto",
    version: 1,
    text: "La garantía es de 12 meses.",
    source_notes: "",
    content_hash: "abc",
    approved: false,
    state: "DRAFT",
    approved_at: null,
    ...overrides,
  };
}

function context(overrides: Partial<KnowledgeContextRevision> = {}): KnowledgeContextRevision {
  return {
    id: "ctx-1",
    version: 1,
    context_text: "Somos una empresa de componentes.",
    source_notes: "",
    content_hash: "def",
    approved: false,
    state: "DRAFT",
    approved_at: null,
    ...overrides,
  };
}

function renderPage() {
  return render(createElement(App, null, createElement(AutomationPage)));
}

describe("knowledge approval", () => {
  beforeEach(() => {
    vi.mocked(getKnowledgeFacts).mockReset().mockResolvedValue([fact()]);
    vi.mocked(getKnowledgeContexts).mockReset().mockResolvedValue([context()]);
    vi.mocked(approveKnowledgeFact).mockReset();
    vi.mocked(approveKnowledgeContext).mockReset();
  });

  it("shows a saved fact as an unapproved draft with an approve action", async () => {
    renderPage();
    expect(await screen.findByText("Garantía · v1")).toBeInTheDocument();
    expect(screen.getAllByText("Borrador sin aprobar").length).toBe(2);
    expect(screen.queryByText("Aprobada")).not.toBeInTheDocument();
  });

  it("approves a fact only after the confirmation word is typed", async () => {
    vi.mocked(approveKnowledgeFact).mockResolvedValue(fact({ approved: true, state: "APPROVED" }));
    renderPage();
    await screen.findByText("Garantía · v1");

    fireEvent.click(screen.getAllByRole("button", { name: "Aprobar" })[0]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Aprobar contenido" })).toBeDisabled();
    expect(approveKnowledgeFact).not.toHaveBeenCalled();

    vi.mocked(getKnowledgeFacts).mockResolvedValue([fact({ approved: true, state: "APPROVED" })]);
    fireEvent.change(within(dialog).getByLabelText(/Escribí CONFIRMAR/), { target: { value: "CONFIRMAR" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Aprobar contenido" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Aprobar contenido" }));

    await waitFor(() => expect(approveKnowledgeFact).toHaveBeenCalledWith("rev-1"));
    expect(approveKnowledgeContext).not.toHaveBeenCalled();
    expect(await screen.findByText("Aprobada")).toBeInTheDocument();
  });

  it("does not offer approval for a superseded or already approved revision", async () => {
    vi.mocked(getKnowledgeFacts).mockResolvedValue([
      fact({ id: "rev-2", version: 2, approved: true, state: "APPROVED" }),
      fact({ id: "rev-1", version: 1, state: "SUPERSEDED" }),
    ]);
    vi.mocked(getKnowledgeContexts).mockResolvedValue([]);
    renderPage();
    await screen.findByText("Garantía · v2");
    expect(screen.getByText("Reemplazada")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Aprobar" })).not.toBeInTheDocument();
  });
});

const shadow = {
  mode: "SHADOW" as const,
  mode_label: "Sólo observar",
  policy_version: "2026-07",
  live_enabled_at: null,
  live_enabled_by: null,
};

describe("LIVE confirmation", () => {
  beforeEach(() => {
    vi.mocked(getKnowledgeFacts).mockReset().mockResolvedValue([]);
    vi.mocked(getKnowledgeContexts).mockReset().mockResolvedValue([]);
    vi.mocked(getAutomationConfiguration).mockReset().mockResolvedValue(shadow);
    vi.mocked(reauthenticate).mockReset().mockResolvedValue({ reauthentication_active: true });
    vi.mocked(setAutomationLive).mockReset();
  });

  it("sends the typed word with the password check when enabling LIVE", async () => {
    vi.mocked(setAutomationLive).mockResolvedValue({ ...shadow, mode: "LIVE", mode_label: "Activas" });
    renderPage();
    fireEvent.change(await screen.findByLabelText("Contraseña actual"), { target: { value: "clave-segura" } });
    fireEvent.click(screen.getByRole("button", { name: "Revisar y activar LIVE" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/Escribí CONFIRMAR/), { target: { value: "CONFIRMAR" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Activar Envío real" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Activar Envío real" }));

    await waitFor(() => expect(reauthenticate).toHaveBeenCalledWith("clave-segura"));
    await waitFor(() => expect(setAutomationLive).toHaveBeenCalledWith("enable-live", "CONFIRMAR"));
  });

  it("asks for confirmation before switching LIVE off", async () => {
    vi.mocked(getAutomationConfiguration).mockResolvedValue({ ...shadow, mode: "LIVE", mode_label: "Activas" });
    vi.mocked(setAutomationLive).mockResolvedValue(shadow);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Desactivar LIVE" }));
    const dialog = await screen.findByRole("dialog");
    expect(setAutomationLive).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText(/Escribí CONFIRMAR/), { target: { value: "CONFIRMAR" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Desactivar Envío real" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Desactivar Envío real" }));

    await waitFor(() => expect(setAutomationLive).toHaveBeenCalledWith("disable-live"));
  });
});
