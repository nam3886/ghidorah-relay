import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import {
  eventTone,
  fmtBytes,
  fmtClock,
  fmtDayClock,
  fmtTook,
  isRefunded,
  keyName,
  loadFailedText,
  newestFirst,
  plural,
  progressStep,
  recheckToast,
  regenerateToast,
  shortId,
} from "@/components/jobs/job-text";

describe("the failed load of the list", () => {
  it("says what failed and what the relay answered, as drawn", () => {
    const err = new ApiError(502, { error: { code: "upstream.bad_gateway", message: "bad gateway" } });
    expect(loadFailedText(err, "bad gateway")).toBe("Could not load the jobs: the relay answered 502 (bad gateway).");
  });

  it("names what failed: the drawer's read of one job", () => {
    const err = new ApiError(502, { error: { code: "upstream.bad_gateway", message: "bad gateway" } });
    expect(loadFailedText(err, "bad gateway", "the job")).toBe(
      "Could not load the job: the relay answered 502 (bad gateway).",
    );
  });

  it("with no answer from the relay says the app's own words", () => {
    expect(loadFailedText(new TypeError("Failed to fetch"), "Network error, please try again")).toBe(
      "Could not load the jobs: network error, please try again.",
    );
  });
});

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

  it("a refund is ok and an accepted job is info, whatever their source says", () => {
    expect(eventTone(ev(1, 1, "refund", "failed"))).toBe("ok");
    expect(eventTone(ev(1, 1, "accepted", "failed"))).toBe("info");
  });
});

describe("drawer formats", () => {
  it("a clock is hh:mm:ss in local time, a day clock adds dd/mm, a missing time is a dash", () => {
    const at = new Date(2026, 9, 5, 9, 4, 7).getTime();
    expect(fmtClock(at)).toBe("09:04:07");
    expect(fmtDayClock(at)).toBe("05/10 09:04:07");
    // a time that is not set is the list empty-cell dash
    expect([fmtClock(null), fmtDayClock(undefined), fmtClock(0)]).toEqual(["—", "—", "—"]);
  });

  it("how long a job took reads in seconds, minutes or hours", () => {
    expect([0, 35_000, 95_000, 252_000, 3_720_000].map(fmtTook)).toEqual([
      "0 s",
      "35 s",
      "1 min 35 s",
      "4 min 12 s",
      "1 h 2 min",
    ]);
  });

  it("a size reads in bytes, KB or MB, powers of 1000", () => {
    expect([120, 999, 1000, 5000, 18_400_000].map(fmtBytes)).toEqual(["120 B", "999 B", "1.0 KB", "5.0 KB", "18.4 MB"]);
  });

  it("a count takes an s except for one", () => {
    expect([plural(1, "point"), plural(0, "point"), plural(4, "image")]).toEqual(["1 point", "0 points", "4 images"]);
  });
});
