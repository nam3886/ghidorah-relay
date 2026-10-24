// Pure rules of the Accounts view: what each cell says and what an edit or an import sends.
import type { Account, AccountPatch, ImportBody } from "@/api/accounts";
import type { CommandStatus } from "@/api/commands";
import { IconCamera, IconCheck, IconPencilMenu, IconPower, IconTrashMenu } from "@/components/shared/icons";
import type { MenuItem } from "@/components/shared/ActionMenu.vue";

// The server's largest page: the Show chips count and filter the accounts on hand, so the page asks for all it can.
export const PAGE_SIZE = 200;
export const AUTO_REFRESH_MS = 10000;
export const WAKE_LABEL = "Wake: clear cooldown and strikes";

// `host:port`, empty when the account has no proxy. The password is never shown.
export function proxyText(a: Pick<Account, "proxy_host" | "proxy_port">): string {
  return a.proxy_host ? `${a.proxy_host}:${a.proxy_port}` : "";
}

// Only what changed: a label that differs, a proxy that differs (`null` when it was emptied, which removes it) and the
// Taking jobs switch when it was flipped (`enabled` left out: the form has no switch, an account in use).
// Returns null when nothing changed, so the dialog just closes.
export function accountChanges(a: Account, label: string, proxy: string, enabled?: boolean): AccountPatch | null {
  const body: AccountPatch = {};
  if (label !== a.label) body.label = label;
  const next = proxy.trim();
  if (next !== proxyText(a)) body.proxy = next || null;
  if (enabled !== undefined && enabled !== (a.state !== "disabled")) body.enabled = enabled;
  return Object.keys(body).length ? body : null;
}

// One credential per line; blank lines are dropped. The proxy, when given, applies to every line, and so does the
// account's identity (gemini's Google account email). Returns null when there is no credential at all.
export function importBody(provider: string, text: string, proxy: string, identity = ""): ImportBody | null {
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) return null;
  const shared = proxy.trim();
  const who = identity.trim();
  return {
    provider,
    items: lines.map((credential) => ({
      credential,
      ...(shared ? { proxy: shared } : {}),
      ...(who ? { identity: who } : {}),
    })),
  };
}

// The providers whose import names the account (the email picks gemini's session).
export const IDENTITY_PROVIDERS = new Set(["gemini"]);

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

// How full a pool's meter is: what is left of the budget, in % (0 when the count or the budget is unknown).
export const poolShare = (c: Account["capacity"][number]): number =>
  c.budget && c.left_today !== null && c.left_today !== undefined
    ? Math.max(0, Math.min(100, (c.left_today / c.budget) * 100))
    : 0;

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

// What the shared CommandChip draws of a chip.
export const chipProps = (c: ChipState) => ({ state: c.state, time: c.time, message: c.message });

// The chips that outlive automatic read number `read`: those still running, and those set after the one before it.
export const chipsAfterRead = (chips: Record<string, ChipState>, read: number): Record<string, ChipState> =>
  Object.fromEntries(Object.entries(chips).filter(([, c]) => !c.settled || c.read >= read - 1));

export type RowAction = "check" | "shot" | "wake" | "edit" | "delete";

// The second line of an account: who it is, and ` · composer <v>` when it has one. Its lane names the provider.
export function accountSub(a: Pick<Account, "display_name" | "identity" | "composer">): string {
  const name = a.display_name || a.identity || "";
  return [name, a.composer ? `composer ${a.composer}` : ""].filter(Boolean).join(" · ");
}

// The items of a row menu. Shot is drawn always but cannot be picked without a screenshot; a phone row has no Check
// button, so its menu starts with Check.
export function rowMenu(a: Pick<Account, "has_screenshot">, phone: boolean): MenuItem[] {
  const items: MenuItem[] = [
    { key: "shot", label: "Shot", icon: IconCamera, disabled: !a.has_screenshot, hint: SHOT_HINT(a) },
    { key: "wake", label: "Wake", icon: IconPower },
    { key: "edit", label: "Edit", icon: IconPencilMenu },
    { key: "delete", label: "Delete", icon: IconTrashMenu, danger: true, separated: true },
  ];
  return phone ? [{ key: "check", label: "Check", icon: IconCheck }, ...items] : items;
}
// the hint at the right of a Shot that cannot be picked (MenusToasts, "Accounts · row menu")
const SHOT_HINT = (a: Pick<Account, "has_screenshot">): string => (a.has_screenshot ? "" : "none yet");

// ---- B · Ledger: one health per row, the issue filter and the summary --------------------------------------------

// A pool is low when what is left of its budget is under a tenth (red on its meter).
export const LOW_SHARE = 0.1;
// The meter's tone by what is left: red under a tenth, amber under a quarter, else the provider's own.
export const poolTone = (share: number): "bad" | "warn" | "ok" =>
  share < LOW_SHARE ? "bad" : share < 0.25 ? "warn" : "ok";

const n = (x: number): string => x.toLocaleString("en-US");

// What is left of a pool as a fraction of its budget, or null when the count or the budget is unknown.
export const poolLeft = (c: Account["capacity"][number]): number | null =>
  c.budget && c.left_today !== null && c.left_today !== undefined ? Math.max(0, c.left_today) / c.budget : null;

// `912 / 1,050` (a desktop meter; `912/1,050` on a phone), `?` for an unknown count.
export function poolFigure(c: Account["capacity"][number], sep = " / "): string {
  const left = c.left_today === null || c.left_today === undefined ? "?" : n(c.left_today);
  return c.budget ? `${left}${sep}${n(c.budget)}` : left;
}

// The first pool that is low, the video pool first.
export const lowPool = (a: Pick<Account, "capacity">): Account["capacity"][number] | undefined =>
  poolsInOrder(a.capacity).find((c) => {
    const left = poolLeft(c);
    return left !== null && left < LOW_SHARE;
  });

export type HealthKind = "off" | "dead" | "proxy" | "unknown" | "asleep" | "low" | "inuse" | "paused" | "ok";

// The last check could not read the account (a time limit, an engine error) and no check has read it since: the row
// shows its last answer, which may be old.
export const checkUnanswered = (a: Pick<Account, "check_failed_ms" | "last_checked_ms">): boolean =>
  !!a.check_failed_ms && (!a.last_checked_ms || a.check_failed_ms > a.last_checked_ms);

// "In use · 2 jobs", "In use · 1 job", or "In use" when only a check or a command holds it.
export const inUseLabel = (jobs: number | undefined): string =>
  jobs ? `In use · ${jobs} ${jobs === 1 ? "job" : "jobs"}` : "In use";
export interface Health {
  kind: HealthKind;
  // the pill's words: "Dead", "Asleep until 15:10", "In use"
  label: string;
  // the tablet's shorter pill: "Asleep · 15:10"
  short: string;
  // the line under the pill (a code or a reason), "" when there is none
  why: string;
  tone: "bad" | "warn" | "info" | "ok" | "neutral";
}

// One health per account, the first that holds: switched off, dead, its proxy failing, asleep, low on a pool, in use,
// its provider paused in Settings, else available.
export function healthOf(a: Account, paused = false, now: number = Date.now()): Health {
  const h = (kind: HealthKind, label: string, tone: Health["tone"], why = "", short = label): Health => ({
    kind,
    label,
    short,
    why,
    tone,
  });
  if (a.state === "disabled") return h("off", "Switched off", "neutral");
  if (a.state === "dead") return h("dead", "Dead", "bad", a.state_reason ?? "");
  if (a.proxy_host && a.proxy_ok === false) {
    const status = proxyStatus(a, now).text;
    return h("proxy", "Proxy failing", "bad", status.replace(/^failed · /, ""));
  }
  if (checkUnanswered(a)) {
    return h("unknown", "Unknown", "neutral", a.last_checked_ms ? `last answer ${hhmm(a.last_checked_ms)}` : "");
  }
  const asleep = asleepInfo(a, now);
  if (asleep) return h("asleep", `Asleep until ${asleep.until}`, "warn", asleep.why ?? "", `Asleep · ${asleep.until}`);
  const low = lowPool(a);
  if (low) return h("low", "Low credits", "warn", `${low.pool} ${low.left_today ?? 0} of ${low.budget} left`);
  if (a.in_use) return h("inuse", inUseLabel(a.jobs_in_use), "info");
  if (paused) return h("paused", "Paused", "neutral");
  return h("ok", "Available", "ok");
}

// The order of the list: what needs a look first, then the rest; the server's order inside each.
const RANK: Record<HealthKind, number> = {
  dead: 0,
  proxy: 1,
  unknown: 2,
  asleep: 3,
  low: 4,
  inuse: 5,
  ok: 6,
  paused: 7,
  off: 8,
};
export const healthRank = (h: Health): number => RANK[h.kind];

// The Show chips: an issue and the healths it holds. A phone draws no Switched off chip.
export type Issue = "look" | "dead" | "proxy" | "asleep" | "low" | "off" | "all";
export const ISSUES: { key: Issue; label: string; kinds: HealthKind[] | null }[] = [
  { key: "look", label: "Needs a look", kinds: ["dead", "proxy", "unknown", "asleep", "low"] },
  { key: "dead", label: "Dead", kinds: ["dead"] },
  { key: "proxy", label: "Proxy failing", kinds: ["proxy"] },
  { key: "asleep", label: "Asleep", kinds: ["asleep"] },
  { key: "low", label: "Low credits", kinds: ["low"] },
  { key: "off", label: "Switched off", kinds: ["off"] },
  { key: "all", label: "All", kinds: null },
];
export function inIssue(issue: Issue, h: Health): boolean {
  const kinds = ISSUES.find((i) => i.key === issue)?.kinds;
  return !kinds || kinds.includes(h.kind);
}

// "2 dead, 1 proxy failing, 4 asleep, 1 low on credits": the issues that have accounts, in that order.
export function issueLine(healths: Health[]): string {
  const count = (k: HealthKind) => healths.filter((h) => h.kind === k).length;
  const parts: [number, string][] = [
    [count("dead"), "dead"],
    [count("proxy"), "proxy failing"],
    [count("asleep"), "asleep"],
    [count("low"), "low on credits"],
  ];
  return parts
    .filter(([c]) => c > 0)
    .map(([c, word]) => `${c} ${word}`)
    .join(", ");
}

// The row's one action: Re-check a dead, proxy-failing or low account, Wake an asleep one; none for the others.
export const rowAction = (h: Health): "check" | "wake" | null =>
  h.kind === "asleep"
    ? "wake"
    : h.kind === "dead" || h.kind === "proxy" || h.kind === "unknown" || h.kind === "low"
      ? "check"
      : null;

// The providers whose accounts a check could not read (AccountsStates "Partial"): their names in the provider order
// of `rows`, when the oldest last answer among them was, and whether every failure was the check's time limit.
export function unanswered(accounts: Account[]): { providers: string[]; since: number | null; timedOut: boolean } {
  const rows = accounts.filter((a) => a.state !== "disabled" && checkUnanswered(a));
  const times = rows.map((a) => a.last_checked_ms).filter((t): t is number => !!t);
  return {
    providers: [...new Set(rows.map((a) => a.provider))],
    since: times.length ? Math.min(...times) : null,
    timedOut: rows.length > 0 && rows.every((a) => /time limit/.test(a.last_error ?? "")),
  };
}

// "flow and qwen", "dola, flow and qwen"
export const andList = (names: string[]): string =>
  names.length < 2 ? (names[0] ?? "") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;

// The proxy's check line under its address: `ok · 4 min ago`, `failed · 5 min ago`, `not checked`, or `no proxy`.
export const proxyLine = (a: Account, now: number = Date.now()): string =>
  a.proxy_host ? proxyChipText(a, now) : "no proxy";

// The tablet's account line: the proxy, then the reason when there is one.
export const tabletSub = (a: Account, h: Health): string =>
  [a.proxy_host ? proxyText(a) : "no proxy", h.why].filter(Boolean).join(" · ");

// ---- the detail's health history ---------------------------------------------------------------------------------
export type Cell = 0 | 1 | 2 | null;
const localDay = (ms: number): string => {
  const d = new Date(ms);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
};

// The server's hours ([UTC hour start ms, worst]) as the drawer draws them in the browser's time: the last 14 local
// days ending today (each the worst of its hours, null when nothing was recorded) and today's 24 local hours (null for
// an hour with nothing recorded, the hours to come included). `first` and `middle` are the starts of the first and the
// 7th day, for the axis.
export function healthCells(hours: [number, number][], now: number = Date.now()) {
  const worst = (a: Cell, b: number): Cell => (a === null || b > a ? (b as Cell) : a);
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const starts = Array.from({ length: 14 }, (_, i) => {
    const d = new Date(today);
    d.setDate(d.getDate() - 13 + i);
    return d.getTime();
  });
  const index = new Map(starts.map((s, i) => [localDay(s), i]));
  const days: Cell[] = starts.map(() => null);
  const byHour: Cell[] = Array.from({ length: 24 }, () => null);
  for (const [at, w] of hours) {
    const i = index.get(localDay(at));
    if (i !== undefined) days[i] = worst(days[i] ?? null, w);
    if (i === 13) {
      const h = new Date(at).getHours();
      byHour[h] = worst(byHour[h] ?? null, w);
    }
  }
  return { days, hours: byHour, first: starts[0]!, middle: starts[6]! };
}

// The history in one sentence for a screen reader: "14 days: 11 usable, 2 partly asleep, 1 with a dead session or
// its proxy down, 0 with no record".
export function healthSummary(days: Cell[]): string {
  const n = (v: Cell) => days.filter((d) => d === v).length;
  return `${days.length} days: ${n(0)} usable, ${n(1)} partly asleep, ${n(2)} with a dead session or its proxy down, ${n(null)} with no record`;
}

// "2 Oct", a day of the month.
export const dayMonth = (ms: number): string =>
  new Date(ms).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
