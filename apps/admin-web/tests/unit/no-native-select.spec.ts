import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// No native select element anywhere in the app: the design draws its selects, and a native one looks and opens
// differently on every system (the macOS popup button and menu on the owner's Mac). Use FilterSelect, whose `field`
// variant is the canvas's labelled select.
const SRC = join(process.cwd(), "src");
const NATIVE = /<select\b/i;
const sources = (readdirSync(SRC, { recursive: true }) as string[])
  .map((f) => f.split("\\").join("/"))
  .filter((f) => /\.(vue|ts|tsx|js|html)$/.test(f));

describe("no native select", () => {
  it("no source file draws a native select", () => {
    const found = sources.filter((f) => NATIVE.test(readFileSync(join(SRC, f), "utf8")));
    expect(found).toEqual([]);
  });
});
