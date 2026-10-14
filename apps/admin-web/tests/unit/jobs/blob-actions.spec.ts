import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { openBlob, saveBlob } from "@/components/jobs/blob-actions";
import { hasKeptKey } from "@/components/jobs/kept-key";

const created = vi.fn<(b: Blob) => string>();
const revoked = vi.fn<(url: string) => void>();

const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };

beforeEach(() => {
  vi.useFakeTimers();
  created.mockReset().mockReturnValue("blob:one");
  revoked.mockReset();
  URL.createObjectURL = created;
  URL.revokeObjectURL = revoked;
});
afterEach(() => {
  URL.createObjectURL = original.create;
  URL.revokeObjectURL = original.revoke;
  vi.useRealTimers();
  document.body.replaceChildren();
});

describe("blob files", () => {
  it("opens in a new tab without an opener, then revokes the URL a minute later", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const blob = new Blob(["x"]);
    openBlob(blob);
    expect(created).toHaveBeenCalledWith(blob);
    expect(open).toHaveBeenCalledWith("blob:one", "_blank", "noopener");
    vi.advanceTimersByTime(59_999);
    expect(revoked).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(revoked).toHaveBeenCalledWith("blob:one");
  });

  it("saves under the given name through a link that is gone right after", () => {
    const clicked: { href: string; download: string }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.getAttribute("href") ?? "", download: this.download });
      expect(document.body.contains(this)).toBe(true);
    });
    saveBlob(new Blob(["x"]), "job-aaaaaaaa-0");
    expect(clicked).toEqual([{ href: "blob:one", download: "job-aaaaaaaa-0" }]);
    expect(document.querySelector("a")).toBeNull();
    vi.advanceTimersByTime(60_000);
    expect(revoked).toHaveBeenCalledWith("blob:one");
  });
});

describe("kept key record", () => {
  const record = (value: unknown) => sessionStorage.setItem("regenerateOperations", JSON.stringify(value));
  beforeEach(() => sessionStorage.clear());

  it("finds a key kept for the action, and none for another", () => {
    record({ "job:j1": "a".repeat(32) });
    expect(hasKeptKey("job:j1")).toBe(true);
    expect(hasKeptKey("job:j2")).toBe(false);
  });

  it("a value the server would refuse is not a kept key", () => {
    record({ "job:short": "abc", "job:long": "a".repeat(65), "job:odd": "a".repeat(15) + "!", "job:num": 7 });
    for (const action of ["job:short", "job:long", "job:odd", "job:num"]) expect(hasKeptKey(action)).toBe(false);
  });

  it("a record that is not an object, or not JSON, holds nothing", () => {
    record(["x"]);
    expect(hasKeptKey("0")).toBe(false);
    record(null);
    expect(hasKeptKey("job:j1")).toBe(false);
    sessionStorage.setItem("regenerateOperations", "not json");
    expect(hasKeptKey("job:j1")).toBe(false);
  });

  it("no record, or storage that cannot be read, holds nothing", () => {
    expect(hasKeptKey("job:j1")).toBe(false);
    vi.stubGlobal("sessionStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
    });
    expect(hasKeptKey("job:j1")).toBe(false);
  });
});
