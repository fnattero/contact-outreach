import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
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
  restoreBlockedReason,
  stateLabel,
  stateLevel,
  verdictLabel,
  verdictLevel,
} from "@/app/prospects/prospect-helpers";
import { getCampaigns, getProspects, prospectsExportUrl, restoreProspect, type Prospect, type Provenance } from "@/lib/api";

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
  return { ...actual, getProspects: vi.fn(), getCampaigns: vi.fn(), restoreProspect: vi.fn() };
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
    relevance_verdict: null,
    relevance_reason: null,
    relevance_checked_at: null,
    relevance_override: false,
    campaign_state: "DISCOVERING",
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

  it("labels a removed business by who removed it, not as a verdict on the business", () => {
    expect(stateLabel("SKIPPED_IRRELEVANT", "Irrelevante")).toBe("Descartado por el filtro");
  });

  it("maps every filter verdict to plain Spanish and a level, with a neutral fallback", () => {
    expect([verdictLabel("FIT"), verdictLabel("UNCLEAR"), verdictLabel("UNFIT")]).toEqual(["Encaja", "Dudoso", "No encaja"]);
    expect([verdictLevel("FIT"), verdictLevel("UNCLEAR"), verdictLevel("UNFIT")]).toEqual(["success", "warning", "danger"]);
    expect(verdictLabel("MYSTERY")).toBe("Sin evaluar");
    expect(verdictLevel("MYSTERY")).toBe("inactive");
  });

  it("carries the verdict filter and drops it when blank", () => {
    expect(filtersFromForm({ verdict: "UNFIT" }, 1, 25).verdict).toBe("UNFIT");
    expect(filtersFromForm({ verdict: "" }, 1, 25).verdict).toBeUndefined();
    expect(hasActiveFilters({ verdict: "UNFIT" })).toBe(true);
    expect(prospectsExportUrl({ verdict: "UNFIT" })).toBe("/api/v1/prospects/export.csv?verdict=UNFIT");
  });

  it("allows restoring only while the campaign can still change its audience", () => {
    expect(restoreBlockedReason("DISCOVERING")).toBeNull();
    expect(restoreBlockedReason("AWAITING_APPROVAL")).toBeNull();
    expect(restoreBlockedReason("RUNNING")).toMatch(/ya empezó a enviar/);
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

  it("shows the verdict and, when expanded, the reason and the manual-restore note", async () => {
    vi.mocked(getProspects).mockResolvedValue({
      data: [
        prospect({
          relevance_verdict: "UNCLEAR",
          relevance_reason: "No se puede confirmar que tenga taller propio.",
          relevance_checked_at: "2026-10-02T12:00:00Z",
          relevance_override: true,
        }),
        prospect({ id: "prospect-2", name: "Sin evaluar" }),
      ],
      meta: { page: 1, page_size: 25, total: 2 },
    });

    render(createElement(ProspectsPage));

    expect(await screen.findByText("Dudoso")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /Expand row/i })[0]);
    expect(await screen.findByText("No se puede confirmar que tenga taller propio.")).toBeInTheDocument();
    expect(screen.getByText(/Lo recuperaste a mano/)).toBeInTheDocument();
  });

  it("offers Recuperar only for removed businesses and explains when it is blocked", async () => {
    vi.mocked(getProspects).mockResolvedValue({
      data: [
        prospect({ id: "a", name: "Descartado Uno", pipeline_state: "SKIPPED_IRRELEVANT", campaign_state: "AWAITING_APPROVAL" }),
        prospect({ id: "b", name: "Descartado Dos", pipeline_state: "SKIPPED_IRRELEVANT", campaign_state: "RUNNING" }),
        prospect({ id: "c", name: "En cola", pipeline_state: "QUEUED" }),
      ],
      meta: { page: 1, page_size: 25, total: 3 },
    });

    render(createElement(ProspectsPage));

    await screen.findByText("Descartado Uno");
    const buttons = screen.getAllByRole("button", { name: "Recuperar" });
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toBeEnabled();
    expect(buttons[1]).toBeDisabled();
    expect(screen.getAllByText(/ya empezó a enviar/).length).toBeGreaterThan(0);
  }, 20000);

  it("restores after a plain confirmation and refreshes the list", async () => {
    vi.mocked(getProspects).mockResolvedValue({
      data: [prospect({ id: "a", name: "Descartado Uno", pipeline_state: "SKIPPED_IRRELEVANT" })],
      meta: { page: 1, page_size: 25, total: 1 },
    });
    vi.mocked(restoreProspect).mockResolvedValue(prospect({ id: "a" }));

    render(createElement(App, null, createElement(ProspectsPage)));

    fireEvent.click(await screen.findByRole("button", { name: "Recuperar" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/no lo va a descartar de nuevo/)).toBeInTheDocument();
    // A plain confirmation: restoring is additive, so no typed word is asked for.
    expect(within(dialog).queryByLabelText(/Escribí/)).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Recuperar" }));

    await waitFor(() => expect(restoreProspect).toHaveBeenCalledWith("a"));
    await waitFor(() => expect(getProspects).toHaveBeenCalledTimes(2));
  });

  it("shares the Audiencia header with the filter, through tabs", async () => {
    vi.mocked(getProspects).mockResolvedValue({ data: [prospect()], meta: { page: 1, page_size: 25, total: 1 } });

    render(createElement(ProspectsPage));

    const tabs = await screen.findByRole("navigation", { name: "Audiencia" });
    expect(within(tabs).getByRole("link", { name: "Negocios" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Filtro" })).toHaveAttribute("href", "/settings/relevance");
  });
});
