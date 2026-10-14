import { describe, expect, it } from "vitest";
import { isRefunded, progressStep, recheckToast, regenerateToast, shortId } from "@/components/jobs/job-text";

describe("job text", () => {
  it("the id is its first 8 characters", () => {
    expect(shortId("0123456789abcdef")).toBe("01234567");
  });

  it.each([
    ["queued", 1],
    ["waiting", 2],
    ["unknown", 2],
    ["fetching", 3],
    ["done", 4],
    ["failed", 4],
    ["submitting", 2],
    ["something-new", 2],
  ])("the bar for %s is at step %i", (status, step) => {
    expect(progressStep(status)).toBe(step);
  });

  it("refunded means failed, charged, and nothing left charged", () => {
    expect(isRefunded({ status: "failed", price: 10, net_charged: 0 })).toBe(true);
    expect(isRefunded({ status: "failed", price: 10, net_charged: 10 })).toBe(false);
    expect(isRefunded({ status: "failed", price: 0, net_charged: 0 })).toBe(false);
    expect(isRefunded({ status: "done", price: 10, net_charged: 0 })).toBe(false);
  });

  it("the re-check toast counts the re-checked jobs and says success", () => {
    expect(recheckToast({ rechecked: ["a", "b"], skipped: {} })).toEqual({ text: "Re-checked 2", type: "success" });
  });

  it("the re-check toast adds the jobs left alone and turns to info", () => {
    expect(recheckToast({ rechecked: ["a"], skipped: { b: "job.no_recheck", c: "x" } })).toEqual({
      text: "Re-checked 1, left 2 alone",
      type: "info",
    });
  });

  it("the regenerate toast splits made, already made and failed", () => {
    const out = {
      created: [
        { from: "a", id: "n1", status: "queued", replayed: false },
        { from: "b", id: "n2", status: "queued", replayed: true },
        { from: "c", id: "n3", status: "queued", replayed: true },
      ],
      failed: { d: "wallet.insufficient" },
    };
    expect(regenerateToast(out)).toEqual({ text: "Made 1, already made 2, failed 1", type: "info" });
  });

  it("the regenerate toast is success when nothing failed", () => {
    const out = { created: [{ from: "a", id: "n1", status: "queued", replayed: false }], failed: {} };
    expect(regenerateToast(out)).toEqual({ text: "Made 1, already made 0, failed 0", type: "success" });
  });
});
