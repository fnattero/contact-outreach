import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ContactDetailPage from "@/app/contacts/[id]/page";
import { EraseBusinessControl } from "@/components/erase-business-control";
import { eraseContactData, getCommunicationPlans, getContact, reauthenticate, type ContactDetail } from "@/lib/api";

const env = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR" }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "c1" }),
  usePathname: () => "/contacts/c1",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
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
    eraseContactData: vi.fn(),
    getCommunicationPlans: vi.fn(),
    getContact: vi.fn(),
    reauthenticate: vi.fn(),
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
    last_interaction_at: null,
    open_task_count: 0,
    next_follow_up_at: null,
    emails: [
      { id: "e1", original_email: "ana@example.com", label: "", is_preferred: true, validity: "VALID", validated_at: null, invalid_reason: "", active_restriction_count: 0 },
    ],
    restrictions: [],
    timelines: [],
    ...overrides,
  } as unknown as ContactDetail;
}

async function passPassword(password = "clave-segura") {
  fireEvent.click(await screen.findByRole("button", { name: "Eliminar datos de este negocio" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("button", { name: "Continuar" })).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Contraseña actual"), { target: { value: password } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Continuar" }));
  return screen.findByLabelText(/Escribí ELIMINAR/);
}

beforeEach(() => {
  env.role = "ADMIN";
  for (const mocked of [eraseContactData, getCommunicationPlans, getContact, reauthenticate]) {
    (mocked as unknown as ReturnType<typeof vi.fn>).mockReset();
  }
  vi.mocked(getContact).mockResolvedValue(contact());
  vi.mocked(getCommunicationPlans).mockResolvedValue([]);
  vi.mocked(reauthenticate).mockResolvedValue({ reauthentication_active: true });
});

describe("erase business control", () => {
  it("erases only after the password and the typed word, and says what is kept", async () => {
    const erase = vi.fn().mockResolvedValue({});
    const onErased = vi.fn();
    render(createElement(App, null, createElement(EraseBusinessControl, { erase, onErased })));

    const word = await passPassword();
    const dialog = word.closest("[role=dialog]") as HTMLElement;
    expect(within(dialog).getByText(/Se conserva solo su dirección de correo/)).toBeInTheDocument();
    expect(within(dialog).getByText(/tenés que borrarlos vos desde Gmail/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Eliminar datos" })).toBeDisabled();
    fireEvent.change(word, { target: { value: "eliminar" } });
    expect(within(dialog).getByRole("button", { name: "Eliminar datos" })).toBeDisabled();
    expect(erase).not.toHaveBeenCalled();

    fireEvent.change(word, { target: { value: "ELIMINAR" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Eliminar datos" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Eliminar datos" }));

    await waitFor(() => expect(reauthenticate).toHaveBeenCalledWith("clave-segura"));
    await waitFor(() => expect(erase).toHaveBeenCalledWith("ELIMINAR"));
    await waitFor(() => expect(onErased).toHaveBeenCalledTimes(1));
  });

  it("shows why it failed and erases nothing when the password is wrong", async () => {
    const erase = vi.fn();
    const onErased = vi.fn();
    vi.mocked(reauthenticate).mockRejectedValue({ status: 401, detail: "La contraseña no es correcta." });
    render(createElement(App, null, createElement(EraseBusinessControl, { erase, onErased })));

    const word = await passPassword("equivocada");
    fireEvent.change(word, { target: { value: "ELIMINAR" } });
    const dialog = word.closest("[role=dialog]") as HTMLElement;
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Eliminar datos" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Eliminar datos" }));

    expect(await screen.findByText("La contraseña no es correcta.")).toBeInTheDocument();
    expect(erase).not.toHaveBeenCalled();
    expect(onErased).not.toHaveBeenCalled();
  });
});

describe("contact detail and erasure", () => {
  it("lets an administrator erase the contact's business", async () => {
    vi.mocked(eraseContactData).mockResolvedValue({ organization_id: "o1", erased_at: "2026-11-17T00:00:00Z", already_erased: false });
    render(createElement(App, null, createElement(ContactDetailPage)));

    const word = await passPassword();
    fireEvent.change(word, { target: { value: "ELIMINAR" } });
    const dialog = word.closest("[role=dialog]") as HTMLElement;
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Eliminar datos" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Eliminar datos" }));

    await waitFor(() => expect(eraseContactData).toHaveBeenCalledWith("c1", "ELIMINAR"));
    await waitFor(() => expect(getContact).toHaveBeenCalledTimes(2));
  });

  it("never offers erasure to a seller", async () => {
    env.role = "VENDEDOR";
    render(createElement(App, null, createElement(ContactDetailPage)));

    await screen.findByRole("heading", { name: "Ana" });
    expect(screen.queryByRole("button", { name: "Eliminar datos de este negocio" })).not.toBeInTheDocument();
  });

  it("shows an erased business as such and offers nothing to add to it", async () => {
    vi.mocked(getContact).mockResolvedValue(contact({ name: "", organization_name: "", status: "ERASED" }));
    render(createElement(App, null, createElement(ContactDetailPage)));

    expect(await screen.findByRole("heading", { name: "Negocio con datos eliminados" })).toBeInTheDocument();
    expect(screen.getByText("Se eliminaron los datos de este negocio")).toBeInTheDocument();
    for (const control of ["Eliminar datos de este negocio", "Agregar email", "Agregar restricción manual", "Activar revisión periódica"]) {
      expect(screen.queryByRole("button", { name: control })).not.toBeInTheDocument();
    }
  });
});
