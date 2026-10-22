import { readFileSync } from "node:fs";
import { join } from "node:path";
import postcss from "postcss";
import { describe, expect, it } from "vitest";
import { stripDataUrls } from "@/styles/strip-data-urls";

const run = async (css: string) => (await postcss([stripDataUrls]).process(css, { from: undefined })).css;

describe("the build step that drops data: URLs from vendor CSS", () => {
  it("removes a declaration that carries a data: URL, quoted or not, in any case, and keeps its neighbours", async () => {
    const out = await run(
      `.a{color:red;mask:url("data:image/svg+xml;base64,AAAA") 0 0/100% no-repeat;width:5px}` +
        `.b{-webkit-mask:url(data:image/svg+xml,%3Csvg%3E)}` +
        `.c{background:URL( 'DATA:image/png;base64,AA' )}`,
    );
    expect(out).toBe(`.a{color:red;width:5px}.b{}.c{}`);
  });

  it("removes a declaration whose data URL is a quoted string inside image-set(), first or later in the list", async () => {
    const out = await run(
      `.d{background:image-set("data:image/png;base64,AA" 1x,url(/x.png) 2x);margin:0}` +
        `.e{background:image-set(url(/x.png) 1x,"data:image/png;base64,AA" 2x)}` +
        `.f{background-image:-webkit-image-set(url(data:image/png;base64,AA) 1x)}`,
    );
    expect(out).toBe(`.d{margin:0}.e{}.f{}`);
  });

  it("sees a data URL behind a comment inside the value", async () => {
    const out = await run(
      `.g{mask:url(/*x*/data:image/svg+xml,%3Csvg%3E)}.h{background:image-set(/* a */"data:image/png;base64,AA" 1x)}` +
        `.i{background:url(/* data:x, */ /a.png)}`,
    );
    expect(out).toBe(`.g{}.h{}.i{background:url(/* data:x, */ /a.png)}`);
  });

  it("is wired into the PostCSS step of the build config, so unwiring it fails", () => {
    // read as text: the config builds a file URL for the source folder, which Vitest cannot import
    const config = readFileSync(join(process.cwd(), "vite.config.ts"), "utf8");
    expect(config).toContain('import { stripDataUrls } from "./src/styles/strip-data-urls";');
    expect(config).toContain("css: { postcss: { plugins: [stripDataUrls] } },");
  });

  it("keeps a plain url() to a file and a value that only mentions data", async () => {
    const css = `.a{background:url(/ui/app/assets/x.png)}.b{content:"data: none";font-family:data}`;
    expect(await run(css)).toBe(css);
  });

  it("leaves a stylesheet with no data: URL byte for byte", async () => {
    const css = `@media (max-width:767px){.a{margin:0 auto}}`;
    expect(await run(css)).toBe(css);
  });
});
