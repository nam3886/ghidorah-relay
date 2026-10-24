import { readFileSync } from "node:fs";
import { join } from "node:path";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import BrandLogo from "@/components/layout/BrandLogo.vue";
import { useUiStore } from "@/stores/ui";

const read = (file: string): string => readFileSync(join(process.cwd(), file), "utf8");

describe("the entry page", () => {
  const page = read("index.html");

  it("is titled Ghidorah admin", () => {
    expect(page).toContain("<title>Ghidorah admin</title>");
    expect(page).not.toContain("Relay");
  });

  it("has the Ghidorah favicon, a same-origin file", () => {
    expect(page).toContain('<link rel="icon" type="image/svg+xml" href="/brand/ghidorah-favicon.svg" />');
    expect(read("public/brand/ghidorah-favicon.svg")).toContain("<svg");
  });
});

describe("the brand logo", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    document.documentElement.classList.remove("dark");
  });

  it("is the design's mark and the word Ghidorah: the word names it, the mark is decoration", async () => {
    const w = mount(BrandLogo);
    expect(w.text()).toBe("Ghidorah");
    expect(w.find(".brand__word").text()).toBe("Ghidorah");
    const mark = w.find("svg.brand__mark");
    expect(mark.attributes()).toMatchObject({ viewBox: "50 20 510 520", width: "24", height: "24" });
    expect(mark.attributes("aria-hidden")).toBe("true");
    expect(mark.findAll("path")).toHaveLength(2);
    expect(w.findAll("img")).toHaveLength(0);
    // one drawing for both themes: the colours are the tokens, so a theme switch changes nothing in the markup
    const before = w.html();
    useUiStore().setTheme("dark");
    await w.vm.$nextTick();
    expect(w.html()).toBe(before);
  });

  it("takes its colours from the theme tokens: the mark the brand purple, the word the text colour", () => {
    const source = read("src/components/layout/BrandLogo.vue");
    expect(source).toContain("fill: var(--rl-brand);");
    expect(source).toContain("color: var(--rl-fg);");
  });

  it("never says Relay: no source file of the shell or the login page carries the word as a brand", () => {
    for (const file of [
      "src/components/layout/BrandLogo.vue",
      "src/components/layout/TopBar.vue",
      "src/views/LoginView.vue",
    ]) {
      expect(read(file), file).not.toMatch(/Ghidorah Relay|>Relay</);
    }
  });
});

describe("the colours", () => {
  const css = read("src/styles/theme.css");

  it("the brand is #6a35e8 (#8b5cf6 dark), a filled button #6a35e8 (#7c3aed dark) with white text", () => {
    expect(css.match(/--rl-brand: #6a35e8;/g)).toHaveLength(1);
    expect(css.match(/--rl-brand: #8b5cf6;/g)).toHaveLength(1);
    expect(css.match(/--rl-primary: #6a35e8;/g)).toHaveLength(1);
    expect(css.match(/--rl-primary: #7c3aed;/g)).toHaveLength(1);
    expect(css).toContain("--el-color-primary: var(--rl-primary);");
    expect(css).toContain("--el-button-hover-bg-color: var(--rl-primary-hover);");
    expect(css.match(/--rl-on-primary: #ffffff;/g)).toHaveLength(2);
    expect(css).toContain("--el-button-text-color: var(--rl-on-primary);");
  });

  it("the dark surfaces are the kit's", () => {
    for (const hex of ["#0d0e14", "#15161f", "#1b1d28", "#181a24", "#262838", "#1f2130", "#2e3142", "#ecedf3"]) {
      expect(css).toContain(hex);
    }
  });

  it("the radii are 10 px for controls, 16 px for cards and dialogs, a full pill for chips", () => {
    // one shared rule for both themes
    expect(css.match(/--el-border-radius-base: 10px;/g)).toHaveLength(1);
    expect(css.match(/--el-card-border-radius: 16px;/g)).toHaveLength(1);
    expect(css.match(/--el-tag-border-radius: 999px;/g)).toHaveLength(1);
  });

  it("there is no gradient and no glow", () => {
    expect(css).not.toMatch(/gradient\(/);
  });
});
