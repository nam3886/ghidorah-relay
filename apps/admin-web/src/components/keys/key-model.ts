// Pure rules of the Keys view: the wallet text of a row and the body a create or an edit sends.
import { ApiError } from "@/api/client";
import type { ApiKey, KeyBody, Wallet, WalletBody, WalletMode } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import { byProviderOrder } from "@/lib/providers";

// `closed`, `unlimited (N used)`, or `day used/limit · total used/limit` for the limits that are set.
export function walletText(w: Wallet): string {
  if (w.mode === "closed") return "closed";
  if (w.mode === "unlimited") return `unlimited (${w.used_total} used)`;
  const day = w.day_limit === null ? "" : `day ${w.used_day}/${w.day_limit}`;
  const total = w.total_limit === null ? "" : `total ${w.used_total}/${w.total_limit}`;
  return [day, total].filter(Boolean).join(" · ");
}

// The width of a usage bar: the rounded share of the limit used, 0 without a limit, never past 100.
export function usagePercent(used: number, limit: number | null): number {
  if (!limit) return 0;
  return Math.min(100, Math.round((used / limit) * 100));
}

// The width of a relative bar (an unlimited wallet): the rounded share of the busiest key's use, 0 when nobody used
// any, never past 100.
export function relativePercent(used: number, max: number): number {
  if (!max) return 0;
  return Math.min(100, Math.round((used / max) * 100));
}

// The highest use of each wallet (its scope) among the keys that have it open (limit or unlimited), today and in
// total: what a relative bar counts as full.
export type WalletMaxima = ReadonlyMap<string, { day: number; total: number }>;
export function walletMaxima(keys: readonly Pick<ApiKey, "wallets">[]): WalletMaxima {
  const out = new Map<string, { day: number; total: number }>();
  for (const k of keys) {
    for (const w of k.wallets) {
      if (w.mode === "closed") continue;
      const at = out.get(w.scope) ?? { day: 0, total: 0 };
      out.set(w.scope, { day: Math.max(at.day, w.used_day), total: Math.max(at.total, w.used_total) });
    }
  }
  return out;
}

// What a key is called wherever a person reads it: its label, else its prefix (a label of spaces is no label).
export const keyName = (k: Pick<ApiKey, "label" | "prefix">): string => k.label.trim() || k.prefix;

// The spoken form of the two bars of a limit wallet.
export function usageLabel(w: Wallet): string {
  const day = w.day_limit === null ? `${w.used_day} today` : `${w.used_day} of ${w.day_limit} today`;
  const total = w.total_limit === null ? `${w.used_total} in total` : `${w.used_total} of ${w.total_limit} in total`;
  return `Used ${day}, ${total}`;
}

// The spoken form of the two bars of an unlimited wallet.
export function relativeLabel(w: Wallet): string {
  return `Used ${w.used_day} today, ${w.used_total} in total; the bars show the share of the busiest key's use`;
}

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
