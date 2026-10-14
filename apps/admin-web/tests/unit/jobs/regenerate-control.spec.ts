import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import RegenerateControl from "@/components/jobs/RegenerateControl.vue";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { RETRY_SAFE_TEXT } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { abandonOperation } from "@/lib/operation-key";

vi.mock("@/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
// The real module with the abandon spy-able: a test can make it refuse.
vi.mock("@/lib/operation-key", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/operation-key")>();
  return { ...actual, abandonOperation: vi.fn(actual.abandonOperation) };
});

const STORE = "regenerateOperations";
const KEPT = "k".repeat(16);
const WARNING =
  "The earlier attempt may already have made jobs. Starting a new one can charge again. Check the Jobs list first.";
const CONFIRM = { title: "Regenerate", message: "Sure?", confirmText: "Go" };

const mounted: VueWrapper[] = [];
afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount();
});
beforeEach(() => {
  sessionStorage.clear();
  vi.mocked(abandonOperation).mockClear();
  vi.mocked(confirmDialog).mockReset().mockResolvedValue(true);
  for (const fn of Object.values(notify)) vi.mocked(fn).mockReset();
});

function control(perform: () => Promise<void>, action = "job:j1", extra: Record<string, unknown> = {}) {
  const w = mount(RegenerateControl, {
    props: { action, label: "Regenerate", confirm: CONFIRM, perform, ...extra },
    attachTo: document.body,
  });
  mounted.push(w);
  return w;
}
const buttons = (w: VueWrapper) => w.findAll("button");
const main = (w: VueWrapper) => buttons(w).find((b) => b.text() === "Regenerate")!;
const startNew = (w: VueWrapper) => buttons(w).find((b) => b.text() === "Start a new regenerate");
const keep = (action = "job:j1") => sessionStorage.setItem(STORE, JSON.stringify({ [action]: KEPT }));
const stored = () => JSON.parse(sessionStorage.getItem(STORE) ?? "{}") as Record<string, string>;

function deferred() {
  let resolve!: () => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<void>((res, rej) => ((resolve = res), (reject = rej)));
  return { promise, resolve, reject };
}

describe("regenerate control", () => {
  it("asks first with the text it was given, and Cancel sends nothing", async () => {
    const perform = vi.fn(() => Promise.resolve());
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    const w = control(perform);
    await main(w).trigger("click");
    await flushPromises();
    expect(confirmDialog).toHaveBeenCalledWith(CONFIRM);
    expect(perform).not.toHaveBeenCalled();
  });

  it("runs the call once after a yes", async () => {
    const perform = vi.fn(() => Promise.resolve());
    const w = control(perform);
    await main(w).trigger("click");
    await flushPromises();
    expect(perform).toHaveBeenCalledTimes(1);
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("is off while the call is in flight, so a second click sends nothing", async () => {
    const flight = deferred();
    const perform = vi.fn(() => flight.promise);
    const w = control(perform);
    await main(w).trigger("click");
    await flushPromises();
    expect(main(w).attributes("disabled")).toBeDefined();
    await main(w).trigger("click");
    await flushPromises();
    expect(perform).toHaveBeenCalledTimes(1);
    flight.resolve();
    await flushPromises();
    expect(main(w).attributes("disabled")).toBeUndefined();
  });

  it("is off when the caller says so (nothing is ticked)", async () => {
    const w = control(vi.fn(), "bulk:", { disabled: true });
    expect(main(w).attributes("disabled")).toBeDefined();
  });

  it("shows a failed call as a toast and gives the button back", async () => {
    const err = new ApiError(503, {});
    err.retrySafe = true;
    const w = control(() => Promise.reject(err));
    await main(w).trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith(RETRY_SAFE_TEXT);
    expect(main(w).attributes("disabled")).toBeUndefined();
  });
});

describe("start a new regenerate", () => {
  it("is not offered while no key is kept", () => {
    expect(startNew(control(vi.fn()))).toBeUndefined();
  });

  it("is offered once a key is kept for this action", () => {
    keep();
    expect(startNew(control(vi.fn()))).toBeDefined();
  });

  it("is not offered for a stored value the server would refuse", () => {
    sessionStorage.setItem(STORE, JSON.stringify({ "job:j1": "short" }));
    expect(startNew(control(vi.fn()))).toBeUndefined();
  });

  it("is not offered for another action's key", () => {
    keep("job:other");
    expect(startNew(control(vi.fn()))).toBeUndefined();
  });

  it("follows the action when the selection changes", async () => {
    keep("bulk:a,b");
    const w = control(vi.fn(), "bulk:a,b");
    expect(startNew(w)).toBeDefined();
    await w.setProps({ action: "bulk:a" });
    expect(startNew(w)).toBeUndefined();
    await w.setProps({ action: "bulk:a,b" });
    expect(startNew(w)).toBeDefined();
  });

  it("appears after a call that failed and kept its key", async () => {
    const w = control(() => {
      keep();
      return Promise.reject(new ApiError(503, {}));
    });
    expect(startNew(w)).toBeUndefined();
    await main(w).trigger("click");
    await flushPromises();
    expect(startNew(w)).toBeDefined();
  });

  it("is hidden while a call is in flight", async () => {
    keep();
    const flight = deferred();
    const w = control(() => flight.promise);
    expect(startNew(w)).toBeDefined();
    await main(w).trigger("click");
    await flushPromises();
    expect(startNew(w)).toBeUndefined();
    flight.resolve();
    await flushPromises();
    expect(startNew(w)).toBeDefined();
  });

  it("warns of the double charge, and Cancel keeps the key", async () => {
    keep();
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    const w = control(vi.fn());
    await startNew(w)!.trigger("click");
    await flushPromises();
    expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ message: WARNING, danger: true }));
    expect(abandonOperation).not.toHaveBeenCalled();
    expect(stored()).toEqual({ "job:j1": KEPT });
    expect(startNew(w)).toBeDefined();
  });

  it("after a yes the kept key is gone, the next regenerate starts fresh, and it says so", async () => {
    sessionStorage.setItem(STORE, JSON.stringify({ "job:j1": KEPT, "job:j2": KEPT }));
    const w = control(vi.fn());
    await startNew(w)!.trigger("click");
    await flushPromises();
    expect(abandonOperation).toHaveBeenCalledWith("job:j1");
    expect(stored()).toEqual({ "job:j2": KEPT });
    expect(startNew(w)).toBeUndefined();
    expect(notify.info).toHaveBeenCalledWith("Done. The next Regenerate starts a new attempt.");
  });

  it("tells the owner when the key could not be dropped, and keeps offering it", async () => {
    keep();
    vi.mocked(abandonOperation).mockReturnValueOnce(false);
    const w = control(vi.fn());
    await startNew(w)!.trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("That regenerate is still running. Wait for its answer first.");
    expect(notify.info).not.toHaveBeenCalled();
    expect(startNew(w)).toBeDefined();
  });
});
