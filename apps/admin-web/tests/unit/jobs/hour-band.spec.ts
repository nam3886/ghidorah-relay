import { describe, expect, it } from "vitest";
import type { HourBucket, Stats } from "@/api/shell";
import {
  axisLabels,
  bandLedger,
  bandTotals,
  barTitle,
  chartLabel,
  HOUR_MS,
  hourRange,
  scaleOf,
  segmentHeight,
} from "@/components/jobs/hour-band";

// The 24 columns of the sample day, oldest first: the hour starts at 21:00 local time the day before.
const COUNTS: [number, number, number][] = [
  [4, 0, 0],
  [3, 0, 0],
  [2, 1, 0],
  [1, 0, 0],
  [1, 0, 0],
  [2, 0, 0],
  [3, 0, 0],
  [5, 0, 0],
  [6, 0, 0],
  [8, 0, 0],
  [9, 0, 0],
  [11, 0, 0],
  [9, 0, 0],
  [10, 0, 0],
  [12, 1, 0],
  [13, 0, 0],
  [11, 0, 0],
  [14, 0, 0],
  [12, 0, 0],
  [13, 1, 0],
  [15, 0, 0],
  [14, 2, 0],
  [2, 0, 0],
  [1, 1, 4],
];
const HOURS: HourBucket[] = COUNTS.map(([delivered, failed, in_progress], i) => ({
  hour_ms: new Date(2026, 9, 14, 21 + i).getTime(),
  delivered,
  failed,
  in_progress,
}));

const STATS: Stats = {
  jobs: { queued: 1, in_progress: 3, done: 1284, failed: 37, pre_job_failures_24h: 6 },
  made: { videos: 335, images: 120 },
  accounts: { usable: 7, available: 8, dead: 1, disabled: 0 },
  keys: 5,
  alerts: [],
};

describe("the 24-hour band", () => {
  it("counts the title and the legend from the 24 columns", () => {
    expect(bandTotals(HOURS)).toEqual({ delivered: 181, failed: 6, in_progress: 4, all: 191 });
  });

  it("titles a column with its hour and the counts that are not zero", () => {
    expect(barTitle(HOURS[0]!)).toBe("21:00 · 4 delivered");
    expect(barTitle(HOURS[2]!)).toBe("23:00 · 2 delivered, 1 failed");
    expect(barTitle(HOURS[23]!)).toBe("20:00 · 1 delivered, 1 failed, 4 in progress");
  });

  it("describes the chart by its busiest hour and the hours that had failures", () => {
    expect(chartLabel(HOURS)).toBe(
      "Jobs per hour over the last 24 hours. Peak 16 jobs at 18:00, failures at 23:00, 11:00, 16:00, 18:00 and 20:00.",
    );
  });

  it("the chart label copes with one failure hour, none, and an empty day", () => {
    const one = HOURS.map((h, i) => ({ ...h, failed: i === 2 ? 1 : 0 }));
    expect(chartLabel(one)).toContain("failures at 23:00.");
    const two = HOURS.map((h, i) => ({ ...h, failed: i === 2 || i === 14 ? 1 : 0 }));
    expect(chartLabel(two)).toContain("failures at 23:00 and 11:00.");
    expect(chartLabel(HOURS.map((h) => ({ ...h, failed: 0 })))).toContain("no failures.");
    expect(chartLabel(HOURS.map((h) => ({ ...h, delivered: 0, failed: 0, in_progress: 0 })))).toBe(
      "Jobs per hour over the last 24 hours. No jobs.",
    );
    expect(
      chartLabel(HOURS.map((h, i) => ({ ...h, delivered: i === 0 ? 1 : 0, failed: 0, in_progress: 0 }))),
    ).toContain("Peak 1 job at");
  });

  it("draws a segment as its count over 20, so the peak hour of 16 jobs is 80% of the column", () => {
    expect(scaleOf(HOURS)).toBe(20);
    expect(segmentHeight(14, 20)).toBe(70);
    expect(segmentHeight(2, 20)).toBe(10);
    expect(segmentHeight(0, 20)).toBe(0);
  });

  it("a busier hour than the scale raises the scale, so no column is taller than its box", () => {
    const busy = HOURS.map((h, i) => (i === 5 ? { ...h, delivered: 40 } : h));
    expect(scaleOf(busy)).toBe(40);
    expect(segmentHeight(40, scaleOf(busy))).toBe(100);
  });

  it("labels every third hour (eight labels), every sixth on a phone (four)", () => {
    expect(axisLabels(HOURS, 3)).toEqual(["21:00", "00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00"]);
    expect(axisLabels(HOURS, 6)).toEqual(["21:00", "03:00", "09:00", "15:00"]);
  });

  it("names the hour chip with an en dash between the hour and the next", () => {
    expect(hourRange(HOURS[23]!.hour_ms)).toBe("Hour 20:00–21:00");
    expect(HOURS[1]!.hour_ms - HOURS[0]!.hour_ms).toBe(HOUR_MS);
  });

  it.each([
    ["Asia/Saigon", Date.UTC(2026, 9, 14, 17)],
    ["America/Los_Angeles", Date.UTC(2026, 9, 15, 7)],
    ["Pacific/Auckland", Date.UTC(2026, 9, 14, 11)],
  ])("labels an hour in the zone of the process (%s), the same one the list clock uses", (tz, midnight) => {
    const zone = process.env.TZ;
    process.env.TZ = tz;
    try {
      expect(hourRange(midnight)).toBe("Hour 00:00–01:00");
      expect(barTitle({ hour_ms: midnight - HOUR_MS, delivered: 1, failed: 0, in_progress: 0 })).toBe(
        "23:00 · 1 delivered",
      );
    } finally {
      if (zone === undefined) delete process.env.TZ;
      else process.env.TZ = zone;
    }
  });

  it("the desktop ledger has eight rows and folds dead and disabled accounts into one", () => {
    const rows = bandLedger(STATS, false);
    expect(rows.map((r) => [r.label, r.value, r.tone ?? null])).toEqual([
      ["In progress", "4", null],
      ["Completed", "1,284", null],
      ["Failed", "37", "bad"],
      ["Failed pre-job · 24 h", "6", "bad"],
      ["Accounts ready", "7 / 8", null],
      ["Accounts dead · disabled", "1 · 0", "bad"],
      ["Videos made", "335", null],
      ["Images made", "120", null],
    ]);
  });

  it("a tablet and a phone get nine rows: dead (bad) and disabled apart", () => {
    const rows = bandLedger(STATS, true);
    expect(rows).toHaveLength(9);
    expect(rows.slice(5, 7).map((r) => [r.label, r.value, r.tone ?? null])).toEqual([
      ["Accounts dead", "1", "bad"],
      ["Accounts disabled", "0", null],
    ]);
  });

  it("has no ledger until the stats arrive", () => {
    expect(bandLedger(null, false)).toEqual([]);
  });
});
