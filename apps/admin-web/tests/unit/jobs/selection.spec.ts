import { describe, expect, it } from "vitest";
import { pickedIds } from "@/components/jobs/selection";
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

  it("the same selection is the same regenerate action, whatever the table order or tick order", () => {
    expect(bulkAction(pickedIds(page, new Set(["b", "c"])))).toBe("bulk:b,c");
    expect(bulkAction(pickedIds([...page].reverse(), new Set(["c", "b"])))).toBe("bulk:b,c");
  });

  it("a different selection is a different action", () => {
    expect(bulkAction(pickedIds(page, new Set(["b", "c"])))).not.toBe(bulkAction(pickedIds(page, new Set(["b"]))));
  });
});
