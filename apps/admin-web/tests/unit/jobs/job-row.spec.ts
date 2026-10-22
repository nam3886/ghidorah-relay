import { describe, expect, it } from "vitest";
import { clock, isRunning, netPoints } from "@/components/jobs/job-row";

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
