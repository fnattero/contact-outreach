import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MessageTemplatesPage from "@/app/settings/message-templates/page";
import { createMessageTemplate, getMessageTemplates, type MessageTemplate } from "@/lib/api";

const auth = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/settings/message-templates",
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
  return { ...actual, getMessageTemplates: vi.fn(), createMessageTemplate: vi.fn() };
});

function template(overrides: Partial<MessageTemplate> = {}): MessageTemplate {
  return {
    id: "t-1",
    kind: "INITIAL",
    subject: "Propuesta de carbones",
    body: "Hola, te escribimos por los carbones.",
    revision: 2,
    content_hash: "h",
    approved_at: "2026-10-01T12:00:00Z",
    active: true,
    ...overrides,
  };
}

beforeEach(() => {
  auth.role = "ADMIN";
  vi.mocked(getMessageTemplates).mockReset().mockResolvedValue([
    template(),
    template({ id: "t-0", revision: 1, subject: "Asunto viejo", active: false }),
    template({ id: "r-1", kind: "REMINDER", subject: "", body: "¿Pudiste verlo?" }),
  ]);
  vi.mocked(createMessageTemplate).mockReset();
});

describe("campaign messages page", () => {
  it("shows the current text of each message, ready to edit, without the word revisión", async () => {
    render(createElement(App, null, createElement(MessageTemplatesPage)));

    expect(await screen.findByText("Hola, te escribimos por los carbones.")).toBeInTheDocument();
    expect(screen.getByText("¿Pudiste verlo?")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Editar" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Escribir" })).toBeInTheDocument();
    expect(screen.queryByText(/revisi[oó]n/i)).toBeNull();
  });

  it("links each message to where it is used, and the signature to the profile", async () => {
    render(createElement(App, null, createElement(MessageTemplatesPage)));
    await screen.findByText("Hola, te escribimos por los carbones.");

    expect(screen.getByRole("link", { name: "Crear una campaña" })).toHaveAttribute("href", "/campaigns/new");
    expect(screen.getByRole("link", { name: "Ver campañas" })).toHaveAttribute("href", "/campaigns");
    expect(screen.getByRole("link", { name: "perfil comercial" })).toHaveAttribute("href", "/settings/profile");
  });

  it("edits a message starting from its current text and saves it as the new one", async () => {
    vi.mocked(createMessageTemplate).mockResolvedValue(
      template({ id: "t-2", revision: 3, body: "Texto nuevo." }),
    );
    render(createElement(App, null, createElement(MessageTemplatesPage)));

    fireEvent.click((await screen.findAllByRole("button", { name: "Editar" }))[0]);
    const dialog = await screen.findByRole("dialog");
    const body = within(dialog).getByLabelText("Texto del correo") as HTMLTextAreaElement;
    expect(body.value).toBe("Hola, te escribimos por los carbones.");
    fireEvent.change(body, { target: { value: "Texto nuevo." } });
    fireEvent.click(await screen.findByRole("button", { name: "Guardar" }));

    await waitFor(() =>
      expect(createMessageTemplate).toHaveBeenCalledWith({
        kind: "INITIAL",
        subject: "Propuesta de carbones",
        body: "Texto nuevo.",
      }),
    );
    expect(await screen.findByText("Texto nuevo.")).toBeInTheDocument();
  });

  it("does not ask for a subject on the reminder, which travels in the same thread", async () => {
    render(createElement(App, null, createElement(MessageTemplatesPage)));

    fireEvent.click((await screen.findAllByRole("button", { name: "Editar" }))[1]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText("Asunto")).toBeNull();
  });
});
