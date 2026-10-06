import type { BusinessProfile, RelevanceFilterMode } from "@/lib/api";

export const MODE_OPTIONS: ReadonlyArray<{ value: RelevanceFilterMode; title: string; explanation: string }> = [
  {
    value: "OFF",
    title: "Desactivado",
    explanation: "No se revisa ningún negocio. Todos los que encuentre la búsqueda quedan en la audiencia.",
  },
  {
    value: "OBSERVE",
    title: "Sólo marcar",
    explanation:
      "Se revisa cada negocio y se anota el resultado en la lista, pero no se descarta ninguno. Sirve para ver qué descartaría antes de activarlo.",
  },
  {
    value: "LENIENT",
    title: "Prudente",
    explanation:
      "Se descartan sólo los negocios que claramente no tienen relación con lo que vendés. Si queda duda, se conservan.",
  },
  {
    value: "STRICT",
    title: "Estricto",
    explanation:
      "Se descartan los que no tienen relación y también los dudosos. Vas a tener menos negocios, pero más parecidos a lo que buscás.",
  },
];

/**
 * A starting point built only from the words the operator already wrote in the business profile.
 * Nothing is generated: the last line is left open on purpose so the person finishes it.
 */
export function criteriaFromProfile(profile: Pick<BusinessProfile, "products" | "description"> | null): string | null {
  const products = profile?.products?.trim() ?? "";
  const description = profile?.description?.trim() ?? "";
  if (!products && !description) return null;
  const lines: string[] = [];
  if (products) lines.push(`Vendemos: ${products}`);
  if (description) lines.push(`Sobre nosotros: ${description}`);
  lines.push("Nos sirven los negocios que puedan usar estos productos en su trabajo diario.");
  lines.push("No nos sirven: ");
  return lines.join("\n");
}
