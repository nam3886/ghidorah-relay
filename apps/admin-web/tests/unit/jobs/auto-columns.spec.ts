import { describe, expect, it } from "vitest";
import { autoColumns, COLUMN_CAP, sameColumns } from "../../../src/components/jobs/auto-columns";

// the 1302 px table of 1440: Job 303.7, Account 85.2, Key 96.8
const TABLE = 1302;
const drawn = (share: number) => (TABLE * share) / 100;

describe("auto columns", () => {
  it("values that fit their drawn widths move nothing", () => {
    expect(autoColumns(TABLE, { account: 60, key: 80 })).toBeNull();
    expect(autoColumns(TABLE, { account: 0, key: 0 })).toBeNull();
  });

  it("Key grows to its longest value and Job gives up exactly that room; Account stays drawn", () => {
    const got = autoColumns(TABLE, { account: 60, key: 101 })!;
    expect(got.key).toBe(101);
    expect(got.account).toBeCloseTo(drawn(6.5438), 6);
    expect(got.job).toBeCloseTo(drawn(20.3257) - (101 - drawn(7.4347)), 6);
    expect(got.job + got.account + got.key).toBeCloseTo(drawn(20.3257) + drawn(6.5438) + drawn(7.4347), 6);
  });

  it("neither column grows over the cap, and Job gives up both", () => {
    const got = autoColumns(TABLE, { account: 300, key: 400 })!;
    expect([got.account, got.key]).toEqual([COLUMN_CAP, COLUMN_CAP]);
    expect(got.job).toBeCloseTo(drawn(20.3257) - (COLUMN_CAP - drawn(6.5438)) - (COLUMN_CAP - drawn(7.4347)), 6);
  });

  it("on a wider table a drawn width over the cap is kept, never cut to the cap", () => {
    // 1700 px: Account drawn 111.2 (under the cap), Key drawn 126.4 (over it)
    const got = autoColumns(1700, { account: 300, key: 300 })!;
    expect(got.account).toBe(COLUMN_CAP);
    expect(got.key).toBeCloseTo((1700 * 7.4347) / 100, 6);
    expect(autoColumns(2400, { account: 300, key: 300 })).toBeNull();
  });

  it("a measure within half a pixel of the last one is the same", () => {
    const a = { job: 250, account: 100, key: 110 };
    expect(sameColumns(a, { job: 250.3, account: 99.8, key: 110.4 })).toBe(true);
    expect(sameColumns(a, { job: 249, account: 100, key: 110 })).toBe(false);
    expect(sameColumns(a, { job: 250, account: 99, key: 110 })).toBe(false);
    expect(sameColumns(a, { job: 250, account: 100, key: 111 })).toBe(false);
    expect(sameColumns(null, null)).toBe(true);
    expect(sameColumns(a, null)).toBe(false);
  });
});
