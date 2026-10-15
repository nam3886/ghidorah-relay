import { describe, expect, it } from "vitest";
import { PAGE_SIZE, PAGE_SIZES } from "@/components/jobs/paging";

describe("paging", () => {
  it("starts at 10 a page and offers 10, 20, 50 and 100", () => {
    expect(PAGE_SIZE).toBe(10);
    expect(PAGE_SIZES).toEqual([10, 20, 50, 100]);
  });
});
