import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// No native date input anywhere in the app: the Logs days are Element Plus date pickers (typed `YYYY-MM-DD`, one look in
// both themes). And the picker's own template and style sheet carry nothing the CSP blocks: no `style=` attribute and no
// `data:` URL (the policy has no `img-src`, so it falls back to 'self').
const SRC = join(process.cwd(), "src");
const NATIVE = /type=["']date["']/i;
const sources = (readdirSync(SRC, { recursive: true }) as string[])
  .map((f) => f.split("\\").join("/"))
  .filter((f) => /\.(vue|ts|tsx|js|html)$/.test(f));
const read = (f: string) => readFileSync(join(SRC, f), "utf8");

describe("no native date", () => {
  it("no source file draws a native date input", () => {
    const found = sources.filter((f) => NATIVE.test(read(f)));
    expect(found).toEqual([]);
  });

  it("the date picker's form and style sheet have no style attribute and no data URL", () => {
    for (const f of ["components/logs/LogsFilterForm.vue", "components/logs/logs-date-field.css"]) {
      const text = read(f);
      expect(text, f).not.toMatch(/\sstyle=/);
      expect(text, f).not.toMatch(/url\(\s*["']?data:/i);
    }
  });
});
