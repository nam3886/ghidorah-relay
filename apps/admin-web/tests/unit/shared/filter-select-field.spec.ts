import { mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import FilterSelect from "@/components/shared/FilterSelect.vue";

// The field variant of FilterSelect: the canvas's labelled select (B/T/P-Alerts toolbar) drawn as our own control.
const OPTIONS = [
  { value: "all", label: "All" },
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
];

function showAt(width: number): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width: 767px")
      ? width <= 767
      : query.includes("min-width: 768px")
        ? width >= 768 && width <= 1279
        : false,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

let wrapper: VueWrapper | null = null;
function field(props: Record<string, unknown> = {}) {
  wrapper = mount(FilterSelect, {
    props: { modelValue: "open", options: OPTIONS, variant: "field", label: "Status", ...props },
    attachTo: document.body,
  });
  return wrapper;
}
const trigger = (w: VueWrapper) => w.find<HTMLButtonElement>('[role="combobox"]');
const listbox = () => document.querySelector('[role="listbox"]');

beforeEach(() => showAt(1440));
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the field variant of FilterSelect", () => {
  it("draws its label above a box that holds every option, only the current one seen, and the select's arrow", () => {
    const w = field();
    expect(w.element.classList.contains("filter-field")).toBe(true);
    const label = w.find("label");
    expect(label.text()).toBe("Status");
    expect(label.attributes("for")).toBe(trigger(w).attributes("id"));
    expect(trigger(w).attributes("aria-labelledby")).toBe(label.attributes("id"));
    const words = trigger(w).findAll(".filter-field__value > span");
    expect(words.map((s) => s.text())).toEqual(["All", "Open", "Acknowledged"]);
    expect(words.filter((s) => s.classes("is-current")).map((s) => s.text())).toEqual(["Open"]);
    expect(words.map((s) => s.attributes("aria-hidden"))).toEqual(["true", undefined, "true"]);
    expect(trigger(w).find("svg path").attributes("d")).toBe("M2 2l4 4 4-4");
    expect(w.find("select").exists()).toBe(false);
  });

  it("its value box is as wide as the longest option on the nearest whole pixel, as a select is", async () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const width = this.classList.contains("filter-field__value") ? 94.3 : 0;
      return { top: 0, bottom: 0, left: 0, right: width, width, height: 0, x: 0, y: 0 } as DOMRect;
    });
    const w = field();
    await nextTick();
    await nextTick();
    expect((w.find(".filter-field__value").element as HTMLElement).style.width).toBe("94px");
  });

  it("is 36 high on a tablet and the drawn chip on a phone", async () => {
    showAt(768);
    expect(field().classes()).toContain("is-tall");
    wrapper!.unmount();
    showAt(390);
    const phone = field();
    expect(phone.classes()).toContain("is-chip");
    expect(phone.classes()).not.toContain("is-tall");
  });

  it("opens the listbox, moves and chooses from the keyboard, and keeps the focus on the box", async () => {
    const w = field();
    trigger(w).element.focus();
    await trigger(w).trigger("keydown", { key: "ArrowDown" });
    await nextTick();
    expect(listbox()).not.toBeNull();
    expect(trigger(w).attributes("aria-expanded")).toBe("true");
    await trigger(w).trigger("keydown", { key: "ArrowDown" });
    await trigger(w).trigger("keydown", { key: "Enter" });
    expect(w.emitted("update:modelValue")).toEqual([["acknowledged"]]);
    expect(listbox()).toBeNull();
    expect(document.activeElement).toBe(trigger(w).element);
  });

  it("disabled: the box is off and opens nothing", async () => {
    const w = field({ disabled: true });
    expect(trigger(w).attributes("disabled")).toBeDefined();
    await trigger(w).trigger("click");
    expect(listbox()).toBeNull();
  });

  it("a press on its own label or box while open does not close it from outside", async () => {
    const w = field();
    await trigger(w).trigger("click");
    await nextTick();
    expect(listbox()).not.toBeNull();
    w.find("label").element.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await nextTick();
    expect(listbox()).not.toBeNull();
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await nextTick();
    expect(listbox()).toBeNull();
  });

  it("on a phone the list hangs 6 px under the chip, not under the borderless control inside it", async () => {
    showAt(390);
    const w = field();
    const rect = (top: number, bottom: number, left: number, width: number) =>
      ({ top, bottom, left, right: left + width, width, height: bottom - top, x: left, y: top }) as DOMRect;
    w.element.getBoundingClientRect = () => rect(100, 144, 29, 183);
    trigger(w).element.getBoundingClientRect = () => rect(102, 142, 89, 116);
    await trigger(w).trigger("click");
    await nextTick();
    await nextTick();
    const list = listbox() as HTMLElement;
    expect(list.style.top).toBe("150px");
    expect(list.style.left).toBe("29px");
  });

  it("the chip variant is unchanged: the trigger is the root, with the word inside", () => {
    wrapper = mount(FilterSelect, {
      props: { modelValue: "open", options: OPTIONS, label: "Status" },
      attachTo: document.body,
    });
    expect(wrapper.element.tagName).toBe("BUTTON");
    expect(wrapper.attributes("role")).toBe("combobox");
    expect(wrapper.find(".filter-select__label").text()).toBe("Status");
    expect(wrapper.find(".filter-select__value").text()).toBe("Open");
    expect(wrapper.find("label").exists()).toBe(false);
  });
});
