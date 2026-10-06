import type { Problem } from "@/lib/api";

/** The one antd form method used here; `Name` is the form's own union of field names. */
export type FieldErrorSink<Name extends string> = {
  setFields(fields: Array<{ name: Name; errors: string[] }>): void;
};

/**
 * Put the backend's `field_errors` on the matching form fields.
 *
 * `fieldNames` is the set of fields the form actually renders. Errors for any other field are
 * left to the page-level message, so nothing is attached to an input that does not exist.
 * Returns true when at least one error was shown inline, so the caller can skip a duplicate alert.
 */
export function applyFieldErrors<Name extends string>(
  form: FieldErrorSink<NoInfer<Name>>,
  problem: unknown,
  fieldNames: readonly Name[],
): boolean {
  const fieldErrors = (problem as Problem | null | undefined)?.field_errors;
  if (!fieldErrors) return false;
  const fields = fieldNames
    .filter((name) => fieldErrors[name] !== undefined)
    .map((name) => {
      const messages = fieldErrors[name];
      return { name, errors: Array.isArray(messages) ? messages : [messages] };
    });
  if (!fields.length) return false;
  form.setFields(fields);
  return true;
}
