import { describe, expect, it } from "vitest";
import { dayLabel, daySpanMethod, groupByDay, isDayRow } from "../../../src/lib/day-rows";

const at = (y: number, mo: number, d: number, h = 12) => new Date(y, mo - 1, d, h).getTime();
const NOW = at(2026, 10, 15, 21);

describe("dayLabel", () => {
  it("Today and Yesterday carry the date; older days are the date; another year adds the year", () => {
    expect(dayLabel(at(2026, 10, 15, 0), NOW)).toBe("Today · 15 Oct");
    expect(dayLabel(at(2026, 10, 15, 23), NOW)).toBe("Today · 15 Oct");
    expect(dayLabel(at(2026, 10, 14, 23), NOW)).toBe("Yesterday · 14 Oct");
    expect(dayLabel(at(2026, 10, 13), NOW)).toBe("13 Oct");
    expect(dayLabel(at(2025, 12, 31), NOW)).toBe("31 Dec 2025");
  });
});

describe("groupByDay", () => {
  const job = (id: string, ms: number | null) => ({ id, ms });
  const opts = { at: (r: { ms: number | null }) => r.ms, now: NOW };

  it("puts one header before each run of rows of the same day, in order", () => {
    const rows = [job("a", at(2026, 10, 15, 20)), job("b", at(2026, 10, 15, 3)), job("c", at(2026, 10, 14, 23))];
    const { items } = groupByDay(rows, opts);
    expect(items.map((i) => (isDayRow(i) ? i.label : (i as { id: string }).id))).toEqual([
      "Today · 15 Oct",
      "a",
      "b",
      "Yesterday · 14 Oct",
      "c",
    ]);
  });

  it("the data rows stay the same objects", () => {
    const rows = [job("a", NOW)];
    expect(groupByDay(rows, opts).items[1]).toBe(rows[0]);
  });

  it("the note is made from the number of data rows under the header", () => {
    const rows = [job("a", NOW), job("b", NOW), job("c", at(2026, 10, 14))];
    const days = groupByDay(rows, { ...opts, note: (n) => `${n} jobs` }).items.filter(isDayRow);
    expect(days.map((d) => d.note)).toEqual(["2 jobs", "1 jobs"]);
    expect(groupByDay(rows, opts).items.filter(isDayRow)[0]!.note).toBeUndefined();
  });

  it("a row with no time joins the group above it; first of all it opens an Unknown date group", () => {
    const items = groupByDay([job("a", null), job("b", NOW), job("c", null)], opts).items;
    expect(items.filter(isDayRow).map((d) => d.label)).toEqual(["Unknown date", "Today · 15 Oct"]);
    expect(items.map((i) => (isDayRow(i) ? "day" : (i as { id: string }).id))).toEqual(["day", "a", "day", "b", "c"]);
  });

  it("an empty list has no header, and header keys are unique", () => {
    expect(groupByDay([], opts).items).toEqual([]);
    const keys = groupByDay([job("a", NOW), job("b", at(2026, 10, 1)), job("c", NOW)], opts)
      .items.filter(isDayRow)
      .map((d) => d.id);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("only a header made here is a day row: a plain row that says kind day is not", () => {
    expect(isDayRow({ kind: "day", id: "x", label: "y" })).toBe(false);
    expect(isDayRow(null)).toBe(false);
    expect(isDayRow(undefined)).toBe(false);
    expect(isDayRow(groupByDay([job("a", NOW)], opts).items[0])).toBe(true);
  });
});

describe("daySpanMethod", () => {
  const day = groupByDay([{ ms: NOW }], { at: (r) => r.ms, now: NOW }).items[0];
  const data = { id: "a" };
  const span = daySpanMethod(6, 1);

  it("on a day row: the selection cell is not drawn, column 1 spans every column, the others vanish", () => {
    expect(span({ row: day, columnIndex: 0 })).toEqual([0, 0]);
    expect(span({ row: day, columnIndex: 1 })).toEqual([1, 6]);
    for (const i of [2, 3, 4, 5]) expect(span({ row: day, columnIndex: i })).toEqual([0, 0]);
  });

  it("without a selection column column 0 carries the label and spans everything", () => {
    const s = daySpanMethod(4, 0);
    expect(s({ row: day, columnIndex: 0 })).toEqual([1, 4]);
    expect(s({ row: day, columnIndex: 1 })).toEqual([0, 0]);
  });

  it("a data row is left alone", () => {
    for (const i of [0, 1, 3]) expect(span({ row: data, columnIndex: i })).toBeUndefined();
  });
});
