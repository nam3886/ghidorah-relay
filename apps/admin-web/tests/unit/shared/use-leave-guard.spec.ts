import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, ref } from "vue";
import { createMemoryHistory, createRouter, RouterView } from "vue-router";
import * as confirm from "../../../src/components/shared/confirm-dialog";
import { askDiscard, cancelPending, useRouteLeaveGuard } from "../../../src/components/shared/use-leave-guard";

vi.mock("../../../src/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
const ask = vi.mocked(confirm.confirmDialog);

// a box the test answers by hand
let answer: (value: boolean) => void = () => undefined;
beforeEach(() => {
  ask.mockReset();
  ask.mockImplementation(() => new Promise<boolean>((resolve) => (answer = resolve)));
});
afterEach(async () => {
  cancelPending();
  await flushPromises();
});

describe("askDiscard", () => {
  it("asks with the discard wording and answers what the box answers", async () => {
    const first = askDiscard();
    expect(ask).toHaveBeenCalledExactlyOnceWith({
      title: "Discard changes?",
      message: "Discard what you typed?",
      confirmText: "Discard",
      cancelText: "Keep editing",
      danger: true,
      closeOnHashChange: false,
    });
    answer(true);
    expect(await first).toBe(true);
    const second = askDiscard("Throw it away?");
    expect(ask).toHaveBeenCalledTimes(2);
    answer(false);
    expect(await second).toBe(false);
  });

  it("while a box is up every further ask is the same promise and no second box opens", async () => {
    const first = askDiscard();
    const second = askDiscard();
    expect(second).toBe(first);
    expect(ask).toHaveBeenCalledTimes(1);
    answer(true);
    await first;
    askDiscard();
    expect(ask).toHaveBeenCalledTimes(2);
  });

  it("cancelPending closes the box as keep even though the box's own promise never settles", async () => {
    const close = vi.spyOn(ElMessageBox, "close").mockImplementation(() => undefined);
    const pending = askDiscard();
    cancelPending();
    expect(close).toHaveBeenCalledTimes(1);
    expect(await pending).toBe(false);
    // the latch is free again
    askDiscard();
    expect(ask).toHaveBeenCalledTimes(2);
  });

  it("cancelPending with no box up does nothing", () => {
    const close = vi.spyOn(ElMessageBox, "close").mockImplementation(() => undefined);
    cancelPending();
    expect(close).not.toHaveBeenCalled();
  });
});

describe("useRouteLeaveGuard", () => {
  const dirty = ref(false);
  const blocked = ref(false);
  async function pages() {
    const Guarded = defineComponent({
      setup() {
        useRouteLeaveGuard(
          () => dirty.value,
          () => blocked.value,
        );
        return () => h("p", "form");
      },
    });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/form", component: Guarded },
        { path: "/other", component: { render: () => h("p", "other") } },
      ],
    });
    await router.push("/form");
    await router.isReady();
    const w = mount({ render: () => h(RouterView) }, { global: { plugins: [router] } });
    await flushPromises();
    return { router, w };
  }
  beforeEach(() => {
    dirty.value = false;
    blocked.value = false;
  });

  it("while a write is in flight the page is not left, with no question, clean or dirty", async () => {
    const { router, w } = await pages();
    blocked.value = true;
    await router.push("/other");
    expect(router.currentRoute.value.path).toBe("/form");
    dirty.value = true;
    await router.push("/other");
    expect(router.currentRoute.value.path).toBe("/form");
    expect(ask).not.toHaveBeenCalled();
    blocked.value = false;
    dirty.value = false;
    await router.push("/other");
    expect(router.currentRoute.value.path).toBe("/other");
    w.unmount();
  });

  it("a reload is warned about while a write is in flight, though nothing is dirty", async () => {
    const { w } = await pages();
    const warned = () => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    expect(warned()).toBe(false);
    blocked.value = true;
    await flushPromises();
    expect(warned()).toBe(true);
    blocked.value = false;
    await flushPromises();
    expect(warned()).toBe(false);
    w.unmount();
  });

  it("a clean form leaves without asking", async () => {
    const { router, w } = await pages();
    await router.push("/other");
    expect(router.currentRoute.value.path).toBe("/other");
    expect(ask).not.toHaveBeenCalled();
    w.unmount();
  });

  it("a dirty form asks; keep stays, discard leaves", async () => {
    const { router, w } = await pages();
    dirty.value = true;
    const stay = router.push("/other");
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(1);
    answer(false);
    await stay;
    expect(router.currentRoute.value.path).toBe("/form");
    const go = router.push("/other");
    await flushPromises();
    answer(true);
    await go;
    expect(router.currentRoute.value.path).toBe("/other");
    w.unmount();
  });

  it("a reload or a closed tab is warned about while dirty, and only then", async () => {
    const { w } = await pages();
    const warned = () => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    expect(warned()).toBe(false);
    dirty.value = true;
    await flushPromises();
    expect(warned()).toBe(true);
    dirty.value = false;
    await flushPromises();
    expect(warned()).toBe(false);
    dirty.value = true;
    await flushPromises();
    w.unmount();
    expect(warned()).toBe(false);
  });
});
