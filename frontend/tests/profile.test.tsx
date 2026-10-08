import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { emailEnding } from "@/app/settings/profile/profile-helpers";
import ProfileSettingsPage from "@/app/settings/profile/page";
import {
  getBusinessProfileVersioned,
  getMessageTemplates,
  updateBusinessProfile,
  type BusinessProfile,
} from "@/lib/api";

const auth = vi.hoisted(() => ({ role: "ADMIN" as "ADMIN" | "VENDEDOR" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/settings/profile",
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
  return {
    ...actual,
    getBusinessProfileVersioned: vi.fn(),
    getMessageTemplates: vi.fn(),
    updateBusinessProfile: vi.fn(),
  };
});

function profile(overrides: Partial<BusinessProfile> = {}): BusinessProfile {
  return {
    company_name: "Componentes del Sur",
    salesperson_name: "Vendedor",
    phone: "1234",
    whatsapp: "5678",
    description: "Distribuidora",
    products: "Carbones",
    differentiators: "Atención",
    address: "Calle 1, CABA",
    website: "https://sur.example",
    signature: "Vendedor · Componentes del Sur",
    additional_instructions: "",
    profile_version: 3,
    ...overrides,
  };
}

beforeEach(() => {
  auth.role = "ADMIN";
  vi.mocked(getBusinessProfileVersioned).mockReset().mockResolvedValue({ data: profile(), etag: '"v3"' });
  vi.mocked(getMessageTemplates)
    .mockReset()
    .mockResolvedValue([
      {
        id: "t",
        kind: "INITIAL",
        subject: "Asunto",
        body: "Hola.\n\nQueda a disposición el catálogo.\n¿Qué día conviene que pase el vendedor?",
        revision: 1,
        content_hash: "h",
        approved_at: "2026-10-01T00:00:00Z",
        active: true,
      },
    ]);
  vi.mocked(updateBusinessProfile).mockReset();
});

function renderPage() {
  return render(createElement(App, null, createElement(ProfileSettingsPage)));
}

describe("emailEnding", () => {
  it("keeps the last lines of the message and the signature as typed", () => {
    expect(emailEnding("A\n\nB\nC\n", "  Firma \n  Empresa  ")).toEqual({ tail: "B\nC", signature: "Firma \n  Empresa" });
    expect(emailEnding("", "Firma")).toEqual({ tail: "", signature: "Firma" });
  });
});

describe("business profile page", () => {
  it("asks only for the four details that are used and says what reaches the email", async () => {
    renderPage();

    expect(await screen.findByLabelText("Empresa")).toBeInTheDocument();
    for (const label of ["Vendedor/a", "Dirección", "Firma"]) expect(screen.getByLabelText(label)).toBeInTheDocument();
    for (const label of ["Teléfono", "WhatsApp", "Sitio web", "Descripción", "Productos", "Diferenciadores", "Instrucciones adicionales"]) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
    expect(screen.getByText(/Fija: va al final de las propuestas/)).toBeInTheDocument();
    expect(screen.getByText(/No se agregan solos al correo/)).toBeInTheDocument();
    expect(screen.getByText("Esta página no es lo que lee la inteligencia artificial")).toBeInTheDocument();
  });

  it("previews how a real proposal ends with the signature, and follows what is typed", async () => {
    renderPage();
    const box = (await screen.findByLabelText("Firma")) as HTMLTextAreaElement;

    const preview = await screen.findByLabelText("Vista previa del final del correo");
    await waitFor(() => expect(preview).toHaveTextContent("¿Qué día conviene que pase el vendedor?"));
    expect(preview).toHaveTextContent("Vendedor · Componentes del Sur");
    fireEvent.change(box, { target: { value: "Saludos, Equipo" } });
    await waitFor(() => expect(preview).toHaveTextContent("Saludos, Equipo"));
  });

  it("saves only the four visible fields and leaves the others untouched", async () => {
    vi.mocked(updateBusinessProfile).mockResolvedValue(profile());
    renderPage();
    const box = (await screen.findByLabelText("Empresa")) as HTMLInputElement;

    fireEvent.change(box, { target: { value: "Otra Empresa" } });
    fireEvent.click(await screen.findByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(updateBusinessProfile).toHaveBeenCalled());
    const [sent, etag] = vi.mocked(updateBusinessProfile).mock.calls[0];
    expect(Object.keys(sent).sort()).toEqual(["address", "company_name", "salesperson_name", "signature"]);
    expect(sent.company_name).toBe("Otra Empresa");
    expect(etag).toBe('"v3"');
  });

  it("does not save a blank signature", async () => {
    renderPage();
    const box = (await screen.findByLabelText("Firma")) as HTMLTextAreaElement;

    fireEvent.change(box, { target: { value: "   " } });
    fireEvent.click(await screen.findByRole("button", { name: "Guardar" }));

    expect(await screen.findByText("Escribí la firma que cierra tus correos.")).toBeInTheDocument();
    expect(updateBusinessProfile).not.toHaveBeenCalled();
  });
});
