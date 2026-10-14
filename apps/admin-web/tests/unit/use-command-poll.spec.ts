import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CommandStatus } from "@/api/commands";
import { getCommand } from "@/api/commands";
import { POLL_INTERVAL_MS, POLL_MAX_TRIES, useCommandPoll } from "@/composables/use-command-poll";
import { notify } from "@/lib/notify";

vi.mock("@/api/commands", () => ({ getCommand: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const command = (status: string, result: CommandStatus["result"] = null): CommandStatus => ({
  id: "c1",
  provider: "dola",
  kind: "check",
  account_id: null,
  job_id: null,
  status,
  result,
  attempts: 1,
  created_ms: 1,
  finished_ms: null,
});
const get = vi.mocked(getCommand);

beforeEach(() => {
  vi.useFakeTimers();
  get.mockReset();
  vi.mocked(notify.success).mockReset();
  vi.mocked(notify.error).mockReset();
  vi.mocked(notify.info).mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("useCommandPoll", () => {
  it("polls every second up to 90 times", () => {
    expect(POLL_INTERVAL_MS).toBe(1000);
    expect(POLL_MAX_TRIES).toBe(90);
  });

  it("stops at done with a success toast, one second between asks", async () => {
    get
      .mockResolvedValueOnce(command("queued"))
      .mockResolvedValueOnce(command("running"))
      .mockResolvedValueOnce(command("done"));
    const pending = useCommandPoll().poll("c1", "Check");
    await vi.advanceTimersByTimeAsync(0);
    expect(get).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(999);
    expect(get).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(get).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1000);
    const result = await pending;
    expect(get).toHaveBeenCalledTimes(3);
    expect(get).toHaveBeenCalledWith("c1");
    expect(result?.status).toBe("done");
    expect(notify.success).toHaveBeenCalledExactlyOnceWith("Check: done");
    expect(notify.error).not.toHaveBeenCalled();
    expect(notify.info).not.toHaveBeenCalled();
  });

  it("stops at failed with the worker error in the toast", async () => {
    get.mockResolvedValue(command("failed", { error: "proxy refused" }));
    const result = await useCommandPoll().poll("c1", "Wake");
    expect(result?.status).toBe("failed");
    expect(notify.error).toHaveBeenCalledExactlyOnceWith("Wake: failed - proxy refused");
    expect(notify.success).not.toHaveBeenCalled();
  });

  it("says just failed when the worker left no error text", async () => {
    get.mockResolvedValueOnce(command("failed")).mockResolvedValueOnce(command("failed", {}));
    await useCommandPoll().poll("c1", "Wake");
    await useCommandPoll().poll("c1", "Wake");
    expect(vi.mocked(notify.error).mock.calls).toEqual([["Wake: failed"], ["Wake: failed"]]);
  });

  it("gives up after 90 asks with an info toast and returns null", async () => {
    get.mockResolvedValue(command("running"));
    const pending = useCommandPoll().poll("c1", "Check all");
    await vi.advanceTimersByTimeAsync(89 * 1000);
    expect(get).toHaveBeenCalledTimes(90);
    expect(notify.info).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(await pending).toBeNull();
    expect(get).toHaveBeenCalledTimes(90);
    expect(notify.info).toHaveBeenCalledExactlyOnceWith("Check all: still running");
    expect(notify.success).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("lets a failed status call end the poll with that error and no toast", async () => {
    const failure = new Error("boom");
    get.mockResolvedValueOnce(command("running")).mockRejectedValueOnce(failure);
    const pending = useCommandPoll().poll("c1", "Check");
    const caught = pending.catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await caught).toBe(failure);
    expect(notify.error).not.toHaveBeenCalled();
    expect(notify.info).not.toHaveBeenCalled();
  });

  it("takes the interval and the number of tries as options", async () => {
    get.mockResolvedValue(command("running"));
    const pending = useCommandPoll({ intervalMs: 10, maxTries: 3 }).poll("c1", "Quick");
    await vi.advanceTimersByTimeAsync(30);
    expect(await pending).toBeNull();
    expect(get).toHaveBeenCalledTimes(3);
  });
});
