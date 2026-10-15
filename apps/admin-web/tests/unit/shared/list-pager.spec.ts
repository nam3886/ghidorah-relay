import { flushPromises, mount } from "@vue/test-utils";
import { Refresh } from "@element-plus/icons-vue";
import { describe, expect, it, vi } from "vitest";
import IconAction from "../../../src/components/shared/IconAction.vue";
import ListPager from "../../../src/components/shared/ListPager.vue";

const offsetPager = (props: Record<string, unknown> = {}) =>
  mount(ListPager, { props: { mode: "offset", offset: 0, limit: 50, total: 120, ...props }, attachTo: document.body });
const cursorPager = (page: number, hasNext: boolean) =>
  mount(ListPager, { props: { mode: "cursor", page, hasNext }, attachTo: document.body });

describe("ListPager, offset mode", () => {
  it("shows the total and one numbered page per limit", () => {
    const w = offsetPager();
    expect(w.find(".el-pagination__total").text()).toContain("120");
    expect(w.findAll("li.number").map((n) => n.text())).toEqual(["1", "2", "3"]);
    expect(w.find("li.number.is-active").text()).toBe("1");
    w.unmount();
  });

  it("the full layout has the size select and the jumper; the default size is 10 with sizes 10, 20, 50, 100", () => {
    const w = mount(ListPager, { props: { total: 95 }, attachTo: document.body });
    expect(w.find(".el-pagination__sizes").exists()).toBe(true);
    expect(w.find(".el-pagination__jump").exists()).toBe(true);
    const pagination = w.findComponent({ name: "ElPagination" });
    expect(pagination.props("pageSize")).toBe(10);
    expect(pagination.props("pageSizes")).toEqual([10, 20, 50, 100]);
    expect(w.findAll("li.number").map((n) => n.text())).toEqual(["1", "2", "3", "4", "5", "6", "10"]);
    w.unmount();
  });

  it("under 640 px the layout is compact: no select, no jumper, the total as text above", async () => {
    const listeners: Array<(e: { matches: boolean }) => void> = [];
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: q === "(max-width: 639px)",
      addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => listeners.push(fn),
      removeEventListener: () => {},
    }));
    const w = offsetPager();
    await flushPromises();
    expect(w.attributes("data-narrow")).toBe("true");
    expect(w.find(".el-pagination__sizes").exists()).toBe(false);
    expect(w.find(".el-pagination__jump").exists()).toBe(false);
    expect(w.find(".pager__text").text()).toBe("120 total");
    listeners[0]!({ matches: false });
    await flushPromises();
    expect(w.attributes("data-narrow")).toBe("false");
    expect(w.find(".el-pagination__sizes").exists()).toBe(true);
    w.unmount();
  });

  it("the active page follows the offset", () => {
    const w = offsetPager({ offset: 100 });
    expect(w.find("li.number.is-active").text()).toBe("3");
    w.unmount();
  });

  it("clicking a page emits move with that page's offset", async () => {
    const w = offsetPager();
    await w.findAll("li.number")[1]!.trigger("click");
    expect(w.emitted("move")).toEqual([[50]]);
    w.unmount();
  });

  it("Next from page 1 moves to the second page's offset; Prev is disabled on page 1", async () => {
    const w = offsetPager();
    expect(w.find("button.btn-prev").attributes("disabled")).toBeDefined();
    await w.find("button.btn-next").trigger("click");
    expect(w.emitted("move")).toEqual([[50]]);
    w.unmount();
  });

  it("a limit that is not in the list is added to the sizes", () => {
    const w = offsetPager({ limit: 30 });
    expect((w.findComponent({ name: "ElPagination" }).props("pageSizes") as number[]).includes(30)).toBe(true);
    w.unmount();
  });

  it("a size change emits size and never a move", async () => {
    const w = offsetPager({ offset: 100 });
    const pagination = w.findComponent({ name: "ElPagination" });
    pagination.vm.$emit("size-change", 25);
    pagination.vm.$emit("update:current-page", 1);
    await flushPromises();
    expect(w.emitted("size")).toEqual([[25]]);
    expect(w.emitted("move")).toBeUndefined();
    w.unmount();
  });
});

describe("ListPager, cursor mode", () => {
  it("has Prev, 'Page N' and Next and no page numbers", () => {
    const w = cursorPager(2, true);
    expect(w.text()).toContain("Page 2");
    expect(w.findAll("li.number")).toHaveLength(0);
    w.unmount();
  });

  it("Prev is disabled on page 1, Next when there is no next page", () => {
    const first = cursorPager(1, true);
    expect(first.find("button.btn-prev").attributes("disabled")).toBeDefined();
    expect(first.find("button.btn-next").attributes("disabled")).toBeUndefined();
    first.unmount();
    const last = cursorPager(3, false);
    expect(last.find("button.btn-prev").attributes("disabled")).toBeUndefined();
    expect(last.find("button.btn-next").attributes("disabled")).toBeDefined();
    last.unmount();
  });

  it("the clicks are reported, one event each", async () => {
    const w = cursorPager(2, true);
    await w.find("button.btn-prev").trigger("click");
    await w.find("button.btn-next").trigger("click");
    expect(w.emitted("prev")).toHaveLength(1);
    expect(w.emitted("next")).toHaveLength(1);
    w.unmount();
  });
});

describe("IconAction", () => {
  it("is a button named by its label and emits click", async () => {
    const w = mount(IconAction, { props: { icon: Refresh, label: "Refresh jobs" }, attachTo: document.body });
    const button = w.find("button");
    expect(button.attributes("aria-label")).toBe("Refresh jobs");
    await button.trigger("click");
    expect(w.emitted("click")).toHaveLength(1);
    w.unmount();
  });

  it("a disabled one does not emit", async () => {
    const w = mount(IconAction, { props: { icon: Refresh, label: "Wake", disabled: true }, attachTo: document.body });
    await w.find("button").trigger("click");
    expect(w.emitted("click")).toBeUndefined();
    w.unmount();
  });
});
