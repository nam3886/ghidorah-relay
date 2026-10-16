import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import DataTable from "../../../src/components/shared/DataTable.vue";
import { groupByDay, isDayRow } from "../../../src/lib/day-rows";

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

const PLANTED = "<img src=x onerror=alert(1)><b>x</b>";
const NOW = new Date(2026, 9, 15, 21).getTime();
const at = (d: number, h = 12) => new Date(2026, 9, d, h).getTime();

const columns = [
  { key: "time", label: "Time" },
  { key: "name", label: "Name" },
  { key: "net", label: "Net", align: "right" as const },
];
const jobs = [
  { id: "a", name: "alpha", time: "20:00", net: 1, ms: at(15, 20) },
  { id: "b", name: "beta", time: "03:00", net: 2, ms: at(15, 3) },
  { id: "c", name: "gamma", time: "23:00", net: 3, ms: at(14, 23) },
];
const grouped = () => groupByDay(jobs, { at: (j) => j.ms, now: NOW, note: (n) => `${n} shown` }).items;

// matchMedia answering by the width named in the query: the phone edge is 767.x, the tablet band ends at 1279.x
function stubWidth(kind: "phone" | "tablet" | "desktop") {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: kind === "phone" ? /max-width:\s*767/.test(q) : kind === "tablet" ? /1279/.test(q) : false,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

const mounted: VueWrapper[] = [];
afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount();
  vi.unstubAllGlobals();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

async function table(props: Record<string, unknown> = {}, slots: Record<string, unknown> = {}) {
  const w = mount(DataTable, {
    props: { columns, rows: grouped(), ...props },
    slots: slots as never,
    attachTo: document.body,
  });
  mounted.push(w);
  await flushPromises();
  await flushPromises();
  return w;
}

const lastIds = (w: VueWrapper): string[] => {
  const events = w.emitted("selection-change") as Array<[Array<{ id: string }>]>;
  return events[events.length - 1]![0].map((r) => r.id);
};
describe("DataTable day rows (table)", () => {
  it("draws one header per day across the table, with the muted note, and the data rows under it", async () => {
    const w = await table();
    const labels = w.findAll("[data-testid='day-row']").map((n) => n.text());
    expect(labels).toEqual(["Today · 15 Oct · 2 shown", "Yesterday · 14 Oct · 1 shown"]);
    expect(w.findAll("tbody tr")).toHaveLength(5);
    expect(w.findAll("tbody tr.is-day")).toHaveLength(2);
  });

  it("the header cell spans every column but the first (the empty selection cell); no other cell of the row stays", async () => {
    const w = await table({ selectable: true });
    const dayRow = w.find("tbody tr.is-day");
    const spanned = dayRow.findAll("td[colspan='3']");
    expect(spanned).toHaveLength(1);
    expect(spanned[0]!.find("[data-testid='day-row']").exists()).toBe(true);
    expect(dayRow.find("td .el-checkbox").exists()).toBe(true);
  });

  it("an Actions column counts: the header spans the data columns and the actions", async () => {
    const w = await table({ selectable: true }, { actions: "<button>x</button>" });
    expect(w.find("tbody tr.is-day td[colspan='4']").exists()).toBe(true);
  });

  it("without a selection column the header cell is the first one and spans all", async () => {
    const w = await table();
    expect(w.find("tbody tr.is-day td[colspan='3']").find("[data-testid='day-row']").exists()).toBe(true);
  });

  it("a day label or note with markup is text", async () => {
    const days = groupByDay([{ id: "x", ms: NOW }], { at: (j) => j.ms, now: NOW, note: () => PLANTED }).items;
    const w = await table({ rows: days });
    expect(w.find("[data-testid='day-row'] img").exists()).toBe(false);
    expect(w.find("[data-testid='day-row']").text()).toContain(PLANTED);
  });

  it("a view's own spanMethod wins over the day default", async () => {
    const own = vi.fn(() => undefined);
    await table({ spanMethod: own });
    expect(own).toHaveBeenCalled();
  });

  it("never renders the user's cell or actions slot for a day row", async () => {
    const seen: string[] = [];
    await table(
      {},
      {
        "cell-name": ({ row }: { row: { id: string } }) => {
          seen.push(row.id);
          return row.id;
        },
        actions: ({ row }: { row: { id: string } }) => {
          seen.push(`act-${row.id}`);
          return "x";
        },
      },
    );
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((id) => !id.includes("day:"))).toBe(true);
  });

  it("clicking a day row never opens anything; clicking a data row does", async () => {
    const w = await table({ rowClickable: true });
    await w.find("tbody tr.is-day").trigger("click");
    expect(w.emitted("row-click")).toBeUndefined();
    await w.findAll("tbody tr:not(.is-day)")[0]!.trigger("click");
    expect((w.emitted("row-click")![0]![0] as { id: string }).id).toBe("a");
  });
});

describe("DataTable selection with day rows", () => {
  it("every day row has a disabled checkbox, hidden by its row class; data rows are enabled", async () => {
    const w = await table({ selectable: true });
    const day = w.findAll("tbody tr.is-day");
    expect(day).toHaveLength(2);
    for (const tr of day) expect(tr.find(".el-checkbox").classes()).toContain("is-disabled");
    for (const tr of w.findAll("tbody tr:not(.is-day)"))
      expect(tr.find(".el-checkbox").classes()).not.toContain("is-disabled");
  });

  it("select all ticks the job rows only: selection-change carries job ids and never a day row", async () => {
    const w = await table({ selectable: true });
    // Element Plus debounces the header checkbox: wait for it
    (w.find("th input[type='checkbox']").element as HTMLInputElement).click();
    await new Promise((r) => setTimeout(r, 100));
    await flushPromises();
    expect(lastIds(w)).toEqual(["a", "b", "c"]);
    for (const [rows] of w.emitted("selection-change") as Array<[unknown[]]>) {
      expect(rows.some(isDayRow)).toBe(false);
    }
  });

  it("with keepSelection on, select all and a reload still never carry a day row", async () => {
    const w = await table({ selectable: true, keepSelection: true });
    (w.find("th input[type='checkbox']").element as HTMLInputElement).click();
    await new Promise((r) => setTimeout(r, 100));
    await flushPromises();
    expect(lastIds(w)).toEqual(["a", "b", "c"]);
    await w.setProps({ rows: groupByDay(jobs.slice(0, 2), { at: (j) => j.ms, now: NOW }).items });
    await flushPromises();
    for (const [rows] of w.emitted("selection-change") as Array<[unknown[]]>) {
      expect(rows.some(isDayRow)).toBe(false);
    }
    expect(w.findAll("tbody tr.is-day .el-checkbox.is-checked")).toHaveLength(0);
  });

  it("clicking a day row's checkbox changes nothing", async () => {
    const w = await table({ selectable: true });
    await w.find("tbody tr.is-day .el-checkbox").trigger("click");
    await w.find("tbody tr.is-day .el-checkbox__input").trigger("click");
    expect(w.emitted("selection-change")).toBeUndefined();
  });

  it("whatever the table reports, a day row never leaves the component", async () => {
    const w = await table({ selectable: true });
    const day = grouped()[0]!;
    w.findComponent({ name: "ElTable" }).vm.$emit("selection-change", [day, jobs[0]]);
    await flushPromises();
    expect(lastIds(w)).toEqual(["a"]);
  });

  it("ticking one job emits that job alone, and its row is marked selected", async () => {
    const w = await table({ selectable: true });
    await w.findAll("tbody tr:not(.is-day)")[1]!.find(".el-checkbox").trigger("click");
    expect(lastIds(w)).toEqual(["b"]);
    expect(w.findAll("tbody tr.is-selected")).toHaveLength(1);
    expect(w.findAll("tbody tr.is-selected")[0]!.text()).toContain("beta");
  });

  it("setSelection ticks the named jobs and ignores a day row's key", async () => {
    const w = await table({ selectable: true });
    const dayKey = (grouped()[0] as { id: string }).id;
    const vm = w.vm as unknown as { setSelection: (ids: string[]) => void };
    vm.setSelection([dayKey, "c"]);
    await flushPromises();
    expect(lastIds(w)).toEqual(["c"]);
    vm.setSelection([dayKey]);
    await flushPromises();
    expect(lastIds(w)).toEqual([]);
  });
});

describe("DataTable tablet and opt-in props", () => {
  it("shows every column unless a tablet column list is given", async () => {
    stubWidth("tablet");
    const w = await table({ rows: jobs });
    expect(w.findAll("thead th").map((th) => th.text())).toEqual(["Time", "Name", "Net"]);
  });

  it("on a tablet shows the priority columns only, and on desktop all of them", async () => {
    stubWidth("tablet");
    const tablet = await table({ rows: jobs, tabletColumns: ["name", "net"] });
    expect(tablet.findAll("thead th").map((th) => th.text())).toEqual(["Name", "Net"]);
    expect(tablet.classes()).toContain("is-tablet");
    stubWidth("desktop");
    const desktop = await table({ rows: jobs, tabletColumns: ["name", "net"] });
    expect(desktop.findAll("thead th").map((th) => th.text())).toEqual(["Time", "Name", "Net"]);
    expect(desktop.classes()).not.toContain("is-tablet");
  });

  it("a tablet table spans the narrowed columns: the day header covers what is left", async () => {
    stubWidth("tablet");
    const w = await table({ tabletColumns: ["name", "net"] });
    expect(w.find("tbody tr.is-day td[colspan='2']").exists()).toBe(true);
  });

  it("toneFill marks the toned rows for a full-row fill; without it only the inset bar", async () => {
    const rowTone = (r: { id: string }) =>
      r.id === "b" ? ("bad" as const) : r.id === "c" ? ("neutral" as const) : undefined;
    const fill = await table({ rows: jobs, rowTone, toneFill: true });
    expect(fill.findAll("tbody tr.row-tone-fill")).toHaveLength(1);
    expect(fill.find("tr.row-tone-fill").classes()).toContain("row-tone-bad");
    const bar = await table({ rows: jobs, rowTone });
    expect(bar.findAll("tbody tr.row-tone")).toHaveLength(1);
    expect(bar.findAll("tbody tr.row-tone-fill")).toHaveLength(0);
  });

  it("rowClassName is added to a data row with that row, and never to a day row", async () => {
    const rowClassName = vi.fn(({ row }: { row: { id: string } }) => `mine-${row.id}`);
    const w = await table({ rowClassName });
    expect(w.findAll("tbody tr.mine-a, tbody tr.mine-b, tbody tr.mine-c")).toHaveLength(3);
    expect(
      w
        .find("tbody tr.is-day")
        .classes()
        .some((c) => c.startsWith("mine-")),
    ).toBe(false);
  });
});

describe("DataTable phone list", () => {
  const slots = { row: ({ row }: { row: { name: string } }) => row.name };

  it("with a row slot a phone gets a stacked list, day headers included, and no table", async () => {
    stubWidth("phone");
    const w = await table({}, slots);
    expect(w.find("table").exists()).toBe(false);
    expect(w.findAll("ul.data-table__list > li")).toHaveLength(5);
    expect(w.findAll("[data-testid='day-row']").map((n) => n.text())).toEqual([
      "Today · 15 Oct · 2 shown",
      "Yesterday · 14 Oct · 1 shown",
    ]);
    expect(w.findAll("li.data-table__item").map((n) => n.text())).toEqual(["alpha", "beta", "gamma"]);
  });

  it("the list and the table are never in the page together", async () => {
    stubWidth("desktop");
    const desktop = await table({}, slots);
    expect(desktop.find("ul.data-table__list").exists()).toBe(false);
    expect(desktop.find("table").exists()).toBe(true);
    stubWidth("phone");
    const noSlot = await table({});
    expect(noSlot.find("ul.data-table__list").exists()).toBe(false);
    expect(noSlot.find("table").exists()).toBe(true);
  });

  it("a day header has no checkbox; a job has one; ticking emits jobs only", async () => {
    stubWidth("phone");
    const w = await table({ selectable: true, selectLabel: (r: { id: string }) => `Select job ${r.id}` }, slots);
    expect(w.findAll("li.data-table__day input")).toHaveLength(0);
    expect(w.findAll("li.data-table__item input")).toHaveLength(3);
    await w.find("input[aria-label='Select job b']").setValue(true);
    await w.find("input[aria-label='Select job c']").setValue(true);
    expect(lastIds(w)).toEqual(["b", "c"]);
    await w.find("input[aria-label='Select job b']").setValue(false);
    expect(lastIds(w)).toEqual(["c"]);
    for (const [rows] of w.emitted("selection-change") as Array<[unknown[]]>) expect(rows.some(isDayRow)).toBe(false);
    expect(w.findAll("li.is-selected")).toHaveLength(1);
  });

  it("setSelection ticks jobs only, and a reload drops rows that left the list", async () => {
    stubWidth("phone");
    const w = await table({ selectable: true }, slots);
    const dayKey = (grouped()[0] as { id: string }).id;
    (w.vm as unknown as { setSelection: (ids: string[]) => void }).setSelection([dayKey, "a", "c"]);
    await flushPromises();
    expect(lastIds(w)).toEqual(["a", "c"]);
    await w.setProps({ rows: groupByDay([jobs[0]!], { at: (j) => j.ms, now: NOW }).items });
    await flushPromises();
    expect(lastIds(w)).toEqual(["a"]);
  });

  it("with keepSelection a reload reports nothing, even when a ticked row left; without it that is reported", async () => {
    stubWidth("phone");
    for (const keepSelection of [true, false]) {
      const w = await table({ selectable: true, keepSelection }, slots);
      await w.findAll("li.data-table__item input")[1]!.setValue(true);
      const before = (w.emitted("selection-change") ?? []).length;
      await w.setProps({ rows: groupByDay([jobs[0]!, jobs[2]!], { at: (j) => j.ms, now: NOW }).items });
      await flushPromises();
      expect((w.emitted("selection-change") ?? []).length).toBe(keepSelection ? before : before + 1);
    }
  });

  it("a clickable row opens by tap and by Enter, not by a button inside it", async () => {
    stubWidth("phone");
    const w = await table(
      { rowClickable: true, rowLabel: (r: { id: string }) => `Open job ${r.id}` },
      { row: `<span class="line">x</span><button class="inner">go</button>` },
    );
    const main = w.findAll(".data-table__main")[0]!;
    expect(main.attributes("role")).toBe("button");
    expect(main.attributes("aria-label")).toBe("Open job a");
    await main.trigger("click");
    await main.trigger("keydown", { key: "Enter" });
    expect(w.emitted("row-click")).toHaveLength(2);
    await main.find(".inner").trigger("click");
    expect(w.emitted("row-click")).toHaveLength(2);
    await w.find("li.data-table__day").trigger("click");
    expect(w.emitted("row-click")).toHaveLength(2);
  });

  it("Enter on a button inside a row does not open the row", async () => {
    stubWidth("phone");
    const w = await table(
      { rowClickable: true },
      { row: '<span class="line">x</span><button class="inner">go</button>' },
    );
    await w.find(".data-table__main .inner").trigger("keydown", { key: "Enter" });
    expect(w.emitted("row-click")).toBeUndefined();
  });

  it("going from the phone list to the table starts a clean selection", async () => {
    const mql = { matches: true, listeners: [] as Array<(e: { matches: boolean }) => void> };
    vi.stubGlobal("matchMedia", (q: string) => ({
      get matches() {
        return /max-width:\s*767/.test(q) ? mql.matches : false;
      },
      addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => mql.listeners.push(fn),
      removeEventListener: () => {},
    }));
    const w = await table({ selectable: true }, slots);
    await w.find("li.data-table__item input").setValue(true);
    expect(lastIds(w)).toEqual(["a"]);
    mql.matches = false;
    mql.listeners.forEach((fn) => fn({ matches: false }));
    await flushPromises();
    await flushPromises();
    expect(w.find("ul.data-table__list").exists()).toBe(false);
    expect(lastIds(w)).toEqual([]);
  });

  it("a row that does not open anything is not a button", async () => {
    stubWidth("phone");
    const w = await table({}, slots);
    expect(w.find(".data-table__main").attributes("role")).toBeUndefined();
    await w.find(".data-table__main").trigger("click");
    expect(w.emitted("row-click")).toBeUndefined();
  });

  it("an empty list says so, and the empty action shows", async () => {
    stubWidth("phone");
    const w = await table(
      { rows: [], emptyText: "No jobs" },
      { ...slots, "empty-action": "<button class='act'>Clear</button>" },
    );
    expect(w.find(".el-empty").text()).toContain("No jobs");
    expect(w.find(".act").exists()).toBe(true);
  });

  it("a toned job carries its tone class", async () => {
    stubWidth("phone");
    const w = await table(
      { rowTone: (r: { id: string }) => (r.id === "a" ? ("bad" as const) : undefined), toneFill: true },
      slots,
    );
    expect(w.findAll("li.row-tone-bad.row-tone-fill")).toHaveLength(1);
  });
});
