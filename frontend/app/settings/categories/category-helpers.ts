import type { SearchCategory } from "@/lib/api";

/** Business types worth suggesting to someone starting from an empty list. */
export const CATEGORY_EXAMPLES: ReadonlyArray<{ name: string; words: readonly string[] }> = [
  { name: "Bobinado de motores", words: ["bobinado", "rebobinado", "bobinador"] },
  { name: "Reparación de motores eléctricos", words: ["motor eléctrico", "reparación de motores"] },
  { name: "Talleres electromecánicos", words: ["electromecánica", "taller electromecánico"] },
  { name: "Autoelectricidad", words: ["autoelectricidad", "electricidad del automotor"] },
];

/** Every word or phrase a business type is searched by, in the order they were saved. */
export function termsOf(category: Pick<SearchCategory, "rules">): string[] {
  const seen = new Set<string>();
  const terms: string[] = [];
  for (const rule of category.rules) {
    for (const term of rule.name_terms) {
      const key = term.toLocaleLowerCase("es");
      if (!seen.has(key)) {
        seen.add(key);
        terms.push(term);
      }
    }
  }
  return terms;
}

/** A typed word or phrase, tidied; empty when there is nothing to add. */
export function cleanTerm(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

/** Add a word unless it is empty or already there (ignoring case). */
export function addTerm(terms: readonly string[], value: string): string[] {
  const term = cleanTerm(value);
  if (!term) return [...terms];
  if (terms.some((existing) => existing.toLocaleLowerCase("es") === term.toLocaleLowerCase("es"))) return [...terms];
  return [...terms, term];
}

/** Each word or phrase is its own rule: a business matches when its name has any of them. */
export function rulesFromTerms(terms: readonly string[]): Array<{ name_terms: string[] }> {
  return terms.map((term) => ({ name_terms: [term] }));
}
