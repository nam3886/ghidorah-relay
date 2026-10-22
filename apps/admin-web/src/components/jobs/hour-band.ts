import type { HourBucket, Stats } from "@/api/shell";
import type { LedgerItem } from "@/components/shared/LedgerList.vue";

// The 24-hour band of the Jobs page: one column per hour, drawn from `getHourlyStats` (24 buckets, oldest first, the
// current hour last). The column height is its count over the scale below, so a quiet day is not stretched to the
// full height; a busier hour than the scale raises it so no column overflows.
export const BAND_SCALE = 20;
export const HOUR_MS = 3_600_000;

const two = (n: number): string => String(n).padStart(2, "0");

// "21:00": the local hour a bucket starts at.
export const hourLabel = (ms: number): string => `${two(new Date(ms).getHours())}:00`;

// "Hour 20:00–21:00" (en dash), the chip of an active hour filter.
export const hourRange = (ms: number): string => `Hour ${hourLabel(ms)}–${hourLabel(ms + HOUR_MS)}`;

export const hourTotal = (h: HourBucket): number => h.delivered + h.failed + h.in_progress;

export function bandTotals(hours: readonly HourBucket[]): {
  delivered: number;
  failed: number;
  in_progress: number;
  all: number;
} {
  const sum = (key: "delivered" | "failed" | "in_progress"): number => hours.reduce((n, h) => n + h[key], 0);
  const delivered = sum("delivered");
  const failed = sum("failed");
  const in_progress = sum("in_progress");
  return { delivered, failed, in_progress, all: delivered + failed + in_progress };
}

// "20:00 · 1 delivered, 1 failed, 4 in progress": the failed and in-progress parts only when there are some.
export function barTitle(h: HourBucket): string {
  const parts = [`${h.delivered} delivered`];
  if (h.failed > 0) parts.push(`${h.failed} failed`);
  if (h.in_progress > 0) parts.push(`${h.in_progress} in progress`);
  return `${hourLabel(h.hour_ms)} · ${parts.join(", ")}`;
}

export const scaleOf = (hours: readonly HourBucket[]): number => Math.max(BAND_SCALE, ...hours.map(hourTotal));

// A segment's height in percent of the column.
export const segmentHeight = (count: number, scale: number): number => (count / scale) * 100;

// The spoken form of the chart: the busiest hour (the first of equals) and the hours that had failures.
export function chartLabel(hours: readonly HourBucket[]): string {
  const base = "Jobs per hour over the last 24 hours.";
  const peak = hours.reduce<HourBucket | null>((top, h) => (hourTotal(h) > (top ? hourTotal(top) : 0) ? h : top), null);
  if (!peak) return `${base} No jobs.`;
  const total = hourTotal(peak);
  const failures = hours.filter((h) => h.failed > 0).map((h) => hourLabel(h.hour_ms));
  const list =
    failures.length > 1 ? `${failures.slice(0, -1).join(", ")} and ${failures[failures.length - 1]}` : failures[0];
  const tail = failures.length ? `failures at ${list}` : "no failures";
  return `${base} Peak ${total} ${total === 1 ? "job" : "jobs"} at ${hourLabel(peak.hour_ms)}, ${tail}.`;
}

// The axis shows every `step`th hour: every 3rd on desktop and tablet (8 labels), every 6th on a phone (4 labels).
export const axisLabels = (hours: readonly HourBucket[], step: number): string[] =>
  hours.filter((_, i) => i % step === 0).map((h) => hourLabel(h.hour_ms));

const count = (n: number): string => n.toLocaleString("en-US");

// The ledger beside the chart. Desktop folds dead and disabled accounts into one row; a tablet and a phone have room
// for two.
export function bandLedger(stats: Stats | null, split: boolean): LedgerItem[] {
  if (!stats) return [];
  const { jobs, accounts, made } = stats;
  const rows: LedgerItem[] = [
    { label: "In progress", value: count(jobs.in_progress + jobs.queued), mono: true },
    { label: "Completed", value: count(jobs.done), mono: true },
    { label: "Failed", value: count(jobs.failed), tone: "bad", mono: true },
    { label: "Failed pre-job · 24 h", value: count(jobs.pre_job_failures_24h), tone: "bad", mono: true },
    { label: "Accounts ready", value: `${accounts.usable} / ${accounts.available}`, mono: true },
  ];
  if (split) {
    rows.push(
      { label: "Accounts dead", value: count(accounts.dead), tone: "bad", mono: true },
      { label: "Accounts disabled", value: count(accounts.disabled), mono: true },
    );
  } else {
    rows.push({
      label: "Accounts dead · disabled",
      value: `${accounts.dead} · ${accounts.disabled}`,
      tone: "bad",
      mono: true,
    });
  }
  rows.push(
    { label: "Videos made", value: count(made.videos), mono: true },
    { label: "Images made", value: count(made.images), mono: true },
  );
  return rows;
}
