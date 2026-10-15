import { describe, expect, it } from "vitest";
import {
  alertType,
  eventTone,
  isRefunded,
  keyName,
  newestFirst,
  progressStep,
  recheckToast,
  regenerateToast,
  shortId,
  timelineType,
} from "@/components/jobs/job-text";

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

describe("key names", () => {
  it("is the label, else the prefix, else the first 8 characters of the id", () => {
    expect(keyName({ id: "0123456789", label: "main", prefix: "rk_1" })).toBe("main");
    expect(keyName({ id: "0123456789", label: "", prefix: "rk_1" })).toBe("rk_1");
    expect(keyName({ id: "0123456789", label: "   ", prefix: " " })).toBe("01234567");
    expect(keyName({ id: "0123456789" })).toBe("01234567");
  });
});

describe("timeline", () => {
  const ev = (id: number, at_ms: number | null, stage = "x", source = "y") => ({ id, at_ms, source, stage, text: "" });

  it("lists the newest event first, the later id first on equal times, and an event with no time last", () => {
    const out = newestFirst([ev(1, 100), ev(2, null), ev(3, 300), ev(4, 100), ev(5, 200)]);
    expect(out.map((e) => e.id)).toEqual([3, 5, 4, 1, 2]);
  });

  it("does not reorder the list it was given", () => {
    const given = [ev(1, 1), ev(2, 2)];
    newestFirst(given);
    expect(given.map((e) => e.id)).toEqual([1, 2]);
  });

  it("a recovery is ok, a failed stage is bad, an unknown stage takes its source's tone, else neutral", () => {
    expect(eventTone(ev(1, 1, "recovered", "failed"))).toBe("ok");
    expect(eventTone(ev(1, 1, "failed", "x"))).toBe("bad");
    expect(eventTone(ev(1, 1, "unheard-of", "failed"))).toBe("bad");
    expect(eventTone(ev(1, 1, "unheard-of", "unheard-of"))).toBe("neutral");
  });

  it("maps a tone to the Element Plus types of the alert and of the timeline item", () => {
    expect(["ok", "bad", "warn", "info", "neutral"].map((t) => alertType(t as never))).toEqual([
      "success",
      "error",
      "warning",
      "warning",
      "info",
    ]);
    expect(["ok", "bad", "warn", "info", "neutral"].map((t) => timelineType(t as never))).toEqual([
      "success",
      "danger",
      "warning",
      "primary",
      "info",
    ]);
  });
});
