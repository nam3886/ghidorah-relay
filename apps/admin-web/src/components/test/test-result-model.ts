import type { PublicJob } from "@/api/test";
import { fmtTook } from "@/components/jobs/job-text";

// What the Result card says about a job, from the PUBLIC status body (doors.job_status): the status word is `stage` for
// a running job (submitting, waiting, fetching, unknown) and `status` otherwise (queued, done, failed).
export const IDLE = "Idle";
export const EMPTY_RESULT = "No models loaded. Enter an API key above and press Load models.";
export const EMPTY_VIDEO_SLOT = "The video appears here when the job is done.";

// The four-step position of a job that is still going. Done and failed have none: they are drawn with a dot.
const STEPS: Record<string, string> = {
  queued: "1/4",
  submitting: "2/4",
  waiting: "2/4",
  unknown: "2/4",
  fetching: "3/4",
};

export function statusWord(job: PublicJob): string {
  return job.stage || job.status;
}

export const stepOf = (job: PublicJob): string | undefined => STEPS[statusWord(job)];

export const shortJobId = (id: string): string => id.slice(0, 8);

const two = (n: number): string => String(n).padStart(2, "0");

// "20:52:30": the clock time of the job's creation, in the browser's time zone
export function clockTime(iso: unknown): string {
  if (typeof iso !== "string") return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

// "1.8 MiB", or KiB below one MiB: the size beside an attached file's name
export function sizeText(bytes: number): string {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MiB` : `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

// A ledger row; a refunded charge reads as the job drawer writes it: the charge struck (`was`), then 0 and "refunded".
export interface LedgerRow {
  label: string;
  value: string;
  was?: string;
  note?: string;
}

// The ledger under the job id: only the fields the status body really carries, in the order the design draws them.
export function jobLedger(job: PublicJob): LedgerRow[] {
  const rows: LedgerRow[] = [];
  const add = (label: string, value: string): void => {
    if (value) rows.push({ label, value });
  };
  if (typeof job.model === "string") add("Model", job.model);
  if (typeof job.duration === "number") add("Duration", `${job.duration} s`);
  if (typeof job.ratio === "string") add("Ratio", job.ratio);
  if (typeof job.points === "number") {
    rows.push(
      job.refunded === true
        ? { label: "Points", value: "0", was: String(job.points), note: "refunded" }
        : { label: "Points", value: String(job.points) },
    );
  }
  add("Created", clockTime(job.created_at));
  return rows;
}

// "1m 48s", or "48s" under a minute (the Jobs page's fmtTook): from the job's creation to its end. Empty when either
// time is missing.
export function tookText(from: unknown, to: unknown): string {
  if (typeof from !== "string" || typeof to !== "string") return "";
  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (!Number.isFinite(ms) || Math.round(ms / 1000) < 0) return "";
  return fmtTook(ms);
}

// The ledger beside the media of a done job, as drawn: its short id, then what the status body carries.
export function doneLedger(job: PublicJob): Array<{ label: string; value: string }> {
  const rows = [{ label: "Job", value: shortJobId(job.id) }];
  if (typeof job.model === "string" && job.model) rows.push({ label: "Model", value: job.model });
  if (typeof job.duration === "number") rows.push({ label: "Duration", value: `${job.duration} s` });
  const took = tookText(job.created_at, job.finished_at);
  if (took) rows.push({ label: "Took", value: took });
  return rows;
}
