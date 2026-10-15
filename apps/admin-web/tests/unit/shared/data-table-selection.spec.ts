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

  it("clicking the checkbox does not open the row", async () => {
    const w = await table({ selectable: true, rowClickable: true });
    await w.findAll("td .el-checkbox")[0]!.trigger("click");
    expect(w.emitted("row-click")).toBeUndefined();
    await w.findAll("td")[1]!.trigger("click");
    expect(w.emitted("row-click")).toHaveLength(1);
  });
});
