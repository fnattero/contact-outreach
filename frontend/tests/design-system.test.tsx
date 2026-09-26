import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { canSeeAdministration, isForbiddenShellRoute } from "@/components/app-shell";
import { DisabledReason } from "@/components/design-system/disabled-reason";
import { StatusBadge, displayValueMap } from "@/components/design-system/status-badge";

describe("StatusBadge", () => {
  it("renders the sending-mode label with an icon instead of the raw enum", () => {
    const { container } = render(<StatusBadge value="dry-run" />);

    expect(screen.getByText("Simulación")).toBeInTheDocument();
    expect(screen.queryByText("dry-run")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("owns the complete display mapping defined by the design specification", () => {
    expect(displayValueMap.LIVE.label).toBe("Envío real");
    expect(displayValueMap.SHADOW.label).toBe("Observación");
    expect(displayValueMap.OFF.label).toBe("Desactivada");
    expect(displayValueMap.DISCONNECTED.label).toBe("Sin conectar");
    expect(displayValueMap.CONNECTED.label).toBe("Conectada");
    expect(displayValueMap.bloqueado.label).toBe("Detenido");
    expect(displayValueMap.fake.technicalValueOnly).toBe(true);
    expect(displayValueMap["Confianza mínima: 0.750"].label).toBe("Confianza mínima: 75%");
    expect(displayValueMap["text-embedding-3-small"].hiddenFromPrimary).toBe(true);
  });
});

describe("DisabledReason", () => {
  it("disables its control and renders the exact blocker", () => {
    render(
      <DisabledReason disabled reason="Falta aprobar el contenido.">
        <button type="button">Enviar</button>
      </DisabledReason>,
    );

    expect(screen.getByRole("button", { name: "Enviar" })).toBeDisabled();
    expect(screen.getByText("Falta aprobar el contenido.")).toBeInTheDocument();
  });
});

describe("AppShell role visibility", () => {
  it("removes administration for VENDEDOR and blocks direct admin URLs", () => {
    expect(canSeeAdministration("VENDEDOR")).toBe(false);
    expect(isForbiddenShellRoute("VENDEDOR", "/audit")).toBe(true);
    expect(isForbiddenShellRoute("VENDEDOR", "/campaigns/new")).toBe(true);
    expect(isForbiddenShellRoute("VENDEDOR", "/campaigns")).toBe(false);
  });

  it("keeps administration available to ADMIN", () => {
    expect(canSeeAdministration("ADMIN")).toBe(true);
    expect(isForbiddenShellRoute("ADMIN", "/audit")).toBe(false);
  });
});
