import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import UsersPage from "@/app/settings/users/page";
import { deleteUser, getUsers, type ManagedUser } from "@/lib/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/settings/users", useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  const { sessionFor } = await import("./session");
  return {
    ...actual,
    useAuth: () => ({ session: { ...sessionFor("ADMIN"), id: 1 }, loading: false, refresh: vi.fn(), signOut: vi.fn() }),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getUsers: vi.fn(), deleteUser: vi.fn() };
});

function user(overrides: Partial<ManagedUser> = {}): ManagedUser {
  return { id: 2, username: "ana", email: "ana@example.invalid", role: "VENDEDOR", is_active: true, date_joined: "2026-10-01T00:00:00Z", ...overrides };
}

beforeEach(() => {
  vi.mocked(getUsers).mockReset().mockResolvedValue([user({ id: 1, username: "yo", role: "ADMIN" }), user()]);
  vi.mocked(deleteUser).mockReset();
});

function renderPage() {
  return render(createElement(App, null, createElement(UsersPage)));
}

describe("users page deletion", () => {
  it("deletes a user only after the confirmation word is typed, and removes the row", async () => {
    vi.mocked(deleteUser).mockResolvedValue(undefined);
    renderPage();
    const row = (await screen.findByText("ana")).closest("tr") as HTMLElement;

    fireEvent.click(within(row).getByRole("button", { name: "Eliminar" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Eliminar usuario" })).toBeDisabled();
    expect(deleteUser).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText(/Escribí ELIMINAR/), { target: { value: "ELIMINAR" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Eliminar usuario" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Eliminar usuario" }));

    await waitFor(() => expect(deleteUser).toHaveBeenCalledWith(2));
    await waitFor(() => expect(screen.queryByText("ana")).toBeNull());
  });

  it("explains why a user with history stays", async () => {
    vi.mocked(deleteUser).mockRejectedValue({ detail: "Este usuario tiene actividad registrada y no se puede eliminar. Desactivalo en su lugar." });
    renderPage();
    const row = (await screen.findByText("ana")).closest("tr") as HTMLElement;

    fireEvent.click(within(row).getByRole("button", { name: "Eliminar" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/Escribí ELIMINAR/), { target: { value: "ELIMINAR" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Eliminar usuario" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Eliminar usuario" }));

    expect(await screen.findByText(/tiene actividad registrada/)).toBeInTheDocument();
    expect(screen.getByText("ana")).toBeInTheDocument();
  });

  it("does not let someone delete their own user, and says why", async () => {
    renderPage();
    const row = (await screen.findByText("yo")).closest("tr") as HTMLElement;

    expect(within(row).getByRole("button", { name: "Eliminar" })).toBeDisabled();
    expect(within(row).getByText("No podés eliminar tu propio usuario.")).toBeInTheDocument();
  });
});
