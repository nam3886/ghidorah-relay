import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { defineComponent } from "vue";
import HintTip from "../../../src/components/shared/HintTip.vue";

afterEach(() => document.body.replaceChildren());

const mounted = (props: Record<string, unknown> = {}) =>
  mount(HintTip, {
    props: { text: "Shown with the sample tracking code (draft frame).", ...props },
    slots: { default: "8 characters" },
    attachTo: document.body,
  });

describe("HintTip", () => {
  it("draws its words in a tab stop, so the keyboard reaches the note too", () => {
    const w = mounted();
    expect(w.text()).toBe("8 characters");
    expect(w.find(".hint-tip").attributes("tabindex")).toBe("0");
    expect(w.find(".hint-tip").attributes("title")).toBeUndefined();
    w.unmount();
  });

  it("is the menu-surface tooltip (light, no arrow, the rl-tip class), with the text as plain content", () => {
    const w = mounted();
    const tip = w.findComponent({ name: "ElTooltip" });
    expect(tip.props("content")).toBe("Shown with the sample tracking code (draft frame).");
    expect(tip.props("effect")).toBe("light");
    expect(tip.props("popperClass")).toBe("rl-tip");
    expect(tip.props("showArrow")).toBe(false);
    expect(tip.props("rawContent")).toBeFalsy();
    w.unmount();
  });

  it("a mono note (a value) adds the mono class to the same tooltip, still as plain content", () => {
    const w = mounted({ mono: true, text: '{\n  "a": 1\n}' });
    const tip = w.findComponent({ name: "ElTooltip" });
    expect(tip.props("popperClass")).toBe("rl-tip rl-tip--mono");
    expect(tip.props("content")).toBe('{\n  "a": 1\n}');
    expect(tip.props("rawContent")).toBeFalsy();
    w.unmount();
  });

  it("Escape on the text asks the tooltip to hide, and another key does not", async () => {
    let hidden = 0;
    const Stub = defineComponent({
      setup(_, { expose, slots }) {
        expose({ hide: () => (hidden += 1) });
        return () => slots.default?.();
      },
    });
    const w = mount(HintTip, {
      props: { text: "note" },
      slots: { default: "words" },
      global: { stubs: { ElTooltip: Stub } },
    });
    await w.find(".hint-tip").trigger("keydown", { key: "a" });
    expect(hidden).toBe(0);
    await w.find(".hint-tip").trigger("keydown", { key: "Escape" });
    expect(hidden).toBe(1);
  });

  it("Escape of an open note goes no further, and with no note up it does", async () => {
    const Stub = defineComponent({
      emits: ["update:visible"],
      setup(_, { expose, slots }) {
        expose({ hide: () => undefined });
        return () => slots.default?.();
      },
    });
    const w = mount(HintTip, {
      props: { text: "note" },
      slots: { default: "words" },
      global: { stubs: { ElTooltip: Stub } },
      attachTo: document.body,
    });
    let reached = 0;
    const count = () => (reached += 1);
    document.addEventListener("keydown", count);
    try {
      const tip = w.findComponent(Stub);
      tip.vm.$emit("update:visible", true);
      await w.find(".hint-tip").trigger("keydown", { key: "Escape" });
      expect(reached).toBe(0);
      tip.vm.$emit("update:visible", false);
      await w.find(".hint-tip").trigger("keydown", { key: "Escape" });
      expect(reached).toBe(1);
    } finally {
      document.removeEventListener("keydown", count);
      w.unmount();
    }
  });

  // happy-dom cannot open an Element Plus popper; the open-on-focus behaviour is proved in the browser tier
  // (test_e2e_settings_frames_test: the preview count takes the focus and the tooltip shows, Escape closes it).
  it("the trigger is the focusable span, hover and focus open it, and a note with markup stays a plain string", () => {
    const w = mounted({ text: "<img src=x onerror=alert(1)>" });
    const tip = w.findComponent({ name: "ElTooltip" });
    expect(tip.props("trigger")).toEqual(["hover", "focus"]);
    expect(tip.props("content")).toBe("<img src=x onerror=alert(1)>");
    expect(tip.props("rawContent")).toBeFalsy();
    expect(w.find(".hint-tip").element.tagName).toBe("SPAN");
    expect(document.body.querySelector("img")).toBeNull();
    w.unmount();
  });
});
