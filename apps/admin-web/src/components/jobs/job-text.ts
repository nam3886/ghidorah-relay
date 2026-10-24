import { ApiError } from "@/api/client";
import type { BulkRecheckResult, BulkRegenerateResult, JobSummary } from "@/api/jobs";

import type { JobEvent } from "@/api/jobs-detail";
import { toneOf, type Tone } from "@/components/shared/status-tones";

export const shortId = (id: string): string => id.slice(0, 8);

// A job's request parameter by the first of its names that is set ("ratio", else "aspect_ratio"), as text; "" when
// none is. The drawer's ledger and its tiles read the same fields through it.
export function jobParam(params: unknown, ...names: string[]): string {
  if (!params || typeof params !== "object") return "";
  for (const name of names) {
    const value = (params as Record<string, unknown>)[name];
    if (value !== undefined && value !== null && value !== "") return String(value);
  }
  return "";
}

// The list's failed load, worded as drawn (B-States): "Could not load the jobs: the relay answered 502 (bad gateway)."
// `said` is the app's own text for the failure (errorText); with no answer from the relay it is all there is to say.
// `what` names what failed: the drawer's read says "the job".
export function loadFailedText(err: unknown, said: string, what = "the jobs"): string {
  const why =
    err instanceof ApiError
      ? `the relay answered ${err.status} (${said})`
      : said.charAt(0).toLowerCase() + said.slice(1);
  return `Could not load ${what}: ${why}.`;
}

// What a key shows in a select or a cell: its label, else its prefix, else the first 8 characters of its id, so no
// option is ever blank.
export function keyName(key: { id: string; label?: string | null; prefix?: string | null }): string {
  return key.label?.trim() || key.prefix?.trim() || shortId(key.id);
}

// Stages the registry of statuses does not know: a recovery and a refund are good news, an accepted job is on its way.
const STAGE_TONES: Record<string, Tone> = { recovered: "ok", refund: "ok", accepted: "info" };

// The stage word (failed, completed...) sets the colour and an unknown stage falls back to its source.
export function eventTone(e: Pick<JobEvent, "source" | "stage">): Tone {
  const named = STAGE_TONES[e.stage];
  if (named) return named;
  const byStage = toneOf(e.stage);
  return byStage === "neutral" ? toneOf(e.source) : byStage;
}

// Newest first; events with no time sort last, and equal times by id, the later one first.
export const newestFirst = (events: readonly JobEvent[]): JobEvent[] =>
  [...events].sort((a, b) => (b.at_ms ?? -Infinity) - (a.at_ms ?? -Infinity) || b.id - a.id);

// The four-step bar of the list: queued 1, waiting and unknown 2, fetching 3, done and failed 4 (a status the bar does
// not know, such as `submitting`, sits at 2).
const STEPS: Record<string, number> = { queued: 1, waiting: 2, unknown: 2, fetching: 3, done: 4, failed: 4 };
export const progressStep = (status: string): number => STEPS[status] ?? 2;

// A failed job that was charged and fully refunded.
export const isRefunded = (job: Pick<JobSummary, "status" | "price" | "net_charged">): boolean =>
  job.status === "failed" && job.price > 0 && job.net_charged === 0;

export function recheckToast(out: BulkRecheckResult): { text: string; type: "success" | "info" } {
  const skipped = Object.keys(out.skipped).length;
  return {
    text: `Re-checked ${out.rechecked.length}${skipped ? `, left ${skipped} alone` : ""}`,
    type: skipped ? "info" : "success",
  };
}

const two = (n: number): string => String(n).padStart(2, "0");

// The drawer's clock formats (local time): "19:41:44" for a timeline row, "15/10 19:40:02" for a ledger value. A
// missing time is "-".
// A time that is not set is the list's empty-cell dash ("—"), here and in fmtDayClock.
export function fmtClock(ms: number | null | undefined): string {
  if (!ms) return "—";
  const d = new Date(ms);
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// "24 Oct 14:28:02" (JobDetail's Created and Finished); `exact` adds the milliseconds, "24 Oct 14:28:20.311"
// (JobDetailTabs' Sent and Updated).
export function fmtDayClock(ms: number | null | undefined, exact = false): string {
  if (!ms) return "—";
  const d = new Date(ms);
  const tail = exact ? `.${String(d.getMilliseconds()).padStart(3, "0")}` : "";
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${fmtClock(ms)}${tail}`;
}

// "35s", "1m 35s", "1h 2m": the time a job took, the one way the admin writes a duration of minutes or hours.
export function fmtTook(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}h ${m}m`;
  return m ? `${m}m ${s}s` : `${s}s`;
}

// "120 B", "4.0 KB", "18.4 MB" (powers of 1000).
export function fmtBytes(n: number): string {
  if (n < 1000) return `${n} B`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)} KB`;
  return `${(n / 1_000_000).toFixed(1)} MB`;
}

export const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;

export function regenerateToast(out: BulkRegenerateResult): { text: string; type: "success" | "info" } {
  const failed = Object.keys(out.failed).length;
  const replayed = out.created.filter((c) => c.replayed).length;
  return {
    text: `Made ${out.created.length - replayed}, already made ${replayed}, failed ${failed}`,
    type: failed ? "info" : "success",
  };
}
