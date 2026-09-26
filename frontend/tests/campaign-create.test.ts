import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  WEEK_OPTIONS,
  buildZoneMap,
  pruneZoneSelection,
  zonesForProvince,
} from "@/app/campaigns/new/campaign-form-helpers";
import NewCampaignPage from "@/app/campaigns/new/page";
import type { SearchZone, SearchZoneGeometry } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    session: { role: "ADMIN" },
    loading: false,
    refresh: vi.fn(),
    signOut: vi.fn(),
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    createCampaign: vi.fn(),
    getCatalogs: vi.fn().mockResolvedValue([]),
    getMessageTemplates: vi.fn().mockResolvedValue([]),
    getSearchCategories: vi.fn().mockResolvedValue([{
      id: "category-1",
      name: "Talleres",
      sort_order: 1,
      rules_revision: 1,
      rules: [],
    }]),
    getSearchZones: vi.fn().mockImplementation((level?: string) => Promise.resolve(level === "PROVINCE"
      ? [zone("province-1", "")]
      : [zone("district-1", "province-1")])),
  };
});

function zone(id: string, parentId: string): SearchZone {
  return {
    id,
    name: `Zona ${id}`,
    official_code: id,
    level: "DISTRICT",
    province_code: parentId,
    province_name: `Provincia ${parentId}`,
    parent_id: parentId,
    selectable: true,
    location_text: `Zona ${id}, Provincia ${parentId}, Argentina`,
    boundary_revision: 1,
    boundary_hash: `hash-${id}`,
  };
}

describe("new campaign scheduling", () => {
  it("offers Saturday and Sunday without adding them to the weekday default implicitly", () => {
    expect(WEEK_OPTIONS.map((day) => day.value)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(WEEK_OPTIONS[5]).toMatchObject({ label: "Sáb", fullLabel: "Sábado" });
    expect(WEEK_OPTIONS[6]).toMatchObject({ label: "Dom", fullLabel: "Domingo" });
  });
});

describe("new campaign prerequisites", () => {
  it("keeps the complete form visible and disables only creation when no catalog exists", async () => {
    render(createElement(NewCampaignPage));

    expect(await screen.findByText("Propósito de la campaña")).toBeInTheDocument();
    expect(screen.getByText("Audiencia y zona")).toBeInTheDocument();
    expect(screen.getByText("Revisión y calendario")).toBeInTheDocument();
    expect(screen.getByText("Opciones avanzadas")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Contenido de la propuesta" }).parentElement)
      .toHaveClass("campaign-form-section");
    expect(screen.getByText("Falta un catálogo para adjuntar")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ir a catálogos" })).toHaveAttribute("href", "/catalogs");
    expect(screen.getByRole("button", { name: "Crear borrador" })).toBeDisabled();
  });
});

describe("new campaign geography", () => {
  const zones = [zone("a", "north"), zone("b", "north"), zone("c", "south")];

  it("keeps only zones that belong to provinces still selected", () => {
    expect(pruneZoneSelection(["a", "c"], zones, ["north"])).toEqual(["a"]);
    expect(zonesForProvince(zones, "south").map((item) => item.id)).toEqual(["c"]);
  });

  it("projects official polygon geometry into a selectable SVG path", () => {
    const geometries: SearchZoneGeometry[] = [{
      id: "a",
      boundary_revision: 1,
      boundary_hash: "hash-a",
      bbox: [-60, -35, -59, -34],
      geojson: {
        type: "Polygon",
        coordinates: [[[-60, -35], [-59, -35], [-59, -34], [-60, -34], [-60, -35]]],
      },
    }];

    const map = buildZoneMap([zones[0]], geometries);

    expect(map.viewBox).toBe("0 0 1000 560");
    expect(map.paths).toHaveLength(1);
    expect(map.paths[0]).toMatchObject({ id: "a", name: "Zona a" });
    expect(map.paths[0].path).toMatch(/^M.+Z$/);
  });
});
