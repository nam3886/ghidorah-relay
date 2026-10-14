// Pure rules of the Alerts view: what a group is called, what the detail says about it and what an acknowledge toasts.
import type { AlertDetail, AlertGroup } from "@/api/alerts";
import { fmtTime } from "@/lib/format";

export const GROUP_LIMIT = 200;
export const ROW_LIMIT = 100;
// ponytail: a kind added to the hub later is listed under "All kinds"; add it here to filter on it.
export const ALERT_KINDS = ["clamp", "drift", "unclear_reply"] as const;

// The title of the unclear-reply group of a reply that held no text at all.
export const EMPTY_WORDING_TITLE = "(no reply text read: the provider's API gave no text)";

export function isEmptyWording(g: Pick<AlertGroup, "kind" | "title">): boolean {
  return g.kind === "unclear_reply" && g.title === EMPTY_WORDING_TITLE;
}

export function groupTitle(g: Pick<AlertGroup, "kind" | "title">): string {
  return isEmptyWording(g) ? "API read failure: no reply text was read" : g.title;
}

// A wording seen under one key only may still be one customer's own words; a billing group is one wallet, not a wording.
export function oneCustomer(g: Pick<AlertGroup, "kind" | "title" | "keys">): boolean {
  return g.kind === "unclear_reply" && g.keys === 1 && !isEmptyWording(g);
}

export function summaryText(d: AlertDetail): string {
  return `${d.kind} / ${d.provider} · first ${fmtTime(d.first_ms)} · last ${fmtTime(d.last_ms)} · ${d.count} rows, ${d.open_count} open · ${d.keys} key(s)`;
}

// How the jobs of an unclear wording ended: delivered (an acceptance), refunded (a refusal or nothing), still open.
export function outcomesText(d: Pick<AlertDetail, "outcomes" | "failed_codes">): string {
  const o = d.outcomes ?? { done: 0, failed: 0, open: 0 };
  const codes = Object.entries(d.failed_codes ?? {})
    .map(([code, n]) => `${code} x${n}`)
    .join(", ");
  return (
    `Jobs of this wording: delivered ${o.done}, refunded ${o.failed}, still open ${o.open}` +
    `${codes ? ` (refused with: ${codes})` : ""}. ` +
    "Delivered means the wording was an acceptance; refunded, a refusal or nothing."
  );
}

export function ackToast(acknowledged: number): string {
  return acknowledged ? `Acknowledged ${acknowledged} row(s)` : "Nothing new to acknowledge";
}

export interface AlertAsk {
  status: "open" | "acknowledged" | "all";
  kind: string;
  // the group whose detail is open
  open: { kind: string; group: string } | null;
}
