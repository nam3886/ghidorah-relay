// Pure rules of the Accounts view: what each cell says and what an edit or an import sends.
import type { Account, AccountPatch, ImportBody } from "@/api/accounts";
import type { CommandStatus } from "@/api/commands";
import type { PoolStats } from "@/api/shell";
import type { LedgerItem } from "@/components/shared/LedgerList.vue";
import type { Tone } from "@/components/shared/status-tones";
import { IconCamera, IconCheck, IconPencilMenu, IconPower, IconTrashMenu } from "@/components/shared/icons";
import type { MenuItem } from "@/components/shared/ActionMenu.vue";

export const PAGE_SIZE = 50;
export const AUTO_REFRESH_MS = 10000;
export const WAKE_LABEL = "Wake: clear cooldown and strikes";

// `host:port`, empty when the account has no proxy. The password is never shown.
export function proxyText(a: Pick<Account, "proxy_host" | "proxy_port">): string {
  return a.proxy_host ? `${a.proxy_host}:${a.proxy_port}` : "";
}

// Only what changed: a label that differs, and a proxy that differs (`null` when it was emptied, which removes it).
// Returns null when nothing changed, so the dialog just closes.
export function accountChanges(a: Account, label: string, proxy: string): AccountPatch | null {
  const body: AccountPatch = {};
  if (label !== a.label) body.label = label;
  const next = proxy.trim();
  if (next !== proxyText(a)) body.proxy = next || null;
  return Object.keys(body).length ? body : null;
}

// One credential per line; blank lines are dropped. The proxy, when given, applies to every line.
// Returns null when there is no credential at all.
export function importBody(provider: string, text: string, proxy: string): ImportBody | null {
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) return null;
  const shared = proxy.trim();
  return { provider, items: lines.map((credential) => (shared ? { credential, proxy: shared } : { credential })) };
}

// Why a pasted line was rejected, in a sentence as the canvas writes it. Only the known codes are named; anything else
// gets one fixed sentence, so no text the server sends back (which could carry a pasted secret) is ever shown.
const REJECTED = new Map<string, string>([
  ["credential_unparseable", "The line is not a cookie jar."],
  ["proxy_invalid", "The proxy is not host:port or host:port:user:pass."],
]);
export const REJECTED_OTHER = "The line could not be imported.";
export const rejectedText = (reason: string): string => REJECTED.get(reason) ?? REJECTED_OTHER;

// "21:30", the local clock of a time.
export const hhmm = (ms: number): string =>
  new Date(ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });

// "20:55:02", the local clock to the second.
export const hhmmss = (ms: number): string => new Date(ms).toLocaleTimeString("en-GB", { hour12: false });

export interface Asleep {
  // when it wakes, "21:30"
  until: string;
  // "strike" when the nap comes from strikes; null otherwise
  kind: string | null;
  // the code that put it to sleep (the cooldown reason, or the reason its pool rests)
  why: string | null;
}

// Until when an account sleeps, and why: its own cooldown first, else the first pool that rests. Only a time still
// ahead counts. A cooldown on an account that has strikes is a strike nap.
export function asleepInfo(a: Account, now: number = Date.now()): Asleep | null {
  if (a.cooldown_until_ms && a.cooldown_until_ms > now) {
    return { until: hhmm(a.cooldown_until_ms), kind: a.strikes > 0 ? "strike" : null, why: a.cooldown_reason };
  }
  const pool = a.capacity.find((c) => c.rest_until_ms && c.rest_until_ms > now);
  if (pool?.rest_until_ms) return { until: hhmm(pool.rest_until_ms), kind: null, why: pool.rest_reason };
  return null;
}

// "asleep until 21:30 · strike": the one-line form (desktop and phone).
export const asleepLine = (s: Asleep): string => `asleep until ${s.until}${s.kind ? ` · ${s.kind}` : ""}`;

// The reason under the status pill. A switched-off account's reason ("disabled by admin") only repeats its pill: the
// canvas draws a switched-off row with none.
export const statusReason = (a: Pick<Account, "state" | "state_reason">): string | null =>
  a.state === "disabled" ? null : a.state_reason;

// The error line a narrow row shows: the account's last error, unless it only repeats the reason already shown.
export function extraError(a: Pick<Account, "last_error" | "state_reason">): string | null {
  return a.last_error && a.last_error !== a.state_reason ? a.last_error : null;
}

// An account's pools, the video pool first: the one the boards draw (a live account has an image pool too).
export const poolsInOrder = (capacity: Account["capacity"]): Account["capacity"] =>
  [...capacity].sort((a, b) => Number(b.pool === "video") - Number(a.pool === "video"));

// The tablet's counts line: "31 videos · 12 images", "1 video · 1 image".
export function madeLine(made: Account["made"]): string {
  const count = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  return `${count(made.video, "video")} · ${count(made.image, "image")}`;
}

// `left / budget`, with `?` when the count is unknown. The cell shows it after the pool name.
export function pointsFigure(c: Account["capacity"][number]): string {
  const left = c.left_today === null || c.left_today === undefined ? "?" : c.left_today;
  return `${left}${c.budget !== null && c.budget !== undefined ? ` / ${c.budget}` : ""}`;
}

// How long ago, for a "checked" stamp: the table stays short, the title carries the exact time. A time in the future
// (clock skew) reads as just now.
export function ago(epochMs: number | null | undefined, now: number = Date.now()): string {
  if (!epochMs) return "never";
  const minutes = Math.floor(Math.max(0, now - epochMs) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

export function proxyCheck(a: Account): { label: string; tone: "ok" | "bad" | "neutral" } {
  if (a.proxy_ok === true) return { label: "ok", tone: "ok" };
  if (a.proxy_ok === false) return { label: "failed", tone: "bad" };
  return { label: "not checked", tone: "neutral" };
}

// The proxy chip's words: `ok · 4 min ago`, `failed · 5 min ago`, or `not checked`.
export function proxyChipText(a: Account, now: number = Date.now()): string {
  const { label } = proxyCheck(a);
  return a.proxy_ok === null || a.proxy_ok === undefined ? label : `${label} · ${ago(a.proxy_checked_ms, now)}`;
}

// The result of an account's last proxy check as the Edit dialog says it: a failure carries the reason the check
// stored (`proxy check: connect timeout after 8 s`), else the time of the check.
export function proxyStatus(a: Account, now: number = Date.now()): { tone: "ok" | "bad" | "neutral"; text: string } {
  const { tone } = proxyCheck(a);
  const reason = a.last_error?.startsWith("proxy check: ") ? a.last_error.slice("proxy check: ".length) : "";
  if (a.proxy_ok === false && reason) return { tone, text: `failed · ${reason}` };
  return { tone, text: proxyChipText(a, now) };
}

// What a finished check found, from the command's outcome and the account row it just changed.
// In the toasts' form, "<label> done: <detail>": "Check done: signed_out, account dead (account.dead_session)".
export function checkSummary(outcome: string | undefined, a: Pick<Account, "state" | "state_reason">): string {
  return `Check done: ${outcome ? `${outcome}, ` : ""}account ${a.state}${a.state_reason ? ` (${a.state_reason})` : ""}`;
}

// The pool card's left half: every figure is /admin/stats', counted over all accounts, not the page on screen.
export function healthItems(
  stats: {
    usable: number;
    available: number;
    dead: number;
    disabled: number;
    in_use?: number;
    asleep?: number;
  } | null,
): LedgerItem[] {
  const count = (n: number | undefined, tone?: Tone): Pick<LedgerItem, "value" | "tone"> => ({
    value: n ?? "-",
    tone: n ? tone : undefined,
  });
  return [
    { label: "Accounts ready", value: stats ? `${stats.usable} / ${stats.available}` : "-", mono: true },
    { label: "Accounts dead", mono: true, ...count(stats?.dead, "bad") },
    { label: "Accounts disabled", mono: true, ...count(stats?.disabled) },
    { label: "In use", mono: true, ...count(stats?.in_use) },
    { label: "Asleep", mono: true, ...count(stats?.asleep) },
  ];
}

// The pool card's right half, from GET /admin/stats/pool. The points are the video pool's.
export function poolItems(pool: PoolStats | null): LedgerItem[] {
  const video = pool?.pools.find((p) => p.pool === "video");
  const accounts = (n: number) => `${n} account${n === 1 ? "" : "s"}`;
  const drift = video?.drift ?? 0;
  const failed = pool?.proxy_failed ?? 0;
  return [
    { label: "Video points left", value: pool ? `${video?.left ?? 0} / ${video?.budget ?? 0}` : "-", mono: true },
    { label: "Points unknown", value: pool ? accounts(video?.unknown ?? 0) : "-", mono: true },
    { label: "Drift flagged", value: pool ? drift : "-", mono: true, tone: drift ? "warn" : undefined },
    { label: "Proxy checks failed", value: pool ? failed : "-", mono: true, tone: failed ? "bad" : undefined },
    {
      label: "Last full check",
      value: pool ? (pool.last_full_check_ms ? hhmmss(pool.last_full_check_ms) : "never") : "-",
      mono: true,
    },
  ];
}

// The chip line under a command's control (B-States "Command status"): `running…` while the worker runs it, then
// `ok · <time it finished>` or `error · <the worker's reason>`. `read` is the number of automatic reads of the list
// (every 10 s) made when it was set: a finished chip stays through the next one and goes with the one after, so it is
// seen at least one period. The reads other commands make do not count: two quick Checks keep both results.
export interface ChipState {
  state: "running" | "ok" | "error";
  time?: string;
  message?: string;
  // the poll has ended (a result, an error, or it gave up while the worker was still at it)
  settled: boolean;
  read: number;
}

// The chip for the end of a poll: the command as it ended, or null when the poll gave up while it still ran.
export function commandChip(command: CommandStatus | null, read: number): ChipState {
  if (!command) return { state: "running", settled: true, read };
  if (command.status === "failed") return { state: "error", message: command.result?.error, settled: true, read };
  return { state: "ok", time: hhmmss(command.finished_ms ?? Date.now()), settled: true, read };
}

// The loading list's bars, row by row (px): the account line and its second line, the status pill. The pills are the
// Jobs skeleton's (B-States, loading); the lines follow the Accounts columns.
export const SKELETON_ROWS = [
  { label: 96, sub: 150, pill: 72 },
  { label: 80, sub: 130, pill: 84 },
  { label: 104, sub: 160, pill: 64 },
  { label: 72, sub: 120, pill: 78 },
  { label: 88, sub: 140, pill: 70 },
] as const;
export const barWidth = (px: number) => ({ width: `${px}px` });

// What the shared CommandChip draws of a chip.
export const chipProps = (c: ChipState) => ({ state: c.state, time: c.time, message: c.message });

// The chips that outlive automatic read number `read`: those still running, and those set after the one before it.
export const chipsAfterRead = (chips: Record<string, ChipState>, read: number): Record<string, ChipState> =>
  Object.fromEntries(Object.entries(chips).filter(([, c]) => !c.settled || c.read >= read - 1));

export type RowAction = "check" | "shot" | "wake" | "edit" | "delete";

// The second line of an account: `<name> · <provider>` and ` · composer <v>` when it has one.
export function accountSub(a: Pick<Account, "display_name" | "identity" | "provider" | "composer">): string {
  const name = a.display_name || a.identity || "";
  return [name, a.provider, a.composer ? `composer ${a.composer}` : ""].filter(Boolean).join(" · ");
}

// The items of a row menu. Shot is drawn always but cannot be picked without a screenshot; a phone row has no Check
// button, so its menu starts with Check.
export function rowMenu(a: Pick<Account, "has_screenshot">, phone: boolean): MenuItem[] {
  const items: MenuItem[] = [
    { key: "shot", label: "Shot", icon: IconCamera, disabled: !a.has_screenshot },
    { key: "wake", label: "Wake", icon: IconPower },
    { key: "edit", label: "Edit", icon: IconPencilMenu },
    { key: "delete", label: "Delete", icon: IconTrashMenu, danger: true, separated: true },
  ];
  return phone ? [{ key: "check", label: "Check", icon: IconCheck }, ...items] : items;
}
