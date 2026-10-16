import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  clockTime,
  dateClock,
  dayAfter,
  dayStart,
  isLogStatus,
  isServerError,
  logQuery,
  PAGE_SIZE,
  STATUS_OPTIONS,
  thousands,
  viewerZone,
  dayKey,
  dayNote,
} from "@/components/logs/log-window";

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
    expect(logQuery(filters, "", 0)).toEqual({ limit: 50, offset: 0, status: "all", tz: viewerZone() });
  });

  it("From alone sets since only; To alone sets until only", () => {
    expect(logQuery({ ...filters, from: "2026-10-15" }, "", 0)).toEqual({
      limit: 50,
      tz: viewerZone(),
      offset: 0,
      status: "all",
      since_ms: Date.UTC(2026, 9, 14, 17),
    });
    expect(logQuery({ ...filters, to: "2026-10-15" }, "", 0)).toEqual({
      limit: 50,
      tz: viewerZone(),
      offset: 0,
      status: "all",
      until_ms: Date.UTC(2026, 9, 15, 17),
    });
  });

  it("both dates, with the search trimmed, the provider, the status and the offset", () => {
    expect(logQuery({ q: "  abc  ", status: "pre_job", from: "2026-10-01", to: "2026-10-31" }, "dola", 100)).toEqual({
      limit: 50,
      tz: viewerZone(),
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

describe("how a request is written", () => {
  it("a page is 50 requests", () => {
    expect(PAGE_SIZE).toBe(50);
  });

  it("the Status select offers the canvas words for the four statuses", () => {
    expect(STATUS_OPTIONS.map((o) => [o.value, o.label])).toEqual([
      ["all", "All"],
      ["ok", "OK below 400"],
      ["error", "Errors"],
      ["pre_job", "Failed before a job"],
    ]);
  });

  it("a number has a thousands comma", () => {
    expect(thousands(1980)).toBe("1,980");
    expect(thousands(388)).toBe("388");
    expect(thousands(4812)).toBe("4,812");
  });

  it("only a code of 500 or more is a server error", () => {
    expect(isServerError(502)).toBe(true);
    expect(isServerError(500)).toBe(true);
    expect(isServerError(499)).toBe(false);
    expect(isServerError(422)).toBe(false);
  });

  it("the clock has two digits for each part, in local time; a missing time is a dash", () => {
    const ms = new Date(2026, 9, 5, 8, 4, 9).getTime();
    expect(clockTime(ms)).toBe("08:04:09");
    expect(dateClock(ms)).toBe("05/10 08:04:09");
    expect(clockTime(null)).toBe("—");
    expect(dateClock(undefined)).toBe("—");
  });
});

describe("the day headers", () => {
  it("the query carries the viewer's own zone, so the server counts each local day in it", () => {
    expect(viewerZone()).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
    expect(logQuery({ q: "", status: "all", from: "", to: "" }, "", 0).tz).toBe(viewerZone());
  });

  it("a day's key is its local date with two digits for month and day", () => {
    expect(dayKey(new Date(2026, 0, 5, 23, 59).getTime())).toBe("2026-01-05");
    expect(dayKey(new Date(2026, 9, 15, 0, 0).getTime())).toBe("2026-10-15");
  });

  it("the note counts the rows shown, and says of how many when the day has more", () => {
    expect(dayNote(7, 7)).toBe("7 requests shown");
    expect(dayNote(7)).toBe("7 requests shown");
    expect(dayNote(1, 1)).toBe("1 request shown");
    expect(dayNote(5, 4761)).toBe("5 of 4,761 requests shown");
    expect(dayNote(1, 2)).toBe("1 of 2 requests shown");
  });
});
