// Pure rules of the Pools and budgets editor: how its inputs read and edit the text draft of `<p>.pools`, the JSON the
// API takes ({pool: {combine, windows: [{name, kind: "day", tz, budget: {tier: units}}]}}, one window a pool for now).
import type { SettingItem } from "@/api/settings";

export const TIERS = ["free", "pro", "ultra"] as const;
export type Tier = (typeof TIERS)[number];

interface PoolWindow {
  name: string;
  kind: string;
  tz: string;
  budget: Record<string, unknown>;
}
interface PoolCfg {
  combine?: string;
  windows?: PoolWindow[];
}
export type Pools = Record<string, PoolCfg>;

// The page tells every time in GMT+7, the owner's clock.
export const SHOWN_OFFSET_MIN = 7 * 60;
const DAY_MIN = 24 * 60;

export function poolsOf(draft: string | undefined, item: SettingItem): Pools {
  try {
    const v = JSON.parse(draft ?? "") as unknown;
    if (v && typeof v === "object" && !Array.isArray(v)) return v as Pools;
  } catch {
    /* a draft that is not JSON keeps the stored value */
  }
  return (item.value ?? {}) as Pools;
}

const firstWindow = (cfg: PoolCfg | undefined): PoolWindow | undefined => cfg?.windows?.[0];

// What a budget input shows: the tier's units in the pool's window, "" for none (the pool allows: unknown).
export function budgetText(pools: Pools, pool: string, tier: Tier): string {
  const v = firstWindow(pools[pool])?.budget?.[tier];
  return v === undefined || v === null ? "" : String(v);
}

// The zone every window resets in, the first one when they differ; UTC when no pool has a window.
export const poolsTz = (pools: Pools): string =>
  Object.values(pools)
    .map(firstWindow)
    .find((w) => w)?.tz ?? "UTC";

// Key order aside, the same JSON: the stored text then, so an edit typed back is no change.
const canon = (v: unknown): string =>
  JSON.stringify(v, (_k, x: unknown) =>
    x && typeof x === "object" && !Array.isArray(x)
      ? Object.fromEntries(Object.entries(x as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
      : x,
  );
const finish = (next: Pools, item: SettingItem): string =>
  canon(next) === canon(item.value) ? JSON.stringify(item.value) : JSON.stringify(next);

// The draft after one budget input changed: a whole number is sent as a number, anything else as typed (the server
// names the fault); an emptied input drops the tier (no budget: the pool allows). A pool with no window gets a day
// window in the zone the others use.
export function withBudget(
  draft: string | undefined,
  item: SettingItem,
  pool: string,
  tier: Tier,
  text: string,
): string {
  const pools = structuredClone(poolsOf(draft, item));
  const cfg = pools[pool] ?? { combine: "sum", windows: [] };
  const had = firstWindow(cfg);
  if (!had && !text.trim()) return finish(pools, item);
  const w: PoolWindow = had ?? { name: "day", kind: "day", tz: poolsTz(pools), budget: {} };
  w.budget = { ...w.budget };
  if (!text.trim()) delete w.budget[tier];
  else w.budget[tier] = /^\d+$/.test(text.trim()) ? Number(text.trim()) : text;
  pools[pool] = { ...cfg, windows: [w, ...(cfg.windows ?? []).slice(1)] };
  return finish(pools, item);
}

// The draft with every window resetting in `tz`.
export function withTz(draft: string | undefined, item: SettingItem, tz: string): string {
  const pools = structuredClone(poolsOf(draft, item));
  for (const cfg of Object.values(pools)) for (const w of cfg.windows ?? []) w.tz = tz;
  return finish(pools, item);
}

// A zone's offset from UTC in minutes now (null for a name the browser does not know).
export function offsetMin(tz: string, at: Date = new Date()): number | null {
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "longOffset" })
      .formatToParts(at)
      .find((p) => p.type === "timeZoneName")?.value;
    const m = /GMT(?:([+-])(\d{2}):(\d{2}))?/.exec(part ?? "");
    if (!m) return null;
    return m[1] ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 0;
  } catch {
    return null;
  }
}

const hhmm = (min: number): string =>
  `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

// When a day in `tz` starts, on the GMT+7 clock: "07:00" for UTC. The zone itself when the browser cannot place it.
export function resetText(tz: string): string {
  const off = offsetMin(tz);
  return off === null ? tz : hhmm((((SHOWN_OFFSET_MIN - off) % DAY_MIN) + DAY_MIN) % DAY_MIN);
}

// The zone whose day starts at a GMT+7 time typed as "07:00" (or "7"): UTC, or the fixed Etc/GMT zone of that offset.
// Null when the text is no time, or not on the hour (no fixed zone has it).
export function tzForReset(text: string): string | null {
  const m = /^(\d{1,2})(?::(\d{2}))?$/.exec(text.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2] ?? "0");
  if (h > 23 || min > 59 || min) return null;
  let off = SHOWN_OFFSET_MIN - h * 60;
  if (off < -12 * 60) off += DAY_MIN;
  const hours = off / 60;
  return hours === 0 ? "UTC" : `Etc/GMT${hours > 0 ? "-" : "+"}${Math.abs(hours)}`;
}

// "Next reset in 16 h 28 min": the time to the next midnight in `tz`.
export function nextResetText(tz: string, at: Date = new Date()): string {
  const off = offsetMin(tz, at);
  if (off === null) return "";
  const local = (((Math.floor(at.getTime() / 60000) + off) % DAY_MIN) + DAY_MIN) % DAY_MIN;
  const left = DAY_MIN - local;
  return `Next reset in ${Math.floor(left / 60)}h ${left % 60}m.`;
}
