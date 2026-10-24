import { describe, expect, it } from "vitest";
import { age, clock, isRunning, isSlow, netPoints, pointsText, statusPill } from "@/components/jobs/job-row";

describe("job row text", () => {
  it("clock is the local time of day on a 24-hour clock, and a dash for no time", () => {
    expect(clock(new Date(2026, 9, 15, 20, 54, 22).getTime())).toBe("20:54:22");
    expect(clock(new Date(2026, 9, 15, 0, 5, 9).getTime())).toBe("00:05:09");
    expect(clock(null)).toBe("—");
    expect(clock(undefined)).toBe("—");
  });

  it("a job is running while it is queued, submitting, waiting or fetching", () => {
    for (const s of ["queued", "submitting", "waiting", "fetching"]) expect(isRunning(s)).toBe(true);
    for (const s of ["done", "failed", "unknown", ""]) expect(isRunning(s)).toBe(false);
  });

  it("net points: no figure for a running job that has charged nothing, the net for every other", () => {
    expect(netPoints({ status: "queued", net_charged: 0 })).toBeNull();
    expect(netPoints({ status: "waiting", net_charged: 0 })).toBeNull();
    expect(netPoints({ status: "queued", net_charged: 2 })).toBe(2);
    expect(netPoints({ status: "done", net_charged: 0 })).toBe(0);
    expect(netPoints({ status: "failed", net_charged: 0 })).toBe(0);
    expect(netPoints({ status: "unknown", net_charged: 2 })).toBe(2);
  });
});

describe("ledger row cells", () => {
  const NOW = Date.UTC(2026, 9, 24, 12, 0, 0);

  it("age counts back from now in s, m, h, then days and hours", () => {
    expect(age(NOW - 59_000, NOW)).toBe("59 s");
    expect(age(NOW - 28 * 60_000, NOW)).toBe("28 m");
    expect(age(NOW - 3 * 3_600_000, NOW)).toBe("3 h");
    expect(age(NOW - (3 * 24 + 4) * 3_600_000, NOW)).toBe("3 d 4 h");
    expect(age(NOW - 2 * 86_400_000, NOW)).toBe("2 d");
    expect(age(null, NOW)).toBe("—");
  });

  it("only a queued job that waited 10 min or more is slow", () => {
    expect(isSlow({ status: "queued", created_ms: NOW - 10 * 60_000 }, NOW)).toBe(true);
    expect(isSlow({ status: "queued", created_ms: NOW - 9 * 60_000 }, NOW)).toBe(false);
    expect(isSlow({ status: "waiting", created_ms: NOW - 60 * 60_000 }, NOW)).toBe(false);
  });

  it("the pill says what a job on its way is doing with its step, the others their word", () => {
    expect(statusPill("waiting")).toEqual({ text: "Rendering 2/4", tone: "brand" });
    expect(statusPill("fetching")).toEqual({ text: "Fetching 3/4", tone: "brand" });
    expect(statusPill("submitting")).toEqual({ text: "Submitting 2/4", tone: "brand" });
    expect(statusPill("queued")).toEqual({ text: "Queued", tone: "neutral" });
    expect(statusPill("failed")).toEqual({ text: "Failed", tone: "bad" });
    expect(statusPill("unknown")).toEqual({ text: "Unknown", tone: "warn" });
    expect(statusPill("done")).toEqual({ text: "Done", tone: "ok" });
  });

  it("points: the net, net / price when part of it was given back, a dash before any charge", () => {
    expect(pointsText({ status: "done", net_charged: 40, price: 40 })).toBe("40");
    expect(pointsText({ status: "failed", net_charged: 0, price: 2 })).toBe("0 / 2");
    expect(pointsText({ status: "queued", net_charged: 0, price: 1 })).toBe("—");
  });
});
