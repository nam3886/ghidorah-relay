import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import DataTable from "../../../src/components/shared/DataTable.vue";

// The real el-table, attached to the document, with the layout observer it needs under happy-dom (there is no
// layout engine: the stub only has to exist so the table finishes its first render).
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

const columns = [
  { key: "name", label: "Name" },
  { key: "count", label: "Count", align: "right" as const },
];
const rows = [
  { id: "a", name: "alpha", count: 3 },
  { id: "b", name: null, count: 12 },
];

const mounted: VueWrapper[] = [];
afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount();
});

async function table(props: Record<string, unknown> = {}, slots: Record<string, string> = {}) {
  const w = mount(DataTable, { props: { columns, rows, ...props }, slots, attachTo: document.body });
  mounted.push(w);
  await flushPromises();
  await flushPromises();
  return w;
}

describe("DataTable states", () => {
  it("ready: header labels, cells, a dash for an empty value", async () => {
    const w = await table();
    expect(w.attributes("data-state")).toBe("ready");
    expect(w.text()).toContain("Name");
    expect(w.text()).toContain("alpha");
    const cells = w.findAll("tbody tr").map((tr) => tr.findAll("td").map((td) => td.text()));
    expect(cells).toEqual([
      ["alpha", "3"],
      ["-", "12"],
    ]);
  });

  it("numeric columns carry the tabular-number class", async () => {
    const w = await table();
    expect(w.findAll("tbody tr")[0]!.findAll("td")[1]!.classes()).toContain("is-numeric");
    expect(w.findAll("tbody tr")[0]!.findAll("td")[0]!.classes()).not.toContain("is-numeric");
  });

  it("loading with no rows shows the skeleton and no table", async () => {
    const w = await table({ rows: [], loading: true });
    expect(w.attributes("data-state")).toBe("loading");
    expect(w.attributes("aria-busy")).toBe("true");
    expect(w.find(".el-skeleton").exists()).toBe(true);
    expect(w.find("table").exists()).toBe(false);
  });

  it("refreshing keeps the old rows and marks the table dimmed and busy", async () => {
    const w = await table({ loading: true });
    expect(w.attributes("data-state")).toBe("refreshing");
    expect(w.classes()).toContain("is-refreshing");
    expect(w.attributes("aria-busy")).toBe("true");
    expect(w.text()).toContain("alpha");
    expect(w.find(".el-skeleton").exists()).toBe(false);
  });

  it("not loading: not busy, not dimmed", async () => {
    const w = await table();
    expect(w.attributes("aria-busy")).toBe("false");
    expect(w.classes()).not.toContain("is-refreshing");
  });

  it("error: an alert with the message, no rows, and Retry emits retry", async () => {
    const w = await table({ error: "Network error, please try again" });
    expect(w.attributes("data-state")).toBe("error");
    const alert = w.find('[role="alert"]');
    expect(alert.text()).toContain("Network error, please try again");
    expect(w.text()).not.toContain("alpha");
    await alert.find("button").trigger("click");
    expect(w.emitted("retry")).toHaveLength(1);
  });

  it("an error wins over loading", async () => {
    expect((await table({ rows: [], loading: true, error: "x" })).attributes("data-state")).toBe("error");
  });

  it("empty: the empty text and the action slot", async () => {
    const w = await table(
      { rows: [], emptyText: "No accounts yet. Import accounts to start." },
      { "empty-action": '<button class="go">Import</button>' },
    );
    expect(w.attributes("data-state")).toBe("empty");
    expect(w.text()).toContain("No accounts yet. Import accounts to start.");
    expect(w.find("button.go").exists()).toBe(true);
  });

  it("the default empty text", async () => {
    expect((await table({ rows: [] })).text()).toContain("Nothing here yet");
  });
});

describe("DataTable cells, actions and rows", () => {
  it("never renders the user's slots for Element Plus's empty probe row", async () => {
    const seen: unknown[] = [];
    const w = mount(DataTable, {
      props: { columns, rows },
      slots: {
        "cell-name": (scope: { row: { id?: string } }) => {
          seen.push(scope.row);
          return `n-${scope.row.id}`;
        },
        actions: (scope: { row: { id?: string } }) => {
          seen.push(scope.row);
          return `a-${scope.row.id}`;
        },
      },
      attachTo: document.body,
    });
    mounted.push(w);
    await flushPromises();
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((row) => (row as { id?: string }).id !== undefined)).toBe(true);
  });

  it("gives the Actions column room for its buttons, and a caller can change it", async () => {
    const actionsSlot = '<template #actions="{ row }"><button class="act">{{ row.id }}</button></template>';
    const w = await table({}, { actions: actionsSlot });
    const col = w.find("col[name^='el-table_'][width='200']");
    expect(col.exists()).toBe(true);
    const wide = await table({ actionsWidth: 320 }, { actions: actionsSlot });
    expect(wide.find("col[width='320']").exists()).toBe(true);
  });

  const actions = '<template #actions="{ row }"><button class="act">{{ row.id }}</button></template>';

  it("a cell slot replaces the cell and the actions slot adds a column per row", async () => {
    const w = await table(
      {},
      { "cell-name": '<template #cell-name="{ row }"><b class="cell">{{ row.id }}!</b></template>', actions },
    );
    expect(w.findAll("b.cell").map((b) => b.text())).toEqual(["a!", "b!"]);
    expect(w.findAll("button.act").map((b) => b.text())).toEqual(["a", "b"]);
    expect(w.text()).toContain("Actions");
  });

  it("without an actions slot there is no Actions column", async () => {
    expect((await table()).text()).not.toContain("Actions");
  });

  it("a row click emits the row, but a click on a button inside does not", async () => {
    const w = await table({ rowClickable: true }, { actions });
    await w.findAll("tbody tr")[1]!.findAll("td")[0]!.trigger("click");
    expect(w.emitted("row-click")).toEqual([[rows[1]]]);
    await w.find("button.act").trigger("click");
    expect(w.emitted("row-click")).toHaveLength(1);
  });

  it("a row does not emit when it is not clickable", async () => {
    const w = await table();
    await w.findAll("tbody tr")[0]!.findAll("td")[0]!.trigger("click");
    expect(w.emitted("row-click")).toBeUndefined();
  });

  it("marks the rows that need a look, not the neutral ones", async () => {
    const w = await table({ rowTone: (r: { id: string }) => (r.id === "a" ? "bad" : "neutral") });
    const trs = w.findAll("tbody tr");
    expect(trs[0]!.classes()).toContain("row-tone-bad");
    expect(trs[1]!.classes().join(" ")).not.toContain("row-tone");
  });

  it("keeps the header while the rows scroll: every list gets a max height unless it sets its own", async () => {
    // Element Plus draws a fixed header only for a table with a max height; the rows then scroll under it.
    const fallback = await table();
    expect(fallback.find(".el-table").attributes("style")).toContain("max-height: calc(100vh - 16rem)");
    expect(fallback.find(".el-table__header-wrapper").exists()).toBe(true);
    const own = await table({ maxHeight: "300px" });
    expect(own.find(".el-table").attributes("style")).toContain("max-height: 300px");
  });

  it("server text is text: planted markup makes no element", async () => {
    const w = await table({ rows: [{ id: "x", name: "<img src=x onerror=alert(1)>", count: 1 }] });
    expect(w.find("img").exists()).toBe(false);
    expect(w.text()).toContain("<img src=x onerror=alert(1)>");
  });
});
