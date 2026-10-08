import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigurationForm } from "@/app/settings/integrations/configuration-form";
import { buildConfigurationPatch, credentialStatus, hasChanges } from "@/app/settings/integrations/configuration-helpers";
import { getIntegrationConfiguration, reauthenticate, saveIntegrationConfiguration, type IntegrationConfiguration } from "@/lib/api";

vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  const { sessionFor } = await import("./session");
  return { ...actual, useAuth: () => ({ session: sessionFor("ADMIN"), loading: false, refresh: vi.fn(), signOut: vi.fn() }) };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getIntegrationConfiguration: vi.fn(), saveIntegrationConfiguration: vi.fn(), reauthenticate: vi.fn() };
});

function config(overrides: Partial<IntegrationConfiguration> = {}): IntegrationConfiguration {
  return {
    extractor_provider: "fake", overture_min_confidence: "0.750", website_fetcher: "fake", llm_provider: "fake",
    llm_model: "fake-deterministic", relevance_llm_model: "", ollama_base_url: "http://127.0.0.1:11434", openai_compatible_base_url: "",
    embedding_provider: "fake", embedding_model: "text-embedding-3-small", embedding_dimensions: 1536,
    gmail_provider: "fake", gmail_oauth_client_id: "",
    llm_credential: { configured: false, source: "NONE" }, gmail_credential: { configured: false, source: "NONE" },
    revision: 1, ...overrides,
  };
}
const values = (c: IntegrationConfiguration) => ({
  extractor_provider: c.extractor_provider, overture_min_confidence: c.overture_min_confidence,
  website_fetcher: c.website_fetcher, llm_provider: c.llm_provider, llm_model: c.llm_model,
  relevance_llm_model: c.relevance_llm_model,
  ollama_base_url: c.ollama_base_url, openai_compatible_base_url: c.openai_compatible_base_url,
  embedding_provider: c.embedding_provider, embedding_model: c.embedding_model,
  embedding_dimensions: c.embedding_dimensions, gmail_provider: c.gmail_provider,
  gmail_oauth_client_id: c.gmail_oauth_client_id,
});

describe("buildConfigurationPatch", () => {
  it("sends only what changed", () => {
    const current = config();
    expect(buildConfigurationPatch({ ...values(current), llm_model: "otro" }, current)).toEqual({ llm_model: "otro" });
    expect(hasChanges(buildConfigurationPatch(values(current), current))).toBe(false);
  });

  it("treats a blank secret as keep-current and sends a new one trimmed", () => {
    const current = config();
    expect(buildConfigurationPatch({ ...values(current), llm_api_key: "   " }, current)).toEqual({});
    expect(buildConfigurationPatch({ ...values(current), llm_api_key: " sk-new " }, current)).toEqual({ llm_api_key: "sk-new" });
  });

  it("sends a removal flag only when it is ticked", () => {
    const current = config();
    expect(buildConfigurationPatch({ ...values(current), remove_llm_api_key: false }, current)).toEqual({});
    expect(buildConfigurationPatch({ ...values(current), remove_gmail_oauth_client_secret: true }, current)).toEqual({ remove_gmail_oauth_client_secret: true });
  });

  it("describes a credential by state and source, never by value", () => {
    expect(credentialStatus({ configured: true, source: "ENCRYPTED" })).toBe("Configurada (almacenamiento cifrado)");
    expect(credentialStatus({ configured: false, source: "NONE" })).toBe("No configurada (sin origen configurado)");
  });
});

describe("ConfigurationForm", () => {
  beforeEach(() => {
    vi.mocked(getIntegrationConfiguration).mockResolvedValue(config());
    vi.mocked(reauthenticate).mockReset().mockResolvedValue({ reauthentication_active: true });
    vi.mocked(saveIntegrationConfiguration).mockReset().mockResolvedValue(config({ llm_model: "otro", revision: 2 }));
  });

  it("never pre-fills a credential field", async () => {
    render(createElement(App, null, createElement(ConfigurationForm)));

    expect(await screen.findByLabelText("Nueva clave de API")).toHaveValue("");
    expect(screen.getByLabelText("Nuevo secreto de cliente")).toHaveValue("");
  });

  it("asks for the password and confirms it before saving", async () => {
    render(createElement(App, null, createElement(ConfigurationForm)));
    fireEvent.change(await screen.findByLabelText("Modelo para las respuestas", { selector: "#llm_model" }), { target: { value: "otro" } });
    fireEvent.click(await screen.findByRole("button", { name: "Guardar" }));

    const dialog = await screen.findByRole("dialog");
    expect(saveIntegrationConfiguration).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText("Contraseña actual"), { target: { value: "mi-clave" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /Confirmar y guardar/ }));

    await waitFor(() => expect(saveIntegrationConfiguration).toHaveBeenCalledWith({ llm_model: "otro" }));
    expect(reauthenticate).toHaveBeenCalledWith("mi-clave");
    expect(vi.mocked(reauthenticate).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(saveIntegrationConfiguration).mock.invocationCallOrder[0]);
  });

  it("does not save when the password is wrong", async () => {
    vi.mocked(reauthenticate).mockRejectedValue({ status: 401, detail: "La contraseña no es válida." });
    render(createElement(App, null, createElement(ConfigurationForm)));
    fireEvent.change(await screen.findByLabelText("Modelo para las respuestas", { selector: "#llm_model" }), { target: { value: "otro" } });
    fireEvent.click(await screen.findByRole("button", { name: "Guardar" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Contraseña actual"), { target: { value: "mala" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /Confirmar y guardar/ }));

    expect(await screen.findByText("La contraseña no es válida.")).toBeInTheDocument();
    expect(saveIntegrationConfiguration).not.toHaveBeenCalled();
  });
});

describe("ConfigurationForm layout", () => {
  it("lists every integration by purpose, folded, with a separate model for replies and for the audience", async () => {
    vi.mocked(getIntegrationConfiguration).mockResolvedValue(config());
    render(createElement(App, null, createElement(ConfigurationForm)));

    await screen.findByRole("button", { name: "IA: contestar correos" });
    for (const title of [
      "Gmail: enviar y recibir correos",
      "IA: conexión",
      "IA: contestar correos",
      "IA: revisar la audiencia",
      "Búsqueda de negocios",
      "Lectura de sitios web",
      "Búsqueda en tu información",
    ]) {
      expect(screen.getByRole("button", { name: title })).toHaveAttribute("aria-expanded", "false");
    }
    expect(screen.queryByText("Opciones avanzadas")).toBeNull();
    const replies = screen.getByLabelText("Modelo para las respuestas");
    const audience = screen.getByLabelText("Modelo para revisar la audiencia");
    expect(replies.closest("section")).not.toBe(audience.closest("section"));
  });

  it("unfolds a section when its title is clicked", async () => {
    vi.mocked(getIntegrationConfiguration).mockResolvedValue(config());
    render(createElement(App, null, createElement(ConfigurationForm)));

    fireEvent.click(await screen.findByRole("button", { name: "IA: conexión" }));

    expect(screen.getByRole("button", { name: "IA: conexión" })).toHaveAttribute("aria-expanded", "true");
  });

  it("shows the save bar only after something changed", async () => {
    vi.mocked(getIntegrationConfiguration).mockResolvedValue(config());
    render(createElement(App, null, createElement(ConfigurationForm)));
    const model = await screen.findByLabelText("Modelo para las respuestas");
    expect(screen.queryByRole("region", { name: "Cambios sin guardar" })).toBeNull();

    fireEvent.change(model, { target: { value: "otro" } });

    expect(await screen.findByRole("region", { name: "Cambios sin guardar" })).toBeInTheDocument();
  });

  it("asks for the service address only for the service that needs one", async () => {
    vi.mocked(getIntegrationConfiguration).mockResolvedValue(config({ llm_provider: "openai-compatible" }));
    render(createElement(App, null, createElement(ConfigurationForm)));

    expect(await screen.findByLabelText("Dirección del servicio")).toBeInTheDocument();
    expect(screen.queryByLabelText("Dirección de Ollama")).toBeNull();
  });

  it("offers the audience-review model next to the reply one", async () => {
    vi.mocked(getIntegrationConfiguration).mockResolvedValue(config());
    render(createElement(App, null, createElement(ConfigurationForm)));

    expect(await screen.findByLabelText("Modelo para revisar la audiencia")).toBeInTheDocument();
  });
});
