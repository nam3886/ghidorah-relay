import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import IconAction from "../../../src/components/shared/IconAction.vue";
import ListPager from "../../../src/components/shared/ListPager.vue";
import { pageSlots } from "../../../src/components/shared/page-list";
import { IconRefresh } from "../../../src/components/shared/icons";

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

const numbers = (w: ReturnType<typeof pager>) => w.findAll("button.number").map((n) => n.text());

describe("pageSlots", () => {
  it("draws the one canvas state, page 1 of 133, as 1 2 3 … 133", () => {
    expect(pageSlots(1, 133)).toEqual([1, 2, 3, "…", 133]);
  });

  it("five pages or fewer show every number and no ellipsis", () => {
    expect(pageSlots(1, 1)).toEqual([1]);
    expect(pageSlots(2, 3)).toEqual([1, 2, 3]);
    expect(pageSlots(5, 5)).toEqual([1, 2, 3, 4, 5]);
  });

  it("near the start the first three pages show, near the end the last three", () => {
    expect(pageSlots(3, 133)).toEqual([1, 2, 3, "…", 133]);
    expect(pageSlots(131, 133)).toEqual([1, "…", 131, 132, 133]);
    expect(pageSlots(133, 133)).toEqual([1, "…", 131, 132, 133]);
    expect(pageSlots(4, 6)).toEqual([1, "…", 4, 5, 6]);
  });

  it("in the middle the page and its two neighbours show between two ellipses", () => {
    expect(pageSlots(4, 133)).toEqual([1, "…", 3, 4, 5, "…", 133]);
    expect(pageSlots(60, 133)).toEqual([1, "…", 59, 60, 61, "…", 133]);
    expect(pageSlots(130, 133)).toEqual([1, "…", 129, 130, 131, "…", 133]);
  });
});

describe("ListPager", () => {
  it("shows 'Showing a-b of total' and one numbered page per limit", () => {
    const w = pager();
    expect(w.find(".pager__text").text()).toBe("Showing 1–50 of 120");
    expect(numbers(w)).toEqual(["1", "2", "3"]);
    expect(w.find("button.number.is-active").text()).toBe("1");
    expect(w.find("button.number.is-active").attributes("aria-current")).toBe("page");
    w.unmount();
  });

  it("the last page is cut at the total, a thousand gets a separator, and an empty list says so", () => {
    expect(pager({ offset: 100 }).find(".pager__text").text()).toBe("Showing 101–120 of 120");
    expect(pager({ total: 1321, limit: 10 }).find(".pager__text").text()).toBe("Showing 1–10 of 1,321");
    expect(pager({ total: 0 }).find(".pager__text").text()).toBe("Showing 0 of 0");
  });

  it("draws the Jobs state exactly: 1 2 3 … 133, a plain ellipsis, a wider last button", () => {
    const w = pager({ total: 1321, limit: 10 });
    expect(numbers(w)).toEqual(["1", "2", "3", "133"]);
    expect(w.findAll(".pager__gap").map((g) => g.text())).toEqual(["…"]);
    expect(w.find(".pager__gap").element.tagName).toBe("SPAN");
    const classes = w.findAll("button.number").map((b) => b.classes().includes("is-wide"));
    expect(classes).toEqual([false, false, false, true]);
    w.unmount();
  });

  it("draws the Request logs state exactly: 1 2 3 … 97, the two-digit last page as wide as 133", () => {
    const w = pager({ total: 4812, limit: 50 });
    expect(numbers(w)).toEqual(["1", "2", "3", "97"]);
    const classes = w.findAll("button.number").map((b) => b.classes().includes("is-wide"));
    expect(classes).toEqual([false, false, false, true]);
    w.unmount();
  });

  it("only the last page is wide, and only with two digits or more: page 100 in the middle stays 32", () => {
    const wideOf = (w: ReturnType<typeof pager>) =>
      w.findAll("button.number").map((b) => `${b.text()}:${b.classes().includes("is-wide")}`);
    const middle = pager({ offset: 990, total: 1330, limit: 10 });
    expect(wideOf(middle)).toEqual(["1:false", "99:false", "100:false", "101:false", "133:true"]);
    const small = pager({ offset: 0, total: 90, limit: 10 });
    expect(wideOf(small).some((x) => x.endsWith(":true"))).toBe(false);
    const last = pager({ offset: 1320, total: 1321, limit: 10 });
    expect(wideOf(last)).toEqual(["1:false", "131:false", "132:false", "133:true"]);
    middle.unmount();
    small.unmount();
    last.unmount();
  });

  it("one page: the single page is current and both arrows are disabled", () => {
    const w = pager({ total: 4, limit: 10 });
    expect(numbers(w)).toEqual(["1"]);
    expect(w.find("button.btn-prev").attributes("aria-disabled")).toBe("true");
    expect(w.find("button.btn-next").attributes("aria-disabled")).toBe("true");
    w.unmount();
  });

  it("has the size select, text Previous and Next and no jumper; default size 10, sizes 10, 20, 50, 100", async () => {
    const w = mount(ListPager, { props: { total: 95 }, attachTo: document.body });
    const size = w.find(".pager__size");
    expect(size.exists()).toBe(true);
    expect(w.find("button.pager__size").attributes("aria-haspopup")).toBe("listbox");
    expect(w.find("button.pager__size").text()).toBe("10 / page");
    expect(w.find("button.pager__size svg").exists()).toBe(false);
    expect(w.find("input").exists()).toBe(false);
    expect(w.find("button.btn-prev").text()).toBe("Previous");
    expect(w.find("button.btn-next").text()).toBe("Next");
    const select = w.findComponent({ name: "FilterSelect" });
    expect((select.props("options") as Array<{ value: number }>).map((o) => o.value)).toEqual([10, 20, 50, 100]);
    expect(select.props("modelValue")).toBe(10);
    w.unmount();
  });

  it("on a phone it is Previous, a count and Next: no select, no numbers, no Showing text", async () => {
    stubPhone(true);
    const w = pager({ offset: 50, total: 1321, limit: 10 });
    await flushPromises();
    expect(w.attributes("data-phone")).toBe("true");
    expect(w.find(".pager__size").exists()).toBe(false);
    expect(w.findAll("button.number")).toHaveLength(0);
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
    expect(w.find("button.number.is-active").text()).toBe("3");
    w.unmount();
  });

  it("clicking a page emits move with that page's offset; the current page emits nothing", async () => {
    const w = pager();
    await w.findAll("button.number")[1]!.trigger("click");
    expect(w.emitted("move")).toEqual([[50]]);
    await w.findAll("button.number")[0]!.trigger("click");
    expect(w.emitted("move")).toEqual([[50]]);
    w.unmount();
  });

  it("Next from page 1 moves to the second page's offset; Previous is disabled and silent on page 1", async () => {
    const w = pager();
    expect(w.find("button.btn-prev").attributes("aria-disabled")).toBe("true");
    expect(w.find("button.btn-prev").attributes("disabled")).toBeUndefined();
    await w.find("button.btn-prev").trigger("click");
    expect(w.emitted("move")).toBeUndefined();
    await w.find("button.btn-next").trigger("click");
    expect(w.emitted("move")).toEqual([[50]]);
    w.unmount();
  });

  it("Next is disabled and silent on the last page; Previous steps back one page", async () => {
    const w = pager({ offset: 100 });
    expect(w.find("button.btn-next").attributes("aria-disabled")).toBe("true");
    await w.find("button.btn-next").trigger("click");
    expect(w.emitted("move")).toBeUndefined();
    await w.find("button.btn-prev").trigger("click");
    expect(w.emitted("move")).toEqual([[50]]);
    w.unmount();
  });

  it("a list that shrank under the page on screen moves the pager to the last page, and only then", async () => {
    const w = pager({ offset: 20, limit: 10, total: 25 });
    await w.setProps({ total: 24 });
    expect(w.emitted("move")).toBeUndefined();
    await w.setProps({ total: 12 });
    expect(w.emitted("move")).toEqual([[10]]);
    await w.setProps({ total: 0, offset: 10 });
    expect(w.emitted("move")).toEqual([[10], [0]]);
    w.unmount();
  });

  it("a limit that is not in the list is added to the sizes", () => {
    const w = pager({ limit: 30 });
    const values = (w.findComponent({ name: "FilterSelect" }).props("options") as Array<{ value: number }>).map(
      (o) => o.value,
    );
    expect(values).toEqual([10, 20, 30, 50, 100]);
    w.unmount();
  });

  it("a size change emits size with the number and never a move", async () => {
    const w = pager({ offset: 100 });
    w.findComponent({ name: "FilterSelect" }).vm.$emit("update:modelValue", "25");
    await flushPromises();
    expect(w.emitted("size")).toEqual([[25]]);
    expect(w.emitted("move")).toBeUndefined();
    w.unmount();
  });
});

describe("IconAction", () => {
  it("is a button named by its label and emits click", async () => {
    const w = mount(IconAction, { props: { icon: IconRefresh, label: "Refresh jobs" }, attachTo: document.body });
    const button = w.find("button");
    expect(button.attributes("aria-label")).toBe("Refresh jobs");
    await button.trigger("click");
    expect(w.emitted("click")).toHaveLength(1);
    w.unmount();
  });

  it("a disabled one does not emit", async () => {
    const w = mount(IconAction, {
      props: { icon: IconRefresh, label: "Wake", disabled: true },
      attachTo: document.body,
    });
    await w.find("button").trigger("click");
    expect(w.emitted("click")).toBeUndefined();
    w.unmount();
  });
});
