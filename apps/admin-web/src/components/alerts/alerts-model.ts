// Pure rules of the Alerts view: what a group is called, what the detail says about it and what an acknowledge toasts.
import type { AlertDetail, AlertGroup } from "@/api/alerts";

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

export const ONE_CUSTOMER_NOTE = "One customer only: may be the customer’s own words.";

// What the group detail says under its title: how the stored reply relates to the job outcome.
export const REPLY_NOTE = "The reply is stored as the job’s answer; the job outcome decides what the customer got.";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

// `14 Oct 10:02`, the time of a group or a row (local time); a dash for a missing one.
export function alertTime(ms: number | null | undefined): string {
  if (!ms) return "—";
  const d = new Date(ms);
  const two = (n: number): string => String(n).padStart(2, "0");
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

export interface GroupFact {
  label: string;
  value: string | number;
  tone?: "warn";
}

// The facts of a group, as the ledger panel shows them. `Open` takes the warning colour while rows are open.
export function groupFacts(d: AlertDetail): GroupFact[] {
  return [
    { label: "Kind", value: d.kind },
    { label: "Provider", value: d.provider },
    { label: "First seen", value: alertTime(d.first_ms) },
    { label: "Last seen", value: alertTime(d.last_ms) },
    { label: "Rows", value: d.count },
    d.open_count > 0 ? { label: "Open", value: d.open_count, tone: "warn" } : { label: "Open", value: d.open_count },
    { label: "Keys", value: d.keys },
  ];
}

// How the jobs of an unclear wording ended: delivered (an acceptance), refunded (a refusal or nothing), still open.
export function outcomesText(d: Pick<AlertDetail, "outcomes">): string {
  const o = d.outcomes ?? { done: 0, failed: 0, open: 0 };
  const open = o.open ? `, ${o.open} still open` : "";
  return `${o.done} delivered after the reply, ${o.failed} refunded${open}.`;
}

// A reply row's job outcome in the words of the detail: a failed job was refunded, so it reads `refunded`.
export function outcomeStatus(jobStatus: string): string {
  return jobStatus === "failed" ? "refunded" : jobStatus;
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
