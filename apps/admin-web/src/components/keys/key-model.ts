// Pure rules of the Keys view: the wallet text of a row and the body a create or an edit sends.
import { ApiError } from "@/api/client";
import type { ApiKey, KeyBody, Wallet, WalletBody, WalletMode } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";

export const PAGE_SIZE = 50;

// `closed`, `unlimited (N used)`, or `day used/limit, total used/limit` for the limits that are set.
export function walletText(w: Wallet): string {
  if (w.mode === "closed") return "closed";
  if (w.mode === "unlimited") return `unlimited (${w.used_total} used)`;
  const day = w.day_limit === null ? "" : `day ${w.used_day}/${w.day_limit}`;
  const total = w.total_limit === null ? "" : `total ${w.used_total}/${w.total_limit}`;
  return [day, total].filter(Boolean).join(", ");
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

// One wallet per distinct (provider, kind) of the catalog, in a stable order.
export function walletPairs(models: CatalogModel[]): [string, string][] {
  const seen = new Set<string>();
  for (const m of models) seen.add(`${m.owned_by}${SEP}${String(m.kind)}`);
  return [...seen].sort().map((s) => s.split(SEP) as [string, string]);
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
