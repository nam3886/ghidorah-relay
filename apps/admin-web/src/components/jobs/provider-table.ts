import type { ProviderSummary, ProvidersSummary } from "@/api/shell";
import { byProviderOrder } from "@/lib/providers";

// What the "Jobs today" table at the top of Jobs draws from GET /admin/providers/summary: one row per provider, its
// finished jobs (done + failed) per hour of the billing day as small bars, the hours still to come as flat stubs.

export const DAY_SLOTS = 24;

export interface Bar {
  kind: "bar" | "zero" | "future";
  pct: number; // height, % of the busiest hour of any provider
  title: string;
}

// "07:00": an hour's start in the billing day's zone.
export const hourLabel = (ms: number, tz: string): string =>
  new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(ms);

const finished = (h: ProvidersSummary["hours"][number], name: string): number =>
  (h.done[name] ?? 0) + (h.failed[name] ?? 0);

// The rows in the drawn provider order.
export const rowsOf = (s: ProvidersSummary): ProviderSummary[] => {
  const by = new Map(s.providers.map((p) => [p.provider, p]));
  return byProviderOrder([...by.keys()]).map((n) => by.get(n)!);
};

// One bar per hour of the day: the hours so far from the server, then stubs up to 24 (a 25-hour day keeps its 25).
// Every row shares one scale, the busiest hour of any provider, so the rows compare.
export function hourBars(s: ProvidersSummary, name: string): Bar[] {
  const peak = Math.max(1, ...s.hours.flatMap((h) => s.providers.map((p) => finished(h, p.provider))));
  const bars: Bar[] = s.hours.map((h) => {
    const n = finished(h, name);
    const failed = h.failed[name] ?? 0;
    const title = `${hourLabel(h.h, s.day_tz)}: ${n} finished${failed ? `, ${failed} failed` : ""}`;
    return n ? { kind: "bar", pct: (n / peak) * 100, title } : { kind: "zero", pct: 0, title };
  });
  const last = s.hours.length ? s.hours[s.hours.length - 1]!.h : s.day_start_ms - 3_600_000;
  for (let i = 1; bars.length < DAY_SLOTS; i++) {
    bars.push({ kind: "future", pct: 0, title: `${hourLabel(last + i * 3_600_000, s.day_tz)}: not yet` });
  }
  return bars;
}

// "11 / 32 video pts", "120 / 320 image jobs"
export const poolText = (p: ProviderSummary["pools"][number]): string =>
  `${p.left.toLocaleString("en-US")} / ${p.budget.toLocaleString("en-US")} ${p.pool} ${p.unit === "jobs" ? "jobs" : p.unit === "uses" ? "uses" : "pts"}`;

export const poolShare = (p: ProviderSummary["pools"][number]): number =>
  p.budget > 0 ? Math.min(100, (p.left / p.budget) * 100) : 0;

export interface Totals {
  running: number;
  queued: number;
  done: number;
  failed: number;
  usable: number;
  accounts: number;
}

export const totalsOf = (rows: ProviderSummary[]): Totals =>
  rows.reduce(
    (t, p) => ({
      running: t.running + p.running,
      queued: t.queued + p.queued,
      done: t.done + p.done_today,
      failed: t.failed + p.failed_today,
      usable: t.usable + p.accounts.usable,
      accounts: t.accounts + p.accounts.total,
    }),
    { running: 0, queued: 0, done: 0, failed: 0, usable: 0, accounts: 0 },
  );

// How a provider sends now, or that it takes no new job.
export const pathText = (p: ProviderSummary): string => (p.claims_enabled ? p.execution : "paused");
