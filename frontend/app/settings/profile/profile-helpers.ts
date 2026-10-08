/**
 * The end of an email as it leaves the app: the last lines of the message, a blank line and the
 * signature exactly as typed (the backend joins them the same way). Only the tail is shown so the
 * preview stays short.
 */
export function emailEnding(body: string, signature: string, lines = 2): { tail: string; signature: string } {
  const tail = body
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.trim() !== "")
    .slice(-lines)
    .join("\n");
  return { tail, signature: signature.trim() };
}
