import type { LiveAlert, ProviderSummary, ProvidersSummary } from "@/api/shell";
import { DAY_SLOTS, hourLabel, rowsOf } from "./provider-table";

// What the Today section of Jobs (Ledger) draws from GET /admin/providers/summary: the jobs finished today as one bar
// per hour stacked by provider, the totals beside it, and one card per provider. The order, the hour labels and the
// totals are the Accounts table's (provider-table.ts).
export { rowsOf, totalsOf } from "./provider-table";

// the tallest hour of the desktop chart and of the phone's spark line, in px (Ledger: 170 and 42)
export const CHART_PX = 170;
export const SPARK_PX = 42;

export interface Segment {
  provider: string;
  px: number;
}
export interface Slot {
  // an hour still to come: a flat 3 px stub
  future: boolean;
  // the hour under way: drawn whole, the others a little lighter
  now: boolean;
  // done jobs of each provider that had any, the drawn order from the bottom up
  segs: Segment[];
  // the done jobs of every provider that hour
  total: number;
  title: string;
}

// One slot per hour of the billing day: the hours so far, then stubs up to 24 (a 25-hour day keeps its 25). Every
// hour shares one scale, the busiest hour so far, so `px` (of `height`) compares across the day. A provider that
// finished at least one job that hour is at least 2 px.
export function hourSlots(s: ProvidersSummary, height = CHART_PX): Slot[] {
  const names = rowsOf(s).map((p) => p.provider);
  const totals = s.hours.map((h) => names.reduce((n, p) => n + (h.done[p] ?? 0), 0));
  const peak = Math.max(1, ...totals);
  const slots: Slot[] = s.hours.map((h, i) => ({
    future: false,
    now: i === s.hours.length - 1,
    total: totals[i]!,
    segs: names
      .filter((p) => (h.done[p] ?? 0) > 0)
      .map((p) => ({ provider: p, px: Math.max(2, Math.round(((h.done[p] ?? 0) / peak) * height)) })),
    title: `${hourLabel(h.h, s.day_tz)}: ${totals[i]} done`,
  }));
  const last = s.hours.length ? s.hours[s.hours.length - 1]!.h : s.day_start_ms - 3_600_000;
  for (let i = 1; slots.length < DAY_SLOTS; i++) {
    const at = hourLabel(last + i * 3_600_000, s.day_tz);
    slots.push({ future: true, now: false, total: 0, segs: [], title: `${at}: not yet` });
  }
  return slots;
}

// The chart's axis: the day's start, +6 h, +12 h, the clock now, and the end of the day.
export function axisOf(s: ProvidersSummary, now: number): string[] {
  const at = (h: number): string => hourLabel(s.day_start_ms + h * 3_600_000, s.day_tz);
  return [at(0), at(6), at(12), `${hourLabel(now, s.day_tz)} now`, at(24) === at(0) ? "24:00" : at(24)];
}

// "2.4%": the failed share of the jobs finished today, one decimal; nothing finished is 0%.
export function failedShare(done: number, failed: number): string {
  const all = done + failed;
  return all ? `${Math.round((failed / all) * 1000) / 10}%` : "0%";
}

// A provider fails "a lot" above 3% of its finished jobs: its failed count turns red.
export const failsALot = (p: Pick<ProviderSummary, "done_today" | "failed_today">): boolean =>
  p.failed_today / Math.max(1, p.done_today + p.failed_today) > 0.03;

// How a provider sends now, or that it takes no new job: "Browser", "HTTP", "Paused".
export const pathLabel = (p: ProviderSummary): string =>
  !p.claims_enabled ? "Paused" : p.execution === "http" ? "HTTP" : "Browser";

// The share of its accounts a provider can use, 0 to 100.
export const usableShare = (p: ProviderSummary): number =>
  p.accounts.total ? Math.round((p.accounts.usable / p.accounts.total) * 100) : 0;

// What needs a look on a provider's card: the first live alert about it, in the relay's own words.
export const causeOf = (alerts: readonly LiveAlert[] | undefined, provider: string): string =>
  alerts?.find((a) => a.provider === provider)?.message ?? "";
