import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { fmtTime } from "@/lib/format";

const realTz = process.env.TZ;
beforeEach(() => {
  process.env.TZ = "UTC";
});
afterEach(() => {
  if (realTz === undefined) delete process.env.TZ;
  else process.env.TZ = realTz;
});

describe("fmtTime", () => {
  it("shows a dash for a missing time", () => {
    expect(fmtTime(0)).toBe("-");
    expect(fmtTime(null)).toBe("-");
    expect(fmtTime(undefined)).toBe("-");
  });

  it("uses the en-GB day-first form with a 24 hour clock", () => {
    expect(fmtTime(Date.UTC(2026, 0, 2, 3, 4, 5))).toBe("02/01/2026, 03:04:05");
    expect(fmtTime(Date.UTC(2026, 9, 14, 15, 30, 0))).toBe("14/10/2026, 15:30:00");
  });

  it("writes midnight as 00, not 24", () => {
    expect(fmtTime(Date.UTC(2026, 5, 7, 0, 0, 9))).toBe("07/06/2026, 00:00:09");
  });

  it("never offers a line break: the date and the clock stay together", () => {
    const text = fmtTime(Date.UTC(2026, 9, 15, 4, 9, 24));
    expect(text).not.toMatch(/[ \t\n]/);
    expect(text).toContain(" ");
  });
});
