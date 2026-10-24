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

// The resolutions the model takes (`limits.resolutions`, vibes-video: 480p, 720p), or none.
export function resolutionsOf(m: PublicModel | null): string[] {
  const list = (m?.limits as { resolutions?: unknown } | undefined)?.resolutions;
  return Array.isArray(list) ? list.filter((r): r is string => typeof r === "string" && r !== "") : [];
}

// The styles the model names (`limits.styles`, dola's image models), or none.
export function stylesOf(m: PublicModel | null): string[] {
  const list = (m?.limits as { styles?: unknown } | undefined)?.styles;
  return Array.isArray(list) ? list.filter((s): s is string => typeof s === "string" && s !== "") : [];
}

// False only for a model that says it takes no input image (`limits.max_images` 0: meta-image, vibes-image).
export const takesImages = (m: PublicModel | null): boolean =>
  (m?.limits as { max_images?: unknown } | undefined)?.max_images !== 0;

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

// What a model costs and makes, the quiet line under the model picker: "2–4 points · 5–30 s", "1 point",
// "1 point · 480p or 720p". From the catalog's default prices (`prices`: one per length, or one `default`) and limits.
export function modelMeta(m: PublicModel | null): string {
  const prices = m?.prices && typeof m.prices === "object" ? Object.values(m.prices as Record<string, unknown>) : [];
  const points = prices.filter((p): p is number => typeof p === "number");
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const cost = !points.length ? "" : lo === hi ? `${lo} ${lo === 1 ? "point" : "points"}` : `${lo}–${hi} points`;
  const lengths = durationsOf(m);
  const short = Math.min(...lengths);
  const long = Math.max(...lengths);
  const length = !lengths.length ? "" : short === long ? `${short} s` : `${short}–${long} s`;
  const sizes = resolutionsOf(m);
  const size = sizes.length > 1 ? `${sizes.slice(0, -1).join(", ")} or ${sizes[sizes.length - 1]}` : (sizes[0] ?? "");
  return [cost, length, size].filter(Boolean).join(" · ");
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

// The wallet limits of /v1/points as ledger rows: the key's points wallet first (`points · today 3 / 20`,
// `points · total 14 / 200`, and for a wallet with no limit at all `points unlimited (9 used)`), then each provider's
// cap by name (`dola cap · today 1 / 5`). Null when the text is not the points answer (an error message, or a shape
// this does not know): the caller then shows the text itself.
export function walletRows(points: string): Array<{ label: string; value: string }> | null {
  let data: unknown;
  try {
    data = JSON.parse(points);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object") return null;
  const answer = data as { points?: unknown; providers?: unknown };
  const caps =
    answer.providers && typeof answer.providers === "object" && !Array.isArray(answer.providers)
      ? Object.entries(answer.providers as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b))
      : [];
  const rows: Array<{ label: string; value: string }> = [];
  for (const [label, wallet] of [
    ["points", answer.points] as const,
    ...caps.map(([p, w]) => [`${p} cap`, w] as const),
  ]) {
    if (!isWallet(wallet)) continue;
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
  return rows.length ? rows : null;
}
