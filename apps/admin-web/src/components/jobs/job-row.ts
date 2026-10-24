import type { JobSummary } from "@/api/jobs";

// What a row of the Jobs list draws from a job.

// "20:54:22": the local time of day, 24-hour clock.
export const clock = (ms: number | null | undefined): string =>
  ms ? new Date(ms).toLocaleTimeString("en-GB", { hour12: false }) : "—";

// The browser's own IANA zone, exactly as the browser reports it: it cuts the day headers and the clock. The server
// accepts every IANA name, so no alias is renamed here.
export function localZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
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

// "59 s", "28 m", "3 h", "3 d 4 h": how long ago a job was made (Ledger's Age column). A missing time is a dash.
export function age(ms: number | null | undefined, now: number): string {
  if (!ms) return "—";
  const s = Math.max(0, Math.floor((now - ms) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`;
}

// A queued job that has waited this long draws its age in the warning ink (Ledger: "28 m", bold amber).
export const SLOW_QUEUE_MS = 10 * 60_000;
export const isSlow = (job: Pick<JobSummary, "status" | "created_ms">, now: number): boolean =>
  job.status === "queued" && !!job.created_ms && now - job.created_ms >= SLOW_QUEUE_MS;

export type PillTone = "brand" | "neutral" | "ok" | "bad" | "warn";

// The status pill of a row (Ledger): a job on its way says what it is doing and its step of four ("Rendering 2/4"),
// a queued one waits in grey, the rest say their word with a capital.
const STEP: Record<string, number> = { submitting: 2, waiting: 2, fetching: 3 };
const WORD: Record<string, string> = { submitting: "Submitting", waiting: "Rendering", fetching: "Fetching" };
const TONE: Record<string, PillTone> = { queued: "neutral", done: "ok", failed: "bad", unknown: "warn" };
export function statusPill(status: string): { text: string; tone: PillTone } {
  if (WORD[status]) return { text: `${WORD[status]} ${STEP[status]}/4`, tone: "brand" };
  return { text: status.charAt(0).toUpperCase() + status.slice(1), tone: TONE[status] ?? "neutral" };
}

// The Points cell: the net the key kept, "net / price" when that is not the whole price ("0 / 2" refunded), a dash
// for a job on its way that has charged nothing yet.
export function pointsText(job: Pick<JobSummary, "status" | "net_charged" | "price">): string {
  const net = netPoints(job);
  if (net === null) return "—";
  return net === job.price ? String(net) : `${net} / ${job.price}`;
}
