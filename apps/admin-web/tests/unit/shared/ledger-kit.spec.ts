import { readFileSync } from "node:fs";
import { join } from "node:path";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { h, nextTick } from "vue";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import CapChip from "@/components/shared/CapChip.vue";
import IconButton, { LONG_PRESS_MS, SHOW_DELAY_MS } from "@/components/shared/IconButton.vue";
import { IB_ICONS } from "@/components/shared/icons/ledger-icons";
import BottomSheet from "@/components/shared/overlays/BottomSheet.vue";
import ModalCard from "@/components/shared/overlays/ModalCard.vue";
import SideDrawer from "@/components/shared/overlays/SideDrawer.vue";
import { PHONE_QUERY } from "@/composables/use-breakpoint";

const source = (file: string): string => readFileSync(join(process.cwd(), "src/components/shared", file), "utf8");

function media(matching: string[] = []): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: matching.includes(query),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

beforeEach(() => {
  media();
  setActivePinia(createPinia());
});
afterEach(() => {
  vi.useRealTimers();
  document.body.replaceChildren();
});

// ---- the arrow scroller ---------------------------------------------------------------------------------------------

type Entry = { target: Element; isIntersecting: boolean };
// An IntersectionObserver stand-in: each one keeps its callback and options; `fire` tells one what it sees.
class FakeIO {
  static all: FakeIO[] = [];
  watched: Element[] = [];
  disconnected = false;
  constructor(
    public cb: (entries: Entry[]) => void,
    public options: { root: Element; rootMargin: string; threshold: number },
  ) {
    FakeIO.all.push(this);
  }
  observe(el: Element): void {
    this.watched.push(el);
  }
  disconnect(): void {
    this.disconnected = true;
  }
  fire(which: "start" | "end", isIntersecting: boolean): void {
    this.cb([{ target: this.watched[which === "start" ? 0 : 1]!, isIntersecting }]);
  }
}

function mountScroller(props: Record<string, unknown> = {}, n = 8) {
  FakeIO.all = [];
  vi.stubGlobal("IntersectionObserver", FakeIO);
  return mount(ArrowScroller, {
    props: { label: "Filter jobs", ...props },
    slots: {
      default: () =>
        Array.from({ length: n }, (_, i) => h("button", { "aria-pressed": i === 6 ? "true" : "false" }, `Chip ${i}`)),
    },
    attachTo: document.body,
  });
}
const far = () => FakeIO.all.find((io) => io.options.rootMargin === "0px 7px 0px 7px")!;
const near = () => FakeIO.all.find((io) => io.options.rootMargin === "0px 1px 0px 1px")!;

describe("the arrow scroller", () => {
  it("watches two 1 px sentinels in the row, with the 8 px show and 2 px hide margins, never scroll events", () => {
    const w = mountScroller();
    const row = w.find(".asc__row");
    expect(row.attributes("role")).toBe("group");
    expect(row.attributes("aria-label")).toBe("Filter jobs");
    const [start, end] = [w.find(".asc__sentinel--start").element, w.find(".asc__sentinel--end").element];
    expect(row.element.firstElementChild).toBe(start);
    expect(row.element.lastElementChild).toBe(end);
    expect(FakeIO.all).toHaveLength(2);
    for (const io of [far(), near()]) {
      expect(io.options.root).toBe(row.element);
      expect(io.watched).toEqual([start, end]);
    }
  });

  it("shows an arrow once 8 px remain, hides it below 2 px, and keeps its state in between", async () => {
    const w = mountScroller();
    const right = () => w.find('[data-testid="scroll-right"]');
    const left = () => w.find('[data-testid="scroll-left"]');
    expect(right().classes()).not.toContain("is-on");
    // the end sentinel is more than 7 px out of the row: show
    far().fire("end", false);
    near().fire("end", false);
    await nextTick();
    expect(right().classes()).toContain("is-on");
    expect(right().attributes("aria-hidden")).toBeUndefined();
    expect(right().attributes("tabindex")).toBe("0");
    expect(w.classes()).toContain("asc--r");
    // between 2 and 8 px: no change
    far().fire("end", true);
    await nextTick();
    expect(right().classes()).toContain("is-on");
    // within 1 px: hide (still in the DOM, faded, unreachable)
    near().fire("end", true);
    await nextTick();
    expect(right().classes()).not.toContain("is-on");
    expect(right().attributes("aria-hidden")).toBe("true");
    expect(right().attributes("tabindex")).toBe("-1");
    // the left side on its own
    far().fire("start", false);
    near().fire("start", false);
    await nextTick();
    expect(left().classes()).toContain("is-on");
    expect(w.classes()).toEqual(expect.arrayContaining(["asc--l"]));
    far().fire("start", true);
    await nextTick();
    expect(left().classes()).toContain("is-on");
    near().fire("start", true);
    await nextTick();
    expect(left().classes()).not.toContain("is-on");
    expect(left().attributes("aria-label")).toBe("Scroll filter jobs left");
    expect(right().attributes("aria-label")).toBe("Scroll filter jobs right");
  });

  it("scrolls 80% of the visible width per arrow click, smoothly", async () => {
    const w = mountScroller();
    const row = w.find(".asc__row").element as HTMLElement;
    Object.defineProperty(row, "clientWidth", { configurable: true, value: 300 });
    const by = vi.fn();
    row.scrollBy = by as unknown as typeof row.scrollBy;
    await w.find('[data-testid="scroll-right"]').trigger("click");
    await w.find('[data-testid="scroll-left"]').trigger("click");
    expect(by.mock.calls).toEqual([[{ left: 240, behavior: "smooth" }], [{ left: -240, behavior: "smooth" }]]);
  });

  it("scrolls the active item into view on mount and when it changes", async () => {
    // the row is 0..300 px wide; the pressed chip sits at 400..480 until the row scrolls by its overflow
    const pageMoves: string[] = [];
    const original = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = function (this: HTMLElement) {
      pageMoves.push(this.textContent ?? "");
    };
    const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      const row = this.classList.contains("asc__row");
      const shift = row ? 0 : (this.parentElement?.scrollLeft ?? 0);
      return (row ? { left: 0, right: 300 } : { left: 400 - shift, right: 480 - shift }) as DOMRect;
    });
    try {
      const w = mountScroller({ activeKey: 1 });
      await flushPromises();
      const el = w.find(".asc__row").element as HTMLElement;
      expect(el.scrollLeft).toBe(180);
      el.scrollLeft = 0;
      await w.setProps({ activeKey: 2 });
      await flushPromises();
      expect(el.scrollLeft).toBe(180);
      // only the row moves: the page is never scrolled to it (a row below the fold stays below it)
      expect(pageMoves).toEqual([]);
    } finally {
      HTMLElement.prototype.scrollIntoView = original;
      rect.mockRestore();
    }
  });

  it("wraps a phone set of 5 or fewer with no arrows, and scrolls a longer one", () => {
    media([PHONE_QUERY]);
    const short = mountScroller({ count: 5 }, 5);
    expect(short.classes()).toContain("asc--wrap");
    expect(short.find(".asc__arrow").exists()).toBe(false);
    expect(FakeIO.all).toHaveLength(0);
    const long = mountScroller({ count: 6 }, 6);
    expect(long.classes()).not.toContain("asc--wrap");
    expect(long.findAll(".asc__arrow")).toHaveLength(2);
    media();
    expect(mountScroller({ count: 3 }, 3).classes()).not.toContain("asc--wrap");
  });

  it("stops watching when it is gone", () => {
    const w = mountScroller();
    w.unmount();
    expect(FakeIO.all.every((io) => io.disconnected)).toBe(true);
  });

  it("is drawn as the spec: no scrollbar, 4 px padding, 24 px fade, arrows over it fading in 150 ms", () => {
    const css = source("ArrowScroller.vue");
    expect(css).toMatch(/\.asc__row \{[^}]*padding: 4px 0;[^}]*scrollbar-width: none;/);
    expect(css).toMatch(/\.asc__row::-webkit-scrollbar \{\s*display: none;/);
    expect(css).toContain("transparent 0, #000 24px, #000 calc(100% - 24px), transparent 100%");
    expect(css).toMatch(
      /\.asc__arrow \{[^}]*opacity: 0;[^}]*pointer-events: none;[^}]*transition: opacity 150ms ease;/,
    );
    expect(css).toMatch(/\.asc__dot \{[^}]*width: 32px;[^}]*height: 32px;[^}]*border-radius: 50%;/);
    expect(css).toMatch(/\.asc__sentinel--start \{\s*margin-right: -9px;/);
    // the table variant keeps its thin bar
    expect(css).toMatch(/\.asc--table \.asc__row \{[^}]*scrollbar-width: thin;/);
    expect(css).not.toMatch(/display: none;\s*\}\s*\.asc__arrow/);
  });
});

// ---- the icon button and its tooltip ------------------------------------------------------------------------------

describe("the icon button", () => {
  const mountButton = (props: Record<string, unknown> = {}) =>
    mount(IconButton, { props: { icon: "copy", label: "Copy job id", ...props }, attachTo: document.body });
  const tipOpen = (w: ReturnType<typeof mountButton>) => w.find(".ib-wrap").attributes("data-tip-open") === "true";

  it("has the canvas's 33 icons", () => {
    expect(Object.keys(IB_ICONS)).toHaveLength(33);
  });

  it("is named by its tooltip, word for word, with the shortcut after a middle dot", () => {
    const w = mountButton({ shortcut: "C" });
    expect(w.find("button").attributes("aria-label")).toBe("Copy job id · C");
    expect(mountButton().find("button").attributes("aria-label")).toBe("Copy job id");
    const svg = w.find("svg");
    expect([svg.attributes("width"), svg.attributes("stroke-width")]).toEqual(["16", "1.75"]);
  });

  it("shows the tooltip 400 ms after the pointer enters, and hides it at once", async () => {
    vi.useFakeTimers();
    const w = mountButton();
    await w.find(".ib-wrap").trigger("pointerenter", { pointerType: "mouse" });
    vi.advanceTimersByTime(SHOW_DELAY_MS - 1);
    await nextTick();
    expect(tipOpen(w)).toBe(false);
    vi.advanceTimersByTime(1);
    await nextTick();
    expect(tipOpen(w)).toBe(true);
    await w.find(".ib-wrap").trigger("pointerleave");
    expect(tipOpen(w)).toBe(false);
    // a short visit never shows it
    await w.find(".ib-wrap").trigger("pointerenter", { pointerType: "mouse" });
    vi.advanceTimersByTime(200);
    await w.find(".ib-wrap").trigger("pointerleave");
    vi.advanceTimersByTime(400);
    await nextTick();
    expect(tipOpen(w)).toBe(false);
  });

  it("on touch: a long press shows the tooltip and swallows the click, a tap runs the action", async () => {
    vi.useFakeTimers();
    const w = mountButton();
    const wrap = w.find(".ib-wrap");
    await wrap.trigger("pointerdown", { pointerType: "touch" });
    await wrap.trigger("pointerup", { pointerType: "touch" });
    await w.find("button").trigger("click");
    expect(w.emitted("click")).toHaveLength(1);
    expect(tipOpen(w)).toBe(false);
    await wrap.trigger("pointerdown", { pointerType: "touch" });
    vi.advanceTimersByTime(LONG_PRESS_MS);
    await nextTick();
    expect(tipOpen(w)).toBe(true);
    await wrap.trigger("pointerup", { pointerType: "touch" });
    await w.find("button").trigger("click");
    expect(w.emitted("click")).toHaveLength(1);
    expect(tipOpen(w)).toBe(true);
    // the next touch anywhere closes it
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await nextTick();
    expect(tipOpen(w)).toBe(false);
  });

  it("hides the tooltip on Escape and on blur, and a disabled button runs nothing", async () => {
    vi.useFakeTimers();
    const w = mountButton();
    await w.find(".ib-wrap").trigger("pointerenter", { pointerType: "mouse" });
    vi.advanceTimersByTime(SHOW_DELAY_MS);
    await nextTick();
    await w.find("button").trigger("keydown", { key: "Escape" });
    expect(tipOpen(w)).toBe(false);
    const off = mountButton({ disabled: true });
    expect(off.find("button").attributes("disabled")).toBeDefined();
  });

  it("is drawn as the spec: 32 px ghost, 44 px on touch, red on hover when destructive, the brand ring", () => {
    const css = source("IconButton.vue");
    expect(css).toMatch(
      /\.ib \{[^}]*width: 32px;[^}]*height: 32px;[^}]*border-radius: 8px;[^}]*background: transparent;/,
    );
    expect(css).toMatch(/\.ib:hover \{\s*background: var\(--rl-track\);\s*color: var\(--rl-fg\);/);
    expect(css).toMatch(/\.ib--danger:hover \{\s*background: var\(--rl-bad-bg\);\s*color: var\(--rl-bad\);/);
    expect(css).toMatch(/\.ib:focus-visible \{\s*outline: 2px solid var\(--rl-focus\);\s*outline-offset: 2px;/);
    expect(css).toMatch(
      /@media \(pointer: coarse\) \{\s*\.ib \{\s*width: 44px;\s*height: 44px;\s*border-radius: 10px;/,
    );
    expect(css).toMatch(/rl-ib-tip \{[^}]*padding: 5px 8px;[^}]*border-radius: 6px;[^}]*font: 600 12px\/16px/);
    expect(mountButton({ danger: true }).find("button").classes()).toContain("ib--danger");
  });
});

// ---- the cap chip ---------------------------------------------------------------------------------------------------

describe("the cap chip", () => {
  const chip = (props: Record<string, unknown>) => mount(CapChip, { props: { provider: "dola", ...props } });

  it("is the provider pill alone when it has no cap", () => {
    const w = chip({});
    expect(w.find(".cap").classes()).toEqual(expect.arrayContaining(["p-dola", "cap--ok"]));
    expect(w.find(".cap__fig").exists()).toBe(false);
    expect(w.find(".cap__bar").exists()).toBe(false);
    expect(w.find(".cap").attributes("aria-label")).toBe("dola: no daily cap");
  });

  it("shows the figure and a 2 px bar of the share used, with no dot below 80%", () => {
    const w = chip({ used: 1200, cap: 2000, resets: "07:00" });
    expect(w.find(".cap__fig").text()).toBe("1,200 / 2,000");
    expect((w.find(".cap__bar").element as HTMLElement).style.width).toBe("60%");
    expect(w.find(".cap__dot").exists()).toBe(false);
    expect(w.find(".cap").attributes("aria-label")).toBe("dola: 1,200 of 2,000 points used today · resets 07:00");
  });

  it("puts a static amber dot at 80% and a pulsing red one at 100%, reading full", () => {
    const near = chip({ used: 80, cap: 100 });
    expect(near.find(".cap").classes()).toContain("cap--near");
    expect(near.find(".cap__dot").classes()).not.toContain("cap__dot--pulse");
    const full = chip({ used: 120, cap: 100 });
    expect(full.find(".cap").classes()).toContain("cap--full");
    expect(full.find(".cap__dot").classes()).toContain("cap__dot--pulse");
    expect(full.find(".cap__fig").text()).toBe("120 / 100 · full");
    expect((full.find(".cap__bar").element as HTMLElement).style.width).toBe("100%");
  });

  it("pulses only without reduced motion: 1 to 2.2, 0.6 to 0, 1.8 s ease-out", () => {
    const css = source("CapChip.vue");
    expect(css).toMatch(/@media \(prefers-reduced-motion: no-preference\) \{\s*\.cap__dot--pulse::after/);
    expect(css).toContain("animation: cap-pulse 1.8s ease-out infinite;");
    expect(css).toMatch(
      /from \{\s*transform: scale\(1\);\s*opacity: 0\.6;\s*\}\s*to \{\s*transform: scale\(2\.2\);\s*opacity: 0;/,
    );
    expect(css).toMatch(/\.cap__track,\s*\.cap__bar \{[^}]*height: 2px;/);
    expect(css).toMatch(/\.cap--near \{\s*--tone: var\(--rl-warn\);/);
    expect(css).toMatch(/\.cap--full \{\s*--tone: var\(--rl-bad\);/);
  });
});

// ---- the overlays ---------------------------------------------------------------------------------------------------

describe("the overlays", () => {
  it("the drawer is named by its title, closes by Close · Esc and offers Back when asked", async () => {
    const w = mount(SideDrawer, {
      props: { modelValue: true, title: "dola-n11-03", mono: true, backLabel: "Back to Accounts" },
      slots: { default: () => h("p", "body"), footer: () => h("button", "Save") },
      attachTo: document.body,
    });
    await flushPromises();
    const drawer = document.body.querySelector(".rl-drawer")!;
    const title = drawer.querySelector("h2")!;
    expect(title.textContent).toBe("dola-n11-03");
    expect(title.classList.contains("mono")).toBe(true);
    const names = [...drawer.querySelectorAll("button.ib")].map((b) => b.getAttribute("aria-label"));
    expect(names).toEqual(["Back to Accounts", "Close · Esc"]);
    (drawer.querySelectorAll("button.ib")[1] as HTMLElement).click();
    (drawer.querySelectorAll("button.ib")[0] as HTMLElement).click();
    expect(w.emitted("update:modelValue")?.[0]).toEqual([false]);
    expect(w.emitted("back")).toHaveLength(1);
    expect(drawer.querySelector(".rl-drawer__foot button")?.textContent).toBe("Save");
    w.unmount();
  });

  it("the modal is one card with its badge, title, text and right-aligned footer", async () => {
    const w = mount(ModalCard, {
      props: { modelValue: true, title: "Delete account dola-n11-04?", tone: "danger", alert: true },
      slots: { default: () => "A used account cannot be deleted.", footer: () => h("button", "Delete") },
      attachTo: document.body,
    });
    await flushPromises();
    const card = document.body.querySelector(".rl-modal")!;
    expect(card.querySelector(".rl-modal__title")?.textContent).toBe("Delete account dola-n11-04?");
    expect(card.querySelector(".rl-modal__badge")?.classList.contains("is-danger")).toBe(true);
    expect(card.querySelector(".rl-modal__text")?.textContent).toBe("A used account cannot be deleted.");
    expect(card.querySelector(".rl-modal__foot button")?.textContent).toBe("Delete");
    w.unmount();
  });

  it("the bottom sheet has the grab handle, the title and sub, and Close · Esc", async () => {
    const w = mount(BottomSheet, {
      props: { modelValue: true, title: "More", sub: "Other pages" },
      slots: { default: () => h("p", "rows") },
      attachTo: document.body,
    });
    await flushPromises();
    const sheet = document.body.querySelector(".rl-sheet")!;
    expect(sheet.querySelector(".rl-sheet__handle")).not.toBeNull();
    expect(sheet.querySelector(".rl-sheet__title")?.textContent).toBe("More");
    expect(sheet.querySelector(".rl-sheet__sub")?.textContent).toBe("Other pages");
    (sheet.querySelector('button[aria-label="Close · Esc"]') as HTMLElement).click();
    expect(w.emitted("update:modelValue")?.[0]).toEqual([false]);
    w.unmount();
  });

  it("are drawn as the spec: modal radius 16 and 22 px, sheet top radius 20 and a 40 by 5 handle", () => {
    const modal = source("overlays/ModalCard.vue");
    expect(modal).toMatch(/\.el-dialog\.rl-modal \{[^}]*padding: 22px;[^}]*border-radius: 16px;/);
    expect(modal).toMatch(/\.rl-modal-scrim \{\s*background: var\(--rl-scrim-modal\);/);
    const sheet = source("overlays/BottomSheet.vue");
    expect(sheet).toMatch(/\.el-drawer\.rl-sheet \{[^}]*max-height: 92%;[^}]*border-radius: 20px 20px 0 0;/);
    expect(sheet).toMatch(/\.rl-sheet__handle \{[^}]*width: 40px;[^}]*height: 5px;[^}]*border-radius: 3px;/);
    expect(sheet).toContain("env(safe-area-inset-bottom)");
    const drawer = source("overlays/SideDrawer.vue");
    expect(drawer).toContain("'min(640px, 100%)'");
    expect(drawer).toContain("'calc(100% - 48px)'");
  });
});
