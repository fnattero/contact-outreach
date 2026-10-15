import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ContactDetailPage from "@/app/contacts/[id]/page";
import NewContactPage from "@/app/contacts/new/page";
import {
  addContactEmail,
  createContact,
  createContactRestriction,
  getCommunicationPlans,
  getContact,
  revokeContactRestriction,
  setPreferredEmail,
  validateContactEmail,
  type ContactDetail,
} from "@/lib/api";

const HOSTILE = `<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>`;

const env = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR", push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "c1" }),
  usePathname: () => "/contacts/c1",
  useRouter: () => ({ push: env.push, replace: vi.fn() }),
}));
vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  const { sessionFor } = await import("./session");
  return {
    ...actual,
    useAuth: () => ({ session: sessionFor(env.role), loading: false, refresh: vi.fn(), signOut: vi.fn() }),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    addContactEmail: vi.fn(),
    createContact: vi.fn(),
    createContactRestriction: vi.fn(),
    getCommunicationPlans: vi.fn(),
    getContact: vi.fn(),
    revokeContactRestriction: vi.fn(),
    setPreferredEmail: vi.fn(),
    validateContactEmail: vi.fn(),
  };
});

function contact(overrides: Partial<ContactDetail> = {}): ContactDetail {
  return {
    id: "c1",
    name: "Ana",
    organization_name: "Taller Ana",
    organization_id: "o1",
    preferred_email: "ana@example.com",
    status: "ACTIVE",
    last_interaction_at: "2026-10-01T10:00:00Z",
    open_task_count: 0,
    next_follow_up_at: null,
    emails: [
      { id: "e1", original_email: "ana@example.com", label: "Principal", is_preferred: true, validity: "VALID", validated_at: null, invalid_reason: "", active_restriction_count: 0 },
      { id: "e2", original_email: "ventas@example.com", label: "", is_preferred: false, validity: "UNKNOWN", validated_at: null, invalid_reason: "", active_restriction_count: 0 },
    ],
    restrictions: [
      { id: "r-manual", scope: "CONTACT", kind: "MANUAL", evidence: "Pidió no recibir más", revoked_at: null, created_at: "2026-10-01T10:00:00Z" },
      { id: "r-unsub", scope: "CONTACT", kind: "UNSUBSCRIBE", evidence: "Baja por correo", revoked_at: null, created_at: "2026-10-01T10:00:00Z" },
      { id: "r-old", scope: "EMAIL", kind: "MANUAL", evidence: "Ya no aplica", revoked_at: "2026-10-02T10:00:00Z", created_at: "2026-10-01T10:00:00Z" },
    ],
    timelines: [
      {
        subject: "Propuesta",
        first_at: "2026-10-01T10:00:00Z",
        last_at: "2026-10-01T11:00:00Z",
        items: [{ direction: "inbound", sender: HOSTILE, happened_at: "2026-10-01T10:00:00Z", body: HOSTILE, needs_attention: true }],
      },
    ],
    ...overrides,
  } as unknown as ContactDetail;
}

const inApp = (element: Parameters<typeof createElement>[0]) => render(createElement(App, null, createElement(element)));

beforeEach(() => {
  env.role = "ADMIN";
  env.push.mockReset();
  delete (window as unknown as { __xss?: number }).__xss;
  for (const mocked of [addContactEmail, createContact, createContactRestriction, getCommunicationPlans, getContact, revokeContactRestriction, setPreferredEmail, validateContactEmail]) {
    (mocked as unknown as ReturnType<typeof vi.fn>).mockReset();
  }
  vi.mocked(getContact).mockResolvedValue(contact());
  vi.mocked(getCommunicationPlans).mockResolvedValue([]);
});

describe("contact detail", () => {
  it("shows hostile conversation text as plain text", async () => {
    const { container } = inApp(ContactDetailPage);

    expect((await screen.findAllByText(HOSTILE, { exact: false })).length).toBeGreaterThan(0);
    expect(container.querySelector("img[onerror], script")).toBeNull();
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
    expect(screen.getByText("Requiere atención")).toBeInTheDocument();
    expect(screen.getByText("La ficha muestra texto plano")).toBeInTheDocument();
  });

  it("gives a seller a read-only view and never even asks for the scheduled plans", async () => {
    env.role = "VENDEDOR";

    inApp(ContactDetailPage);
    await screen.findByRole("heading", { name: "Ana" });

    for (const control of ["Agregar email", "Agregar restricción manual", "Activar revisión periódica", "Preferir", "Validar", "Revocar"]) {
      expect(screen.queryByRole("button", { name: control })).not.toBeInTheDocument();
    }
    expect(screen.queryByText("Seguimiento programado")).not.toBeInTheDocument();
    expect(getCommunicationPlans).not.toHaveBeenCalled();
  });

  it("offers an administrator only the actions that make sense for each address", async () => {
    inApp(ContactDetailPage);
    await screen.findByText("ventas@example.com");

    expect(screen.getAllByRole("button", { name: "Preferir" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Validar" })).toHaveLength(1);
    expect(getCommunicationPlans).toHaveBeenCalledWith("c1");
  });

  it("changes the preferred address through the API", async () => {
    vi.mocked(setPreferredEmail).mockResolvedValue(undefined as never);
    inApp(ContactDetailPage);
    await screen.findByText("ventas@example.com");

    fireEvent.click(screen.getByRole("button", { name: "Preferir" }));

    await waitFor(() => expect(setPreferredEmail).toHaveBeenCalledWith("c1", "e2"));
  });

  it("asks the server to validate an address that was never checked", async () => {
    vi.mocked(validateContactEmail).mockResolvedValue(undefined as never);
    inApp(ContactDetailPage);
    await screen.findByText("ventas@example.com");

    fireEvent.click(screen.getByRole("button", { name: "Validar" }));

    await waitFor(() => expect(validateContactEmail).toHaveBeenCalledWith("c1", "e2"));
  });

  it("adds an email only when it is a valid address", async () => {
    vi.mocked(addContactEmail).mockResolvedValue(undefined as never);
    inApp(ContactDetailPage);
    const email = await screen.findByLabelText("Email");

    fireEvent.change(email, { target: { value: "no-es-un-email" } });
    fireEvent.click(screen.getByRole("button", { name: "Agregar email" }));
    await waitFor(() => expect(document.querySelector(".ant-form-item-explain-error")).not.toBeNull());
    expect(addContactEmail).not.toHaveBeenCalled();

    fireEvent.change(email, { target: { value: "nuevo@example.com" } });
    fireEvent.change(screen.getByLabelText("Etiqueta"), { target: { value: "Compras" } });
    fireEvent.click(screen.getByRole("button", { name: "Agregar email" }));
    await waitFor(() => expect(addContactEmail).toHaveBeenCalledWith("c1", "nuevo@example.com", "Compras", false));
  });

  it("revokes only a manual, still-active restriction, and only after confirming", async () => {
    vi.mocked(revokeContactRestriction).mockResolvedValue(undefined as never);
    inApp(ContactDetailPage);
    await screen.findByText("Baja por correo");

    // The unsubscribe and the already-revoked restriction cannot be revoked from here.
    expect(screen.getAllByRole("button", { name: "Revocar" })).toHaveLength(1);
    expect(screen.getByText("Revocada")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Revocar" }));
    expect(revokeContactRestriction).not.toHaveBeenCalled();
    const confirmation = await waitFor(() => {
      const popup = document.querySelector(".ant-popover") as HTMLElement | null;
      expect(popup).not.toBeNull();
      return popup as HTMLElement;
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "Revocar" }));
    await waitFor(() => expect(revokeContactRestriction).toHaveBeenCalledWith("c1", "r-manual", "Revisión manual del equipo."));
  });

  it("explains a failed action with the server's words", async () => {
    vi.mocked(setPreferredEmail).mockRejectedValue({ detail: "Ese email está restringido." });
    inApp(ContactDetailPage);
    await screen.findByText("ventas@example.com");

    fireEvent.click(screen.getByRole("button", { name: "Preferir" }));

    expect(await screen.findByText("Ese email está restringido.")).toBeInTheDocument();
  });

  it("explains a contact that cannot be loaded", async () => {
    vi.mocked(getContact).mockRejectedValue({ detail: "El contacto no existe." });

    inApp(ContactDetailPage);

    expect(await screen.findByText("El contacto no existe.")).toBeInTheDocument();
  });

  it("creates a restriction for the whole contact with the reason typed", async () => {
    vi.mocked(createContactRestriction).mockResolvedValue(undefined as never);
    inApp(ContactDetailPage);
    await screen.findByText("ventas@example.com");

    const scope = screen.getByLabelText("Alcance");
    fireEvent.mouseDown(scope);
    fireEvent.click(await screen.findByText("Todo el contacto", { selector: ".ant-select-item-option-content" }));
    fireEvent.change(screen.getByLabelText("Motivo"), { target: { value: "Pidió no ser contactado" } });
    fireEvent.click(screen.getByRole("button", { name: "Agregar restricción manual" }));

    await waitFor(() => expect(createContactRestriction).toHaveBeenCalledWith("c1", "CONTACT", "Pidió no ser contactado", undefined));
  });

  it("does not create a restriction without a reason", async () => {
    inApp(ContactDetailPage);
    await screen.findByText("ventas@example.com");

    fireEvent.click(screen.getByRole("button", { name: "Agregar restricción manual" }));

    await waitFor(() => expect(document.querySelector(".ant-form-item-explain-error")).not.toBeNull());
    expect(createContactRestriction).not.toHaveBeenCalled();
  });
});

describe("new contact", () => {
  it("refuses a seller on the page itself", () => {
    env.role = "VENDEDOR";

    inApp(NewContactPage);

    expect(screen.getByText("No tenés permisos para crear contactos.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear contacto" })).not.toBeInTheDocument();
  });

  it("creates the contact and opens its page", async () => {
    vi.mocked(createContact).mockResolvedValue({ id: "new-1" } as never);
    inApp(NewContactPage);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "nuevo@example.com" } });
    fireEvent.change(screen.getByLabelText("Nombre de la organización"), { target: { value: "Nuevo SA" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear contacto" }));

    await waitFor(() => expect(createContact).toHaveBeenCalledWith({ email: "nuevo@example.com", organization_name: "Nuevo SA", contact_name: undefined }));
    await waitFor(() => expect(env.push).toHaveBeenCalledWith("/contacts/new-1"));
  });

  it("does not submit an invalid email", async () => {
    inApp(NewContactPage);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear contacto" }));

    expect(await screen.findByText("Indicá un email válido.")).toBeInTheDocument();
    expect(createContact).not.toHaveBeenCalled();
  });

  it("shows why the server refused the contact", async () => {
    vi.mocked(createContact).mockRejectedValue({ detail: "Ese email pertenece a otra organización.", field_errors: { email: ["duplicado"] } });
    inApp(NewContactPage);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "dup@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear contacto" }));

    expect(await screen.findByText("Ese email pertenece a otra organización.")).toBeInTheDocument();
    expect(env.push).not.toHaveBeenCalled();
  });
});
