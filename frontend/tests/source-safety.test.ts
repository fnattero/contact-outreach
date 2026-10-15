import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/** Every first-party source file the browser or the server runs (not tests, not dependencies). */
function sourceFiles(): string[] {
  const root = process.cwd();
  const found: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      if (["node_modules", ".next", "tests", "coverage", "public"].includes(entry)) continue;
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.(tsx?|mjs)$/.test(entry) && !entry.endsWith(".d.ts")) found.push(full);
    }
  };
  walk(root);
  return found.sort();
}

const FILES = sourceFiles().map((file) => ({ path: relative(process.cwd(), file), text: readFileSync(file, "utf8") }));

// Patterns that turn untrusted text into markup or code. React escapes everything else by default,
// so keeping these out of the code is what keeps message bodies, names and errors inert.
const FORBIDDEN: Array<[string, RegExp]> = [
  ["dangerouslySetInnerHTML", /dangerouslySetInnerHTML/],
  ["innerHTML / outerHTML", /\b(?:inner|outer)HTML\b/],
  ["insertAdjacentHTML", /insertAdjacentHTML/],
  ["document.write", /document\s*\.\s*write/],
  ["eval", /\beval\s*\(/],
  ["Function constructor", /new\s+Function\s*\(/],
  ["string timers", /set(?:Timeout|Interval)\s*\(\s*["'`]/],
  ["javascript: URL", /javascript\s*:/i],
  ["data: URL in href", /href\s*=\s*\{?\s*["'`]data:/i],
  ["opener-exposing window.open", /window\.open\s*\(/],
  ["cookie access", /document\s*\.\s*cookie/],
];

describe("browser code stays free of markup and code injection sinks", () => {
  it("scans the application sources", () => {
    expect(FILES.length).toBeGreaterThan(40);
    expect(FILES.some((file) => file.path === "lib/api.ts")).toBe(true);
    expect(FILES.some((file) => file.path.startsWith("app/"))).toBe(true);
  });

  it.each(FORBIDDEN)("contains no %s", (_name, pattern) => {
    const offenders = FILES.filter((file) => pattern.test(file.text)).map((file) => file.path);
    expect(offenders).toEqual([]);
  });

  it("opens no link in a new tab without noopener", () => {
    const offenders = FILES.filter(
      (file) => /target\s*=\s*["']_blank["']/.test(file.text) && !/rel\s*=\s*["'][^"']*noopener/.test(file.text),
    ).map((file) => file.path);
    expect(offenders).toEqual([]);
  });

  it("keeps secrets and tokens out of browser storage", () => {
    const storageWrites = FILES.flatMap((file) =>
      [...file.text.matchAll(/(?:local|session)Storage\s*\.\s*setItem\s*\(\s*([^,)]+)/g)].map((match) => ({
        file: file.path,
        key: match[1].trim(),
      })),
    );

    for (const { key } of storageWrites) {
      expect(key.toLowerCase(), key).not.toMatch(/token|csrf|password|secret|credential/);
    }
  });

  it("sends no request to another origin", () => {
    const offenders = FILES.filter((file) => /fetch\s*\(\s*["'`]https?:\/\//.test(file.text)).map(
      (file) => file.path,
    );
    expect(offenders).toEqual([]);
  });
});
