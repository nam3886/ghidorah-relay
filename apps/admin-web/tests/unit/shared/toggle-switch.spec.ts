import { mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import ToggleSwitch from "../../../src/components/shared/ToggleSwitch.vue";

let wrapper: VueWrapper | null = null;
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
});

const mountSwitch = (props: Record<string, unknown>) => {
  wrapper = mount(ToggleSwitch, { props: { modelValue: true, label: "Enable main", ...props } });
  return wrapper;
};

describe("ToggleSwitch", () => {
  it("is a switch with the label and the state; a click asks for the opposite", async () => {
    const w = mountSwitch({ size: "desktop" });
    const sw = w.get("[role=switch]");
    expect(sw.attributes("aria-label")).toBe("Enable main");
    expect(sw.attributes("aria-checked")).toBe("true");
    await sw.trigger("click");
    expect(w.emitted("change")).toEqual([[false]]);
    await w.setProps({ modelValue: false });
    expect(w.get("[role=switch]").attributes("aria-checked")).toBe("false");
    await w.get("[role=switch]").trigger("click");
    expect(w.emitted("change")).toEqual([[false], [true]]);
  });

  it("the track carries the on state, so the knob and the fill move with it", async () => {
    const w = mountSwitch({});
    expect(w.get(".toggle-switch__track").classes()).toContain("is-on");
    await w.setProps({ modelValue: false });
    expect(w.get(".toggle-switch__track").classes()).not.toContain("is-on");
  });

  it("each width has its own box: the class names the size, and no size is the responsive box", () => {
    expect(mountSwitch({ size: "desktop" }).get("button").classes()).toContain("toggle-switch--desktop");
    wrapper!.unmount();
    expect(mountSwitch({ size: "tablet" }).get("button").classes()).toContain("toggle-switch--tablet");
    wrapper!.unmount();
    expect(mountSwitch({ size: "phone" }).get("button").classes()).toContain("toggle-switch--phone");
    wrapper!.unmount();
    const auto = mountSwitch({}).get("button").classes();
    expect(auto).toContain("toggle-switch--auto");
    expect(auto).not.toContain("toggle-switch--phone");
  });

  it("a disabled switch cannot be clicked and says so", async () => {
    const w = mountSwitch({ disabled: true });
    expect(w.get("button").attributes("disabled")).toBeDefined();
    await w.get("button").trigger("click");
    expect(w.emitted("change")).toBeUndefined();
  });

  it("the title is shown only when given", () => {
    expect(mountSwitch({ title: "Disable key" }).get("button").attributes("title")).toBe("Disable key");
    wrapper!.unmount();
    expect(mountSwitch({}).get("button").attributes("title")).toBeUndefined();
  });
});
