import { render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProspectsPage from "@/app/prospects/page";
import { ProvenanceDetails } from "@/app/prospects/provenance-details";
import {
  PIPELINE_STATE_OPTIONS,
  collectAttribution,
  contactSourceLabel,
  filtersFromForm,
  hasActiveFilters,
  stateLabel,
  stateLevel,
} from "@/app/prospects/prospect-helpers";
import { getCampaigns, getProspects, prospectsExportUrl, type Prospect, type Provenance } from "@/lib/api";

const auth = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/prospects",
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
    provenance: null,
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

function provenance(overrides: Partial<Provenance> = {}): Provenance {
  return {
    release_id: "2026-07-22.0",
    overture_id: "08f2a100-6f6a-4f31-9a6f-2e931c237f81",
    confidence: "0.920",
    matched_rule: { taxonomy_code: "", name_terms: ["bobinado"] },
    contact_source: { email: "uno@taller.example", source: "website_mailto", source_url: "https://taller.example/contacto" },
    attribution: ["Overture Maps Foundation, overturemaps.org"],
    licenses: ["CDLA Permissive 2.0"],
    ...overrides,
  };
}

describe("provenance", () => {
  it("labels where a contact came from and falls back to the raw source", () => {
    expect(contactSourceLabel("overture")).toBe("Overture Places");
    expect(contactSourceLabel("website_visible_text")).toBe("Sitio web, texto visible");
    expect(contactSourceLabel("otra_fuente")).toBe("otra_fuente");
  });

  it("collects each attribution line once, in the order first seen", () => {
    expect(
      collectAttribution([
        { provenance: { attribution: ["A", "B"] } },
        { provenance: null },
        { provenance: { attribution: ["B", "C"] } },
      ]),
    ).toEqual(["A", "B", "C"]);
  });

  it("shows the release, record id, rule, contact origin, attribution and licences", () => {
    render(createElement(ProvenanceDetails, { provenance: provenance() }));

    expect(screen.getByText("Overture 2026-07-22.0")).toBeInTheDocument();
    expect(screen.getByText("08f2a100-6f6a-4f31-9a6f-2e931c237f81")).toBeInTheDocument();
    expect(screen.getByText("Términos: bobinado")).toBeInTheDocument();
    expect(screen.getByText(/Sitio web, enlace directo de correo/)).toBeInTheDocument();
    expect(screen.getByText("Overture Maps Foundation, overturemaps.org")).toBeInTheDocument();
    expect(screen.getByText("CDLA Permissive 2.0")).toBeInTheDocument();
  });

  it("says plainly when a record has no Overture provenance", () => {
    render(createElement(ProvenanceDetails, { provenance: null }));

    expect(screen.getByText(/no proviene de Overture/)).toBeInTheDocument();
  });

  it("explains a record that kept no structured rule or selected email", () => {
    render(createElement(ProvenanceDetails, { provenance: provenance({ matched_rule: null, contact_source: null, overture_id: null }) }));

    expect(screen.getByText("Este registro no conserva una regla estructurada.")).toBeInTheDocument();
    expect(screen.getByText("No se seleccionó un correo.")).toBeInTheDocument();
    expect(screen.getByText("No conservado en este registro anterior")).toBeInTheDocument();
  });
});

describe("prospects page", () => {
  it("keeps the data attribution visible beneath the table", async () => {
    vi.mocked(getProspects).mockResolvedValue({
      data: [prospect({ provenance: provenance() })],
      meta: { page: 1, page_size: 25, total: 1 },
    });

    render(createElement(ProspectsPage));

    expect(await screen.findByLabelText("Atribución de datos")).toHaveTextContent("Overture Maps Foundation, overturemaps.org");
  });

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
