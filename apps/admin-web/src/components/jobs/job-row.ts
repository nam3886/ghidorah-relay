import type { JobSummary } from "@/api/jobs";

// What a row of the Jobs list draws from a job.

// "20:54:22": the local time of day, 24-hour clock.
export const clock = (ms: number | null | undefined): string =>
  ms ? new Date(ms).toLocaleTimeString("en-GB", { hour12: false }) : "—";

// The browser's own IANA zone, which cuts the day headers and the clock. The server's time zone data names Vietnam
// `Asia/Ho_Chi_Minh` only, so the older alias a browser may report is renamed.
export function localZone(): string {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return zone === "Asia/Saigon" ? "Asia/Ho_Chi_Minh" : zone;
}

// "2026-10-15": the calendar day of a time in `zone`, the key of the server's `day_totals`.
export const dayKey = (ms: number | null | undefined, zone: string): string =>
  ms ? new Intl.DateTimeFormat("en-CA", { timeZone: zone }).format(ms) : "";

// A job that is still on its way shows its step ("2/4 waiting") instead of a dot.
const RUNNING = new Set(["queued", "submitting", "waiting", "fetching"]);
export const isRunning = (status: string): boolean => RUNNING.has(status);

// Net points: what the key kept. A job still on its way that has charged nothing yet has no figure (the cell shows a
// dash), not a zero.
export const netPoints = (job: Pick<JobSummary, "status" | "net_charged">): number | null =>
  isRunning(job.status) && job.net_charged === 0 ? null : job.net_charged;
