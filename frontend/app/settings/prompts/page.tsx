import { redirect } from "next/navigation";

// The reply instructions now live with the rest of the automatic replies.
export default function PromptsSettingsRedirect(): never {
  redirect("/automation/writing");
}
