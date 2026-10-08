import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OvertureSettingsPage from "@/app/settings/overture/page";
import { getOvertureStatus, type OvertureStatus } from "@/lib/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ push: vi.fn() }) }));

vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  const { sessionFor } = await import("./session");
  return { ...actual, useAuth: () => ({ session: sessionFor("ADMIN"), loading: false, refresh: vi.fn(), signOut: vi.fn() }) };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getOvertureStatus: vi.fn() };
});

function status(overrides: Partial<OvertureStatus> = {}): OvertureStatus {
  return {
    latest_snapshot_id: "snap-1",
    active_snapshot_id: "snap-1",
    attribution: {
      release_id: "2026-07-22.0",
      attribution: "Overture Maps Foundation, overturemaps.org",
      licenses: ["CDLA Permissive 2.0"],
      notices: ["Los datos pueden contener errores."],
    },
    provinces: [],
    latest_verified_release: null,
    release_checks: [],
    partitions: [],
    ...overrides,
  };
}

beforeEach(() => vi.mocked(getOvertureStatus).mockReset());

describe("overture coverage page", () => {
  it("shows the attribution, licences and notices of the active snapshot", async () => {
    vi.mocked(getOvertureStatus).mockResolvedValue(status());

    render(createElement(OvertureSettingsPage));

    expect(await screen.findByText("Overture Maps Foundation, overturemaps.org")).toBeInTheDocument();
    expect(screen.getByText("Versión 2026-07-22.0")).toBeInTheDocument();
    expect(screen.getByText("CDLA Permissive 2.0")).toBeInTheDocument();
    expect(screen.getByText("Los datos pueden contener errores.")).toBeInTheDocument();
  });

  it("explains that attribution appears once a snapshot is active", async () => {
    vi.mocked(getOvertureStatus).mockResolvedValue(status({ attribution: null, active_snapshot_id: null }));

    render(createElement(OvertureSettingsPage));

    expect(await screen.findByText("La atribución aparecerá acá cuando haya datos cargados.")).toBeInTheDocument();
  });
});
