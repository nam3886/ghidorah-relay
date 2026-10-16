import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, ref } from "vue";
import DetailDrawer from "../../../src/components/shared/DetailDrawer.vue";

const mounted: VueWrapper[] = [];
afterEach(() => {
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

  it("a title with markup is text, in the heading and in the name", async () => {
    await drawer({ title: PLANTED });
    expect(document.body.querySelector("h2 img")).toBeNull();
    expect(document.body.querySelector("h2")!.textContent).toBe(PLANTED);
    expect(dialog()!.getAttribute("aria-label")).toBe(PLANTED);
  });
});
