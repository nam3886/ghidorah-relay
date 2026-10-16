import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import ActionMenu from "../../../src/components/shared/ActionMenu.vue";
import CommandChip from "../../../src/components/shared/CommandChip.vue";
import DrawerPanel from "../../../src/components/shared/DrawerPanel.vue";
import DrawerTabs from "../../../src/components/shared/DrawerTabs.vue";
import FilterSelect from "../../../src/components/shared/FilterSelect.vue";
import { IconPencilMenu, IconTrashMenu } from "../../../src/components/shared/icons";

afterEach(() => document.body.replaceChildren());

const options = [
  { value: "", label: "All" },
  { value: "failed", label: "Failed" },
  { value: "done", label: "Done" },
];

describe("FilterSelect", () => {
  it("is a listbox button: the label word, the value and a chevron inside the control", () => {
    const w = mount(FilterSelect, { props: { modelValue: "", options, label: "Status" }, attachTo: document.body });
    const button = w.find("button");
    expect(button.attributes("aria-haspopup")).toBe("listbox");
    expect(button.find(".filter-select__label").text()).toBe("Status");
    expect(button.find(".filter-select__value").text()).toBe("All");
    expect(button.find("svg").exists()).toBe(true);
    w.unmount();
  });

  it("the height follows the size and the chevron can be left out", () => {
    const toolbar = mount(FilterSelect, { props: { modelValue: "", options } });
    expect(toolbar.find("button").classes()).toContain("is-toolbar");
    const pager = mount(FilterSelect, { props: { modelValue: "", options, size: "pager", chevron: false } });
    expect(pager.find("button").classes()).toContain("is-pager");
    expect(pager.find("svg").exists()).toBe(false);
    expect(
      mount(FilterSelect, { props: { modelValue: "", options, size: "header" } })
        .find("button")
        .classes(),
    ).toContain("is-header");
  });

  it("shows the label of the current value, and nothing when no option matches", () => {
    expect(
      mount(FilterSelect, { props: { modelValue: "failed", options } })
        .find(".filter-select__value")
        .text(),
    ).toBe("Failed");
    expect(
      mount(FilterSelect, { props: { modelValue: "zzz", options } })
        .find(".filter-select__value")
        .text(),
    ).toBe("");
  });

  it("choosing an item emits update:modelValue with its value", async () => {
    const w = mount(FilterSelect, { props: { modelValue: "", options, label: "Status" }, attachTo: document.body });
    w.findComponent({ name: "ElDropdown" }).vm.$emit("command", "done");
    await flushPromises();
    expect(w.emitted("update:modelValue")).toEqual([["done"]]);
    w.unmount();
  });
});

describe("ActionMenu", () => {
  const items = [
    { key: "edit", label: "Edit", icon: IconPencilMenu },
    { key: "delete", label: "Delete", icon: IconTrashMenu, danger: true, separated: true },
  ];

  it("is a button named More that has a popup menu, closed, with the dots drawn at stroke 2.4", () => {
    const w = mount(ActionMenu, { props: { items }, attachTo: document.body });
    const button = w.find("button");
    expect(button.attributes("aria-label")).toBe("More");
    expect(button.attributes("aria-haspopup")).toBe("menu");
    expect(button.attributes("aria-expanded")).toBe("false");
    expect(button.find("svg").attributes("stroke-width")).toBe("2.4");
    expect(document.body.querySelector("[role=menu]")).toBeNull();
    w.unmount();
  });

  it("opens a menu named Actions; a chosen item emits select with its key and closes it", async () => {
    const w = mount(ActionMenu, { props: { items }, attachTo: document.body });
    await w.find("button").trigger("click");
    expect(w.find("button").attributes("aria-expanded")).toBe("true");
    const menu = document.body.querySelector("[role=menu]")!;
    expect(menu.getAttribute("aria-label")).toBe("Actions");
    const entries = [...menu.querySelectorAll<HTMLElement>("[role=menuitem]")];
    expect(entries.map((e) => e.textContent?.trim())).toEqual(["Edit", "Delete"]);
    entries[1]!.click();
    await flushPromises();
    expect(w.emitted("select")).toEqual([["delete"]]);
    expect(document.body.querySelector("[role=menu]")).toBeNull();
    w.unmount();
  });

  it("an item without an icon still draws its label", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const w = mount(ActionMenu, { props: { items: [{ key: "x", label: "Plain" }] }, attachTo: document.body });
    await w.find("button").trigger("click");
    const entry = document.body.querySelector("[role=menuitem]")!;
    expect(entry.textContent?.trim()).toBe("Plain");
    expect(entry.querySelector("svg")).toBeNull();
    // no component is made for a missing icon (Vue would warn about the undefined type)
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
    w.unmount();
  });
});

describe("CommandChip", () => {
  it("running: a spinner and `running…`, and no time", () => {
    const w = mount(CommandChip, { props: { state: "running", time: "20:55:07" } });
    expect(w.text()).toBe("running…");
    expect(w.find(".command-chip__spinner").exists()).toBe(true);
    expect(w.find(".command-chip__time").exists()).toBe(false);
    expect(w.classes()).toContain("is-running");
  });

  it("ok: a dot, `ok ·` and the time in mono", () => {
    const w = mount(CommandChip, { props: { state: "ok", time: "20:55:07" } });
    expect(w.text()).toBe("ok · 20:55:07");
    expect(w.find(".command-chip__time").text()).toBe("20:55:07");
    expect(w.find(".command-chip__dot").exists()).toBe(true);
    expect(w.classes()).toContain("is-ok");
  });

  it("error: a dot and `error · <message>`; the message is text", () => {
    const w = mount(CommandChip, { props: { state: "error", message: "connect timeout after 8 s" } });
    expect(w.text()).toBe("error · connect timeout after 8 s");
    expect(w.classes()).toContain("is-error");
    const planted = mount(CommandChip, { props: { state: "error", message: "<b>x</b>" } });
    expect(planted.find("b").exists()).toBe(false);
    expect(planted.text()).toBe("error · <b>x</b>");
  });
});

describe("DrawerTabs", () => {
  const tabs = [
    { key: "overview", label: "Overview" },
    { key: "timeline", label: "Timeline" },
    { key: "technical", label: "Technical" },
  ];
  const mounted = (modelValue = "overview") =>
    mount(DrawerTabs, { props: { tabs, modelValue, idPrefix: "job", label: "Job sections" }, attachTo: document.body });

  it("is a tablist of tabs, the current one selected and the only one in the tab order", () => {
    const w = mounted("timeline");
    expect(w.attributes("role")).toBe("tablist");
    expect(w.attributes("aria-label")).toBe("Job sections");
    const rows = w.findAll("[role='tab']");
    expect(rows.map((t) => t.text())).toEqual(["Overview", "Timeline", "Technical"]);
    expect(rows.map((t) => t.attributes("aria-selected"))).toEqual(["false", "true", "false"]);
    expect(rows.map((t) => t.attributes("tabindex"))).toEqual(["-1", "0", "-1"]);
    w.unmount();
  });

  it("clicking a tab emits its key", async () => {
    const w = mounted();
    await w.findAll("[role='tab']")[2]!.trigger("click");
    expect(w.emitted("update:modelValue")).toEqual([["technical"]]);
    w.unmount();
  });

  it("the arrow keys move to the next and the previous tab and wrap round", async () => {
    const w = mounted();
    const rows = w.findAll("[role='tab']");
    await rows[0]!.trigger("keydown", { key: "ArrowRight" });
    await rows[0]!.trigger("keydown", { key: "ArrowLeft" });
    expect(w.emitted("update:modelValue")).toEqual([["timeline"], ["technical"]]);
    w.unmount();
  });

  it("the focus moves with the arrow: next, previous, and round the ends in both directions", async () => {
    const w = mounted();
    const rows = w.findAll("[role='tab']");
    const focused = () => document.activeElement?.id;
    await rows[0]!.trigger("keydown", { key: "ArrowRight" });
    expect(focused()).toBe("job-tab-timeline");
    await rows[1]!.trigger("keydown", { key: "ArrowLeft" });
    expect(focused()).toBe("job-tab-overview");
    await rows[0]!.trigger("keydown", { key: "ArrowLeft" });
    expect(focused()).toBe("job-tab-technical");
    await rows[2]!.trigger("keydown", { key: "ArrowRight" });
    expect(focused()).toBe("job-tab-overview");
    expect(document.activeElement).toBe(rows[0]!.element);
    w.unmount();
  });

  it("a key that is not an arrow moves neither the selection nor the focus", async () => {
    const w = mounted();
    const rows = w.findAll("[role='tab']");
    (rows[0]!.element as HTMLElement).focus();
    await rows[0]!.trigger("keydown", { key: "ArrowDown" });
    await rows[0]!.trigger("keydown", { key: "a" });
    expect(w.emitted("update:modelValue")).toBeUndefined();
    expect(document.activeElement).toBe(rows[0]!.element);
    w.unmount();
  });

  it("each tab has an id and names the panel it controls; a tab key never collides with another prefix", () => {
    const w = mounted();
    const rows = w.findAll("[role='tab']");
    expect(rows.map((t) => t.attributes("id"))).toEqual(["job-tab-overview", "job-tab-timeline", "job-tab-technical"]);
    expect(rows.map((t) => t.attributes("aria-controls"))).toEqual([
      "job-panel-overview",
      "job-panel-timeline",
      "job-panel-technical",
    ]);
    const other = mount(DrawerTabs, { props: { tabs, modelValue: "overview", idPrefix: "log" } });
    expect(other.find("[role='tab']").attributes("id")).toBe("log-tab-overview");
    expect(other.find("[role='tab']").attributes("aria-controls")).not.toBe(rows[0]!.attributes("aria-controls"));
    w.unmount();
  });
});

describe("DrawerPanel", () => {
  const panel = (name: string, selected: string) =>
    mount(DrawerPanel, { props: { idPrefix: "job", name, selected }, slots: { default: "<p>body</p>" } });

  it("is a tabpanel whose id is the one its tab controls and that its tab labels", () => {
    const w = panel("timeline", "timeline");
    expect(w.attributes("role")).toBe("tabpanel");
    expect(w.attributes("id")).toBe("job-panel-timeline");
    expect(w.attributes("aria-labelledby")).toBe("job-tab-timeline");
    expect(w.text()).toBe("body");
  });

  it("the tab and the panel of one key point at each other", () => {
    const tabsW = mount(DrawerTabs, {
      props: { tabs: [{ key: "technical", label: "Technical" }], modelValue: "technical", idPrefix: "job" },
    });
    const p = panel("technical", "technical");
    expect(tabsW.find("[role='tab']").attributes("aria-controls")).toBe(p.attributes("id"));
    expect(p.attributes("aria-labelledby")).toBe(tabsW.find("[role='tab']").attributes("id"));
  });

  it("only the selected tab's panel is shown", () => {
    expect(panel("timeline", "timeline").attributes("style") ?? "").not.toContain("display: none");
    expect(panel("timeline", "overview").attributes("style")).toContain("display: none");
  });
});
