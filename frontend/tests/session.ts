import type { Capability, UserSession } from "@/lib/api";

const SELLER_CAPABILITIES: Capability[] = ["view_summary", "view_campaigns", "view_sent_messages", "view_contacts"];

const ADMIN_CAPABILITIES: Capability[] = [
  ...SELLER_CAPABILITIES,
  "manage_users",
  "manage_campaigns",
  "approve_campaigns",
  "send_replies",
  "manage_contacts",
  "manage_knowledge",
  "manage_automation",
  "manage_configuration",
  "manage_integrations",
  "download_pdfs",
  "export_data",
  "view_jobs",
  "retry_jobs",
  "view_audit",
];

/** A session shaped like the backend's: an admin holds every capability, a seller only the read ones. */
export function sessionFor(role: UserSession["role"], capabilities?: Capability[]): Pick<UserSession, "role" | "capabilities"> {
  return { role, capabilities: capabilities ?? (role === "ADMIN" ? ADMIN_CAPABILITIES : SELLER_CAPABILITIES) };
}
