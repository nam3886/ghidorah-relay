import { describe, expect, it } from "vitest";
import type { ProviderSummary, ProvidersSummary } from "@/api/shell";
import {
  axisOf,
  causeOf,
  failedShare,
  failsALot,
  hourSlots,
  pathLabel,
  rowsOf,
  totalsOf,
  usableShare,
} from "@/components/jobs/today";

const H = 3_600_000;
const START = Date.UTC(2026, 9, 23, 0);
const provider = (name: string, over: Partial<ProviderSummary> = {}): ProviderSummary => ({
  provider: name,
  execution: "http",
  claims_enabled: true,
  running: 0,
  queued: 0,
  done_today: 0,
  failed_today: 0,
  accounts: { usable: 0, total: 0 },
  pools: [],
  ...over,
});
// three hours so far: dola 2 done at 00:00, vibes 3 done + 1 failed and dola 1 done at 02:00
const SUMMARY: ProvidersSummary = {
  day_tz: "UTC",
  day_start_ms: START,
  providers: [
    provider("vibes", { running: 1, queued: 2, done_today: 3, failed_today: 1, accounts: { usable: 4, total: 5 } }),
    provider("dola", { execution: "browser", running: 2, done_today: 3, accounts: { usable: 6, total: 8 } }),
    provider("flow", { claims_enabled: false }),
  ],
  hours: [
    { h: START, done: { dola: 2, vibes: 0, flow: 0 }, failed: { dola: 0, vibes: 0, flow: 0 } },
    { h: START + H, done: { dola: 0, vibes: 0, flow: 0 }, failed: { dola: 0, vibes: 0, flow: 0 } },
    { h: START + 2 * H, done: { dola: 1, vibes: 3, flow: 0 }, failed: { dola: 0, vibes: 1, flow: 0 } },
  ],
};

describe("the Today section", () => {
  it("draws the providers in the drawn order: dola, meta, vibes, flow", () => {
    expect(rowsOf(SUMMARY).map((p) => p.provider)).toEqual(["dola", "vibes", "flow"]);
  });

  it("stacks each hour's done jobs by provider on one scale, then flat stubs for the hours to come", () => {
    const slots = hourSlots(SUMMARY, 100);
    expect(slots).toHaveLength(24);
    expect(slots.map((s) => s.future).slice(0, 4)).toEqual([false, false, false, true]);
    expect(slots[2]!.segs).toEqual([
      { provider: "dola", px: 25 },
      { provider: "vibes", px: 75 },
    ]);
    expect(slots[0]!.segs).toEqual([{ provider: "dola", px: 50 }]);
    expect(slots[1]!.segs).toEqual([]);
    expect(slots[2]!.now).toBe(true);
    expect(slots[0]!.now).toBe(false);
    expect(slots[2]!.title).toBe("02:00: 4 done");
    expect(slots[23]!.title).toBe("23:00: not yet");
  });

  it("a provider with one job in a busy hour is still 2 px high", () => {
    const busy: ProvidersSummary = {
      ...SUMMARY,
      hours: [{ h: START, done: { dola: 1, vibes: 999 }, failed: {} }],
    };
    expect(hourSlots(busy, 170)[0]!.segs[0]).toEqual({ provider: "dola", px: 2 });
  });

  it("the axis reads the day's start, +6 h, +12 h, the clock now and the day's end", () => {
    expect(axisOf(SUMMARY, START + 14 * H + 32 * 60_000)).toEqual(["00:00", "06:00", "12:00", "14:32 now", "24:00"]);
  });

  it("totals add every provider; the failed share has one decimal", () => {
    expect(totalsOf(SUMMARY.providers)).toEqual({
      running: 3,
      queued: 2,
      done: 6,
      failed: 1,
      usable: 10,
      accounts: 13,
    });
    expect(failedShare(1241, 31)).toBe("2.4%");
    expect(failedShare(0, 0)).toBe("0%");
  });

  it("a provider fails a lot above 3% of what it finished", () => {
    expect(failsALot({ done_today: 446, failed_today: 19 })).toBe(true);
    expect(failsALot({ done_today: 138, failed_today: 2 })).toBe(false);
  });

  it("names the send path, Paused when it takes no new job, and the usable share", () => {
    const [dola, vibes, flow] = rowsOf(SUMMARY);
    expect([pathLabel(dola!), pathLabel(vibes!), pathLabel(flow!)]).toEqual(["Browser", "HTTP", "Paused"]);
    expect(usableShare(dola!)).toBe(75);
    expect(usableShare(flow!)).toBe(0);
  });

  it("a card's cause is the first live alert about its provider", () => {
    const alerts = [
      { kind: "disk", severity: "warning", message: "disk 91%", provider: null },
      { kind: "rate", severity: "error", message: "dola fails 4.1%", provider: "dola" },
    ];
    expect(causeOf(alerts, "dola")).toBe("dola fails 4.1%");
    expect(causeOf(alerts, "vibes")).toBe("");
    expect(causeOf(undefined, "dola")).toBe("");
  });
});
