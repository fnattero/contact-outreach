import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AttentionPage from "@/app/attention/page";
import AuditPage from "@/app/audit/page";
import CampaignsPage from "@/app/campaigns/page";
import ContactsPage from "@/app/contacts/page";
import OutboundPage from "@/app/outbound/page";
import ResponsesPage from "@/app/responses/page";
import {
  getAttention,
  getAuditEvents,
  getAutomationConfiguration,
  getCampaigns,
  getContacts,
  getDashboardSummary,
  getInboundMessages,
  getInboundThread,
  getOutboundMessages,
  resolveHumanTask,
} from "@/lib/api";

// Text an attacker could put in a business name, a message body or a subject. It must reach the
// screen as plain characters, never as an element or a script.
const HOSTILE = `<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>`;

const auth = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/contacts",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  const { sessionFor: forRole } = await import("./session");
  return {
    ...actual,
    useAuth: () => ({ session: forRole(auth.role), loading: false, refresh: vi.fn(), signOut: vi.fn() }),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getAttention: vi.fn(),
    getAuditEvents: vi.fn(),
    getAutomationConfiguration: vi.fn(),
    getCampaigns: vi.fn(),
    getContacts: vi.fn(),
    getDashboardSummary: vi.fn(),
    getInboundMessages: vi.fn(),
    getInboundThread: vi.fn(),
    getOutboundMessages: vi.fn(),
    resolveHumanTask: vi.fn(),
  };
});

const page = <T,>(data: T) => ({ data, meta: { page: 1, page_size: 25, total: Array.isArray(data) ? data.length : 1 } });
const inApp = (element: Parameters<typeof createElement>[0]) => render(createElement(App, null, createElement(element)));

function noInjection(container: HTMLElement) {
  expect(container.querySelector("img[onerror], script")).toBeNull();
  expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
}

beforeEach(() => {
  auth.role = "ADMIN";
  delete (window as unknown as { __xss?: number }).__xss;
  for (const mocked of [
    getAttention,
    getAuditEvents,
    getAutomationConfiguration,
    getCampaigns,
    getContacts,
    getDashboardSummary,
    getInboundMessages,
    getInboundThread,
    getOutboundMessages,
    resolveHumanTask,
  ]) {
    (mocked as unknown as ReturnType<typeof vi.fn>).mockReset();
  }
});

describe("contacts", () => {
  const contact = {
    id: "c1",
    name: HOSTILE,
    organization_name: HOSTILE,
    preferred_email: "a@example.com",
    status: "ACTIVE",
    last_interaction_at: null,
    open_task_count: 0,
    next_follow_up_at: null,
  };

  it("shows hostile names as plain text", async () => {
    vi.mocked(getContacts).mockResolvedValue(page([contact]) as never);

    const { container } = inApp(ContactsPage);

    expect((await screen.findAllByText(HOSTILE, { exact: false })).length).toBeGreaterThan(0);
    noInjection(container);
  });

  it("offers contact creation to administrators only", async () => {
    vi.mocked(getContacts).mockResolvedValue(page([contact]) as never);
    const admin = inApp(ContactsPage);
    expect(await screen.findByRole("button", { name: "Nuevo contacto" })).toBeInTheDocument();
    admin.unmount();

    auth.role = "VENDEDOR";
    inApp(ContactsPage);
    await screen.findAllByText(HOSTILE, { exact: false });
    expect(screen.queryByRole("button", { name: "Nuevo contacto" })).not.toBeInTheDocument();
  });

  it("explains a failed load with the server's own words", async () => {
    vi.mocked(getContacts).mockRejectedValue({ status: 500, detail: "No se pudo leer la agenda." });

    inApp(ContactsPage);

    expect(await screen.findByText("No se pudo leer la agenda.")).toBeInTheDocument();
  });
});

describe("campaigns", () => {
  const campaign = {
    id: "k1",
    name: HOSTILE,
    state: "RUNNING",
    state_label: "En curso",
    discovery_state: "RUNNING",
    discovery_state_label: "Buscando",
    delivery_mode: "LIVE",
  };

  it("shows a hostile name as plain text and hides the audience size from a seller", async () => {
    auth.role = "VENDEDOR";
    vi.mocked(getCampaigns).mockResolvedValue(page([campaign]) as never);

    const { container } = inApp(CampaignsPage);

    expect((await screen.findAllByText(HOSTILE, { exact: false })).length).toBeGreaterThan(0);
    noInjection(container);
    expect(screen.getByText("Solo administración")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva campaña" })).not.toBeInTheDocument();
  });

  it("shows an administrator the audience and the way to create a campaign", async () => {
    vi.mocked(getCampaigns).mockResolvedValue(
      page([{ ...campaign, name: "Taller", metrics: { enrollments: 12, prospects: 12, initial_messages: 0, sent: 0, review_ready: 0, queued: 0, errors: 0 } }]) as never,
    );

    inApp(CampaignsPage);

    expect(await screen.findByText("Taller")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nueva campaña" })).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("explains an empty list differently for each role", async () => {
    vi.mocked(getCampaigns).mockResolvedValue(page([]) as never);
    const admin = inApp(CampaignsPage);
    expect(await screen.findByText("Todavía no hay campañas")).toBeInTheDocument();
    expect(screen.getByText("Crear campaña")).toBeInTheDocument();
    admin.unmount();

    auth.role = "VENDEDOR";
    inApp(CampaignsPage);
    expect(await screen.findByText("Volver al resumen")).toBeInTheDocument();
    expect(screen.queryByText("Crear campaña")).not.toBeInTheDocument();
  });
});

describe("attention queue", () => {
  const task = {
    id: "t1",
    contact_id: "c1",
    contact_name: HOSTILE,
    kind: "REPLY_REVIEW",
    reason: "MEETING_OR_DATE",
    status: "OPEN",
    title: HOSTILE,
    summary: HOSTILE,
    next_step: "",
    opened_at: "2026-10-01T10:00:00Z",
  };

  it("shows hostile task text as plain text and gives a seller no way to decide", async () => {
    auth.role = "VENDEDOR";
    vi.mocked(getAttention).mockResolvedValue([task] as never);

    const { container } = inApp(AttentionPage);

    expect((await screen.findAllByText(HOSTILE, { exact: false })).length).toBeGreaterThan(0);
    noInjection(container);
    expect(screen.getByText(/solo lectura/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resolver" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Descartar" })).not.toBeInTheDocument();
  });

  it("lets an administrator resolve a case only after a confirmation, with their note", async () => {
    vi.mocked(getAttention).mockResolvedValue([{ ...task, contact_name: "Ana", title: "Pidió reunión" }] as never);
    vi.mocked(resolveHumanTask).mockResolvedValue({ id: "t1", status: "RESOLVED" } as never);
    inApp(AttentionPage);

    fireEvent.change(await screen.findByLabelText("Nota para Ana"), { target: { value: "Llamé y quedó resuelto." } });
    fireEvent.click(screen.getByRole("button", { name: "Resolver" }));

    expect(resolveHumanTask).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Resolver revisión" })).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/Escribí CONFIRMAR/), { target: { value: "CONFIRMAR" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Resolver revisión" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Resolver revisión" }));
    await waitFor(() => expect(resolveHumanTask).toHaveBeenCalledWith("t1", "resolve", "Llamé y quedó resuelto."));
    await waitFor(() => expect(screen.queryByText("Pidió reunión")).not.toBeInTheDocument());
  });

  it("orders the most urgent cases first", async () => {
    vi.mocked(getAttention).mockResolvedValue([
      { ...task, id: "a", title: "Rutina", reason: "OTHER", opened_at: "2026-10-01T09:00:00Z" },
      { ...task, id: "b", title: "Queja", reason: "COMPLAINT", opened_at: "2026-10-01T11:00:00Z" },
    ] as never);
    inApp(AttentionPage);

    const titles = (await screen.findAllByRole("heading", { level: 2 })).map((item) => item.textContent);

    expect(titles.indexOf("Queja")).toBeLessThan(titles.indexOf("Rutina"));
  });

  it("explains an empty queue and a failed load", async () => {
    vi.mocked(getAttention).mockResolvedValue([] as never);
    const empty = inApp(AttentionPage);
    expect(await screen.findByText("No hay revisiones abiertas")).toBeInTheDocument();
    empty.unmount();

    vi.mocked(getAttention).mockRejectedValue({ detail: "Sin conexión con el servidor." });
    inApp(AttentionPage);
    expect(await screen.findByText("Sin conexión con el servidor.")).toBeInTheDocument();
  });
});

describe("audit trail", () => {
  const event = {
    id: "e1",
    created_at: "2026-10-01T10:00:00Z",
    actor: HOSTILE,
    actor_type: "USER",
    action: "campaign.approved",
    entity_type: "Campaign",
    entity_id: "k1",
    correlation_id: "corr-1",
  };

  it("refuses a seller on the page itself and shows no event", async () => {
    auth.role = "VENDEDOR";
    vi.mocked(getAuditEvents).mockResolvedValue(page([event]) as never);

    inApp(AuditPage);

    expect(await screen.findByText("No tenés permisos para ver auditoría.")).toBeInTheDocument();
    expect(screen.queryByText(HOSTILE, { exact: false })).not.toBeInTheDocument();
  });

  it("shows an administrator the events, with a hostile actor as plain text", async () => {
    vi.mocked(getAuditEvents).mockResolvedValue(page([event]) as never);

    const { container } = inApp(AuditPage);

    expect(await screen.findByText(HOSTILE, { exact: false })).toBeInTheDocument();
    noInjection(container);
  });

  it("filters by actor and offers to clear an empty result", async () => {
    vi.mocked(getAuditEvents).mockResolvedValue(page([event, { ...event, id: "e2", actor: "Ana" }]) as never);
    inApp(AuditPage);
    await screen.findByText("Ana");

    fireEvent.change(screen.getByLabelText("Filtrar por actor"), { target: { value: "zzz" } });
    expect(await screen.findByText("No hay eventos para estos filtros")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Limpiar filtros" }));

    expect(await screen.findByText("Ana")).toBeInTheDocument();
  });

  it("explains a failed load", async () => {
    vi.mocked(getAuditEvents).mockRejectedValue({ detail: "La auditoría no responde." });

    inApp(AuditPage);

    expect(await screen.findByText("La auditoría no responde.")).toBeInTheDocument();
  });
});

describe("mail log and inbox", () => {
  const inbound = {
    id: "m1",
    external_at: "2026-10-01T10:00:00Z",
    sender: HOSTILE,
    subject: HOSTILE,
    classification: "INTERESTED",
    classification_label: "Interesado",
    is_human: true,
    is_read: false,
    gmail_thread_id: "t",
    campaign_id: null,
    body_preview: HOSTILE,
  };
  const summary = { safety: { send_mode: "live", send_kill_switch: false } };

  beforeEach(() => {
    vi.mocked(getAttention).mockResolvedValue([] as never);
    vi.mocked(getDashboardSummary).mockResolvedValue(summary as never);
    vi.mocked(getAutomationConfiguration).mockResolvedValue(null as never);
    vi.mocked(getCampaigns).mockResolvedValue(page([]) as never);
  });

  it("shows hostile mail as plain text in the list and in the conversation", async () => {
    vi.mocked(getInboundMessages).mockResolvedValue(page([inbound]) as never);
    vi.mocked(getInboundThread).mockResolvedValue({
      inbound: { ...inbound, body_text: HOSTILE },
      timeline: [{ direction: "inbound", at: "2026-10-01T10:00:00Z", sender: HOSTILE, body_text: HOSTILE, classification: "INTERESTED" }],
    } as never);

    const { container } = inApp(ResponsesPage);

    expect((await screen.findAllByText(HOSTILE, { exact: false })).length).toBeGreaterThan(2);
    noInjection(container);
  });

  it("gives a seller no reply box, and an administrator one for a human message", async () => {
    vi.mocked(getInboundMessages).mockResolvedValue(page([{ ...inbound, sender: "Ana", subject: "Hola", body_preview: "x" }]) as never);
    vi.mocked(getInboundThread).mockResolvedValue({ inbound: { ...inbound, body_text: "x" }, timeline: [] } as never);
    auth.role = "VENDEDOR";
    const seller = inApp(ResponsesPage);
    await screen.findByText("No hay mensajes en este hilo.");
    expect(screen.queryByLabelText("Texto de la respuesta")).not.toBeInTheDocument();
    seller.unmount();

    auth.role = "ADMIN";
    inApp(ResponsesPage);
    expect(await screen.findByLabelText("Texto de la respuesta")).toBeInTheDocument();
  });

  it("explains why a reply cannot be sent when real sending is off", async () => {
    vi.mocked(getDashboardSummary).mockResolvedValue({ safety: { send_mode: "dry-run", send_kill_switch: false } } as never);
    vi.mocked(getInboundMessages).mockResolvedValue(page([{ ...inbound, sender: "Ana" }]) as never);
    vi.mocked(getInboundThread).mockResolvedValue({ inbound: { ...inbound, body_text: "x" }, timeline: [] } as never);
    inApp(ResponsesPage);

    fireEvent.change(await screen.findByLabelText("Texto de la respuesta"), { target: { value: "Hola" } });

    expect(screen.getByRole("button", { name: "Revisar y autorizar" })).toBeDisabled();
  });

  it("explains an inbox that failed to load or is empty", async () => {
    vi.mocked(getInboundMessages).mockRejectedValue({ detail: "Gmail no respondió." });
    const failed = inApp(ResponsesPage);
    expect(await screen.findByText("Gmail no respondió.")).toBeInTheDocument();
    failed.unmount();

    vi.mocked(getInboundMessages).mockResolvedValue(page([]) as never);
    inApp(ResponsesPage);
    expect(await screen.findByText("Todavía no hay respuestas")).toBeInTheDocument();
  });

  it("lists sent mail with a hostile recipient as plain text and a plain reason for each failure", async () => {
    vi.mocked(getOutboundMessages).mockResolvedValue(
      page([
        {
          id: "o1",
          created_at: "2026-10-01T10:00:00Z",
          recipient: HOSTILE,
          subject: HOSTILE,
          kind: "INITIAL",
          kind_label: "Propuesta",
          state: "SEND_FAILED",
          state_label: "Falló",
          sent_at: null,
          simulated_at: null,
          campaign_id: null,
          body_text: HOSTILE,
          error: "Gmail connection refused: token=abc123",
        },
      ]) as never,
    );

    const { container } = inApp(OutboundPage);

    expect(await screen.findByText(HOSTILE, { exact: false })).toBeInTheDocument();
    noInjection(container);
    expect(screen.getByText("Gmail no está listo para este envío.")).toBeInTheDocument();
  });

  it("explains an empty or failed mail log", async () => {
    vi.mocked(getOutboundMessages).mockResolvedValue(page([]) as never);
    const empty = inApp(OutboundPage);
    expect(await screen.findByText("Todavía no hay envíos")).toBeInTheDocument();
    empty.unmount();

    vi.mocked(getOutboundMessages).mockRejectedValue({ detail: "El registro no está disponible." });
    inApp(OutboundPage);
    expect(await screen.findByText("El registro no está disponible.")).toBeInTheDocument();
  });
});
