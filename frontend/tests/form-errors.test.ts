import { describe, expect, it, vi } from "vitest";
import { applyFieldErrors } from "@/lib/form-errors";

const FIELDS = ["email", "reason", "evidence"] as const;

describe("applyFieldErrors", () => {
  it("puts each backend error on its own field and reports that something was shown inline", () => {
    const setFields = vi.fn();

    const shown = applyFieldErrors(
      { setFields },
      { field_errors: { email: ["Indicá un correo válido."], reason: "Elegí un motivo." } },
      FIELDS,
    );

    expect(shown).toBe(true);
    expect(setFields).toHaveBeenCalledWith([
      { name: "email", errors: ["Indicá un correo válido."] },
      { name: "reason", errors: ["Elegí un motivo."] },
    ]);
  });

  it("ignores fields the form does not render so no error lands on a missing input", () => {
    const setFields = vi.fn();

    const shown = applyFieldErrors({ setFields }, { field_errors: { password: ["No debería verse."] } }, FIELDS);

    expect(shown).toBe(false);
    expect(setFields).not.toHaveBeenCalled();
  });

  it.each([
    ["no field errors", { detail: "Algo falló." }],
    ["null", null],
    ["undefined", undefined],
    ["a non-object", "boom"],
  ])("returns false for %s, leaving the page-level message to the caller", (_label, problem) => {
    const setFields = vi.fn();

    expect(applyFieldErrors({ setFields }, problem, FIELDS)).toBe(false);
    expect(setFields).not.toHaveBeenCalled();
  });
});
