import { render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProspectsPage from "@/app/prospects/page";
import {
  PIPELINE_STATE_OPTIONS,
  filtersFromForm,
  hasActiveFilters,
  stateLabel,
  stateLevel,
} from "@/app/prospects/prospect-helpers";
import { getCampaigns, getProspects, prospectsExportUrl, type Prospect } from "@/lib/api";

const auth = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/prospects",
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  return {
    ...actual,
    useAuth: () => ({ session: { role: auth.role }, loading: false, refresh: vi.fn(), signOut: vi.fn() }),
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getProspects: vi.fn(), getCampaigns: vi.fn() };
});

function prospect(overrides: Partial<Prospect> = {}): Prospect {
  return {
    id: "prospect-1",
    name: "Taller Uno",
    address: "Calle 1",
    neighborhood: "Palermo",
    category: "Motores",
    website: "",
    pipeline_state: "QUEUED",
    pipeline_state_label: "En cola",
    primary_email: "uno@taller.example",
    historical_score: null,
    campaign: { id: "campaign-1", name: "Primera campaña" },
    created_at: "2026-10-01T12:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  auth.role = "ADMIN";
  vi.mocked(getCampaigns).mockResolvedValue({ data: [], meta: { page: 1, page_size: 25, total: 0 } });
  vi.mocked(getProspects).mockReset();
});

describe("prospect helpers", () => {
  it("builds filters without blanks and trims free text", () => {
    expect(filtersFromForm({ q: "  taller ", campaign: "", state: undefined, category: "   ", neighborhood: "Palermo" }, 2, 25)).toEqual({
      q: "taller",
      campaign: undefined,
      state: undefined,
      category: undefined,
      neighborhood: "Palermo",
      page: 2,
      page_size: 25,
    });
  });

  it("detects whether any filter is set, ignoring whitespace", () => {
    expect(hasActiveFilters({})).toBe(false);
    expect(hasActiveFilters({ q: "   ", category: " " })).toBe(false);
    expect(hasActiveFilters({ state: "ERROR" })).toBe(true);
  });

  it("covers every pipeline state the backend can report", () => {
    expect(PIPELINE_STATE_OPTIONS.map((option) => option.value).sort()).toEqual(
      [
        "ANALYZED", "DISCOVERED", "EMAIL_FOUND", "ENRICHED", "ERROR", "QUEUED",
        "SKIPPED_DUPLICATE", "SKIPPED_IRRELEVANT", "SKIPPED_NO_EMAIL",
      ],
    );
  });

  it("falls back to the server label and a neutral level for an unknown state", () => {
    expect(stateLabel("QUEUED", "x")).toBe("En cola");
    expect(stateLabel("MYSTERY", "Etiqueta del servidor")).toBe("Etiqueta del servidor");
    expect(stateLevel("ERROR")).toBe("danger");
    expect(stateLevel("MYSTERY")).toBe("inactive");
  });

  it("exports the same filters but never the page", () => {
    expect(prospectsExportUrl({ q: "taller", state: "ERROR", page: 3, page_size: 25 })).toBe(
      "/api/v1/prospects/export.csv?q=taller&state=ERROR",
    );
    expect(prospectsExportUrl()).toBe("/api/v1/prospects/export.csv");
  });
});

describe("prospects page", () => {
  it("lists prospects with their campaign link and the selected email", async () => {
    vi.mocked(getProspects).mockResolvedValue({
      data: [prospect(), prospect({ id: "prospect-2", name: "Taller Dos", primary_email: null, pipeline_state: "ERROR", pipeline_state_label: "Error" })],
      meta: { page: 1, page_size: 25, total: 2 },
    });

    render(createElement(ProspectsPage));

    expect(await screen.findByText("Taller Uno")).toBeInTheDocument();
    expect(screen.getByText("uno@taller.example")).toBeInTheDocument();
    expect(screen.getByText("Sin correo seleccionado")).toBeInTheDocument();
    expect(screen.getByText("2 resultados.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Primera campaña" })[0]).toHaveAttribute("href", "/campaigns/campaign-1");
    expect(getProspects).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 25 }));
  });

  it("explains an empty audience instead of showing a bare table", async () => {
    vi.mocked(getProspects).mockResolvedValue({ data: [], meta: { page: 1, page_size: 25, total: 0 } });

    render(createElement(ProspectsPage));

    expect(await screen.findByText("Todavía no hay prospectos")).toBeInTheDocument();
  });

  it("refuses a seller without calling the API", () => {
    auth.role = "VENDEDOR";

    render(createElement(ProspectsPage));

    expect(screen.getByText("No tenés permisos para ver la audiencia.")).toBeInTheDocument();
    return waitFor(() => expect(getProspects).not.toHaveBeenCalled());
  });
});
