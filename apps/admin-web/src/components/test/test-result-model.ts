import type { JobDetail } from "@/api/jobs-detail";
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

// One step of the Result card's timeline (Test.dc.html): reached steps are filled, the next ones hollow; a failure
// ends it in red. `time` is "" for a step that has not happened, or whose time the relay does not store.
export interface TimelineStep {
  label: string;
  time: string;
  note: string;
  reached: boolean;
  bad?: boolean;
}

const clockOfMs = (ms: number | null | undefined): string => {
  if (!ms) return "";
  const d = new Date(ms);
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
};

const plural = (n: number, one: string): string => `${n} ${one}${n === 1 ? "" : "s"}`;

// What the upstream took, from what the job stores: the figure the provider reported (`upstream_price`) against the
// expected one (`account_cost`), in the unit of the model's pool. Empty when neither is stored.
export function upstreamNote(
  d: Pick<JobDetail, "provider" | "upstream_price" | "account_cost">,
  unit: string | null,
): string {
  const u = unit ? ` ${unit}` : "";
  if (d.upstream_price !== null) {
    const expected =
      d.account_cost === null
        ? ""
        : d.account_cost === d.upstream_price
          ? ", as expected"
          : `, expected ${d.account_cost}`;
    return `${d.provider} took ${d.upstream_price}${u}${expected}`;
  }
  return d.account_cost === null ? "" : `expected ${d.account_cost}${u}; ${d.provider} reported no figure`;
}

// The timeline of a job from the ADMIN job detail (the public status stores no claim and no send time): Accepted and
// Charged at its creation, Claimed by its account (the claim time is not stored: no time), Sent upstream at `sent_ms`,
// Upstream done at its first `completed` event, Delivered at `finished_ms`. A failed job ends with Failed instead of
// the steps it never reached.
export function timelineSteps(d: JobDetail | null, unit: string | null): TimelineStep[] {
  if (!d) return [];
  const at = (stage: string): number | null => d.events.find((e) => e.stage === stage)?.at_ms ?? null;
  const params = (d.params && typeof d.params === "object" ? d.params : {}) as Record<string, unknown>;
  const shape = [
    d.model,
    typeof params.duration === "number" ? `${params.duration} s` : "",
    typeof params.ratio === "string" ? params.ratio : "",
  ];
  const outputs = d.media.filter((m) => m.role === "output").length;
  const doneAt = at("completed");
  const steps: TimelineStep[] = [
    {
      label: "Accepted",
      time: clockOfMs(d.created_ms),
      note: `POST ${d.media_type === "image" ? "/v1/images/generations" : "/v1/video/generations"}`,
      reached: true,
    },
    {
      label: `Charged ${plural(d.price, "point")}`,
      time: clockOfMs(d.created_ms),
      note: d.price !== d.net_charged ? `${d.net_charged} kept after refunds` : "",
      reached: true,
    },
    {
      label: d.account_label ? `Claimed by ${d.account_label}` : "Claimed by an account",
      time: "",
      note: d.claims > 1 ? `claimed ${d.claims} times` : "",
      reached: d.account_id !== null,
    },
    {
      label: "Sent upstream",
      time: clockOfMs(d.sent_ms),
      note: shape.filter(Boolean).join(" · "),
      reached: d.sent_ms !== null,
    },
    {
      label: "Upstream done",
      time: clockOfMs(doneAt),
      note: upstreamNote(d, unit),
      reached: doneAt !== null || d.status === "done",
    },
    {
      label: "Delivered",
      time: d.status === "done" ? clockOfMs(d.finished_ms) : "",
      note:
        d.status === "done"
          ? [
              plural(outputs, d.media_type === "image" ? "image" : "video"),
              d.callback.status ? `webhook ${d.callback.status}` : "",
            ]
              .filter(Boolean)
              .join(" · ")
          : "",
      reached: d.status === "done",
    },
  ];
  if (d.status !== "failed") return steps;
  const kept = steps.filter((s) => s.reached);
  return [
    ...kept,
    { label: "Failed", time: clockOfMs(d.finished_ms), note: d.error_code ?? "", reached: true, bad: true },
  ];
}
