import { describe, expect, it, vi } from "vitest";
import { confirmDialog } from "../../../src/components/shared/confirm-dialog";

function box(outcome: "resolve" | unknown) {
  return { confirm: vi.fn(() => (outcome === "resolve" ? Promise.resolve("confirm") : Promise.reject(outcome))) };
}

describe("confirmDialog", () => {
  it("resolves true on confirm", async () => {
    expect(await confirmDialog({ title: "Delete", message: "Sure?" }, box("resolve"))).toBe(true);
  });

  it.each(["cancel", "close"])("resolves false on %s (Cancel, Esc, backdrop)", async (action) => {
    expect(await confirmDialog({ title: "Delete", message: "Sure?" }, box(action))).toBe(false);
  });

  it("does not swallow a real error", async () => {
    const failure = new Error("boom");
    await expect(confirmDialog({ title: "t", message: "m" }, box(failure))).rejects.toBe(failure);
  });

  it("passes the text as plain text and never turns HTML on", async () => {
    const b = box("resolve");
    const html = "<img src=x onerror=alert(1)>";
    await confirmDialog({ title: "Clear", message: html, confirmText: "Clear logs", danger: true }, b);
    const [message, title, options] = b.confirm.mock.calls[0] as unknown as [string, string, Record<string, unknown>];
    expect(message).toBe(html);
    expect(title).toBe("Clear");
    expect(options.dangerouslyUseHTMLString).toBeFalsy();
    expect(options.confirmButtonText).toBe("Clear logs");
    expect(options.cancelButtonText).toBe("Cancel");
  });

  it("every box carries the confirm class that draws the title, the muted sentence and the solid destructive button", async () => {
    for (const danger of [true, false]) {
      const b = box("resolve");
      await confirmDialog({ title: "t", message: "m", danger }, b);
      expect((b.confirm.mock.calls[0] as unknown[])[2]).toMatchObject({ customClass: "rl-confirm" });
    }
  });

  it("marks a destructive confirm red and a plain one not", async () => {
    const danger = box("resolve");
    await confirmDialog({ title: "t", message: "m", danger: true }, danger);
    expect((danger.confirm.mock.calls[0] as unknown[])[2]).toMatchObject({
      type: "warning",
      confirmButtonClass: "el-button--danger",
    });
    const plain = box("resolve");
    await confirmDialog({ title: "t", message: "m" }, plain);
    expect((plain.confirm.mock.calls[0] as unknown[])[2]).toMatchObject({ type: "info", confirmButtonText: "Confirm" });
  });
});
