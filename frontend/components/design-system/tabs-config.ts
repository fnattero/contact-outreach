import type { SectionTab } from "./section-tabs";

// Tab sets shared by the pages of one section, so every page of the section shows the same bar.
export const AUDIENCE_TABS: readonly SectionTab[] = [
  { href: "/prospects", label: "Negocios", capability: "manage_campaigns" },
  { href: "/settings/relevance", label: "Filtro", capability: "manage_configuration" },
];

export const MAIL_TABS: readonly SectionTab[] = [
  { href: "/responses", label: "Recibidos" },
  { href: "/outbound", label: "Enviados" },
];
