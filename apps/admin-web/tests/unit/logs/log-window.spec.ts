import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  clockMs,
  clockTime,
  dayAfter,
  dayStart,
  durationText,
  errorTone,
  filterQuery,
  fullTime,
  isLogStatus,
  isSlow,
  logKey,
  logQuery,
  PAGE_SIZE,
  splitRoute,
  statusTone,
  STATUS_OPTIONS,
  thousands,
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
    expect(logQuery(filters, "", 0)).toEqual({ limit: 50, offset: 0, status: "all" });
  });

  it("From alone sets since only; To alone sets until only", () => {
    expect(logQuery({ ...filters, from: "2026-10-15" }, "", 0)).toEqual({
      limit: 50,
      offset: 0,
      status: "all",
      since_ms: Date.UTC(2026, 9, 14, 17),
    });
    expect(logQuery({ ...filters, to: "2026-10-15" }, "", 0)).toEqual({
      limit: 50,
      offset: 0,
      status: "all",
      until_ms: Date.UTC(2026, 9, 15, 17),
    });
  });

  it("both dates, with the search trimmed, the provider, the status and the offset", () => {
    expect(logQuery({ q: "  abc  ", status: "pre_job", from: "2026-10-01", to: "2026-10-31" }, "dola", 100)).toEqual({
      limit: 50,
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
  it("accepts the five statuses and nothing else", () => {
    for (const s of ["all", "2xx", "4xx", "5xx", "pre_job"]) expect(isLogStatus(s)).toBe(true);
    for (const s of ["", "done", "ok", "error", "3xx", undefined, null, ["2xx"], 3]) expect(isLogStatus(s)).toBe(false);
  });
});

describe("the filters alone (the export)", () => {
  it("carry the key, the provider, the status, the search and the window, and no page", () => {
    const f = { q: " x ", status: "5xx" as const, from: "2026-10-01", to: "2026-10-01", key: "key-a" };
    expect(filterQuery(f, "dola")).toEqual({
      status: "5xx",
      provider: "dola",
      key_id: "key-a",
      q: "x",
      since_ms: dayStart("2026-10-01"),
      until_ms: dayAfter("2026-10-01"),
    });
    expect(logQuery(f, "dola", 50)).toEqual({ limit: 50, offset: 50, ...filterQuery(f, "dola") });
    expect(filterQuery({ ...f, key: "" }, "")).not.toHaveProperty("key_id");
  });
});

describe("how a request is written", () => {
  it("a page is 50 requests", () => {
    expect(PAGE_SIZE).toBe(50);
  });

  it("the Status chips offer the canvas words for the status families", () => {
    expect(STATUS_OPTIONS.map((o) => [o.value, o.label])).toEqual([
      ["all", "All"],
      ["2xx", "2xx"],
      ["4xx", "4xx"],
      ["5xx", "5xx"],
      ["pre_job", "Failed before a job"],
    ]);
  });

  it("a number has a thousands comma", () => {
    expect(thousands(1980)).toBe("1,980");
    expect(thousands(388)).toBe("388");
    expect(thousands(4812)).toBe("4,812");
  });

  it("a status is ok below 400, a warning from 400, bad from 500", () => {
    expect([200, 399, 400, 499, 500, 503].map(statusTone)).toEqual(["ok", "ok", "warn", "warn", "bad", "bad"]);
  });

  it("an error code takes its status's family, and is red on a request that went through", () => {
    expect([200, 422, 429, 500, 503].map(errorTone)).toEqual(["bad", "warn", "warn", "bad", "bad"]);
  });

  it("the clock has two digits for each part, in local time; a missing time is a dash", () => {
    const ms = new Date(2026, 9, 5, 8, 4, 9, 7).getTime();
    expect(clockTime(ms)).toBe("08:04:09");
    expect(clockMs(ms)).toBe(".007");
    expect(fullTime(ms)).toBe("5 Oct 08:04:09.007");
    expect(clockTime(null)).toBe("—");
    expect(clockMs(undefined)).toBe("");
    expect(fullTime(undefined)).toBe("—");
  });

  it("a duration is in ms, and from one second in seconds with two decimals, the slow ones marked", () => {
    expect(durationText(412)).toBe("412 ms");
    expect(durationText(999)).toBe("999 ms");
    expect(durationText(1000)).toBe("1.00 s");
    expect(durationText(1840)).toBe("1.84 s");
    expect(isSlow(999)).toBe(false);
    expect(isSlow(1000)).toBe(true);
  });

  it("a route splits into its method and its path; one with no method is all path", () => {
    expect(splitRoute("POST /v1/videos")).toEqual({ method: "POST", path: "/v1/videos" });
    expect(splitRoute("GET /v1/jobs/9c41e7b2")).toEqual({ method: "GET", path: "/v1/jobs/9c41e7b2" });
    expect(splitRoute("/v1/models")).toEqual({ method: "", path: "/v1/models" });
  });
});

describe("the query", () => {
  it("carries no zone: the list has no day headers to count", () => {
    expect(logQuery({ q: "", status: "all", from: "", to: "" }, "", 0)).not.toHaveProperty("tz");
  });
});

describe("logKey", () => {
  it("names a request's key by its label, else its prefix (a label of spaces is no label), unknown key for none", () => {
    const row = (key_label: string | null) => ({ key_id: "0123456789", key_label, key_prefix: "rk_1" });
    expect(logKey(row("main"))).toBe("main");
    expect(logKey(row(""))).toBe("rk_1");
    expect(logKey(row("   "))).toBe("rk_1");
    expect(logKey({ key_id: null, key_label: null, key_prefix: null })).toBe("unknown key");
  });
});
