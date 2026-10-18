import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ElDialog } from "element-plus";
import { defineComponent, h, nextTick, ref } from "vue";
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
  it("draws its label above a box that holds every option, only the current one seen, and the page's chevron", () => {
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
    expect(trigger(w).find("svg path").attributes("d")).toBe("m6 9 6 6 6-6");
    expect(trigger(w).find("svg").attributes("width")).toBe("14");
    expect(w.find("select").exists()).toBe(false);
  });

  it("its value box is as wide as the longest option on a whole pixel, as a select is", async () => {
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

  it("the field draws the page's chevron, 14 px, on a desktop, a tablet and a phone chip", async () => {
    const w = field();
    expect(
      trigger(w)
        .findAll("svg path")
        .map((p) => p.attributes("d")),
    ).toEqual(["m6 9 6 6 6-6"]);
    const chevron = trigger(w).find(".filter-field__chevron");
    expect(chevron.exists()).toBe(true);
    expect(chevron.attributes("width")).toBe("14");
    expect(trigger(w).find(".filter-field__arrow").exists()).toBe(false);
    wrapper!.unmount();
    showAt(768);
    const tablet = field();
    expect(tablet.classes()).toContain("is-tall");
    expect(trigger(tablet).find(".filter-field__chevron").exists()).toBe(true);
    wrapper!.unmount();
    showAt(390);
    const chip = field();
    expect(chip.classes()).toContain("is-chip");
    expect(trigger(chip).find(".filter-field__chevron").exists()).toBe(true);
  });

  it("a form's field (block) is no chip on a phone, and the list hangs under the box", async () => {
    showAt(390);
    const phone = field({ block: true });
    expect(phone.classes()).not.toContain("is-chip");
    const rect = (top: number, bottom: number, left: number, width: number) =>
      ({ top, bottom, left, right: left + width, width, height: bottom - top, x: left, y: top }) as DOMRect;
    phone.element.getBoundingClientRect = () => rect(100, 170, 16, 358);
    trigger(phone).element.getBoundingClientRect = () => rect(126, 170, 16, 358);
    await trigger(phone).trigger("click");
    await nextTick();
    await nextTick();
    expect((listbox() as HTMLElement).style.top).toBe("176px");
  });

  describe("in a dialog: Escape closes the open list only", () => {
    // the dialog asks `beforeClose` the moment it is to close (Escape included), before any leave transition: its
    // model turns false only after that transition, so the model alone cannot show that a close was asked for
    const closing = ref(0);
    const InDialog = defineComponent({
      setup: () => () =>
        h(ElDialog, { modelValue: true, title: "Import", beforeClose: () => closing.value++ }, () =>
          h(FilterSelect, { modelValue: "open", options: OPTIONS, variant: "field", label: "Status" }),
        ),
    });
    const escape = (el: Element) =>
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
    async function opened() {
      closing.value = 0;
      wrapper = mount(InDialog, { attachTo: document.body });
      await flushPromises();
      return document.querySelector<HTMLButtonElement>('[role="combobox"]')!;
    }

    it("an open list: Escape closes the list, the dialog stays open and the focus is on the select", async () => {
      const box = await opened();
      box.focus();
      box.click();
      await nextTick();
      expect(listbox()).not.toBeNull();
      escape(box);
      await flushPromises();
      expect(listbox()).toBeNull();
      expect(closing.value).toBe(0);
      expect(document.activeElement).toBe(box);
    });

    // the list is 200 x 120 at (100, 50) with its scrollbar in the last 10 px: its content box ends at x = 190
    function sized(list: HTMLElement) {
      list.getBoundingClientRect = () =>
        ({ left: 100, top: 50, right: 300, bottom: 170, width: 200, height: 120, x: 100, y: 50 }) as DOMRect;
      Object.defineProperty(list, "clientWidth", { value: 190 });
      Object.defineProperty(list, "clientHeight", { value: 120 });
      return list;
    }
    const press = (el: Element, x: number, y: number) => {
      const event = new PointerEvent("pointerdown", { clientX: x, clientY: y, bubbles: true, cancelable: true });
      el.dispatchEvent(event);
      return event;
    };

    it("a press on the list's padding keeps the focus on the select, and Escape still leaves the dialog open", async () => {
      const box = await opened();
      box.focus();
      box.click();
      await nextTick();
      const list = sized(listbox() as HTMLElement);
      expect(press(list, 103, 53).defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(box);
      escape(box);
      await flushPromises();
      expect(listbox()).toBeNull();
      expect(closing.value).toBe(0);
    });

    it("a press in the list's scrollbar gutter is left alone, so the scrollbar can be dragged", async () => {
      const box = await opened();
      box.click();
      await nextTick();
      const list = sized(listbox() as HTMLElement);
      expect(press(list, 295, 100).defaultPrevented).toBe(false);
      expect(press(list, 185, 100).defaultPrevented).toBe(true);
    });

    it("a closed select: Escape goes on to the dialog, which closes", async () => {
      const box = await opened();
      box.focus();
      escape(box);
      await flushPromises();
      expect(closing.value).toBe(1);
    });
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

  it("takes the floor of the measured width, as Chrome sizes a select: 117.6 is 117 (B-Logs' Status)", async () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const width = this.classList.contains("filter-field__value") ? 117.6 : 0;
      return { top: 0, bottom: 0, left: 0, right: width, width, height: 0, x: 0, y: 0 } as DOMRect;
    });
    const w = field();
    await nextTick();
    await nextTick();
    expect((w.find(".filter-field__value").element as HTMLElement).style.width).toBe("117px");
  });

  it("as a form's field (block) keeps its label above the box on a phone too, never the chip", () => {
    showAt(390);
    const w = field({ block: true });
    expect(w.classes()).toContain("is-block");
    expect(w.classes()).not.toContain("is-chip");
    expect(w.find("label").text()).toBe("Status");
    wrapper!.unmount();
    expect(field().classes()).not.toContain("is-block");
  });
});

// The field inside an Element Plus dialog (the key form's Mode): the list opens over the dialog, and the dialog leaves
// the list its keys and clicks.
describe("the field inside a dialog", () => {
  const value = ref<string | number>("open");
  const visible = ref(true);
  function inDialog() {
    value.value = "open";
    visible.value = true;
    wrapper = mount(
      defineComponent({
        setup: () => () =>
          h(
            ElDialog,
            { modelValue: visible.value, "onUpdate:modelValue": (v: boolean) => (visible.value = v), title: "Form" },
            () =>
              h(FilterSelect, {
                modelValue: value.value,
                "onUpdate:modelValue": (v: string | number) => (value.value = v),
                options: OPTIONS,
                variant: "field",
                label: "Mode",
              }),
          ),
      }),
      { attachTo: document.body },
    );
  }
  const settle = async () => {
    await nextTick();
    await nextTick();
  };
  const button = () => document.querySelector<HTMLButtonElement>('[role="combobox"]')!;
  const press = (key: string) =>
    button().dispatchEvent(new KeyboardEvent("keydown", { key, code: key, bubbles: true, cancelable: true }));

  it("the list sits above an open dialog", async () => {
    inDialog();
    await settle();
    button().click();
    await settle();
    const overlay = document.querySelector<HTMLElement>(".el-overlay")!;
    expect(Number((listbox() as HTMLElement).style.zIndex)).toBeGreaterThan(Number(overlay.style.zIndex));
  });

  it("Escape closes only the list: the dialog stays open and the focus is back on the trigger", async () => {
    inDialog();
    await settle();
    button().focus();
    press("ArrowDown");
    await settle();
    expect(listbox()).not.toBeNull();
    press("Escape");
    await settle();
    expect(listbox()).toBeNull();
    expect(visible.value).toBe(true);
    expect(document.querySelector(".el-dialog")).not.toBeNull();
    expect(document.activeElement).toBe(button());
  });

  it("the dialog's focus trap leaves the keyboard to it: the arrows and Enter pick an option", async () => {
    inDialog();
    await settle();
    button().focus();
    press("ArrowDown");
    await settle();
    press("ArrowDown");
    press("Enter");
    await settle();
    expect(value.value).toBe("acknowledged");
    expect(listbox()).toBeNull();
    expect(visible.value).toBe(true);
    expect(document.activeElement).toBe(button());
  });

  it("a click on an option picks it and leaves the dialog open", async () => {
    inDialog();
    await settle();
    button().click();
    await settle();
    [...document.querySelectorAll<HTMLElement>('[role="option"]')]
      .find((o) => o.textContent?.trim() === "All")!
      .click();
    await settle();
    expect(value.value).toBe("all");
    expect(visible.value).toBe(true);
    expect(document.querySelector(".el-dialog")).not.toBeNull();
  });
});
