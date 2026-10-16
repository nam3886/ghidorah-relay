import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import ActionMenu, { type MenuItem } from "@/components/shared/ActionMenu.vue";
import MiniChip from "@/components/accounts/MiniChip.vue";
import { IconCamera, IconPencilMenu, IconPower, IconTrashMenu } from "@/components/shared/icons";

let wrapper: VueWrapper | null = null;
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
});

describe("MiniChip", () => {
  it("is a 20 px chip in the tone, with the words", () => {
    wrapper = mount(MiniChip, { props: { tone: "warn" }, slots: { default: "drift" } });
    expect(wrapper.attributes("data-tone")).toBe("warn");
    expect(wrapper.text()).toBe("drift");
  });
});

describe("ActionMenu", () => {
  const items: MenuItem[] = [
    { key: "shot", label: "Shot", icon: IconCamera, disabled: true },
    { key: "wake", label: "Wake", icon: IconPower },
    { key: "edit", label: "Edit", icon: IconPencilMenu },
    { key: "delete", label: "Delete", icon: IconTrashMenu, danger: true, separated: true },
  ];
  const mountMenu = () => {
    wrapper = mount(ActionMenu, {
      props: { items, triggerLabel: "More actions for main", menuLabel: "Account actions" },
      attachTo: document.body,
    });
    return wrapper;
  };
  const trigger = () => document.body.querySelector<HTMLButtonElement>("[aria-haspopup=menu]")!;
  const menu = () => document.body.querySelector<HTMLElement>("[role=menu]");
  const entries = () => [...document.body.querySelectorAll<HTMLElement>("[role=menuitem]")];

  it("the trigger names itself and is closed; a click opens a named menu in the body, with the items in order", async () => {
    const w = mountMenu();
    expect(trigger().getAttribute("aria-label")).toBe("More actions for main");
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
    expect(menu()).toBeNull();
    await w.get("button").trigger("click");
    expect(trigger().getAttribute("aria-expanded")).toBe("true");
    expect(menu()!.getAttribute("aria-label")).toBe("Account actions");
    expect(entries().map((e) => e.textContent?.trim())).toEqual(["Shot", "Wake", "Edit", "Delete"]);
    expect(document.body.querySelectorAll("[role=separator]")).toHaveLength(1);
    // the menu is not inside the component (a table that scrolls would clip it)
    expect(w.element.querySelector("[role=menu]")).toBeNull();
  });

  it("the first item that can be picked takes the focus", async () => {
    await mountMenu().get("button").trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(entries()[1]);
  });

  it("a pick emits its key and closes the menu; a disabled item emits nothing and stays open", async () => {
    const w = mountMenu();
    await w.get("button").trigger("click");
    entries()[0]!.click();
    await flushPromises();
    expect(w.emitted("select")).toBeUndefined();
    expect(menu()).not.toBeNull();
    entries()[2]!.click();
    await flushPromises();
    expect(w.emitted("select")).toEqual([["edit"]]);
    expect(menu()).toBeNull();
  });

  it("Escape closes it and gives the focus back to the trigger", async () => {
    const w = mountMenu();
    await w.get("button").trigger("click");
    await flushPromises();
    menu()!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it("the arrow keys walk the items that can be picked, around", async () => {
    await mountMenu().get("button").trigger("click");
    await flushPromises();
    const press = (key: string) => menu()!.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    press("ArrowDown");
    expect(document.activeElement).toBe(entries()[2]);
    press("ArrowDown");
    expect(document.activeElement).toBe(entries()[3]);
    press("ArrowDown");
    expect(document.activeElement).toBe(entries()[1]);
    press("ArrowUp");
    expect(document.activeElement).toBe(entries()[3]);
  });

  it("a press elsewhere closes it; a press inside does not", async () => {
    await mountMenu().get("button").trigger("click");
    entries()[1]!.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(menu()).not.toBeNull();
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await flushPromises();
    expect(menu()).toBeNull();
  });

  it("a scroll or a resize keeps the menu open and moves it under the trigger", async () => {
    const w = mountMenu();
    const rect = (top: number) =>
      ({ top, bottom: top + 30, right: 500, left: 470, width: 30, height: 30, x: 470, y: top }) as DOMRect;
    const at = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rect(100));
    await w.get("button").trigger("click");
    expect(menu()!.style.top).toBe("136px");
    at.mockReturnValue(rect(40));
    window.dispatchEvent(new Event("scroll"));
    await flushPromises();
    expect(menu()).not.toBeNull();
    expect(menu()!.style.top).toBe("76px");
    at.mockRestore();
  });

  it("opens above the trigger when it would run past the bottom of the window, and there is room above", async () => {
    const w = mountMenu();
    const rect = (top: number) =>
      ({ top, bottom: top + 30, right: 500, left: 470, width: 30, height: 30, x: 470, y: top }) as DOMRect;
    const at = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rect(700));
    const height = vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(160);
    vi.stubGlobal("innerHeight", 800);
    await w.get("button").trigger("click");
    await flushPromises();
    expect(menu()!.style.top).toBe("534px");
    // no room above either: it stays under the trigger
    at.mockReturnValue(rect(100));
    window.dispatchEvent(new Event("scroll"));
    await flushPromises();
    expect(menu()!.style.top).toBe("136px");
    at.mockRestore();
    height.mockRestore();
    vi.unstubAllGlobals();
  });

  it("a second click on the trigger closes it", async () => {
    const w = mountMenu();
    await w.get("button").trigger("click");
    await w.get("button").trigger("click");
    await flushPromises();
    expect(menu()).toBeNull();
  });

  it("the danger item is marked, so it is drawn in the bad colour", async () => {
    await mountMenu().get("button").trigger("click");
    expect(entries()[3]!.classList.contains("is-danger")).toBe(true);
    expect(entries()[2]!.classList.contains("is-danger")).toBe(false);
  });
});
