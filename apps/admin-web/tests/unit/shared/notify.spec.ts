import { afterEach, describe, expect, it, vi } from "vitest";
import { isPhoneNow, makeNotify } from "../../../src/lib/notify";

const PLANTED = "<img src=x onerror=alert(1)><b>x</b>";

function stub() {
  const message = Object.assign(vi.fn(), { closeAll: vi.fn() });
  return { message, notify: makeNotify(message) };
}
const lastOptions = (message: ReturnType<typeof stub>["message"]) =>
  message.mock.calls[message.mock.calls.length - 1]![0] as Record<string, unknown>;

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
      message: "Check failed",
      duration: 5000,
      showClose: true,
    });
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
    expect(lastOptions(message).message).toBe(PLANTED);
    expect(lastOptions(message).dangerouslyUseHTMLString).toBeUndefined();
  });

  it("clear closes every toast", () => {
    const { message, notify } = stub();
    notify.clear();
    expect(message.closeAll).toHaveBeenCalledTimes(1);
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
