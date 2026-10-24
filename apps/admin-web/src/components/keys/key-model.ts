// Pure rules of the Keys view: the balance, the caps and the filters of a row, and the body a create, an edit or a
// wallet change sends.
import { ApiError } from "@/api/client";
import type { ApiKey, KeyBody, Wallet, WalletBody, WalletMode } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import { byProviderOrder } from "@/lib/providers";

// What a key is called wherever a person reads it: its label, else its prefix (a label of spaces is no label).
export const keyName = (k: Pick<ApiKey, "label" | "prefix">): string => k.label.trim() || k.prefix;

// `03/09/2026`; a key made today shows the hour instead of the year: `15/10 20:54`. Local time.
export function createdText(ms: number | null, now: number = Date.now()): string {
  if (!ms) return "-";
  const d = new Date(ms);
  const two = (n: number): string => String(n).padStart(2, "0");
  const date = `${two(d.getDate())}/${two(d.getMonth() + 1)}`;
  if (d.toDateString() === new Date(now).toDateString()) return `${date} ${two(d.getHours())}:${two(d.getMinutes())}`;
  return `${date}/${d.getFullYear()}`;
}

// The list's failed load, worded as the canvas words a failed list (B-States): "Could not load the API keys: the relay
// answered 502 (bad gateway)." `said` is the app's own text for the failure (errorText); with no answer from the relay
// it is all there is to say.
export function loadFailedText(err: unknown, said: string): string {
  const why =
    err instanceof ApiError
      ? `the relay answered ${err.status} (${said})`
      : said.charAt(0).toLowerCase() + said.slice(1);
  return `Could not load the API keys: ${why}.`;
}

// A key's allowed models as the list draws them: one group per provider, in the drawn provider order, each model
// marked paused when the admin switched it off (`disabled` in the catalog). A name the catalog does not know (a model
// that left it) goes in a last group with no provider. A model is found by its id or one of its aliases. `only`: the
// groups of that provider alone ("" for every one). A key that allows any model ("*") has no groups.
export interface ModelGroup {
  provider: string;
  models: Array<{ id: string; paused: boolean }>;
}
export function modelGroups(allowed: readonly string[], catalog: readonly CatalogModel[], only = ""): ModelGroup[] {
  const owner = new Map<string, CatalogModel>();
  for (const m of catalog) {
    owner.set(m.id, m);
    const aliases = Array.isArray(m.aliases) ? m.aliases : [];
    for (const a of aliases) if (typeof a === "string" && !owner.has(a)) owner.set(a, m);
  }
  const groups = new Map<string, ModelGroup>();
  for (const id of allowed) {
    if (id === "*") continue;
    const m = owner.get(id);
    const provider = m?.owned_by ?? "";
    if (only && provider !== only) continue;
    const g = groups.get(provider) ?? { provider, models: [] };
    g.models.push({ id, paused: m?.disabled === true });
    groups.set(provider, g);
  }
  const named = byProviderOrder([...groups.keys()].filter(Boolean));
  return [...named, ...(groups.has("") ? [""] : [])].map((p) => groups.get(p)!);
}

// Whether a key reaches a provider at all: any model, or one of that provider's models by id or alias.
export const keyReaches = (k: Pick<ApiKey, "models">, provider: string, catalog: readonly CatalogModel[]): boolean =>
  k.models.includes("*") || modelGroups(k.models, catalog, provider).length > 0;

export function unfinishedText(k: Pick<ApiKey, "unfinished" | "max_unfinished_effective">): string {
  return `${k.unfinished} / ${k.max_unfinished_effective === 0 ? "unlimited" : k.max_unfinished_effective}`;
}

// What the operator types. Numbers stay text until sent, so an empty box and a 0 differ.
export interface WalletRow {
  scope: string;
  mode: WalletMode;
  day: string;
  total: string;
}

export interface KeyForm {
  label: string;
  maxUnfinished: string;
  any: boolean;
  models: string[];
  wallets: WalletRow[];
}

export const POINTS = "*";

// What a scope is called: the points wallet, or a provider's cap.
export const scopeName = (scope: string): string => (scope === POINTS ? "points" : `${scope} cap`);

// The points wallet comes first, then the providers' caps by name.
export function compareWallets(a: { scope: string }, b: { scope: string }): number {
  if (a.scope === b.scope) return 0;
  if (a.scope === POINTS) return -1;
  if (b.scope === POINTS) return 1;
  return a.scope < b.scope ? -1 : 1;
}
export const sortedWallets = <T extends { scope: string }>(wallets: readonly T[]): T[] =>
  [...wallets].sort(compareWallets);

// The wallets of a form: the points wallet, then one cap per distinct provider of the catalog, in a stable order.
export function walletScopes(models: CatalogModel[]): string[] {
  return sortedWallets([POINTS, ...new Set(models.map((m) => m.owned_by))].map((scope) => ({ scope }))).map(
    (w) => w.scope,
  );
}

// The form of a new key (null) or of an existing one. A wallet the key does not have yet starts closed.
export function keyForm(models: CatalogModel[], key: ApiKey | null): KeyForm {
  const byScope = new Map((key?.wallets ?? []).map((w) => [w.scope, w]));
  return {
    label: key?.label ?? "",
    maxUnfinished: key && key.max_unfinished !== null ? String(key.max_unfinished) : "",
    any: !key || key.models.includes("*"),
    models: key ? key.models.filter((m) => m !== "*") : [],
    wallets: walletScopes(models).map((scope) => {
      const w = byScope.get(scope);
      return {
        scope,
        mode: w?.mode ?? "closed",
        day: w?.day_limit ? String(w.day_limit) : "",
        total: w?.total_limit ? String(w.total_limit) : "",
      };
    }),
  };
}

// How many parts of the form differ from what it opened with: the label, the max, the allowed models, and each
// wallet (the footer's "3 unsaved changes").
export function changedCount(a: KeyForm, b: KeyForm): number {
  const models = (f: KeyForm) => (f.any ? "*" : [...f.models].sort().join(","));
  const wallet = (w: WalletRow | undefined) => (w ? `${w.mode}|${w.day}|${w.total}` : "");
  let count = Number(a.label !== b.label) + Number(a.maxUnfinished !== b.maxUnfinished);
  count += Number(models(a) !== models(b));
  for (const w of a.wallets) count += Number(wallet(w) !== wallet(b.wallets.find((x) => x.scope === w.scope)));
  return count;
}

// The limits count only in `limit` mode, and only the boxes that are filled.
function walletBody(row: WalletRow): WalletBody {
  const body: WalletBody = { scope: row.scope, mode: row.mode };
  if (row.mode === "limit") {
    if (row.day) body.day_limit = Number(row.day);
    if (row.total) body.total_limit = Number(row.total);
  }
  return body;
}

// `max unfinished`: empty = the default (null), 0 = unlimited. Any model overrides the list with `*`.
export function keyBody(form: KeyForm): KeyBody {
  return {
    label: form.label.trim(),
    models: form.any ? ["*"] : [...form.models],
    max_unfinished: form.maxUnfinished === "" ? null : Number(form.maxUnfinished),
    wallets: form.wallets.map(walletBody),
  };
}

// A failed create is shown from this fixed table, by error code, and never from the server's own message or param: the
// create is the one call that handles a raw key, so nothing the server sends back may reach the page as text.
export const CREATE_ERRORS: Readonly<Record<string, string>> = {
  "input.invalid": "Some of the values are not accepted. Check the highlighted field.",
  "resource.not_found": "A provider or model in the form no longer exists. Reload the page and try again.",
  "auth.invalid_session": "Your session has ended, sign in again.",
};
export const CREATE_ERROR_DEFAULT = "The key could not be created. Try again.";
export const CREATE_ERROR_NETWORK = "Network error, please try again.";

export interface CreateError {
  text: string;
  // the form field to highlight, from a fixed list (never the server's text)
  field: "label" | "models" | "max_unfinished" | "wallets" | null;
}

const FIELDS = ["label", "models", "max_unfinished", "wallets"] as const;

export function createError(err: unknown): CreateError {
  if (!(err instanceof ApiError)) return { text: CREATE_ERROR_NETWORK, field: null };
  // `wallets.3` names the wallets field; anything not on the list highlights nothing
  const head = err.param.split(".")[0];
  const field = FIELDS.find((f) => f === head) ?? null;
  return { text: CREATE_ERRORS[err.code] ?? CREATE_ERROR_DEFAULT, field };
}

export function deleteToast(result: string): string {
  return result === "deleted" ? "Key deleted" : "Key has jobs: disabled instead";
}

// The delete question's sentence: a key with jobs is only disabled, and one that has some says so with their count.
export function deleteText(k: Pick<ApiKey, "jobs">): string {
  const rule = "A key that has jobs is only disabled.";
  if (!k.jobs) return rule;
  const jobs = `${n(k.jobs)} ${k.jobs === 1 ? "job" : "jobs"}`;
  return `${rule} This one has ${jobs}, so it will be disabled, not removed.`;
}

// ---- the list: balance, caps, state, filters ---------------------------------------------------------------------

function n(x: number): string {
  return x.toLocaleString("en-US");
}

// The key's points wallet (scope `*`).
export const pointsWallet = (k: Pick<ApiKey, "wallets">): Wallet | undefined =>
  k.wallets.find((w) => w.scope === POINTS);

// Below this share of its limit a balance is low (amber), as a cap chip turns amber at 80 % used.
export const LOW_SHARE = 0.2;

// What the points wallet has left. A limit wallet's balance is its total less what it used in total; one with a day
// limit only, what is left of today's. `left` is null for a closed or an unlimited wallet (`word` says which). The
// tone: red when nothing is left or the wallet is closed, amber under LOW_SHARE of the limit.
export interface Balance {
  left: number | null;
  word: "" | "closed" | "unlimited";
  tone: "ok" | "warn" | "bad";
}
export function balanceOf(w: Wallet | undefined): Balance {
  if (!w || w.mode === "closed") return { left: null, word: "closed", tone: "bad" };
  if (w.mode === "unlimited") return { left: null, word: "unlimited", tone: "ok" };
  const byTotal = w.total_limit !== null;
  const limit = (byTotal ? w.total_limit : w.day_limit) ?? 0;
  const left = Math.max(0, limit - (byTotal ? w.used_total : w.used_day));
  return { left, word: "", tone: left === 0 ? "bad" : left < limit * LOW_SHARE ? "warn" : "ok" };
}

// The balance as a figure: "3,905", or the word of a closed or an unlimited wallet.
export const balanceText = (b: Balance): string => (b.left === null ? b.word : n(b.left));

// The line under a balance: what the key spent today, against the day limit when there is one.
export function todayText(w: Wallet | undefined): string {
  if (!w || w.mode === "closed") return "";
  if (!w.used_day) return "nothing today";
  return w.mode === "limit" && w.day_limit !== null
    ? `−${n(w.used_day)} of ${n(w.day_limit)} today`
    : `−${n(w.used_day)} today`;
}

// One chip per provider the key may use (any model, or a model of that provider) whose cap is open, in the drawn
// provider order. `cap` is the cap's day limit, null when it has none (unlimited, or a total only). `only`: that
// provider's chip alone ("" for every one).
export interface CapFigure {
  provider: string;
  used: number;
  cap: number | null;
}
export function capChips(
  k: Pick<ApiKey, "models" | "wallets">,
  catalog: readonly CatalogModel[],
  only = "",
): CapFigure[] {
  const caps = new Map(k.wallets.filter((w) => w.scope !== POINTS).map((w) => [w.scope, w]));
  const any = k.models.includes("*");
  const reached = new Set(modelGroups(k.models, catalog).map((g) => g.provider));
  return byProviderOrder([...caps.keys()])
    .filter((p) => (!only || p === only) && caps.get(p)!.mode !== "closed" && (any || reached.has(p)))
    .map((p) => {
      const w = caps.get(p)!;
      return { provider: p, used: w.used_day, cap: w.mode === "limit" ? w.day_limit : null };
    });
}

// The state a phone row's pill shows: paused (disabled) first, then the points wallet's tone.
export type KeyState = "ok" | "low" | "empty" | "closed" | "paused";
export function keyState(k: Pick<ApiKey, "enabled" | "wallets">): KeyState {
  if (!k.enabled) return "paused";
  const b = balanceOf(pointsWallet(k));
  if (b.word === "closed") return "closed";
  return b.tone === "bad" ? "empty" : b.tone === "warn" ? "low" : "ok";
}
export const STATE_PILL: Readonly<Record<KeyState, { tone: string; text: string }>> = {
  ok: { tone: "ok", text: "Enabled" },
  low: { tone: "warn", text: "Low" },
  empty: { tone: "bad", text: "Empty" },
  closed: { tone: "bad", text: "Closed" },
  paused: { tone: "neutral", text: "Paused" },
};

// The Show chips of the list.
export type KeyFilter = "all" | "active" | "low" | "paused";
export const KEY_FILTERS: ReadonlyArray<{ value: KeyFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "low", label: "Low or empty" },
  { value: "paused", label: "Paused" },
];
export function inFilter(k: Pick<ApiKey, "enabled" | "wallets">, f: KeyFilter): boolean {
  if (f === "active") return k.enabled;
  if (f === "paused") return !k.enabled;
  if (f === "low") return balanceOf(pointsWallet(k)).tone !== "ok";
  return true;
}
// The search: a piece of the label or of the prefix, in any case.
export function inSearch(k: Pick<ApiKey, "label" | "prefix">, q: string): boolean {
  const t = q.trim().toLowerCase();
  return !t || k.label.toLowerCase().includes(t) || k.prefix.toLowerCase().includes(t);
}

// The sentence under the title: "6 keys, 4 used today. 2,600 points spent today; 1 wallet is empty." `short` is the
// phone's: "6 keys, 4 used today · 2,600 points spent today".
export function summaryText(keys: readonly Pick<ApiKey, "enabled" | "wallets">[], short = false): string {
  const used = keys.filter((k) => (pointsWallet(k)?.used_day ?? 0) > 0).length;
  const spent = keys.reduce((sum, k) => sum + (pointsWallet(k)?.used_day ?? 0), 0);
  const head = `${n(keys.length)} ${keys.length === 1 ? "key" : "keys"}, ${n(used)} used today`;
  const points = `${n(spent)} ${spent === 1 ? "point" : "points"} spent today`;
  if (short) return `${head} · ${points}`;
  const empty = keys.filter((k) => balanceOf(pointsWallet(k)).tone === "bad").length;
  const tail = empty === 0 ? "" : empty === 1 ? "; 1 wallet is empty" : `; ${n(empty)} wallets are empty`;
  return `${head}. ${points}${tail}.`;
}

// The jobs cell: "1,912 jobs" over "2 / 20 unfinished".
export const jobsText = (k: Pick<ApiKey, "jobs">): string => `${n(k.jobs)} ${k.jobs === 1 ? "job" : "jobs"}`;

// The line under a cap in the form: "total 412 / 600", "no total cap", "unlimited · 318 used today", "closed".
export function capLine(w: Wallet | undefined): string {
  if (!w || w.mode === "closed") return "closed";
  if (w.mode === "unlimited") return `unlimited · ${n(w.used_day)} used today`;
  return w.total_limit === null ? "no total cap" : `total ${n(w.used_total)} / ${n(w.total_limit)}`;
}

// ---- the wallet change -------------------------------------------------------------------------------------------

// A top-up raises the points wallet's total by `amount`, a deduction lowers it, never under what it already used (the
// balance stays 0 or more). A wallet without a total (closed, or a day limit only) gets one: what it used plus the
// top-up, its day limit kept. An unlimited wallet has no balance to change. `body` is the wallet the patch sends; it is
// null, with `problem` saying why, when the change cannot be made.
export type WalletChange = "top" | "deduct";
export interface ChangePlan {
  now: number;
  after: number;
  body: WalletBody | null;
  problem: string;
}
export function planChange(w: Wallet | undefined, change: WalletChange, amount: number): ChangePlan {
  const now = balanceOf(w).left ?? 0;
  if (w?.mode === "unlimited")
    return { now, after: now, body: null, problem: "An unlimited wallet has no balance. Set a total in Edit key." };
  const hasTotal = w?.mode === "limit" && w.total_limit !== null;
  const start = hasTotal ? now : 0;
  const whole = Number.isInteger(amount) && amount >= 1;
  const after = Math.max(0, !whole ? start : change === "top" ? start + amount : start - amount);
  if (!whole) return { now, after, body: null, problem: "The amount must be a whole number of 1 or more." };
  if (change === "deduct" && amount > start)
    return { now, after, body: null, problem: `At most ${n(start)} points can be deducted.` };
  const base = hasTotal ? w!.total_limit! : (w?.used_total ?? 0);
  const total = change === "top" ? base + amount : base - amount;
  if (total < 1) return { now, after, body: null, problem: "To empty it, close the wallet in Edit key." };
  const day = w?.mode === "limit" ? w.day_limit : null;
  const body: WalletBody = { scope: POINTS, mode: "limit", total_limit: total };
  if (day !== null) body.day_limit = day;
  return { now, after, body, problem: "" };
}
