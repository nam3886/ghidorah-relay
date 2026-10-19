import { mount } from "@vue/test-utils";
import { defineComponent, nextTick, type VNode } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isPhoneNow, makeNotify, syncToastLift } from "../../../src/lib/notify";
import { readFileSync } from "node:fs";

// the stylesheet as written (the test runner does not hand a css import back as text)
const css = readFileSync("src/lib/notify.css", "utf8");

const PLANTED = "<img src=x onerror=alert(1)><b>x</b>";

// Element Plus answers a toast with a handle that closes it; the stand-in hands one out per call
function stub() {
  const close = vi.fn();
  const message = Object.assign(
    vi.fn((_options: object) => ({ close })),
    { closeAll: vi.fn() },
  );
  return { message, close, notify: makeNotify(message) };
}
const lastOptions = (message: ReturnType<typeof stub>["message"]) =>
  message.mock.calls[message.mock.calls.length - 1]![0] as Record<string, unknown>;
// the toast's body, drawn as Element Plus draws its default slot
const body = (options: Record<string, unknown>) =>
  mount(defineComponent({ render: () => (options.message as () => VNode)() }));

function width(phone: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: phone && /max-width:\s*767/.test(q) }));
}
afterEach(() => vi.unstubAllGlobals());

describe("notify", () => {
  it("an error stays 5 s and has a close button; a success closes by itself after 2.5 s without one", () => {
    const { message, notify } = stub();
    notify.error("Check failed");
    expect(lastOptions(message)).toMatchObject({
      type: "error",
      duration: 5000,
      showClose: true,
    });
    expect(body(lastOptions(message)).text()).toBe("Check failed");
    notify.success("Saved");
    expect(lastOptions(message)).toMatchObject({ type: "success", duration: 2500 });
    expect(lastOptions(message).showClose).toBeUndefined();
    notify.info("FYI");
    expect(lastOptions(message)).toMatchObject({ type: "info", duration: 2500 });
  });

  it("toasts stack at the bottom right and equal repeats group", () => {
    const { message, notify } = stub();
    width(false);
    for (const send of [notify.success, notify.error, notify.info]) {
      send("x");
      expect(lastOptions(message)).toMatchObject({ placement: "bottom-right", grouping: true });
      expect(lastOptions(message).offset).toBeUndefined();
    }
  });

  describe("a toast above a drawer footer", () => {
    // happy-dom lays nothing out: each footer is given the rects and styles a browser would give it
    type Over = { height?: number; top?: number; rects?: number; display?: string; parentClass?: string };
    function footer(over: Over = {}) {
      const parent = document.createElement("div");
      parent.className = over.parentClass ?? "";
      const foot = document.createElement("div");
      foot.className = "detail-drawer__foot";
      const set = (height: number) => {
        foot.getBoundingClientRect = () =>
          ({
            height,
            width: height ? 600 : 0,
            top: over.top ?? 600,
            bottom: (over.top ?? 600) + height,
            left: 100,
            right: 700,
          }) as DOMRect;
      };
      set(over.height ?? 57);
      foot.getClientRects = () => Array.from({ length: over.rects ?? 1 }) as unknown as DOMRectList;
      if (over.display) foot.style.display = over.display;
      parent.append(foot);
      document.body.append(parent);
      return { parent, foot, set };
    }
    const lift = () => {
      syncToastLift();
      return document.documentElement.style.getPropertyValue("--rl-toast-lift");
    };
    afterEach(() => {
      document.body.replaceChildren();
      syncToastLift();
    });

    it("lifts by the footer's height when a drawer opens, and is back at 0 when it closes", () => {
      width(false);
      expect(lift()).toBe("0px");
      const { parent } = footer({ height: 57 });
      expect(lift()).toBe("57px");
      parent.remove();
      expect(lift()).toBe("0px");
    });

    it("lifts a toast of every kind the same way and moves none of them: only the page-wide lift changes", () => {
      const { message, notify } = stub();
      width(false);
      notify.success("Saved");
      notify.error("Failed");
      const sent = () =>
        message.mock.calls.map((c) => JSON.stringify(c[0], (_k, v) => (typeof v === "function" ? 0 : v)));
      const before = sent();
      footer({ height: 57 });
      expect(lift()).toBe("57px");
      // the same two calls, as they were sent: nothing was changed, re-sent or closed when the footer appeared
      expect(message).toHaveBeenCalledTimes(2);
      expect(sent()).toEqual(before);
      expect(before).toHaveLength(2);
      // a toast raised with a footer drawn since the last look measures it at once, and gets no offset of its own
      document.body.replaceChildren();
      syncToastLift();
      footer({ height: 65 });
      notify.error("Failed again");
      expect(document.documentElement.style.getPropertyValue("--rl-toast-lift")).toBe("65px");
      expect(lastOptions(message).offset).toBeUndefined();
    });

    it("follows a footer that changes height", () => {
      width(true);
      const { set } = footer({ height: 60 });
      expect(lift()).toBe("4px");
      set(117);
      expect(lift()).toBe("61px");
    });

    it("skips a hidden first footer and measures the visible second one", () => {
      width(false);
      footer({ height: 30, rects: 0 });
      footer({ height: 57, display: "none" });
      footer({ height: 65 });
      expect(lift()).toBe("65px");
    });

    it("keeps 0 when no footer is drawn, or one lies outside the window", () => {
      width(false);
      expect(lift()).toBe("0px");
      footer({ rects: 0 });
      footer({ display: "none" });
      footer({ top: 5000 });
      expect(lift()).toBe("0px");
    });

    it("on a phone only what the footer needs beyond the 72 px its toasts already sit at", () => {
      width(true);
      footer({ height: 117 });
      expect(lift()).toBe("61px");
      document.body.replaceChildren();
      footer({ height: 40 });
      expect(lift()).toBe("0px");
    });

    it("ignores the footer of a drawer that is leaving", () => {
      width(false);
      footer({ parentClass: "el-drawer-fade-leave-active" });
      expect(lift()).toBe("0px");
      footer({ height: 57 });
      expect(lift()).toBe("57px");
    });

    it("the slide is a translate of that lift, and it is off for a reader who asks for less motion", () => {
      expect(css).toContain("translate: 0 calc(var(--rl-toast-lift, 0px) * -1);");
      expect(css).toMatch(/translate 0\.25s ease;/);
      const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
      expect(reduced).toContain("html .el-message");
      expect(reduced).not.toContain("translate 0.25s");
    });
  });

  it("on a phone they sit bottom centre, lifted above the bulk bar", () => {
    const { message, notify } = stub();
    width(true);
    for (const send of [notify.success, notify.error, notify.info]) {
      send("x");
      expect(lastOptions(message)).toMatchObject({ placement: "bottom", offset: 72, grouping: true });
    }
  });

  it("the text goes through as a plain string: HTML is never switched on", () => {
    const { message, notify } = stub();
    notify.error(PLANTED);
    expect(lastOptions(message).dangerouslyUseHTMLString).toBeUndefined();
    const drawn = body(lastOptions(message));
    expect(drawn.find(".rl-toast__text").text()).toBe(PLANTED);
    expect(drawn.find("img").exists()).toBe(false);
    expect(drawn.find("b").exists()).toBe(false);
  });

  it("draws the body as B-States: the text, an X close button on an error only", async () => {
    const { message, close, notify } = stub();
    notify.success("Saved");
    const saved = body(lastOptions(message));
    expect(saved.find(".rl-toast__text").text()).toBe("Saved");
    expect(saved.find(".rl-toast__count").exists()).toBe(false);
    expect(saved.find("button").exists()).toBe(false);
    notify.error("Check failed: connect timeout after 8 s");
    const failed = body(lastOptions(message));
    const x = failed.get('button[aria-label="Close message"]');
    expect(x.find("svg path").attributes("d")).toBe("M18 6 6 18M6 6l12 12");
    expect(x.find("svg").attributes("width")).toBe("14");
    expect(x.find("svg").attributes("stroke-width")).toBe("2");
    await x.trigger("click");
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("a repeat while the toast is open is the same body, counted in a ×N pill; a closed toast starts again", async () => {
    const { message, notify } = stub();
    notify.success("Saved");
    const first = lastOptions(message);
    const drawn = body(first);
    notify.success("Saved");
    notify.success("Saved");
    // Element Plus groups a repeat by the identical message: the body must be the same one
    expect(lastOptions(message).message).toBe(first.message);
    await nextTick();
    expect(drawn.find(".rl-toast__count").text()).toBe("×3");
    notify.success("Another");
    expect(lastOptions(message).message).not.toBe(first.message);
    notify.error("Saved");
    expect(lastOptions(message).message).not.toBe(first.message);
    (first.onClose as () => void)();
    notify.success("Saved");
    expect(lastOptions(message).message).not.toBe(first.message);
    expect(body(lastOptions(message)).find(".rl-toast__count").exists()).toBe(false);
  });

  it("clear closes every toast", () => {
    const { message, notify } = stub();
    notify.clear();
    expect(message.closeAll).toHaveBeenCalledTimes(1);
  });

  it("the same words right after clear are a new toast, which the old one's late close leaves alone", () => {
    const { message, notify } = stub();
    notify.success("Saved");
    const closing = lastOptions(message);
    notify.clear();
    // the closed toast is still leaving: the same words must not group into it
    notify.success("Saved");
    const fresh = lastOptions(message);
    expect(fresh.message).not.toBe(closing.message);
    // the old toast finishes leaving: the new one keeps its body, and a repeat still groups into it
    (closing.onClose as () => void)();
    notify.success("Saved");
    expect(lastOptions(message).message).toBe(fresh.message);
  });

  it("the same error right after a click on its X is a new toast, which the old one's late close leaves alone", async () => {
    const { message, close, notify } = stub();
    notify.error("Check failed");
    const closing = lastOptions(message);
    await body(closing).get('button[aria-label="Close message"]').trigger("click");
    expect(close).toHaveBeenCalledTimes(1);
    // the clicked toast is still leaving: the same error must not count into it
    notify.error("Check failed");
    const fresh = lastOptions(message);
    expect(fresh.message).not.toBe(closing.message);
    expect(body(fresh).find(".rl-toast__count").exists()).toBe(false);
    // the old toast finishes leaving: the new one keeps its body, and a repeat still groups into it
    (closing.onClose as () => void)();
    notify.error("Check failed");
    expect(lastOptions(message).message).toBe(fresh.message);
  });
});

describe("isPhoneNow", () => {
  it("asks the phone edge; without matchMedia it is desktop", () => {
    expect(isPhoneNow()).toBe(false);
    const seen: string[] = [];
    vi.stubGlobal("matchMedia", (q: string) => {
      seen.push(q);
      return { matches: true };
    });
    expect(isPhoneNow()).toBe(true);
    expect(seen[0]).toMatch(/max-width:\s*767/);
    width(false);
    expect(isPhoneNow()).toBe(false);
  });
});
