import type { DashboardSummary } from "@/lib/api";

export type ProblemItem = { id: string; level: "danger" | "warning"; title: string; detail: string; href: string; action: string };

/** What is stopping the app from working, each with the place that fixes it. Empty when all is well. */
export function buildProblems(summary: DashboardSummary): ProblemItem[] {
  const problems: ProblemItem[] = [];
  const admin = summary.admin;
  if (admin) {
    if (admin.gmail_status === "ERROR") {
      problems.push({ id: "gmail", level: "danger", title: "Gmail tiene un problema de conexión", detail: "Mientras no se resuelva, no se pueden enviar ni recibir correos.", href: "/settings/integrations", action: "Revisar la conexión" });
    } else if (admin.gmail_status === "DISCONNECTED" || admin.gmail_status === "NONE") {
      problems.push({ id: "gmail", level: "warning", title: admin.gmail_status === "NONE" ? "Todavía no conectaste Gmail" : "Gmail está desconectado", detail: "Sin Gmail no se pueden enviar ni recibir correos.", href: "/settings/integrations", action: "Conectar Gmail" });
    }
    if (admin.failed_sends > 0) {
      problems.push({ id: "sends", level: "danger", title: admin.failed_sends === 1 ? "Un envío falló" : `${admin.failed_sends} envíos fallaron`, detail: "Podés revisar el motivo y reintentarlos.", href: "/jobs", action: "Ver y reintentar" });
    }
    if (!admin.profile_configured) {
      problems.push({ id: "profile", level: "warning", title: "Falta completar el perfil comercial", detail: "Sin él no se puede lanzar una campaña.", href: "/settings/profile", action: "Completar perfil" });
    }
  }
  if (summary.attention.paused_campaigns > 0) {
    const count = summary.attention.paused_campaigns;
    problems.push({ id: "campaigns", level: "warning", title: count === 1 ? "Una campaña está pausada o detenida" : `${count} campañas están pausadas o detenidas`, detail: "No avanzan hasta que decidas cómo seguir.", href: "/campaigns", action: "Ver campañas" });
  }
  return problems;
}
