import type { PublicModel } from "@/api/test";

// What the model picker and the form heading say about a model, from the public catalog entry (routes_catalog.py).

// The catalog has no display name today: an entry that carries `name` is shown by it, any other by its id.
export const titleOf = (m: PublicModel): string => (typeof m.name === "string" && m.name ? m.name : m.id);

export const capital = (word: string): string => word.charAt(0).toUpperCase() + word.slice(1);

// The lengths the model accepts (`limits.durations`), or none when it names no list.
export function durationsOf(m: PublicModel | null): number[] {
  const list = (m?.limits as { durations?: unknown } | undefined)?.durations;
  return Array.isArray(list) ? list.filter((n): n is number => typeof n === "number") : [];
}

// The ratios the model itself takes (`limits.ratios`, dola: video has 21:9, images 2:3), or none when it names no list;
// and the one it uses when none is sent (`limits.default_ratio`), when that is one of them.
export function ratiosOf(m: PublicModel | null): string[] {
  const list = (m?.limits as { ratios?: unknown } | undefined)?.ratios;
  return Array.isArray(list) ? list.filter((r): r is string => typeof r === "string" && r !== "") : [];
}

export function defaultRatioOf(m: PublicModel | null): string {
  const ratio = (m?.limits as { default_ratio?: unknown } | undefined)?.default_ratio;
  return typeof ratio === "string" && ratiosOf(m).includes(ratio) ? ratio : "";
}

// "5, 10, 20, 25 or 30 s"
export function durationsPhrase(durations: number[]): string {
  if (!durations.length) return "";
  const head = durations.slice(0, -1).join(", ");
  const last = durations[durations.length - 1];
  return `${head ? `${head} or ` : ""}${last} s`;
}

interface Wallet {
  day_limit: number | null;
  used_day: number;
  total_limit: number | null;
  used_total: number;
}

const isWallet = (v: unknown): v is Wallet =>
  !!v &&
  typeof v === "object" &&
  typeof (v as Wallet).used_day === "number" &&
  typeof (v as Wallet).used_total === "number";

// The wallet limits of /v1/points as ledger rows: `dola video · today 3 / 20`, `dola video · total 14 / 200`, and for a
// wallet with no limit at all `dola image unlimited (9 used)`. Null when the text is not the points answer (an error
// message, or a shape this does not know): the caller then shows the text itself.
export function walletRows(points: string): Array<{ label: string; value: string }> | null {
  let data: unknown;
  try {
    data = JSON.parse(points);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object") return null;
  const rows: Array<{ label: string; value: string }> = [];
  for (const [provider, kinds] of Object.entries(data as Record<string, unknown>)) {
    if (!kinds || typeof kinds !== "object" || Array.isArray(kinds)) continue;
    // video first, as the model picker lists them (the answer has its own key order)
    const byKind = Object.entries(kinds as Record<string, unknown>).sort(
      ([a], [b]) => Number(b === "video") - Number(a === "video") || a.localeCompare(b),
    );
    for (const [kind, wallet] of byKind) {
      if (!isWallet(wallet)) continue;
      const label = `${provider} ${kind}`;
      if (wallet.day_limit === null && wallet.total_limit === null) {
        rows.push({ label, value: `unlimited (${wallet.used_total} used)` });
        continue;
      }
      if (wallet.day_limit !== null)
        rows.push({ label: `${label} · today`, value: `${wallet.used_day} / ${wallet.day_limit}` });
      if (wallet.total_limit !== null) {
        rows.push({ label: `${label} · total`, value: `${wallet.used_total} / ${wallet.total_limit}` });
      }
    }
  }
  return rows.length ? rows : null;
}
