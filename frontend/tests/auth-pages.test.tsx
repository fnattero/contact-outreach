import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ActivatePage from "@/app/activate/page";
import LoginPage from "@/app/login/page";
import { AuthProvider, useAuth } from "@/components/auth-provider";
import { activate, getSession, login, logout, type UserSession } from "@/lib/api";
import { sessionFor } from "./session";

const nav = vi.hoisted(() => ({ path: "/login", replace: vi.fn(), push: vi.fn() }));
const auth = vi.hoisted(() => ({ refresh: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useRouter: () => ({ replace: nav.replace, push: nav.push }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, login: vi.fn(), activate: vi.fn(), getSession: vi.fn(), logout: vi.fn() };
});
vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  return { ...actual, useAuth: () => ({ session: null, loading: false, refresh: auth.refresh, signOut: vi.fn() }) };
});

const full = (role: "ADMIN" | "VENDEDOR"): UserSession => ({
  ...sessionFor(role),
  id: 1,
  username: "ana",
  email: "ana@example.com",
  workspace_id: "w",
  workspace_name: "Empresa",
  session_expires_at: "2099-01-01T00:00:00Z",
  reauthentication_active: false,
});

beforeEach(() => {
  nav.path = "/login";
  nav.replace.mockReset();
  auth.refresh.mockReset().mockResolvedValue(undefined);
  vi.mocked(login).mockReset();
  vi.mocked(activate).mockReset();
  vi.mocked(getSession).mockReset();
  vi.mocked(logout).mockReset();
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("login page", () => {
  it("masks the password and offers the right autofill hints", () => {
    render(createElement(App, null, createElement(LoginPage)));

    expect(screen.getByLabelText("Contraseña")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Contraseña")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("Usuario")).toHaveAttribute("autocomplete", "username");
  });

  it("signs in, refreshes the session and goes to the dashboard", async () => {
    vi.mocked(login).mockResolvedValue(full("ADMIN"));
    render(createElement(App, null, createElement(LoginPage)));

    type("Usuario", "ana");
    type("Contraseña", "correct horse battery");
    fireEvent.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/dashboard"));
    expect(login).toHaveBeenCalledWith("ana", "correct horse battery");
    expect(auth.refresh).toHaveBeenCalledTimes(1);
  });

  it("shows only the server's neutral message when sign-in fails, and stays on the page", async () => {
    vi.mocked(login).mockRejectedValue({
      status: 401,
      code: "invalid_credentials",
      detail: "No se pudo iniciar sesión con esos datos. Intentá nuevamente.",
    });
    render(createElement(App, null, createElement(LoginPage)));

    type("Usuario", "ana");
    type("Contraseña", "wrong");
    fireEvent.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    expect(await screen.findByText("No se pudo iniciar sesión con esos datos. Intentá nuevamente.")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
    expect(auth.refresh).not.toHaveBeenCalled();
  });

  it("never writes the credentials to browser storage or the address bar", async () => {
    vi.mocked(login).mockResolvedValue(full("ADMIN"));
    render(createElement(App, null, createElement(LoginPage)));

    type("Usuario", "ana");
    type("Contraseña", "super-secret-password");
    fireEvent.click(screen.getByRole("button", { name: "Iniciar sesión" }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalled());

    expect(JSON.stringify({ ...window.localStorage })).not.toContain("super-secret");
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain("super-secret");
    expect(window.location.href).not.toContain("super-secret");
    expect(window.location.href).not.toContain("ana");
  });

  it("does not submit an empty form", async () => {
    render(createElement(App, null, createElement(LoginPage)));

    fireEvent.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    await waitFor(() => expect(document.querySelector(".ant-form-item-explain-error")).not.toBeNull());
    expect(login).not.toHaveBeenCalled();
  });

  it("explains the wait and blocks a second click while signing in", async () => {
    let finish: (value: UserSession) => void = () => undefined;
    vi.mocked(login).mockReturnValue(new Promise<UserSession>((resolve) => (finish = resolve)));
    render(createElement(App, null, createElement(LoginPage)));
    type("Usuario", "ana");
    type("Contraseña", "pw-pw-pw-pw-pw-pw");

    fireEvent.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    expect(await screen.findByRole("button", { name: /Iniciando sesión/ })).toBeInTheDocument();
    finish(full("ADMIN"));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/dashboard"));
    expect(login).toHaveBeenCalledTimes(1);
  });
});

describe("activation page", () => {
  const strong = "una-contraseña-larga-14";

  function openWith(hash: string) {
    window.history.replaceState(null, "", `/activate${hash}`);
    return render(createElement(App, null, createElement(ActivatePage)));
  }

  it("takes the token from the address fragment and removes it from the address bar", async () => {
    openWith("#token=abc123");

    await waitFor(() => expect(window.location.hash).toBe(""));
    expect(window.location.href).not.toContain("abc123");
    expect(screen.queryByText("El enlace de activación no es válido.")).not.toBeInTheDocument();
    expect(JSON.stringify({ ...window.localStorage })).not.toContain("abc123");
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain("abc123");
  });

  it("ignores a token passed in the query string, where servers and logs would see it", async () => {
    window.history.replaceState(null, "", "/activate?token=leaky");
    render(createElement(App, null, createElement(ActivatePage)));

    expect(await screen.findByText("El enlace de activación no es válido.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activar cuenta" })).toBeDisabled();
  });

  it("refuses to activate without a token", async () => {
    openWith("");

    expect(await screen.findByText("El enlace de activación no es válido.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activar cuenta" })).toBeDisabled();
    expect(activate).not.toHaveBeenCalled();
  });

  it("masks both password fields as new passwords", async () => {
    openWith("#token=abc");
    await waitFor(() => expect(window.location.hash).toBe(""));

    for (const field of [screen.getByLabelText("Nueva contraseña"), screen.getByLabelText("Repetir contraseña")]) {
      expect(field).toHaveAttribute("type", "password");
      expect(field).toHaveAttribute("autocomplete", "new-password");
    }
  });

  it("requires at least fourteen characters", async () => {
    openWith("#token=abc");
    await waitFor(() => expect(window.location.hash).toBe(""));

    type("Nueva contraseña", "corta");
    type("Repetir contraseña", "corta");
    fireEvent.click(screen.getByRole("button", { name: "Activar cuenta" }));

    expect(await screen.findByText("Usá al menos 14 caracteres.")).toBeInTheDocument();
    expect(activate).not.toHaveBeenCalled();
  });

  it("requires both passwords to match", async () => {
    openWith("#token=abc");
    await waitFor(() => expect(window.location.hash).toBe(""));

    type("Nueva contraseña", strong);
    type("Repetir contraseña", `${strong}x`);
    fireEvent.click(screen.getByRole("button", { name: "Activar cuenta" }));

    expect(await screen.findByText("Las contraseñas no coinciden.")).toBeInTheDocument();
    expect(activate).not.toHaveBeenCalled();
  });

  it("activates with the token and the passwords, then signs in to the dashboard", async () => {
    vi.mocked(activate).mockResolvedValue(full("VENDEDOR"));
    openWith("#token=abc123");
    await waitFor(() => expect(window.location.hash).toBe(""));

    type("Nueva contraseña", strong);
    type("Repetir contraseña", strong);
    fireEvent.click(screen.getByRole("button", { name: "Activar cuenta" }));

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/dashboard"));
    expect(activate).toHaveBeenCalledWith("abc123", strong, strong);
    expect(auth.refresh).toHaveBeenCalledTimes(1);
  });

  it("shows the server's reason when the link was already used or expired", async () => {
    vi.mocked(activate).mockRejectedValue({ status: 400, detail: "El enlace venció. Pedí uno nuevo." });
    openWith("#token=old");
    await waitFor(() => expect(window.location.hash).toBe(""));

    type("Nueva contraseña", strong);
    type("Repetir contraseña", strong);
    fireEvent.click(screen.getByRole("button", { name: "Activar cuenta" }));

    expect(await screen.findByText("El enlace venció. Pedí uno nuevo.")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });
});

describe("auth provider", () => {
  // The real provider, not the mocked useAuth the page tests above rely on.
  async function realProvider() {
    const actual = await vi.importActual<typeof import("@/components/auth-provider")>("@/components/auth-provider");
    return actual.AuthProvider;
  }

  it("keeps the login and activation pages outside the session check", async () => {
    const Provider = await realProvider();
    vi.mocked(getSession).mockRejectedValue({ status: 401 });
    for (const path of ["/login", "/activate"]) {
      nav.path = path;
      const view = render(createElement(Provider, null, createElement("p", null, `abierta ${path}`)));
      expect(screen.getByText(`abierta ${path}`)).toBeInTheDocument();
      view.unmount();
    }
  });

  it("shows nothing private while the session loads, then asks to sign in again if there is none", async () => {
    const Provider = await realProvider();
    nav.path = "/contacts";
    let reject: (reason: unknown) => void = () => undefined;
    vi.mocked(getSession).mockReturnValue(new Promise<UserSession>((_, fail) => (reject = fail)));

    render(createElement(Provider, null, createElement("p", null, "datos privados")));

    expect(screen.queryByText("datos privados")).not.toBeInTheDocument();
    reject({ status: 401 });
    expect(await screen.findByText("La sesión expiró")).toBeInTheDocument();
    expect(screen.queryByText("datos privados")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Ir a iniciar sesión/ }));
    expect(nav.replace).toHaveBeenCalledWith("/login");
  });

  it("renders the page inside the shell once the server confirms the session", async () => {
    const Provider = await realProvider();
    nav.path = "/dashboard";
    vi.mocked(getSession).mockResolvedValue(full("ADMIN"));

    render(createElement(App, null, createElement(Provider, null, createElement("p", null, "datos privados"))));

    expect(await screen.findByText("datos privados")).toBeInTheDocument();
  });

  it("clears the session and goes to login on sign-out, even when the server call fails", async () => {
    const Provider = await realProvider();
    nav.path = "/dashboard";
    vi.mocked(getSession).mockResolvedValue(full("ADMIN"));
    vi.mocked(logout).mockRejectedValue(new Error("network"));
    let out: () => Promise<void> = async () => undefined;
    function Probe() {
      out = useAuthReal().signOut;
      return null;
    }
    const { useAuth: useAuthReal } = await vi.importActual<typeof import("@/components/auth-provider")>(
      "@/components/auth-provider",
    );

    render(createElement(App, null, createElement(Provider, null, createElement(Probe))));
    await waitFor(() => expect(getSession).toHaveBeenCalled());
    await waitFor(() => expect(typeof out).toBe("function"));
    await out().catch(() => undefined);

    expect(logout).toHaveBeenCalled();
    expect(nav.replace).toHaveBeenCalledWith("/login");
    expect(await screen.findByText("La sesión expiró")).toBeInTheDocument();
  });

  it("refuses to be used outside the provider", async () => {
    const { useAuth: useAuthReal } = await vi.importActual<typeof import("@/components/auth-provider")>(
      "@/components/auth-provider",
    );
    function Orphan() {
      useAuthReal();
      return null;
    }
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    expect(() => render(createElement(Orphan))).toThrow("useAuth debe usarse dentro de AuthProvider");
    spy.mockRestore();
  });
});

// Keeps the unused imports honest for the type checker.
void AuthProvider;
void useAuth;
