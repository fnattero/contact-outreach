import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OvertureSettingsPage from "@/app/settings/overture/page";
import { provinceAction, withQueued } from "@/app/settings/overture/province-helpers";
import { getOvertureStatus, syncOverture, type OvertureStatus, type ProvinceCoverage } from "@/lib/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  const { sessionFor } = await import("./session");
  return { ...actual, useAuth: () => ({ session: sessionFor("ADMIN"), loading: false, refresh: vi.fn(), signOut: vi.fn() }) };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getOvertureStatus: vi.fn(), syncOverture: vi.fn() };
});

function province(overrides: Partial<ProvinceCoverage> = {}): ProvinceCoverage {
  return {
    code: "02", name: "Ciudad de Buenos Aires", state: "MISSING", place_count: 0, release_id: null,
    updated_at: null, error: "", ...overrides,
  };
}

function status(overrides: Partial<OvertureStatus> = {}): OvertureStatus {
  return {
    provinces: [
      province({ state: "READY", place_count: 1200, updated_at: "2026-10-01T12:00:00Z" }),
      province({ code: "06", name: "Buenos Aires" }),
      province({ code: "10", name: "Catamarca", state: "FAILED", error: "No se pudo descargar." }),
    ],
    latest_verified_release: "2026-07-22.0",
    latest_snapshot_id: null, active_snapshot_id: null, attribution: null, release_checks: [], partitions: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(getOvertureStatus).mockReset().mockResolvedValue(status());
  vi.mocked(syncOverture).mockReset();
});

describe("province helpers", () => {
  it("words the button by state and says why it is blocked", () => {
    const ok = { hasVerifiedVersion: true, anotherLoading: false };
    expect(provinceAction("MISSING", ok)).toEqual({ label: "Cargar datos", blockedReason: null });
    expect(provinceAction("READY", ok).label).toBe("Actualizar datos");
    expect(provinceAction("FAILED", ok).label).toBe("Reintentar");
    expect(provinceAction("IMPORTING", ok).blockedReason).toMatch(/se está cargando/);
    expect(provinceAction("MISSING", { ...ok, hasVerifiedVersion: false }).blockedReason).toMatch(/versión de datos verificada/);
    expect(provinceAction("MISSING", { ...ok, anotherLoading: true }).blockedReason).toMatch(/de a una/);
  });

  it("shows a just-queued province as loading before the server reports it", () => {
    const shown = withQueued([province({ code: "06" }), province({ state: "READY" })], new Set(["06", "02"]));
    expect(shown.map((item) => item.state)).toEqual(["IMPORTING", "READY"]);
  });
});

describe("zones page", () => {
  it("lists every province with its state and how many are ready", async () => {
    render(createElement(OvertureSettingsPage));

    expect(await screen.findByText("Provincias (1 de 3 con datos)")).toBeInTheDocument();
    expect(screen.getByText("Con datos")).toBeInTheDocument();
    expect(screen.getByText("Sin datos")).toBeInTheDocument();
    expect(screen.getByText("Falló la carga")).toBeInTheDocument();
    expect(screen.getByText("No se pudo descargar.")).toBeInTheDocument();
    expect(screen.queryByText("Particiones importadas")).toBeNull();
  });

  it("loads a province after a confirmation and shows it as loading", async () => {
    vi.mocked(syncOverture).mockResolvedValue({ status: "queued", celery_task_id: "t", province_code: "06" });
    render(createElement(OvertureSettingsPage));

    const row = (await screen.findByText("Buenos Aires")).closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Cargar datos" }));
    const dialog = await screen.findByRole("dialog");
    expect(syncOverture).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cargar" }));

    await waitFor(() => expect(syncOverture).toHaveBeenCalledWith("06"));
    expect(await screen.findByText("Cargando…", { selector: ".status-badge *, .status-badge" })).toBeInTheDocument();
  });

  it("blocks loading, with the reason, until a verified version exists", async () => {
    vi.mocked(getOvertureStatus).mockResolvedValue(status({ latest_verified_release: null }));
    render(createElement(OvertureSettingsPage));

    expect(await screen.findByText("Todavía no hay una versión de datos para cargar")).toBeInTheDocument();
    const row = screen.getByText("Buenos Aires").closest("tr") as HTMLElement;
    expect(within(row).getByRole("button", { name: "Cargar datos" })).toBeDisabled();
  });
});
