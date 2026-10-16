import { flushPromises, mount } from "@vue/test-utils";
import { Refresh } from "@element-plus/icons-vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import IconAction from "../../../src/components/shared/IconAction.vue";
import ListPager from "../../../src/components/shared/ListPager.vue";

const pager = (props: Record<string, unknown> = {}) =>
  mount(ListPager, { props: { offset: 0, limit: 50, total: 120, ...props }, attachTo: document.body });

// the phone edge of C3: matchMedia answers true for the phone query only
function stubPhone(phone: boolean) {
  const listeners: Array<(e: { matches: boolean }) => void> = [];
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: phone && /max-width:\s*767/.test(q),
    addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => listeners.push(fn),
    removeEventListener: () => {},
  }));
  return listeners;
}
afterEach(() => vi.unstubAllGlobals());

describe("ListPager", () => {
  it("shows 'Showing a-b of total' and one numbered page per limit", () => {
    const w = pager();
    expect(w.find(".pager__text").text()).toBe("Showing 1–50 of 120");
    expect(w.findAll("li.number").map((n) => n.text())).toEqual(["1", "2", "3"]);
    expect(w.find("li.number.is-active").text()).toBe("1");
    w.unmount();
  });

  it("the last page is cut at the total, a thousand gets a separator, and an empty list says so", () => {
    expect(pager({ offset: 100 }).find(".pager__text").text()).toBe("Showing 101–120 of 120");
    expect(pager({ total: 1321, limit: 10 }).find(".pager__text").text()).toBe("Showing 1–10 of 1,321");
    expect(pager({ total: 0 }).find(".pager__text").text()).toBe("Showing 0 of 0");
  });

  it("has the size select, text Previous and Next and no jumper; default size 10, sizes 10, 20, 50, 100", () => {
    const w = mount(ListPager, { props: { total: 95 }, attachTo: document.body });
    expect(w.find(".el-pagination__sizes").exists()).toBe(true);
    expect(w.find(".el-pagination__jump").exists()).toBe(false);
    expect(w.find("button.btn-prev").text()).toBe("Previous");
    expect(w.find("button.btn-next").text()).toBe("Next");
    const pagination = w.findComponent({ name: "ElPagination" });
    expect(pagination.props("pageSize")).toBe(10);
    expect(pagination.props("pageSizes")).toEqual([10, 20, 50, 100]);
    w.unmount();
  });

  it("on a phone it is Previous, a count and Next: no select, no numbers, no Showing text", async () => {
    stubPhone(true);
    const w = pager({ offset: 50, total: 1321, limit: 10 });
    await flushPromises();
    expect(w.attributes("data-phone")).toBe("true");
    expect(w.find(".el-pagination__sizes").exists()).toBe(false);
    expect(w.findAll("li.number")).toHaveLength(0);
    expect(w.find(".pager__count").text()).toBe("6 / 133");
    expect(w.text()).not.toContain("Showing");
    expect(w.find("button.btn-prev").text()).toBe("Previous");
    w.unmount();
  });

  it("on a phone with no rows the count is 1 / 1", async () => {
    stubPhone(true);
    const w = pager({ total: 0 });
    await flushPromises();
    expect(w.find(".pager__count").text()).toBe("1 / 1");
    w.unmount();
  });

  it("the active page follows the offset", () => {
    const w = pager({ offset: 100 });
    expect(w.find("li.number.is-active").text()).toBe("3");
    w.unmount();
  });

  it("clicking a page emits move with that page's offset", async () => {
    const w = pager();
    await w.findAll("li.number")[1]!.trigger("click");
    expect(w.emitted("move")).toEqual([[50]]);
    w.unmount();
  });

  it("Next from page 1 moves to the second page's offset; Prev is disabled on page 1", async () => {
    const w = pager();
    expect(w.find("button.btn-prev").attributes("disabled")).toBeDefined();
    await w.find("button.btn-next").trigger("click");
    expect(w.emitted("move")).toEqual([[50]]);
    w.unmount();
  });

  it("a limit that is not in the list is added to the sizes", () => {
    const w = pager({ limit: 30 });
    expect((w.findComponent({ name: "ElPagination" }).props("pageSizes") as number[]).includes(30)).toBe(true);
    w.unmount();
  });

  it("a size change emits size and never a move", async () => {
    const w = pager({ offset: 100 });
    const pagination = w.findComponent({ name: "ElPagination" });
    pagination.vm.$emit("size-change", 25);
    pagination.vm.$emit("update:current-page", 1);
    await flushPromises();
    expect(w.emitted("size")).toEqual([[25]]);
    expect(w.emitted("move")).toBeUndefined();
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
