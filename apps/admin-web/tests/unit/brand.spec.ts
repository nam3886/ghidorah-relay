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

  const wide = (w: ReturnType<typeof mount>) => w.find("img.brand__full");

  it("shows the logo with light letters in the dark theme and dark letters in the light theme", async () => {
    const w = mount(BrandLogo);
    expect(wide(w).attributes("src")).toContain("ghidorah-logo-light");
    useUiStore().setTheme("dark");
    await w.vm.$nextTick();
    expect(wide(w).attributes("src")).toContain("ghidorah-logo-dark");
    useUiStore().setTheme("light");
    await w.vm.$nextTick();
    expect(wide(w).attributes("src")).toContain("ghidorah-logo-light");
  });

  it("names itself Ghidorah and is only the wide logo, whatever the width", () => {
    for (const variant of ["header", "login"] as const) {
      const w = mount(BrandLogo, { props: { variant } });
      expect(wide(w).attributes("alt")).toBe("Ghidorah");
      expect(w.findAll("img")).toHaveLength(1);
      expect(w.text()).toBe("");
    }
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

  it("the primary is the brand purple in both themes, hover #6d28d9, white text on a solid button", () => {
    expect(css.match(/--rl-primary: #7c3aed;/g)).toHaveLength(2);
    expect(css.match(/--rl-primary-hover: #6d28d9;/g)).toHaveLength(2);
    expect(css).toContain("--el-color-primary: var(--rl-primary);");
    expect(css).toContain("--el-button-hover-bg-color: var(--rl-primary-hover);");
    expect(css.match(/--rl-on-primary: #ffffff;/g)).toHaveLength(2);
    expect(css).toContain("--el-button-text-color: var(--rl-on-primary);");
  });

  it("the dark surfaces are the kit's", () => {
    for (const hex of [
      "#0e0b16",
      "#171222",
      "#211a30",
      "#130e1f",
      "#2a2140",
      "#3e3356",
      "#82738f",
      "#f5f3ff",
      "#b9aec9",
    ]) {
      expect(css).toContain(hex);
    }
  });

  it("the brand radii are 10 px for controls, 16 px for cards and dialogs, 12 px for chips", () => {
    // one shared rule for both themes
    expect(css.match(/--el-border-radius-base: 10px;/g)).toHaveLength(1);
    expect(css.match(/--el-card-border-radius: 16px;/g)).toHaveLength(1);
    expect(css.match(/--el-tag-border-radius: 12px;/g)).toHaveLength(1);
  });

  it("there is no gradient and no glow", () => {
    expect(css).not.toMatch(/gradient\(/);
  });
});
