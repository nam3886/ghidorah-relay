import type { BulkRecheckResult, BulkRegenerateResult, JobSummary } from "@/api/jobs";

import type { JobEvent } from "@/api/jobs-detail";
import { toneOf, type Tone } from "@/components/shared/status-tones";

export const shortId = (id: string): string => id.slice(0, 8);

// What a key shows in a select or a cell: its label, else its prefix, else the first 8 characters of its id, so no
// option is ever blank.
export function keyName(key: { id: string; label?: string | null; prefix?: string | null }): string {
  return key.label?.trim() || key.prefix?.trim() || shortId(key.id);
}

// Element Plus type of a tone, for el-alert (success, warning, error, info) and el-timeline-item (success, warning,
// danger, primary, info).
export const alertType = (tone: Tone): "success" | "warning" | "error" | "info" =>
  tone === "ok" ? "success" : tone === "bad" ? "error" : tone === "warn" || tone === "info" ? "warning" : "info";

// A recovered job is good news; otherwise the stage word (failed, completed...) sets the colour and an unknown stage
// falls back to its source.
export function eventTone(e: Pick<JobEvent, "source" | "stage">): Tone {
  if (e.stage === "recovered") return "ok";
  const byStage = toneOf(e.stage);
  return byStage === "neutral" ? toneOf(e.source) : byStage;
}
export const timelineType = (tone: Tone): "success" | "warning" | "danger" | "primary" | "info" =>
  tone === "ok"
    ? "success"
    : tone === "bad"
      ? "danger"
      : tone === "warn"
        ? "warning"
        : tone === "info"
          ? "primary"
          : "info";

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

export function regenerateToast(out: BulkRegenerateResult): { text: string; type: "success" | "info" } {
  const failed = Object.keys(out.failed).length;
  const replayed = out.created.filter((c) => c.replayed).length;
  return {
    text: `Made ${out.created.length - replayed}, already made ${replayed}, failed ${failed}`,
    type: failed ? "info" : "success",
  };
}
