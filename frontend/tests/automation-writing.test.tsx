import { render, screen } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import WritingPage from "@/app/automation/writing/page";

vi.mock("next/navigation", () => ({
  usePathname: () => "/automation/writing",
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  const { sessionFor } = await import("./session");
  return { ...actual, useAuth: () => ({ session: sessionFor("ADMIN"), loading: false, refresh: vi.fn(), signOut: vi.fn() }) };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getAutomaticReplyPrompt: vi.fn().mockResolvedValue({ automatic_reply_prompt: "Tono cordial." }),
    updateAutomaticReplyPrompt: vi.fn(),
  };
});

describe("how the replies are written", () => {
  it("states when a person takes over, and that the writing text cannot change it", async () => {
    render(createElement(App, null, createElement(WritingPage)));

    expect(await screen.findByText("Cuándo pasa a una persona")).toBeInTheDocument();
    expect(screen.getByText(/pide una reunión o una fecha/)).toBeInTheDocument();
    expect(screen.getByText(/solo cambia cómo suena una respuesta, nunca cuándo se contesta/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "perfil comercial" })).toHaveAttribute("href", "/settings/profile");
    expect(screen.getByDisplayValue("Tono cordial.")).toBeInTheDocument();
  });
});
