import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CopyButton from "../../../src/components/shared/CopyButton.vue";
import CursorPager from "../../../src/components/shared/CursorPager.vue";
import JsonBlock from "../../../src/components/shared/JsonBlock.vue";
import ListToolbar from "../../../src/components/shared/ListToolbar.vue";
import OffsetPager from "../../../src/components/shared/OffsetPager.vue";
import StatusChip from "../../../src/components/shared/StatusChip.vue";

describe("StatusChip", () => {
  it("shows the word with a dot and the tone of the registry", () => {
    const w = mount(StatusChip, { props: { status: "failed" } });
    expect(w.text()).toBe("failed");
    expect(w.attributes("data-tone")).toBe("bad");
    expect(w.classes()).toContain("tone-bad");
    expect(w.find("i.dot").exists()).toBe(true);
  });

  it("is an el-tag whose type follows the tone", () => {
    const type = (tone: "ok" | "info" | "warn" | "bad" | "neutral") =>
      mount(StatusChip, { props: { status: "x", tone } }).classes();
    expect(type("ok")).toContain("el-tag--success");
    expect(type("warn")).toContain("el-tag--warning");
    expect(type("bad")).toContain("el-tag--danger");
    expect(type("info")).toContain("el-tag--info");
    expect(type("neutral")).toContain("tone-neutral");
  });

  it("a number is an HTTP code", () => {
    expect(mount(StatusChip, { props: { status: 503 } }).attributes("data-tone")).toBe("bad");
    expect(mount(StatusChip, { props: { status: 404 } }).attributes("data-tone")).toBe("warn");
    expect(mount(StatusChip, { props: { status: 200 } }).text()).toBe("200");
  });

  it("label and tone replace the word and the lookup", () => {
    const w = mount(StatusChip, { props: { status: "done", label: "Finished", tone: "warn" } });
    expect(w.text()).toBe("Finished");
    expect(w.attributes("data-tone")).toBe("warn");
  });

  it("a word it does not know is neutral, and markup in it is text", () => {
    const w = mount(StatusChip, { props: { status: "<b>x</b>" } });
    expect(w.attributes("data-tone")).toBe("neutral");
    expect(w.find("b").exists()).toBe(false);
  });
});

describe("OffsetPager", () => {
  const pager = (offset: number, total: number, limit = 50) => mount(OffsetPager, { props: { offset, limit, total } });
  const buttons = (w: ReturnType<typeof pager>) => w.findAll("button");

  it("shows from-to of total", () => {
    expect(pager(0, 120).text()).toContain("1-50 of 120");
    expect(pager(100, 120).text()).toContain("101-120 of 120");
    expect(pager(0, 0).text()).toContain("0-0 of 0");
  });

  it("Prev is disabled on the first page and Next on the last", () => {
    expect(buttons(pager(0, 120))[0]!.attributes("disabled")).toBeDefined();
    expect(buttons(pager(0, 120))[1]!.attributes("disabled")).toBeUndefined();
    expect(buttons(pager(100, 120))[1]!.attributes("disabled")).toBeDefined();
    expect(buttons(pager(50, 100))[1]!.attributes("disabled")).toBeDefined();
    expect(buttons(pager(0, 0))[1]!.attributes("disabled")).toBeDefined();
  });

  it("moves by one page and never below zero", async () => {
    const w = pager(50, 200);
    await buttons(w)[1]!.trigger("click");
    await buttons(w)[0]!.trigger("click");
    expect(w.emitted("move")).toEqual([[100], [0]]);
    const near = pager(20, 200);
    await buttons(near)[0]!.trigger("click");
    expect(near.emitted("move")).toEqual([[0]]);
  });
});

describe("CursorPager", () => {
  it("shows the page, disables Prev on page 1 and Next without a next cursor", () => {
    const first = mount(CursorPager, { props: { page: 1, hasNext: true } });
    expect(first.text()).toContain("Page 1");
    expect(first.findAll("button")[0]!.attributes("disabled")).toBeDefined();
    expect(first.findAll("button")[1]!.attributes("disabled")).toBeUndefined();
    const last = mount(CursorPager, { props: { page: 3, hasNext: false } });
    expect(last.findAll("button")[0]!.attributes("disabled")).toBeUndefined();
    expect(last.findAll("button")[1]!.attributes("disabled")).toBeDefined();
  });

  it("reports Prev and Next", async () => {
    const w = mount(CursorPager, { props: { page: 2, hasNext: true } });
    await w.findAll("button")[0]!.trigger("click");
    await w.findAll("button")[1]!.trigger("click");
    expect(w.emitted("prev")).toHaveLength(1);
    expect(w.emitted("next")).toHaveLength(1);
  });
});

describe("ListToolbar", () => {
  it("Refresh emits, and Updated shows the time of the last load", async () => {
    const at = new Date(2026, 9, 14, 9, 5, 7).getTime();
    const w = mount(ListToolbar, { props: { updatedAt: at }, slots: { default: '<span class="filter">f</span>' } });
    expect(w.text()).toContain("Updated 09:05:07");
    expect(w.find(".filter").exists()).toBe(true);
    await w.find("button").trigger("click");
    expect(w.emitted("refresh")).toHaveLength(1);
  });

  it("shows no Updated text before the first load", () => {
    expect(mount(ListToolbar).text()).not.toContain("Updated");
  });

  it("the auto-refresh switch exists only when the view gives a value", async () => {
    expect(mount(ListToolbar).find(".el-switch").exists()).toBe(false);
    const w = mount(ListToolbar, { props: { autoRefresh: false } });
    expect(w.find(".el-switch").exists()).toBe(true);
    await w.find(".el-switch").trigger("click");
    expect(w.emitted("update:autoRefresh")).toEqual([[true]]);
  });

  it("a loading Refresh button is busy and does not fire", async () => {
    const w = mount(ListToolbar, { props: { loading: true } });
    await w.find("button").trigger("click");
    expect(w.emitted("refresh")).toBeUndefined();
  });
});

describe("JsonBlock", () => {
  it("pretty-prints an object", () => {
    const w = mount(JsonBlock, { props: { value: { a: 1, b: [true] } } });
    expect(w.find("pre").text()).toBe(JSON.stringify({ a: 1, b: [true] }, null, 2));
  });

  it("shows a string as it is, nothing for undefined, and survives a value JSON cannot hold", () => {
    expect(
      mount(JsonBlock, { props: { value: '{"already":"text"}' } })
        .find("pre")
        .text(),
    ).toBe('{"already":"text"}');
    expect(
      mount(JsonBlock, { props: { value: undefined } })
        .find("pre")
        .text(),
    ).toBe("");
    const loop: Record<string, unknown> = {};
    loop.self = loop;
    expect(() => mount(JsonBlock, { props: { value: loop } })).not.toThrow();
  });

  it("planted markup is text", () => {
    const w = mount(JsonBlock, { props: { value: { title: "<img src=x onerror=alert(1)>" } } });
    expect(w.find("img").exists()).toBe(false);
    expect(w.find("pre").text()).toContain("<img src=x onerror=alert(1)>");
  });

  it("has a Copy button unless switched off", () => {
    expect(mount(JsonBlock, { props: { value: {} } }).text()).toContain("Copy");
    expect(mount(JsonBlock, { props: { value: {}, copy: false } }).text()).not.toContain("Copy");
  });
});

describe("CopyButton", () => {
  const writeText = vi.fn(async () => undefined);
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    Object.defineProperty(document, "execCommand", { value: vi.fn(() => false), configurable: true, writable: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    writeText.mockClear();
    document.body.replaceChildren();
  });

  it("a copy still pending when the button unmounts leaves no textarea, no toast and no event", async () => {
    let reject: (e: unknown) => void = () => {};
    const exec = vi.fn(() => true);
    Object.defineProperty(document, "execCommand", { value: exec, configurable: true, writable: true });
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: () =>
          new Promise<void>((_, r) => {
            reject = r;
          }),
      },
      configurable: true,
    });
    const w = mount(CopyButton, { props: { text: "dismissed-secret" }, attachTo: document.body });
    await w.find("button").trigger("click");
    w.unmount();
    reject(new DOMException("denied", "NotAllowedError"));
    await flushPromises();
    expect(exec).not.toHaveBeenCalled();
    expect(document.querySelector("textarea")).toBeNull();
    expect(w.emitted("copied")).toBeUndefined();
    expect(document.body.textContent).not.toContain("Could not copy");
    expect(document.body.textContent).not.toContain("Press Ctrl+C");
  });

  it("copies the text, says Copied for two seconds, then goes back to its label", async () => {
    const w = mount(CopyButton, { props: { text: "abc" } });
    await w.find("button").trigger("click");
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith("abc");
    expect(w.text()).toBe("Copied");
    expect(w.emitted("copied")).toEqual([["clipboard"]]);
    vi.advanceTimersByTime(1999);
    await flushPromises();
    expect(w.text()).toBe("Copied");
    vi.advanceTimersByTime(1);
    await flushPromises();
    expect(w.text()).toBe("Copy");
  });

  it("reads a text function at click time and honours a custom label", async () => {
    let current = "one";
    const w = mount(CopyButton, { props: { text: () => current, label: "Copy key" } });
    expect(w.text()).toBe("Copy key");
    current = "two";
    await w.find("button").trigger("click");
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith("two");
  });

  it("on a total failure it does not say Copied and it toasts", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    const w = mount(CopyButton, { props: { text: "abc" } });
    await w.find("button").trigger("click");
    await flushPromises();
    expect(w.text()).toBe("Copy");
    expect(w.emitted("copied")).toEqual([["failed"]]);
    expect(document.body.textContent).toContain("Could not copy");
  });

  it("the last resort selects the text and toasts the hint, without saying Copied", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    const holder = document.createElement("div");
    holder.textContent = "abc";
    document.body.appendChild(holder);
    const w = mount(CopyButton, { props: { text: "abc", selectFrom: () => holder } });
    await w.find("button").trigger("click");
    await flushPromises();
    expect(w.text()).toBe("Copy");
    expect(w.emitted("copied")).toEqual([["select"]]);
    expect(window.getSelection()?.toString()).toBe("abc");
    expect(document.body.textContent).toContain("Press Ctrl+C");
  });
});
