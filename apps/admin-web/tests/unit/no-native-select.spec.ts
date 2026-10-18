import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// No native select element anywhere in the app: the design draws its selects, and a native one looks and opens
// differently on every system (the macOS popup button and menu on the owner's Mac). Use FilterSelect, whose `field`
// variant is the canvas's labelled select. The files below still have one; each page takes its file off the list when
// it converts it, and a file on the list that no longer has one fails too, so the list only shrinks.
const STILL_TO_CONVERT = [
  "components/logs/LogsFilterForm.vue",
  "components/keys/KeyFormDialog.vue",
  "views/TestView.vue",
  "views/FramesView.vue",
];

const SRC = join(process.cwd(), "src");
const NATIVE = /<select\b/i;
const sources = (readdirSync(SRC, { recursive: true }) as string[])
  .map((f) => f.split("\\").join("/"))
  .filter((f) => /\.(vue|ts|tsx|js|html)$/.test(f));

describe("no native select", () => {
  it("no source file draws a native select but the ones still to convert", () => {
    const found = sources.filter((f) => NATIVE.test(readFileSync(join(SRC, f), "utf8")));
    expect(found.filter((f) => !STILL_TO_CONVERT.includes(f))).toEqual([]);
  });

  it("every file still to convert still has one: a converted file leaves the list", () => {
    const converted = STILL_TO_CONVERT.filter((f) => !NATIVE.test(readFileSync(join(SRC, f), "utf8")));
    expect(converted).toEqual([]);
  });
});
