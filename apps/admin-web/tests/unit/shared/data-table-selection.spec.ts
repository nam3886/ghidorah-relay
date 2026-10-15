import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import DataTable from "../../../src/components/shared/DataTable.vue";

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

const columns = [{ key: "name", label: "Name" }];
const rows = [
  { id: "a", name: "alpha" },
  { id: "b", name: "beta" },
  { id: "c", name: "gamma" },
];

const mounted: VueWrapper[] = [];
afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount();
});

async function table(props: Record<string, unknown> = {}) {
  const w = mount(DataTable, { props: { columns, rows, ...props }, attachTo: document.body });
  mounted.push(w);
  await flushPromises();
  await flushPromises();
  return w;
}

const lastIds = (w: VueWrapper): string[] => {
  const events = w.emitted("selection-change") as Array<[Array<{ id: string }>]>;
  return events[events.length - 1]![0].map((r) => r.id);
};

describe("DataTable selection", () => {
  it("has no checkbox column unless selectable", async () => {
    expect((await table()).find(".el-table-column--selection").exists()).toBe(false);
  });

  it("selectable: a header box and one box per row, ticking a row emits the rows", async () => {
    const w = await table({ selectable: true });
    expect(w.findAll("td .el-checkbox")).toHaveLength(3);
    expect(w.find("th .el-checkbox").exists()).toBe(true);
    await w.findAll("td .el-checkbox")[1]!.trigger("click");
    expect(lastIds(w)).toEqual(["b"]);
  });

  it("setSelection ticks exactly the given ids and replaces an earlier selection", async () => {
    const w = await table({ selectable: true });
    const vm = w.vm as unknown as { setSelection: (ids: string[]) => void };
    vm.setSelection(["a", "c"]);
    await flushPromises();
    expect(lastIds(w)).toEqual(["a", "c"]);
    vm.setSelection(["b", "gone"]);
    await flushPromises();
    expect(lastIds(w)).toEqual(["b"]);
  });

  // The same ids in new objects, one row gone: what a reload of the list hands the table.
  const reloaded = [
    { id: "a", name: "alpha" },
    { id: "b", name: "beta" },
  ];
  const ticks = (w: VueWrapper): boolean[] =>
    w.findAll("tbody tr").map((tr) => tr.find(".el-checkbox").classes().includes("is-checked"));
  // setProps on the generic table, its props as loosely typed as the ones table() mounts it with
  const reload = async (w: VueWrapper): Promise<void> => {
    const loose = w as unknown as { setProps: (props: Record<string, unknown>) => Promise<void> };
    await loose.setProps({ rows: reloaded });
    await flushPromises();
  };

  it("keepSelection: new rows keep the ticks of the rows still there, by rowKey, and report nothing", async () => {
    const w = await table({ selectable: true, keepSelection: true });
    (w.vm as unknown as { setSelection: (ids: string[]) => void }).setSelection(["a", "c"]);
    await flushPromises();
    const reports = w.emitted("selection-change")!.length;
    await reload(w);
    expect(ticks(w)).toEqual([true, false]);
    expect(w.emitted("selection-change")).toHaveLength(reports);
    // the next report is a real change, and still carries the tick of the row that left: setSelection drops it
    await w.findAll("td .el-checkbox")[1]!.trigger("click");
    expect(lastIds(w).sort()).toEqual(["a", "b", "c"]);
  });

  it("without keepSelection, new rows clear the ticks and report that", async () => {
    const w = await table({ selectable: true });
    (w.vm as unknown as { setSelection: (ids: string[]) => void }).setSelection(["a", "c"]);
    await flushPromises();
    await reload(w);
    expect(ticks(w)).toEqual([false, false]);
    expect(lastIds(w)).toEqual([]);
  });

  it("clicking the checkbox does not open the row", async () => {
    const w = await table({ selectable: true, rowClickable: true });
    // the box drawn by Element Plus is a span inside the label: that is what a real click lands on
    await w.findAll("td .el-checkbox__inner")[0]!.trigger("click");
    expect(w.emitted("row-click")).toBeUndefined();
    await w.findAll("td")[1]!.trigger("click");
    expect(w.emitted("row-click")).toHaveLength(1);
  });
});
