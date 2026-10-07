import type { RelevanceFilterMode } from "@/lib/api";

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
