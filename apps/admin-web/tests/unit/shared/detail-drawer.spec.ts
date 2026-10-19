import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, ref } from "vue";
import * as confirm from "../../../src/components/shared/confirm-dialog";
import DetailDrawer from "../../../src/components/shared/DetailDrawer.vue";
import { cancelPending } from "../../../src/components/shared/use-leave-guard";

vi.mock("../../../src/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
const ask = vi.mocked(confirm.confirmDialog);

const mounted: VueWrapper[] = [];
afterEach(() => {
  cancelPending();
  while (mounted.length) mounted.pop()!.unmount();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

const PLANTED = "<img src=x onerror=alert(1)><b>x</b>";

async function drawer(
  props: Record<string, unknown> = {},
  slots: Record<string, string> = { default: "<p id='in'>body</p>" },
) {
  const w = mount(DetailDrawer, {
    props: { modelValue: true, title: "Job 3b9e1f0a", testid: "job-drawer", ...props },
    slots,
    attachTo: document.body,
  });
  mounted.push(w);
  await flushPromises();
  return w;
}

const dialog = () => document.body.querySelector<HTMLElement>("[role='dialog']");

describe("DetailDrawer", () => {
  it("is a dialog named by its title, with the fixed test id", async () => {
    await drawer();
    expect(dialog()).not.toBeNull();
    expect(dialog()!.getAttribute("aria-label")).toBe("Job 3b9e1f0a");
    expect(document.body.querySelector("[data-testid='job-drawer']")).not.toBeNull();
    expect(document.body.querySelector("h2")!.textContent).toBe("Job 3b9e1f0a");
  });

  it("slides in from the right at 620 px, and is a full-screen sheet on a phone", async () => {
    const w = await drawer();
    const el = w.findComponent({ name: "ElDrawer" });
    expect(el.props("direction")).toBe("rtl");
    expect(el.props("size")).toBe("620px");
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: /max-width:\s*767/.test(q),
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    const phone = await drawer();
    expect(phone.findComponent({ name: "ElDrawer" }).props("size")).toBe("100%");
  });

  it("a phone sheet has a Back button named by backLabel, the title as h1 and a theme toggle; a desktop drawer has none of them", async () => {
    await drawer();
    expect(document.body.querySelector("button[aria-label='Theme']")).toBeNull();
    expect(document.body.querySelector("h1")).toBeNull();
    document.body.replaceChildren();
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: /max-width:\s*767/.test(q),
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    const w = await drawer({ backLabel: "Back to Jobs" });
    expect(document.body.querySelector("h1")!.textContent).toBe("Job 3b9e1f0a");
    expect(document.body.querySelector("h2")).toBeNull();
    expect(document.body.querySelector("button[aria-label='Theme']")).not.toBeNull();
    expect(document.body.querySelector("button[aria-label='Close']")).toBeNull();
    document.body.querySelector<HTMLButtonElement>("button[aria-label='Back to Jobs']")!.click();
    await flushPromises();
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
    expect(document.body.querySelector(".detail-drawer.is-sheet")).not.toBeNull();
  });

  it("destroys its content on close: nothing of a closed detail stays in the page", async () => {
    let alive = 0;
    const Probe = defineComponent({
      setup() {
        alive += 1;
        return () => h("span", { id: "probe" }, "probe");
      },
      unmounted() {
        alive -= 1;
      },
    });
    const open = ref(true);
    const Host = defineComponent({
      setup: () => () => h(DetailDrawer, { modelValue: open.value, title: "t" }, { default: () => h(Probe) }),
    });
    const w = mount(Host, { attachTo: document.body, global: { stubs: { transition: false } } });
    mounted.push(w);
    await flushPromises();
    expect(document.getElementById("probe")).not.toBeNull();
    expect(w.findComponent({ name: "ElDrawer" }).props("destroyOnClose")).toBe(true);
    open.value = false;
    await flushPromises();
    await new Promise((r) => setTimeout(r, 400));
    expect(document.getElementById("probe")).toBeNull();
    expect(alive).toBe(0);
  });

  it("the close button is named Close and asks to close", async () => {
    const w = await drawer();
    document.body.querySelector<HTMLButtonElement>("button[aria-label='Close']")!.click();
    await flushPromises();
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("chips, tabs, body and footer land in their own regions; chips, tabs and footer only when given", async () => {
    await drawer(
      {},
      {
        default: "<p id='in'>body</p>",
        chips: "<span id='c'>chip</span>",
        tabs: "<span id='t'>tab</span>",
        footer: "<button id='f'>go</button>",
      },
    );
    expect(document.body.querySelector(".detail-drawer__chips #c")).not.toBeNull();
    expect(document.body.querySelector(".detail-drawer__tabs #t")).not.toBeNull();
    expect(document.body.querySelector(".detail-drawer__body #in")).not.toBeNull();
    expect(document.body.querySelector(".detail-drawer__foot #f")).not.toBeNull();
  });

  it("with no chips, tabs or footer none of those regions exist", async () => {
    await drawer();
    expect(document.body.querySelector(".detail-drawer__chips")).toBeNull();
    expect(document.body.querySelector(".detail-drawer__tabs")).toBeNull();
    expect(document.body.querySelector(".detail-drawer__foot")).toBeNull();
  });

  it("heading is the visible h2 text when given, the name stays the title; without it the title shows", async () => {
    await drawer({ heading: "Portrait, soft window light" });
    expect(document.body.querySelector("h2")!.textContent).toBe("Portrait, soft window light");
    expect(dialog()!.getAttribute("aria-label")).toBe("Job 3b9e1f0a");
    document.body.replaceChildren();
    await drawer();
    expect(document.body.querySelector("h2")!.textContent).toBe("Job 3b9e1f0a");
  });

  it("a title with markup is text, in the heading and in the name", async () => {
    await drawer({ title: PLANTED });
    expect(document.body.querySelector("h2 img")).toBeNull();
    expect(document.body.querySelector("h2")!.textContent).toBe(PLANTED);
    expect(dialog()!.getAttribute("aria-label")).toBe(PLANTED);
  });
});

const CANCEL_FOOTER = "<template #footer='{ close }'><button id='cancel' @click='close'>Cancel</button></template>";

describe("DetailDrawer close guard", () => {
  // the box the test answers by hand (true = Discard)
  let answer: (discard: boolean) => void = () => undefined;
  beforeEach(() => {
    ask.mockReset();
    ask.mockImplementation(() => new Promise<boolean>((resolve) => (answer = resolve)));
  });
  const press = (key: string) =>
    document.dispatchEvent(new KeyboardEvent("keydown", { key, code: key, bubbles: true, cancelable: true }));
  // a press and release on the scrim itself, as a mouse does
  const scrim = () => {
    const el = document.body.querySelector<HTMLElement>(".el-overlay")!;
    for (const type of ["mousedown", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(type, { bubbles: true }));
  };
  const closeButton = () => document.body.querySelector<HTMLButtonElement>("button[aria-label='Close']")!;
  const phone = () =>
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: /max-width:\s*767/.test(q),
      addEventListener: () => {},
      removeEventListener: () => {},
    }));

  it("with nothing declared every way to close goes at once, as before", async () => {
    const w = await drawer();
    closeButton().click();
    press("Escape");
    await flushPromises();
    expect(ask).not.toHaveBeenCalled();
    expect(w.emitted("update:modelValue")).toEqual([[false], [false]]);
  });

  it("a dirty drawer asks once with its own wording, and closes only on Discard", async () => {
    const w = await drawer({ dirty: true, discardText: "Throw away the key?" });
    closeButton().click();
    await flushPromises();
    expect(ask).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ message: "Throw away the key?" }));
    answer(false);
    await flushPromises();
    expect(w.emitted("update:modelValue")).toBeUndefined();
    closeButton().click();
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(2);
    answer(true);
    await flushPromises();
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("Escape, the X and the footer's close all ask, and three of them while the box is up make one box", async () => {
    const w = await drawer({ dirty: true }, { default: "<p>body</p>", footer: CANCEL_FOOTER });
    press("Escape");
    closeButton().click();
    document.getElementById("cancel")!.click();
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(1);
    answer(true);
    await flushPromises();
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("the footer's close asks like the X", async () => {
    const w = await drawer({ dirty: true }, { default: "<p>body</p>", footer: CANCEL_FOOTER });
    document.getElementById("cancel")!.click();
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(1);
    answer(true);
    await flushPromises();
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("the phone's Back asks like the X", async () => {
    phone();
    const w = await drawer({ dirty: true, backLabel: "Back to API keys" });
    document.body.querySelector<HTMLButtonElement>("button[aria-label='Back to API keys']")!.click();
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(1);
    answer(true);
    await flushPromises();
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("a busy drawer ignores X, Escape, the footer's close and the scrim, clean or dirty", async () => {
    for (const dirty of [false, true]) {
      document.body.replaceChildren();
      const w = await drawer({ busy: true, dirty }, { default: "<p>body</p>", footer: CANCEL_FOOTER });
      closeButton().click();
      press("Escape");
      document.getElementById("cancel")!.click();
      scrim();
      await flushPromises();
      expect(ask).not.toHaveBeenCalled();
      expect(w.emitted("update:modelValue")).toBeUndefined();
    }
  });

  it("a busy phone sheet ignores Back too", async () => {
    phone();
    const w = await drawer({ busy: true, backLabel: "Back" });
    document.body.querySelector<HTMLButtonElement>("button[aria-label='Back']")!.click();
    await flushPromises();
    expect(w.emitted("update:modelValue")).toBeUndefined();
  });

  it("closeOnScrim false: a click beside the drawer never closes or asks; by default it closes like the X", async () => {
    const form = await drawer({ closeOnScrim: false, dirty: true });
    scrim();
    await flushPromises();
    expect(ask).not.toHaveBeenCalled();
    expect(form.emitted("update:modelValue")).toBeUndefined();
    expect(form.findComponent({ name: "ElDrawer" }).props("closeOnClickModal")).toBe(false);
    document.body.replaceChildren();
    const detail = await drawer();
    expect(detail.findComponent({ name: "ElDrawer" }).props("closeOnClickModal")).toBe(true);
    scrim();
    await flushPromises();
    expect(detail.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("when the parent closes the drawer under a pending box, the box goes as keep and nothing is emitted", async () => {
    const close = vi.spyOn(ElMessageBox, "close").mockImplementation(() => undefined);
    const w = await drawer({ dirty: true });
    closeButton().click();
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(1);
    await w.setProps({ modelValue: false });
    await flushPromises();
    expect(close).toHaveBeenCalledTimes(1);
    expect(w.emitted("update:modelValue")).toBeUndefined();
    // the latch is free: the next close asks again
    await w.setProps({ modelValue: true });
    await flushPromises();
    closeButton().click();
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(2);
  });
});
