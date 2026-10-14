import type { BulkRecheckResult, BulkRegenerateResult, JobSummary } from "@/api/jobs";

export const shortId = (id: string): string => id.slice(0, 8);

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

export function regenerateToast(out: BulkRegenerateResult): { text: string; type: "success" | "info" } {
  const failed = Object.keys(out.failed).length;
  const replayed = out.created.filter((c) => c.replayed).length;
  return {
    text: `Made ${out.created.length - replayed}, already made ${replayed}, failed ${failed}`,
    type: failed ? "info" : "success",
  };
}
