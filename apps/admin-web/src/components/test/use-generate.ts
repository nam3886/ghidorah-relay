import { ref, shallowRef } from "vue";
import { createJob, getJob, type PublicJob, type PublicModel } from "@/api/test";
import { useLatest } from "@/composables/use-latest";
import type { TestSession } from "./use-test-session";

export const POLL_INTERVAL_MS = 3000;
export const POLL_MAX_TRIES = 600;
const FINISHED = new Set(["done", "failed", "completed"]);

export const jobLine = (job: PublicJob): string => `Job ${job.id} ${job.status}${job.stage ? ` (${job.stage})` : ""}`;

// One generation: create the job, then ask for it every 3 s, up to 600 times (30 minutes), until it is done, failed or
// completed. The loop belongs to the Test tab, which is kept alive, so it goes on while another tab is shown. It ends
// only when a new Generate starts, on stop() (sign-out, a 401, the tab being destroyed) or when the page unloads. Each
// loop takes a generation ticket and every answer, error and state write of an older loop is dropped.
export function useGenerate(session: TestSession) {
  const latest = useLatest();
  const running = ref(false);
  // True only while the job is being created (the POST in flight).
  const submitting = ref(false);
  const job = shallowRef<PublicJob | null>(null);
  // The kind of the model the shown job was made for: it decides how its media is drawn.
  const jobKind = ref("");

  const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

  async function start(model: PublicModel, buildBody: () => Promise<Record<string, unknown>>): Promise<void> {
    // A double click while the job is being created: the first request is the only one.
    if (submitting.value) return;
    const mine = latest.next();
    const live = () => latest.isCurrent(mine);
    running.value = true;
    job.value = null;
    jobKind.value = model.kind;
    const show = (next: PublicJob) => {
      job.value = next;
      session.status.value = jobLine(next);
    };
    try {
      // Generate is off only until the job id comes back; polling then runs while a new Generate may start.
      let made: PublicJob;
      submitting.value = true;
      try {
        const body = await buildBody();
        if (!live()) return;
        made = await session.call((key) => createJob(key, model.kind, body));
      } finally {
        submitting.value = false;
      }
      if (!live()) return;
      show(made);
      for (let i = 0; i < POLL_MAX_TRIES && live(); i++) {
        await sleep(POLL_INTERVAL_MS);
        if (!live()) return;
        const next = await session.call((key) => getJob(key, model.kind, made.id));
        if (!live()) return;
        show(next);
        if (FINISHED.has(next.status)) return;
      }
    } catch (err) {
      if (live()) session.status.value = err instanceof Error ? err.message : String(err);
    } finally {
      if (live()) running.value = false;
    }
  }

  function stop(): void {
    latest.invalidate();
    running.value = false;
    submitting.value = false;
  }

  return { running, submitting, job, jobKind, start, stop };
}
