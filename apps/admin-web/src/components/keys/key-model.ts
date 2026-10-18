// Pure rules of the Keys view: the wallet text of a row and the body a create or an edit sends.
import { ApiError } from "@/api/client";
import type { ApiKey, KeyBody, Wallet, WalletBody, WalletMode } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";

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

// The spoken form of the two bars of a limit wallet.
export function usageLabel(w: Wallet): string {
  const day = w.day_limit === null ? `${w.used_day} today` : `${w.used_day} of ${w.day_limit} today`;
  const total = w.total_limit === null ? `${w.used_total} in total` : `${w.used_total} of ${w.total_limit} in total`;
  return `Used ${day}, ${total}`;
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

export function unfinishedText(k: Pick<ApiKey, "unfinished" | "max_unfinished_effective">): string {
  return `${k.unfinished} / ${k.max_unfinished_effective === 0 ? "unlimited" : k.max_unfinished_effective}`;
}

// What the operator types. Numbers stay text until sent, so an empty box and a 0 differ.
export interface WalletRow {
  provider: string;
  kind: string;
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

const SEP = "\u0000";

// Wallets are listed by provider, and a provider's video wallet comes before its image wallet (as the design draws
// them); any other kind follows, by name.
const KIND_ORDER = ["video", "image"];
const kindRank = (kind: string): number => {
  const at = KIND_ORDER.indexOf(kind);
  return at === -1 ? KIND_ORDER.length : at;
};
export function compareWallets(a: { provider: string; kind: string }, b: { provider: string; kind: string }): number {
  if (a.provider !== b.provider) return a.provider < b.provider ? -1 : 1;
  const rank = kindRank(a.kind) - kindRank(b.kind);
  if (rank !== 0) return rank;
  return a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : 0;
}
export const sortedWallets = <T extends { provider: string; kind: string }>(wallets: readonly T[]): T[] =>
  [...wallets].sort(compareWallets);

// One wallet per distinct (provider, kind) of the catalog, in a stable order.
export function walletPairs(models: CatalogModel[]): [string, string][] {
  const seen = new Set<string>();
  for (const m of models) seen.add(`${m.owned_by}${SEP}${String(m.kind)}`);
  const pairs = [...seen].map((s) => s.split(SEP) as [string, string]);
  return pairs.sort(([ap, ak], [bp, bk]) => compareWallets({ provider: ap, kind: ak }, { provider: bp, kind: bk }));
}

// The form of a new key (null) or of an existing one. A wallet the key does not have yet starts closed.
export function keyForm(models: CatalogModel[], key: ApiKey | null): KeyForm {
  const byPair = new Map((key?.wallets ?? []).map((w) => [`${w.provider}${SEP}${w.kind}`, w]));
  return {
    label: key?.label ?? "",
    maxUnfinished: key && key.max_unfinished !== null ? String(key.max_unfinished) : "",
    any: !key || key.models.includes("*"),
    models: key ? key.models.filter((m) => m !== "*") : [],
    wallets: walletPairs(models).map(([provider, kind]) => {
      const w = byPair.get(`${provider}${SEP}${kind}`);
      return {
        provider,
        kind,
        mode: w?.mode ?? "closed",
        day: w?.day_limit ? String(w.day_limit) : "",
        total: w?.total_limit ? String(w.total_limit) : "",
      };
    }),
  };
}

// The limits count only in `limit` mode, and only the boxes that are filled.
function walletBody(row: WalletRow): WalletBody {
  const body: WalletBody = { provider: row.provider, kind: row.kind, mode: row.mode };
  if (row.mode === "limit") {
    if (row.day) body.day_limit = Number(row.day);
    if (row.total) body.total_limit = Number(row.total);
  }
  return body;
}

// `max unfinished`: empty = the default (null), 0 = unlimited. Any model overrides the list with `*`.
export function keyBody(form: KeyForm): KeyBody {
  return {
    label: form.label,
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
