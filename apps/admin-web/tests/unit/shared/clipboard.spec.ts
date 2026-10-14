import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { copyText } from "../../../src/components/shared/clipboard";

// The three copy paths: the clipboard API on a secure context, a hidden textarea and execCommand on plain http (the
// LAN case), and the last resort that selects the text for the user.

function setSecure(value: boolean, writeText?: (t: string) => Promise<void>) {
  Object.defineProperty(window, "isSecureContext", { value, configurable: true });
  Object.defineProperty(navigator, "clipboard", { value: writeText ? { writeText } : undefined, configurable: true });
}

let exec: ReturnType<typeof vi.fn>;

beforeEach(() => {
  exec = vi.fn(() => true);
  Object.defineProperty(document, "execCommand", { value: exec, configurable: true, writable: true });
  document.body.replaceChildren();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("copyText abort and scrub", () => {
  it("when aborted while the clipboard API is pending, a rejection does not start the fallback", async () => {
    let reject: (e: unknown) => void = () => {};
    setSecure(
      true,
      () =>
        new Promise<void>((_, r) => {
          reject = r;
        }),
    );
    const abort = new AbortController();
    const pending = copyText("dismissed-secret", undefined, abort.signal);
    abort.abort();
    reject(new DOMException("denied", "NotAllowedError"));
    expect(await pending).toBe("aborted");
    expect(exec).not.toHaveBeenCalled();
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("an already aborted signal does nothing at all", async () => {
    const writeText = vi.fn(async () => undefined);
    setSecure(true, writeText);
    const abort = new AbortController();
    abort.abort();
    expect(await copyText("x", undefined, abort.signal)).toBe("aborted");
    expect(writeText).not.toHaveBeenCalled();
    expect(exec).not.toHaveBeenCalled();
  });

  it("scrubs the fallback textarea: empty value, detached, no selection left", async () => {
    setSecure(false);
    // something is selected on the page when the copy ends (a real browser selects the textarea's own text)
    const holder = document.createElement("div");
    holder.textContent = "secret-text";
    document.body.appendChild(holder);
    window.getSelection()?.selectAllChildren(holder);
    expect(window.getSelection()?.toString()).toBe("secret-text");
    let area: HTMLTextAreaElement | null = null;
    exec.mockImplementation(() => {
      area = document.querySelector("textarea");
      return true;
    });
    await copyText("secret-text");
    const kept = area as unknown as HTMLTextAreaElement;
    expect(kept).not.toBeNull();
    expect(kept.isConnected).toBe(false);
    expect(kept.value).toBe("");
    expect(window.getSelection()?.toString()).toBe("");
  });

  it("scrubs the textarea even when execCommand throws", async () => {
    setSecure(false);
    let area: HTMLTextAreaElement | null = null;
    exec.mockImplementation(() => {
      area = document.querySelector("textarea");
      throw new Error("not allowed");
    });
    await copyText("secret-text");
    const kept = area as unknown as HTMLTextAreaElement;
    expect(kept.isConnected).toBe(false);
    expect(kept.value).toBe("");
  });
});

describe("copyText", () => {
  it("uses the clipboard API on a secure context and nothing else", async () => {
    const writeText = vi.fn(async () => undefined);
    setSecure(true, writeText);
    expect(await copyText("secret-1")).toBe("clipboard");
    expect(writeText).toHaveBeenCalledWith("secret-1");
    expect(exec).not.toHaveBeenCalled();
  });

  it("on plain http copies through a hidden textarea and execCommand, then removes it", async () => {
    const writeText = vi.fn(async () => undefined);
    setSecure(false, writeText);
    let seen = "";
    exec.mockImplementation(() => {
      const area = document.querySelector("textarea");
      seen = area ? area.value : "(no textarea)";
      return true;
    });
    expect(await copyText("lan-text")).toBe("exec");
    expect(exec).toHaveBeenCalledWith("copy");
    expect(seen).toBe("lan-text");
    expect(writeText).not.toHaveBeenCalled();
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("hides the scratch element with a CSS class and never sets a style string itself", async () => {
    // happy-dom turns any CSSOM write into a style attribute, so the real-browser CSP fixture is the authority for
    // "no style attribute"; here: our code positions the element by its class and writes no style property.
    setSecure(false);
    let area: HTMLTextAreaElement | null = null;
    exec.mockImplementation(() => {
      area = document.querySelector("textarea");
      return true;
    });
    await copyText("x");
    expect(area).not.toBeNull();
    expect((area as unknown as HTMLTextAreaElement).className).toBe("clipboard-scratch");
    expect((area as unknown as HTMLTextAreaElement).getAttribute("style")).toBeNull();
  });

  it("falls back to execCommand when the clipboard API refuses", async () => {
    setSecure(true, async () => {
      throw new DOMException("denied", "NotAllowedError");
    });
    expect(await copyText("t")).toBe("exec");
    expect(exec).toHaveBeenCalledTimes(1);
  });

  it("gives the focus back to the element that had it", async () => {
    setSecure(false);
    const button = document.createElement("button");
    document.body.appendChild(button);
    button.focus();
    const focus = vi.spyOn(button, "focus");
    await copyText("t");
    expect(focus).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(button);
  });

  it("last resort: selects the text of the given element and says so", async () => {
    setSecure(false);
    exec.mockReturnValue(false);
    const box = document.createElement("div");
    box.textContent = "pick me";
    document.body.appendChild(box);
    expect(await copyText("pick me", () => box)).toBe("select");
    expect(window.getSelection()?.toString()).toBe("pick me");
  });

  it("last resort on an input selects its value", async () => {
    setSecure(false);
    exec.mockReturnValue(false);
    const input = document.createElement("input");
    input.value = "in a field";
    document.body.appendChild(input);
    expect(await copyText("in a field", () => input)).toBe("select");
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe("in a field".length);
  });

  it("treats a throwing execCommand as a failed copy, not a crash", async () => {
    setSecure(false);
    exec.mockImplementation(() => {
      throw new Error("not allowed");
    });
    const box = document.createElement("div");
    document.body.appendChild(box);
    expect(await copyText("t", () => box)).toBe("select");
  });

  it("reports failure when nothing worked and there is nothing to select", async () => {
    setSecure(false);
    exec.mockReturnValue(false);
    expect(await copyText("t")).toBe("failed");
    expect(await copyText("t", () => null)).toBe("failed");
  });
});
