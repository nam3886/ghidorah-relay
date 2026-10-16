import type { PublicJob } from "@/api/test";

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

// The ledger under the job id: only the fields the status body really carries, in the order the design draws them.
export function jobLedger(job: PublicJob): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [];
  const add = (label: string, value: string): void => {
    if (value) rows.push({ label, value });
  };
  if (typeof job.model === "string") add("Model", job.model);
  if (typeof job.duration === "number") add("Duration", `${job.duration} s`);
  if (typeof job.ratio === "string") add("Ratio", job.ratio);
  add("Created", clockTime(job.created_at));
  return rows;
}
