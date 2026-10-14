import { describe, expect, it } from "vitest";
import { pickedIds, setPage, toggled } from "@/components/jobs/selection";
import { bulkAction } from "@/lib/operation-key";

const page = [{ id: "c" }, { id: "a" }, { id: "b" }];

describe("selection", () => {
  it("gives the ticked jobs in table order", () => {
    expect(pickedIds(page, new Set(["b", "c"]))).toEqual(["c", "b"]);
  });

  it("leaves out a ticked id that is no longer on the page", () => {
    expect(pickedIds(page, new Set(["a", "gone"]))).toEqual(["a"]);
  });

  it("gives nothing when nothing is ticked", () => {
    expect(pickedIds(page, new Set())).toEqual([]);
  });

  it("ticks and clears one job without touching the old set", () => {
    const before: ReadonlySet<string> = new Set(["a"]);
    const after = toggled(before, "b", true);
    expect([...after].sort()).toEqual(["a", "b"]);
    expect([...before]).toEqual(["a"]);
    expect([...toggled(after, "a", false)]).toEqual(["b"]);
  });

  it("select all ticks every job of the page, and none clears them", () => {
    expect([...setPage(page, true)].sort()).toEqual(["a", "b", "c"]);
    expect(setPage(page, false).size).toBe(0);
  });

  it("the same selection is the same regenerate action, whatever the table order or tick order", () => {
    expect(bulkAction(pickedIds(page, new Set(["b", "c"])))).toBe("bulk:b,c");
    expect(bulkAction(pickedIds([...page].reverse(), new Set(["c", "b"])))).toBe("bulk:b,c");
  });

  it("a different selection is a different action", () => {
    expect(bulkAction(pickedIds(page, new Set(["b", "c"])))).not.toBe(bulkAction(pickedIds(page, new Set(["b"]))));
  });
});
