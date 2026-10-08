import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "@/app/dashboard/page";
import { buildProblems } from "@/app/dashboard/dashboard-helpers";
import {
  getAttention,
  getAutomationConfiguration,
  getDashboardSummary,
  getInboundMessages,
  reauthenticate,
  setSendLive,
  type DashboardSummary,
} from "@/lib/api";

const auth = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
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
    getAttention: vi.fn(),
    getAutomationConfiguration: vi.fn(),
    getDashboardSummary: vi.fn(),
    getInboundMessages: vi.fn(),
    reauthenticate: vi.fn(),
    setSendLive: vi.fn(),
  };
});

function summary(overrides: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    metrics: {
      initial_messages_sent: 0, response_rate: null, positive_response_rate: null, unique_human_responders: 0,
      bounce_rate: null, unsubscribe_rate: null, median_first_response_seconds: null,
      median_human_intervention_seconds: null, open_human_tasks: 0, human_required: 0, automatically_resolved: 0,
    },
    campaigns: [],
    summary: { campaigns: 0, catalogs: 0, categories: 0, zones: 0, responses: 0 },
    attention: { open_human_tasks: 0, paused_campaigns: 0 },
    safety: {
      send_mode: "dry-run", send_kill_switch: true, send_server_allows_live: false, send_app_enabled: false,
      send_effective_live: false, auto_reply_kill_switch: true, relationship_kill_switch: true,
    },
    admin: {
      profile_configured: true, gmail_connected: true, gmail_status: "CONNECTED", failed_sends: 0,
      problem_jobs: 0, prospects: 0, sent_messages: 0,
    },
    ...overrides,
  } as DashboardSummary;
}

beforeEach(() => {
  auth.role = "ADMIN";
  vi.mocked(getDashboardSummary).mockReset().mockResolvedValue(summary());
  vi.mocked(getAttention).mockReset().mockResolvedValue([]);
  vi.mocked(getInboundMessages).mockReset().mockResolvedValue({ data: [], meta: { page: 1, page_size: 25, total: 0 } });
  vi.mocked(getAutomationConfiguration).mockReset().mockResolvedValue({
    mode: "SHADOW", mode_label: "Sólo observar", policy_version: "1", live_enabled_at: null, live_enabled_by: null,
  });
});

describe("buildProblems", () => {
  it("is empty when everything works", () => {
    expect(buildProblems(summary())).toEqual([]);
  });

  it("names each problem with the place that fixes it", () => {
    const found = buildProblems(
      summary({
        attention: { open_human_tasks: 0, paused_campaigns: 2 },
        admin: {
          profile_configured: false, gmail_connected: false, gmail_status: "ERROR", failed_sends: 3,
          problem_jobs: 0, prospects: 0, sent_messages: 0,
        },
      }),
    );

    expect(found.map((item) => [item.id, item.href, item.level])).toEqual([
      ["gmail", "/settings/integrations", "danger"],
      ["sends", "/jobs", "danger"],
      ["profile", "/settings/profile", "warning"],
      ["campaigns", "/campaigns", "warning"],
    ]);
    expect(found[1].title).toBe("3 envíos fallaron");
  });

  it("tells a missing Gmail from a broken one", () => {
    const missing = buildProblems(summary({ admin: { ...summary().admin!, gmail_status: "NONE" } }));
    expect(missing[0]).toMatchObject({ title: "Todavía no conectaste Gmail", level: "warning", action: "Conectar Gmail" });
  });

  it("reports nothing about Gmail or sends to someone without the admin block", () => {
    expect(buildProblems(summary({ admin: undefined }))).toEqual([]);
  });
});

describe("dashboard page", () => {
  it("lists what needs fixing at the top, and nothing when all is well", async () => {
    vi.mocked(getDashboardSummary).mockResolvedValue(
      summary({ admin: { ...summary().admin!, gmail_status: "ERROR", gmail_connected: false, failed_sends: 1 } }),
    );

    render(createElement(DashboardPage));

    const section = await screen.findByRole("region", { name: "Para resolver" });
    expect(within(section).getByText("Gmail tiene un problema de conexión")).toBeInTheDocument();
    expect(within(section).getByRole("link", { name: /Ver y reintentar/ })).toHaveAttribute("href", "/jobs");
  });

  it("has no problems section when nothing is wrong", async () => {
    render(createElement(DashboardPage));

    await screen.findByText("¿Es seguro operar ahora?");
    expect(screen.queryByRole("region", { name: "Para resolver" })).toBeNull();
  });

  it("shows the automatic replies as off, practising or on, with a way to change it", async () => {
    render(createElement(DashboardPage));

    expect(await screen.findByText("Practicando")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Cambiar" })).toHaveAttribute("href", "/automation");
  });

  it("does not offer to change the replies to someone who may not", async () => {
    auth.role = "VENDEDOR";
    vi.mocked(getDashboardSummary).mockResolvedValue(summary({ admin: undefined }));

    render(createElement(DashboardPage));

    await screen.findByText("¿Es seguro operar ahora?");
    expect(screen.queryByRole("link", { name: "Cambiar" })).toBeNull();
    expect(getAutomationConfiguration).not.toHaveBeenCalled();
  });
});

describe("send mode control", () => {
  const live = { server: true, app: false, effective: false };

  function safetyWith(state: { server: boolean; app: boolean; effective: boolean }): DashboardSummary["safety"] {
    return {
      send_mode: state.server ? "live" : "dry-run", send_kill_switch: !state.server,
      send_server_allows_live: state.server, send_app_enabled: state.app, send_effective_live: state.effective,
      auto_reply_kill_switch: true, relationship_kill_switch: true,
    };
  }

  it("explains that the server must allow real sending first", async () => {
    render(createElement(DashboardPage));

    expect(await screen.findByText(/El servidor todavía no permite envíos reales/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pasar a envío real" })).toBeNull();
    expect(screen.getByText("Envíos detenidos")).toBeInTheDocument();
  });

  it("turns real sending on only with the password and the typed word", async () => {
    vi.mocked(getDashboardSummary).mockResolvedValue(summary({ safety: safetyWith(live) }));
    vi.mocked(reauthenticate).mockResolvedValue({ reauthentication_active: true });
    vi.mocked(setSendLive).mockResolvedValue({ server_allows_live: true, app_enabled: true, effective_live: true, enabled_by: "u" });
    render(createElement(DashboardPage));

    fireEvent.click(await screen.findByRole("button", { name: "Pasar a envío real" }));
    const passwordDialog = await screen.findByRole("dialog");
    expect(within(passwordDialog).getByRole("button", { name: "Continuar" })).toBeDisabled();
    fireEvent.change(within(passwordDialog).getByLabelText("Contraseña actual"), { target: { value: "clave-segura" } });
    fireEvent.click(within(passwordDialog).getByRole("button", { name: "Continuar" }));

    const confirm = await screen.findByLabelText(/Escribí CONFIRMAR/);
    expect(setSendLive).not.toHaveBeenCalled();
    fireEvent.change(confirm, { target: { value: "CONFIRMAR" } });
    const dialog = confirm.closest("[role=dialog]") as HTMLElement;
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Encender envíos reales" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Encender envíos reales" }));

    await waitFor(() => expect(reauthenticate).toHaveBeenCalledWith("clave-segura"));
    await waitFor(() => expect(setSendLive).toHaveBeenCalledWith("enable-live", "CONFIRMAR"));
  });

  it("goes back to simulation without any confirmation", async () => {
    vi.mocked(getDashboardSummary).mockResolvedValue(
      summary({ safety: safetyWith({ server: true, app: true, effective: true }) }),
    );
    vi.mocked(setSendLive).mockResolvedValue({ server_allows_live: true, app_enabled: false, effective_live: false, enabled_by: null });
    render(createElement(DashboardPage));

    fireEvent.click(await screen.findByRole("button", { name: "Volver a simulación" }));

    await waitFor(() => expect(setSendLive).toHaveBeenCalledWith("disable-live"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
