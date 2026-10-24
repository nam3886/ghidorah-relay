// Pure rules of the Alerts view: what a group is called, what the detail says about it and what an acknowledge toasts.
import type { AlertDetail, AlertGroup } from "@/api/alerts";
import { ApiError } from "@/api/client";
import { POINTS, scopeName } from "@/components/keys/key-model";

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

const count = (n: number, one: string): string => `${n} ${one}${n === 1 ? "" : "s"}`;

const QUOTE_LIMIT = 200;

// The sentence the Jobs banner says about the first open group (the server lists the most pressing first), as drawn:
// `Dola replied “Sorry, I can’t create that video” to 4 jobs from 3 keys.` The quote is the newest reply as stored
// (`reply`, on one line, cut at 200 characters), else the group's title (its folded wording). A group of another kind,
// or a reply with no text, says its title. The reply words are customer-influenced: shown as text only.
export function groupSentence(
  g: Pick<AlertGroup, "kind" | "title" | "provider" | "open_count" | "keys">,
  reply?: string | null,
): string {
  if (g.kind !== "unclear_reply" || isEmptyWording(g)) return `${groupTitle(g)}.`;
  return `${replied(g, reply)} to ${count(g.open_count, "job")} from ${count(g.keys, "key")}.`;
}

// `Dola replied “Sorry, I can’t create that video”`: who replied and the reply as stored, on one line and cut at 200
// characters, else the group's folded wording.
function replied(g: Pick<AlertGroup, "title" | "provider">, reply?: string | null): string {
  const who = g.provider ? g.provider.charAt(0).toUpperCase() + g.provider.slice(1) : "The provider";
  const said = (reply ?? "").replace(/\s+/g, " ").trim() || g.title;
  const quote = said.length > QUOTE_LIMIT ? `${said.slice(0, QUOTE_LIMIT)}…` : said;
  return `${who} replied “${quote}”`;
}

// The name of a group on the Alerts page, as the canvas writes it: a reply group is who replied and the newest reply
// as stored (`Dola replied “Sorry, I can’t create that video”`); a billing group is its wallet and the key's name
// (`points wallet of key Shop A`, or `dola cap of key Shop A`; the scope from the group id `<key>.<scope>`, `*` for the
// points wallet). Without those fields it is the server's title; the read failure keeps its own words.
export function alertTitle(
  g: Pick<AlertGroup, "kind" | "group" | "title" | "provider" | "reply" | "key_label">,
): string {
  if (g.kind === "unclear_reply") return isEmptyWording(g) ? groupTitle(g) : replied(g, g.reply);
  const scope = g.group.split(".")[1];
  return g.key_label && scope
    ? `${scope === POINTS ? "points wallet" : scopeName(scope)} of key ${g.key_label}`
    : g.title;
}

// The word before a live condition's message (`pool: dola has 7 usable accounts (alert below 8)`): what it is about.
// A kind added to the server later shows as it is.
const LIVE_WORDS = new Map([
  ["accounts_low", "pool"],
  ["account_dead", "account"],
  ["worker_stale", "worker"],
  ["worker_errors", "worker"],
  ["no_worker", "worker"],
  ["error_rate", "jobs"],
  ["drift", "jobs"],
  ["disk_low", "disk"],
  ["webhooks_failed", "webhooks"],
]);

export const liveWord = (kind: string): string => LIVE_WORDS.get(kind) ?? kind;

// A wording seen under one key only may still be one customer's own words; a billing group is one wallet, not a wording.
export function oneCustomer(g: Pick<AlertGroup, "kind" | "title" | "keys">): boolean {
  return g.kind === "unclear_reply" && g.keys === 1 && !isEmptyWording(g);
}

export const ONE_CUSTOMER_NOTE = "One customer only: may be the customer’s own words.";

// What the group detail says under its title: how the stored reply relates to the job outcome. The reply is the
// video's or the image's answer when every job of the group is one, the job's when they are mixed (or unknown).
export function replyNote(media?: string | null): string {
  const whose = media === "video" ? "video’s" : media === "image" ? "image’s" : "job’s";
  return `The reply is stored as the ${whose} answer; the job outcome decides what the customer got.`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

// `14 Oct 10:02`, the time of a group or a row (local time); a dash for a missing one.
export function alertTime(ms: number | null | undefined): string {
  if (!ms) return "—";
  const d = new Date(ms);
  const two = (n: number): string => String(n).padStart(2, "0");
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

// The three severity cards of the page (Alerts.dc.html): the server's `error` is Critical, `warning` a Warning, any other
// word Info. Each card has its own word, tone and note.
export type Severity = "error" | "warning" | "info";
export const SEVERITIES: Severity[] = ["error", "warning", "info"];
export const severityOf = (s: string): Severity => (s === "error" || s === "warning" ? s : "info");
export const SEVERITY_CARD: Record<
  Severity,
  { label: string; word: string; tone: "bad" | "warn" | "info"; note: string }
> = {
  error: { label: "Critical", word: "Critical", tone: "bad", note: "Check now" },
  warning: { label: "Warnings", word: "Warning", tone: "warn", note: "Look today" },
  info: { label: "Info", word: "Info", tone: "info", note: "For the record" },
};

// The line under the title: `6 open: 2 critical, 3 warnings, 1 info.`, only the severities that have some.
export function openSummary(n: Record<Severity, number>): string {
  const total = n.error + n.warning + n.info;
  if (!total) return "Nothing open.";
  const parts = [
    n.error ? `${n.error} critical` : "",
    n.warning ? count(n.warning, "warning") : "",
    n.info ? `${n.info} info` : "",
  ].filter(Boolean);
  return `${total} open: ${parts.join(", ")}.`;
}

// When a group was first seen, as the cards write it: `14:05 · 27 min ago` today, else the day and time.
export function seenText(ms: number | null | undefined, now: number = Date.now()): string {
  if (!ms) return "—";
  const d = new Date(ms);
  if (d.toDateString() !== new Date(now).toDateString()) return alertTime(ms);
  const two = (x: number): string => String(x).padStart(2, "0");
  const mins = Math.max(0, Math.floor((now - ms) / 60_000));
  const ago = mins < 1 ? "just now" : mins < 60 ? `${mins} min ago` : `${Math.floor(mins / 60)} h ago`;
  return `${two(d.getHours())}:${two(d.getMinutes())} · ${ago}`;
}

// The muted line under a group's title: its kind, how many of its rows are open and how many keys it touches.
export function groupLine(g: Pick<AlertGroup, "kind" | "count" | "open_count" | "keys">): string {
  return `${g.kind} · ${g.open_count} of ${count(g.count, "row")} open · ${count(g.keys, "key")}`;
}

// The drawer's Acknowledge says how many rows it marks: `Acknowledge 4 rows`.
export const ackLabel = (open: number): string => `Acknowledge ${count(open, "row")}`;

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

// The list's failed load, worded as the canvas words a failed list (B-States, the Jobs error card):
// "Could not load the alerts: the relay answered 502 (bad gateway)." `said` is the app's own text for the failure
// (errorText); with no answer from the relay it is all there is to say.
export function loadFailedText(err: unknown, said: string): string {
  const why =
    err instanceof ApiError
      ? `the relay answered ${err.status} (${said})`
      : said.charAt(0).toLowerCase() + said.slice(1);
  return `Could not load the alerts: ${why}.`;
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
