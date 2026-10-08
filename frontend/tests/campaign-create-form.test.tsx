import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import NewCampaignPage from "@/app/campaigns/new/page";
import {
  getCatalogs,
  getMessageTemplates,
  getSendMode,
  type MessageTemplate,
  type SearchZone,
  type SendMode,
} from "@/lib/api";
import { clearCampaignDraft, readCampaignDraft } from "@/lib/campaign-draft";

const router = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/campaigns/new" }));
vi.mock("@/components/auth-provider", async () => {
  const { sessionFor } = await import("./session");
  return { useAuth: () => ({ session: sessionFor("ADMIN"), loading: false, refresh: vi.fn(), signOut: vi.fn() }) };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    createCampaign: vi.fn(),
    getCatalogs: vi.fn(),
    getMessageTemplates: vi.fn(),
    getSendMode: vi.fn(),
    getSearchCategories: vi.fn().mockResolvedValue([
      { id: "category-1", name: "Talleres", active: true, sort_order: 1, rules_revision: 1, rules: [] },
    ]),
    getSearchZones: vi.fn().mockImplementation((level?: string) =>
      Promise.resolve(level === "PROVINCE" ? [zone("province-1", "")] : [zone("district-1", "province-1")]),
    ),
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
    location_text: `Zona ${id}`,
    boundary_revision: 1,
    boundary_hash: `hash-${id}`,
  };
}

function template(kind: MessageTemplate["kind"], body: string, subject = ""): MessageTemplate {
  return { id: kind, kind, subject, body, revision: 3, content_hash: "h", approved_at: "2026-10-01T12:00:00Z", active: true };
}

function sendMode(overrides: Partial<SendMode> = {}): SendMode {
  return { server_allows_live: false, app_enabled: false, effective_live: false, enabled_by: null, ...overrides };
}

beforeEach(() => {
  clearCampaignDraft();
  router.push.mockReset();
  vi.mocked(getCatalogs).mockResolvedValue([
    { id: "c1", name: "Catálogo", version: 1, original_filename: "c.pdf", detected_mime: "application/pdf", byte_size: 10, sha256: "x", active: true, missing: false, created_at: "2026-10-01T12:00:00Z" },
  ]);
  vi.mocked(getMessageTemplates).mockResolvedValue([
    template("INITIAL", "Texto de la propuesta inicial", "Propuesta comercial"),
    template("REMINDER", "Texto del recordatorio"),
  ]);
  vi.mocked(getSendMode).mockResolvedValue(sendMode());
});

describe("new campaign messages", () => {
  it("shows the reminder text only once the reminder is turned on", async () => {
    render(createElement(NewCampaignPage));
    expect(await screen.findByText("Texto de la propuesta inicial")).toBeInTheDocument();
    expect(screen.queryByText("Texto del recordatorio")).toBeNull();

    fireEvent.click(screen.getByRole("switch"));

    expect(await screen.findByText("Texto del recordatorio")).toBeInTheDocument();
    expect(screen.getByText("El mismo de la propuesta, en el mismo hilo")).toBeInTheDocument();
  });

  it("keeps what was filled in when going to edit a message, and restores it on return", async () => {
    const view = render(createElement(NewCampaignPage));
    fireEvent.change(await screen.findByLabelText("Nombre de la campaña"), { target: { value: "Mi campaña" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Editar mensaje" })[0]);

    expect(router.push).toHaveBeenCalledWith("/settings/message-templates");
    expect(readCampaignDraft()).toMatchObject({ name: "Mi campaña" });

    view.unmount();
    render(createElement(NewCampaignPage));
    expect(await screen.findByLabelText("Nombre de la campaña")).toHaveValue("Mi campaña");
  });

  it("forgets the draft when the person cancels", async () => {
    render(createElement(NewCampaignPage));
    fireEvent.change(await screen.findByLabelText("Nombre de la campaña"), { target: { value: "Otra" } });
    expect(readCampaignDraft()).not.toBeNull();

    fireEvent.click(screen.getByRole("link", { name: "Cancelar" }));

    expect(readCampaignDraft()).toBeNull();
  });
});

describe("new campaign mode", () => {
  it("explains why automatic sending is not available yet", async () => {
    vi.mocked(getSendMode).mockResolvedValue(sendMode({ server_allows_live: true }));
    render(createElement(NewCampaignPage));

    const automatic = await screen.findByRole("radio", { name: /Automático/ });
    expect(automatic).toBeDisabled();
    expect(screen.getByText(/Los envíos reales están apagados/)).toBeInTheDocument();
  });

  it("explains it when the server itself does not allow real sending", async () => {
    vi.mocked(getSendMode).mockResolvedValue(sendMode({ server_allows_live: false }));
    render(createElement(NewCampaignPage));

    expect(await screen.findByText(/El servidor todavía no permite envíos reales/)).toBeInTheDocument();
  });

  it("offers automatic sending once real sending is on, and asks to confirm it", async () => {
    vi.mocked(getSendMode).mockResolvedValue(sendMode({ server_allows_live: true, app_enabled: true, effective_live: true }));
    render(createElement(NewCampaignPage));

    const automatic = await screen.findByRole("radio", { name: /Automático/ });
    expect(automatic).toBeEnabled();
    expect(screen.queryByText(/Entiendo que, al aprobar/)).toBeNull();

    fireEvent.click(automatic);

    expect(await screen.findByText(/Entiendo que, al aprobar/)).toBeInTheDocument();
  });

  it("describes both ways of approving in plain words", async () => {
    render(createElement(NewCampaignPage));

    const group = (await screen.findByText("¿Cómo querés aprobar los mensajes?")).closest(".ant-form-item") as HTMLElement;
    expect(within(group).getByText("Todos juntos")).toBeInTheDocument();
    expect(within(group).getByText("Uno por uno")).toBeInTheDocument();
    expect(screen.queryByText("Campaña completa")).toBeNull();
  });
});

describe("campaign draft banner", () => {
  it("is written to session storage as the form changes", async () => {
    render(createElement(NewCampaignPage));
    fireEvent.change(await screen.findByLabelText("Nombre de la campaña"), { target: { value: "Guardada" } });

    await waitFor(() => expect(readCampaignDraft()).toMatchObject({ name: "Guardada" }));
  });
});
