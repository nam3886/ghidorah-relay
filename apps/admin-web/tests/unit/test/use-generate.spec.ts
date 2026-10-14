import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import * as api from "@/api/test";
import { useAuthStore } from "@/stores/auth";
import { jobLine, POLL_INTERVAL_MS, POLL_MAX_TRIES, useGenerate } from "@/components/test/use-generate";
import { useTestSession } from "@/components/test/use-test-session";

vi.mock("@/api/test", async (original) => ({
  ...(await original<typeof import("@/api/test")>()),
  listModels: vi.fn(),
  getPoints: vi.fn(),
  createJob: vi.fn(),
  getJob: vi.fn(),
}));

const video = { id: "veo-3", kind: "video", owned_by: "flow" };
const image = { id: "imagen-4", kind: "image", owned_by: "flow" };
const job = (status: string, extra: object = {}) => ({ id: "j1", status, ...extra });
const body = async () => ({ model: "veo-3", prompt: "x" });

async function loadedSession() {
  vi.mocked(api.listModels).mockResolvedValue({ data: [video] });
  vi.mocked(api.getPoints).mockResolvedValue({});
  const s = useTestSession();
  s.typedKey.value = "rk-1";
  await s.loadModels();
  return s;
}

beforeEach(() => {
  vi.useFakeTimers();
  sessionStorage.clear();
  vi.mocked(api.createJob).mockReset();
  vi.mocked(api.getJob).mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("generating", () => {
  it("polls every 3 seconds up to 600 times", () => {
    expect(POLL_INTERVAL_MS).toBe(3000);
    expect(POLL_MAX_TRIES).toBe(600);
  });

  it("creates the job with the loaded key and kind, then asks for it every 3 s until done", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    vi.mocked(api.createJob).mockResolvedValue(job("queued"));
    vi.mocked(api.getJob)
      .mockResolvedValueOnce(job("running", { stage: "render" }))
      .mockResolvedValueOnce(job("done", { result_urls: ["/m/a.mp4"] }));
    const run = g.start(video, body);
    await vi.advanceTimersByTimeAsync(0);
    expect(api.createJob).toHaveBeenCalledWith("rk-1", "video", { model: "veo-3", prompt: "x" });
    expect(g.running.value).toBe(true);
    expect(s.status.value).toBe("Job j1 queued");
    await vi.advanceTimersByTimeAsync(2999);
    expect(api.getJob).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(api.getJob).toHaveBeenCalledWith("rk-1", "video", "j1");
    expect(s.status.value).toBe("Job j1 running (render)");
    await vi.advanceTimersByTimeAsync(3000);
    await run;
    expect(s.status.value).toBe("Job j1 done");
    expect(g.job.value?.result_urls).toEqual(["/m/a.mp4"]);
    expect(g.running.value).toBe(false);
    expect(api.getJob).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.getJob).toHaveBeenCalledTimes(2);
  });

  it("ends on done, failed and completed", async () => {
    for (const end of ["done", "failed", "completed"]) {
      const s = await loadedSession();
      const g = useGenerate(s);
      vi.mocked(api.createJob).mockResolvedValue(job("queued"));
      vi.mocked(api.getJob).mockReset().mockResolvedValue(job(end));
      const run = g.start(video, body);
      await vi.advanceTimersByTimeAsync(3000);
      await run;
      expect(api.getJob).toHaveBeenCalledTimes(1);
      expect(s.status.value).toBe(`Job j1 ${end}`);
      expect(g.running.value).toBe(false);
    }
  });

  it("gives up after 600 polls and goes idle", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    vi.mocked(api.createJob).mockResolvedValue(job("queued"));
    vi.mocked(api.getJob).mockResolvedValue(job("running"));
    const run = g.start(video, body);
    await vi.advanceTimersByTimeAsync(3000 * 600 + 5000);
    await run;
    expect(api.getJob).toHaveBeenCalledTimes(600);
    expect(g.running.value).toBe(false);
  });

  it("a double click during the POST sends exactly one request", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    let release!: (j: ReturnType<typeof job>) => void;
    vi.mocked(api.createJob).mockReturnValue(new Promise((resolve) => (release = resolve)));
    vi.mocked(api.getJob).mockResolvedValue(job("done"));
    const first = g.start(video, body);
    const second = g.start(video, body);
    await vi.advanceTimersByTimeAsync(0);
    expect(g.submitting.value).toBe(true);
    release(job("queued"));
    await vi.advanceTimersByTimeAsync(3000);
    await Promise.all([first, second]);
    expect(api.createJob).toHaveBeenCalledTimes(1);
    expect(g.submitting.value).toBe(false);
  });

  it("the answer of a poll that was in flight when a new Generate started is dropped", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    let late!: (j: ReturnType<typeof job>) => void;
    vi.mocked(api.createJob)
      .mockResolvedValueOnce({ id: "old", status: "queued" })
      .mockResolvedValueOnce({ id: "new", status: "queued" });
    vi.mocked(api.getJob).mockReturnValueOnce(new Promise((resolve) => (late = resolve)));
    const first = g.start(video, body);
    await vi.advanceTimersByTimeAsync(3000);
    expect(api.getJob).toHaveBeenCalledTimes(1);
    const second = g.start(video, body);
    await vi.advanceTimersByTimeAsync(0);
    late({ id: "old", status: "done" });
    await first;
    expect(s.status.value).toBe("Job new queued");
    expect(g.job.value?.id).toBe("new");
    g.stop();
    await vi.advanceTimersByTimeAsync(10000);
    await second;
  });

  it("a new Generate ends the older loop: its answers are dropped and it asks no more", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    vi.mocked(api.createJob)
      .mockResolvedValueOnce({ id: "old", status: "queued" })
      .mockResolvedValueOnce({ id: "new", status: "queued" });
    vi.mocked(api.getJob).mockImplementation(async (_k, _kind, id) => ({ id, status: "running" }));
    const first = g.start(video, body);
    await vi.advanceTimersByTimeAsync(3000);
    expect(vi.mocked(api.getJob).mock.calls.map((c) => c[2])).toEqual(["old"]);
    const second = g.start(image, body);
    await vi.advanceTimersByTimeAsync(0);
    expect(g.jobKind.value).toBe("image");
    await vi.advanceTimersByTimeAsync(3000);
    await first;
    expect(vi.mocked(api.getJob).mock.calls.map((c) => c[2])).toEqual(["old", "new"]);
    expect(s.status.value).toBe("Job new running");
    expect(g.running.value).toBe(true);
    g.stop();
    await vi.advanceTimersByTimeAsync(10000);
    await second;
    expect(api.getJob).toHaveBeenCalledTimes(2);
  });

  it("stop() (sign-out, a 401, the tab destroyed) ends the loop and the state stays as it was", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    vi.mocked(api.createJob).mockResolvedValue(job("queued"));
    vi.mocked(api.getJob).mockResolvedValue(job("running"));
    const run = g.start(video, body);
    await vi.advanceTimersByTimeAsync(3000);
    g.stop();
    expect(g.running.value).toBe(false);
    await vi.advanceTimersByTimeAsync(30000);
    await run;
    expect(api.getJob).toHaveBeenCalledTimes(1);
  });

  it("a public 401 with a poll in flight ends the loop, forgets the key and shows the error", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    expect(sessionStorage.getItem("relay_test_key")).toBe("rk-1");
    let fail!: (e: unknown) => void;
    vi.mocked(api.createJob).mockResolvedValue(job("queued"));
    vi.mocked(api.getJob).mockReturnValue(new Promise((_resolve, reject) => (fail = reject)));
    const run = g.start(video, body);
    await vi.advanceTimersByTimeAsync(3000);
    expect(api.getJob).toHaveBeenCalledTimes(1);
    fail(new api.TestApiError(401, "auth.invalid_key", "bad key"));
    await run;
    expect(s.status.value).toBe("auth.invalid_key: bad key");
    expect(g.running.value).toBe(false);
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.getJob).toHaveBeenCalledTimes(1);
  });

  it("auth.onSignOut with a poll in flight ends the loop, clears the key and drops the late answer", async () => {
    setActivePinia(createPinia());
    const auth = useAuthStore();
    const s = await loadedSession();
    const g = useGenerate(s);
    auth.onSignOut(() => {
      g.stop();
      s.reset();
    });
    let late!: (j: ReturnType<typeof job>) => void;
    vi.mocked(api.createJob).mockResolvedValue(job("queued"));
    vi.mocked(api.getJob).mockReturnValue(new Promise((resolve) => (late = resolve)));
    const run = g.start(video, body);
    await vi.advanceTimersByTimeAsync(3000);
    expect(api.getJob).toHaveBeenCalledTimes(1);
    auth.signOut();
    late(job("done", { result_urls: ["/m/a.mp4"] }));
    await run;
    expect(g.job.value?.status).toBe("queued"); // the late "done" was not drawn
    expect(s.status.value).toBe("Idle");
    expect(g.running.value).toBe(false);
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.getJob).toHaveBeenCalledTimes(1);
  });

  it("an answer that arrives after stop() is not shown", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    let release!: (j: ReturnType<typeof job>) => void;
    vi.mocked(api.createJob).mockReturnValue(new Promise((r) => (release = r)));
    const run = g.start(video, body);
    await vi.advanceTimersByTimeAsync(0);
    g.stop();
    s.status.value = "Idle";
    release(job("queued"));
    await run;
    expect(s.status.value).toBe("Idle");
    expect(g.job.value).toBeNull();
  });

  it("shows an error as `code: message` and goes idle; a stale error is dropped", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    vi.mocked(api.createJob).mockRejectedValue(new api.TestApiError(402, "billing.insufficient", "not enough points"));
    await g.start(video, body);
    expect(s.status.value).toBe("billing.insufficient: not enough points");
    expect(g.running.value).toBe(false);

    let fail!: (e: unknown) => void;
    vi.mocked(api.createJob).mockReturnValue(new Promise((_r, rej) => (fail = rej)));
    const run = g.start(video, body);
    await vi.advanceTimersByTimeAsync(0);
    g.stop();
    s.status.value = "Idle";
    fail(new Error("late"));
    await run;
    expect(s.status.value).toBe("Idle");
  });

  it("shows a body that cannot be built (bad options JSON) in the status line and sends nothing", async () => {
    const s = await loadedSession();
    const g = useGenerate(s);
    await g.start(video, async () => {
      throw new SyntaxError("Unexpected token n in JSON");
    });
    expect(s.status.value).toBe("Unexpected token n in JSON");
    expect(api.createJob).not.toHaveBeenCalled();
    expect(g.running.value).toBe(false);
  });

  it("has the status line `Job <id> <status> (<stage>)`", () => {
    expect(jobLine(job("running", { stage: "render" }))).toBe("Job j1 running (render)");
    expect(jobLine(job("done"))).toBe("Job j1 done");
  });
});
