import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { dayAfter, dayStart, isLogStatus, logQuery } from "@/components/logs/log-window";

// The window is computed in the zone of the machine that runs the browser, so the specs pin the zone and check that it
// took effect (a spec that quietly ran in another zone would prove nothing).
const savedZone = process.env.TZ;
function inZone(zone: string, probe: [number, number, number], minutesWestOfUtc: number) {
  process.env.TZ = zone;
  // minutes WEST of UTC, so New York in winter is 300
  expect(new Date(...probe).getTimezoneOffset(), `TZ=${zone} did not take effect`).toBe(minutesWestOfUtc);
}
afterAll(() => {
  if (savedZone === undefined) delete process.env.TZ;
  else process.env.TZ = savedZone;
});

describe("the window in Asia/Saigon (UTC+7, no clock change)", () => {
  beforeAll(() => inZone("Asia/Saigon", [2026, 9, 15], -420));

  it("a day starts at its own local midnight", () => {
    expect(dayStart("2026-10-15")).toBe(Date.UTC(2026, 9, 14, 17));
  });

  it("the window ends at the start of the next day, not at the end of the day", () => {
    expect(dayAfter("2026-10-15")).toBe(Date.UTC(2026, 9, 15, 17));
    expect(dayAfter("2026-10-15")).toBe(dayStart("2026-10-16"));
    expect(dayAfter("2026-10-15") - dayStart("2026-10-15")).toBe(24 * 3600 * 1000);
  });

  it("rolls over a month end, a year end and a leap day", () => {
    expect(dayAfter("2026-10-31")).toBe(Date.UTC(2026, 9, 31, 17));
    expect(dayAfter("2026-12-31")).toBe(Date.UTC(2026, 11, 31, 17));
    expect(dayAfter("2028-02-28")).toBe(Date.UTC(2028, 1, 28, 17));
    expect(dayAfter("2028-02-29")).toBe(Date.UTC(2028, 1, 29, 17));
  });
});

describe("the window across a clock change (America/New_York)", () => {
  beforeAll(() => inZone("America/New_York", [2026, 0, 15], 300));

  it("the day the clocks go forward is 23 hours long", () => {
    // 2026-03-08: midnight is EST (UTC-5), the next midnight is EDT (UTC-4)
    expect(dayStart("2026-03-08")).toBe(Date.UTC(2026, 2, 8, 5));
    expect(dayAfter("2026-03-08")).toBe(Date.UTC(2026, 2, 9, 4));
    expect(dayAfter("2026-03-08") - dayStart("2026-03-08")).toBe(23 * 3600 * 1000);
  });

  it("the day the clocks go back is 25 hours long", () => {
    // 2026-11-01: midnight is EDT (UTC-4), the next midnight is EST (UTC-5)
    expect(dayStart("2026-11-01")).toBe(Date.UTC(2026, 10, 1, 4));
    expect(dayAfter("2026-11-01")).toBe(Date.UTC(2026, 10, 2, 5));
    expect(dayAfter("2026-11-01") - dayStart("2026-11-01")).toBe(25 * 3600 * 1000);
  });

  it("a plain winter day is 24 hours", () => {
    expect(dayAfter("2026-01-15") - dayStart("2026-01-15")).toBe(24 * 3600 * 1000);
  });
});

describe("logQuery", () => {
  beforeAll(() => inZone("Asia/Saigon", [2026, 9, 15], -420));
  const filters = { q: "", status: "all" as const, from: "", to: "" };

  it("no filters: the page and the status only", () => {
    expect(logQuery(filters, "", 0)).toEqual({ limit: 10, offset: 0, status: "all" });
  });

  it("From alone sets since only; To alone sets until only", () => {
    expect(logQuery({ ...filters, from: "2026-10-15" }, "", 0)).toEqual({
      limit: 10,
      offset: 0,
      status: "all",
      since_ms: Date.UTC(2026, 9, 14, 17),
    });
    expect(logQuery({ ...filters, to: "2026-10-15" }, "", 0)).toEqual({
      limit: 10,
      offset: 0,
      status: "all",
      until_ms: Date.UTC(2026, 9, 15, 17),
    });
  });

  it("both dates, with the search trimmed, the provider, the status and the offset", () => {
    expect(logQuery({ q: "  abc  ", status: "pre_job", from: "2026-10-01", to: "2026-10-31" }, "dola", 100)).toEqual({
      limit: 10,
      offset: 100,
      status: "pre_job",
      provider: "dola",
      q: "abc",
      since_ms: Date.UTC(2026, 8, 30, 17),
      until_ms: Date.UTC(2026, 9, 31, 17),
    });
  });

  it("a blank search is not sent", () => {
    expect(logQuery({ ...filters, q: "   " }, "", 0)).not.toHaveProperty("q");
  });
});

describe("isLogStatus", () => {
  it("accepts the four statuses and nothing else", () => {
    for (const s of ["all", "ok", "error", "pre_job"]) expect(isLogStatus(s)).toBe(true);
    for (const s of ["", "done", "OK", undefined, null, ["ok"], 3]) expect(isLogStatus(s)).toBe(false);
  });
});
