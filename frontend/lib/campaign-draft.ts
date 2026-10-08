import { useSyncExternalStore } from "react";

const KEY = "contact-outreach:campaign-draft";
const EVENT = "contact-outreach:campaign-draft-changed";

/** Values the person typed in the new-campaign form. The confirmation for real sending is never kept. */
export type CampaignDraft = Record<string, unknown>;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function readCampaignDraft(): CampaignDraft | null {
  try {
    const raw = storage()?.getItem(KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as CampaignDraft) : null;
  } catch {
    return null;
  }
}

export function saveCampaignDraft(values: CampaignDraft): void {
  try {
    const { confirm_live: _unused, ...safe } = values;
    void _unused;
    storage()?.setItem(KEY, JSON.stringify(safe));
    window.dispatchEvent(new Event(EVENT));
  } catch {
    // The draft is a convenience: without storage the form simply starts empty next time.
  }
}

export function clearCampaignDraft(): void {
  try {
    storage()?.removeItem(KEY);
    window.dispatchEvent(new Event(EVENT));
  } catch {
    // See saveCampaignDraft.
  }
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** True while a campaign is half-written, so other screens can offer a way back to it. */
export function useHasCampaignDraft(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => storage()?.getItem(KEY) !== null && storage()?.getItem(KEY) !== undefined,
    () => false,
  );
}
