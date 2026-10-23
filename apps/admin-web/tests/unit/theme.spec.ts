import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { runInThisContext } from "node:vm";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { PHONE_QUERY, TABLET_QUERY, useBreakpoint } from "@/composables/use-breakpoint";
import { THEME_KEY } from "@/lib/session";
import { useUiStore } from "@/stores/ui";

function osPrefersDark(dark: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: dark && query === "(prefers-color-scheme: dark)",
    media: query,
  }));
}

// The file is a classic script with no exports: run its text, as the browser does when the page loads it.
const initScript = readFileSync(join(process.cwd(), "public/theme-init.js"), "utf8");
function runThemeInit(): void {
  runInThisContext(initScript);
}

const isDark = (): boolean => document.documentElement.classList.contains("dark");

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});

describe("theme-init.js (runs before the bundle)", () => {
  it("a stored dark choice wins over a light OS", () => {
    osPrefersDark(false);
    localStorage.setItem(THEME_KEY, "dark");
    runThemeInit();
    expect(isDark()).toBe(true);
  });

  it("a stored light choice wins over a dark OS", () => {
    osPrefersDark(true);
    localStorage.setItem(THEME_KEY, "light");
    document.documentElement.classList.add("dark");
    runThemeInit();
    expect(isDark()).toBe(false);
  });

  it("starts from the OS setting when nothing is stored", () => {
    osPrefersDark(true);
    runThemeInit();
    expect(isDark()).toBe(true);
    document.documentElement.classList.remove("dark");
    osPrefersDark(false);
    runThemeInit();
    expect(isDark()).toBe(false);
  });

  it("ignores a stored value that is not a theme", () => {
    osPrefersDark(true);
    localStorage.setItem(THEME_KEY, "purple");
    runThemeInit();
    expect(isDark()).toBe(true);
  });

  it("falls back to the OS setting when storage is blocked", () => {
    osPrefersDark(true);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    runThemeInit();
    expect(isDark()).toBe(true);
  });

  it("uses the same storage key as the app", () => {
    expect(THEME_KEY).toBe("relay_admin_theme");
  });
});

describe("ui store theme", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("starts from the class the init script set", () => {
    document.documentElement.classList.add("dark");
    expect(useUiStore().theme).toBe("dark");
    setActivePinia(createPinia());
    document.documentElement.classList.remove("dark");
    expect(useUiStore().theme).toBe("light");
  });

  it("toggles the class and remembers the choice", () => {
    const ui = useUiStore();
    ui.toggleTheme();
    expect(ui.theme).toBe("dark");
    expect(isDark()).toBe(true);
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    ui.toggleTheme();
    expect(ui.theme).toBe("light");
    expect(isDark()).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("still switches when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const ui = useUiStore();
    ui.toggleTheme();
    expect(ui.theme).toBe("dark");
    expect(isDark()).toBe(true);
  });
});

// ---- the C2 tokens, pinned: the values of DESIGN section 3 -----------------------------------------------------------
const css = readFileSync(join(process.cwd(), "src/styles/theme.css"), "utf8");

function declarations(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2]?.trim()]));
}
const lightBlock = declarations(css.match(/\nhtml:root \{([^}]*)\}/)?.[1] ?? "");
const darkBlock = declarations(css.match(/\nhtml\.dark:root \{([^}]*)\}/)?.[1] ?? "");

const LIGHT: Record<string, string> = {
  "--rl-bg": "#f7f5fb",
  "--rl-surface": "#ffffff",
  "--rl-raised": "#f3effa",
  "--rl-topbar": "#ffffff",
  "--rl-line": "#ece6f3",
  "--rl-line-strong": "#ddd5e8",
  "--rl-control": "#85758f",
  "--rl-fg": "#171321",
  "--rl-muted": "#655a75",
  "--rl-faint": "#76698a",
  "--rl-primary": "#7c3aed",
  "--rl-primary-hover": "#6d28d9",
  "--rl-primary-soft": "#ede9fe",
  "--rl-focus": "#7c3aed",
  "--rl-ok": "#047857",
  "--rl-ok-bg": "#e2f4ec",
  "--rl-warn": "#92400e",
  "--rl-warn-bg": "#fdf1df",
  "--rl-bad": "#be123c",
  "--rl-bad-bg": "#fde7ec",
  "--rl-bad-solid": "#be123c",
  "--rl-info": "#6d28d9",
  "--rl-info-bg": "#efe9fd",
  "--rl-neutral": "#655a75",
  "--rl-neutral-bg": "#f1eef5",
  "--rl-bar-done": "#7c3aed",
  "--rl-bar-fail": "#e11d48",
  "--rl-bar-run": "#c4b5fd",
  "--rl-scrim": "rgb(23 19 33 / 42%)",
  "--rl-on-primary": "#ffffff",
  "--rl-link": "var(--rl-primary)",
  "--rl-tile-1": "#e9e2f5",
  "--rl-tile-2": "#e2ecee",
  "--rl-tile-3": "#f2e9df",
  "--rl-tile-4": "#e6e2ec",
  "--rl-tile-ink": "#655a75",
  "--rl-media": "#1b1626",
  "--rl-media-badge": "rgb(0 0 0 / 60%)",
  "--rl-shadow-pop": "0 12px 32px rgb(10 6 20 / 18%)",
  "--rl-shadow-drawer": "-16px 0 48px rgb(10 6 20 / 28%)",
};
const DARK: Record<string, string> = {
  "--rl-bg": "#0e0b16",
  "--rl-surface": "#171222",
  "--rl-raised": "#211a30",
  "--rl-topbar": "#130e1f",
  "--rl-line": "#2a2140",
  "--rl-line-strong": "#3e3356",
  "--rl-control": "#82738f",
  "--rl-fg": "#f5f3ff",
  "--rl-muted": "#b9aec9",
  "--rl-faint": "#8e82a3",
  "--rl-primary": "#7c3aed",
  "--rl-primary-hover": "#6d28d9",
  "--rl-primary-soft": "#2e1d4b",
  "--rl-focus": "#a78bfa",
  "--rl-ok": "#34d399",
  "--rl-ok-bg": "rgb(52 211 153 / 13%)",
  "--rl-warn": "#fbbf24",
  "--rl-warn-bg": "rgb(251 191 36 / 13%)",
  "--rl-bad": "#fb7185",
  "--rl-bad-bg": "rgb(251 113 133 / 14%)",
  "--rl-bad-solid": "#e11d48",
  "--rl-info": "#c4b5fd",
  "--rl-info-bg": "rgb(139 92 246 / 20%)",
  "--rl-neutral": "#b9aec9",
  "--rl-neutral-bg": "rgb(185 174 201 / 12%)",
  "--rl-bar-done": "#8b5cf6",
  "--rl-bar-fail": "#fb7185",
  "--rl-bar-run": "#4c3a73",
  "--rl-scrim": "rgb(5 3 10 / 60%)",
  "--rl-on-primary": "#ffffff",
  "--rl-link": "#c4b5fd",
  "--rl-tile-1": "#2a2140",
  "--rl-tile-2": "#1d2a2e",
  "--rl-tile-3": "#2e241c",
  "--rl-tile-4": "#251f30",
  "--rl-tile-ink": "#b9aec9",
  "--rl-media": "#0a0710",
  "--rl-media-badge": "rgb(0 0 0 / 60%)",
  "--rl-shadow-pop": "0 12px 32px rgb(10 6 20 / 18%)",
  "--rl-shadow-drawer": "-16px 0 48px rgb(10 6 20 / 28%)",
};

describe("the C2 tokens", () => {
  it("light has the value of DESIGN section 3 for every token", () => {
    for (const [name, value] of Object.entries(LIGHT)) expect(lightBlock[name], name).toBe(value);
  });

  it("dark has the value of DESIGN section 3 for every token", () => {
    for (const [name, value] of Object.entries(DARK)) expect(darkBlock[name], name).toBe(value);
  });

  it("the two themes set the same names, so a token can never exist in one theme only", () => {
    const names = (block: Record<string, string>) => Object.keys(block).sort();
    expect(names(darkBlock)).toEqual(names(lightBlock));
    expect(Object.keys(LIGHT)).toHaveLength(40);
  });

  it("every Element Plus ladder step is set explicitly in each theme and differs between them", () => {
    for (const hue of ["primary", "success", "warning", "danger", "info"]) {
      for (const step of ["light-3", "light-5", "light-7", "light-8", "light-9", "dark-2"]) {
        const name = `--el-color-${hue}-${step}`;
        // a literal in each block, never a reference to another variable
        expect(lightBlock[name], `light ${name}`).toMatch(/^(#[0-9a-f]{6}|rgb\([0-9 /%]+\))$/);
        expect(darkBlock[name], `dark ${name}`).toMatch(/^(#[0-9a-f]{6}|rgb\([0-9 /%]+\))$/);
        expect(darkBlock[name], name).not.toBe(lightBlock[name]);
      }
    }
  });

  it("the two light steps of each Element Plus ladder are the fill of its token", () => {
    const fills = {
      primary: "--rl-primary-soft",
      success: "--rl-ok-bg",
      warning: "--rl-warn-bg",
      danger: "--rl-bad-bg",
      info: "--rl-neutral-bg",
    };
    for (const [hue, token] of Object.entries(fills)) {
      for (const step of ["light-8", "light-9"]) {
        const name = `--el-color-${hue}-${step}`;
        // light-8 of the primary is its own tint; every other step 8 and every step 9 is the token's fill
        if (hue === "primary" && step === "light-8") continue;
        expect(lightBlock[name], `light ${name}`).toBe(LIGHT[token]);
        expect(darkBlock[name], `dark ${name}`).toBe(DARK[token]);
      }
    }
  });

  it("maps the Element Plus colours onto the status tokens, with error as an alias of danger", () => {
    const mapped = declarations(css.match(/\nhtml:root,\nhtml\.dark:root \{([^}]*)\}/)?.[1] ?? "");
    expect(mapped["--el-color-primary"]).toBe("var(--rl-primary)");
    expect(mapped["--el-color-success"]).toBe("var(--rl-ok)");
    expect(mapped["--el-color-warning"]).toBe("var(--rl-warn)");
    expect(mapped["--el-color-danger"]).toBe("var(--rl-bad)");
    expect(mapped["--el-color-error"]).toBe("var(--rl-bad)");
    expect(mapped["--el-color-error-light-9"]).toBe("var(--el-color-danger-light-9)");
    expect(mapped["--el-color-error-light-8"]).toBe("var(--el-color-danger-light-8)");
    // the steps of the ladders are never set again in the shared block (it would win over a theme block)
    expect(Object.keys(mapped).filter((n) => /^--el-color-(primary|success|warning|danger|info)-/.test(n))).toEqual([]);
    expect(mapped["--el-color-error-dark-2"]).toBe("var(--el-color-danger-dark-2)");
    expect(mapped["--el-color-info"]).toBe("var(--rl-neutral)");
    expect(mapped["--el-font-family"]).toBe("var(--rl-font)");
    expect(mapped["--el-font-size-base"]).toBe("14px");
    expect(mapped["--el-border-radius-round"]).toBe("12px");
  });

  it("controls are 36 px high from the tablet width and 44 px below it", () => {
    expect(css).toMatch(/--el-component-size: 36px;/);
    expect(css).toMatch(
      /@media \(max-width: 767px\) \{\s*html:root,\s*html\.dark:root \{\s*--el-component-size: 44px;/,
    );
  });

  it("a default button follows the control scale, a small or large one keeps its own size", () => {
    expect(css).toMatch(
      /html \.el-button:not\(\.el-button--small\):not\(\.el-button--large\) \{\s*--el-button-size: var\(--el-component-size\);\s*height: var\(--el-button-size\);/,
    );
  });

  it("form controls take the control border colour and the focus ring is two pixels", () => {
    expect(css).toMatch(/html \.el-input,\s*html \.el-textarea \{\s*--el-input-border-color: var\(--rl-control\);/);
    expect(css).toMatch(/html \.el-select__wrapper \{\s*box-shadow: 0 0 0 1px var\(--rl-control\) inset;/);
    expect(css).toMatch(/--el-checkbox-input-border: 1px solid var\(--rl-control\);/);
    expect(css).toMatch(/:focus-visible \{\s*outline: 2px solid var\(--rl-focus\);\s*outline-offset: 2px;/);
  });

  it("a solid destructive button is the solid red with white text", () => {
    expect(css).toMatch(/--el-button-bg-color: var\(--rl-bad-solid\);/);
    expect(css).toMatch(
      /--el-button-bg-color: var\(--rl-bad-solid\);[^}]*--el-button-text-color: var\(--rl-on-primary\);/,
    );
  });

  it("disabled controls are a grey fill", () => {
    expect(css).toContain("--el-disabled-bg-color: var(--rl-raised);");
    expect(css).toContain("--el-button-disabled-bg-color: var(--rl-raised);");
  });

  it("reduced motion stops the animations", () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{[^]*animation-duration: 0\.01ms !important;/);
  });

  it("the two fonts come from one variable each, and the mono stack is written once", () => {
    expect(css.match(/--rl-font: "Be Vietnam Pro",/g)).toHaveLength(1);
    expect(css.match(/--rl-mono: "IBM Plex Mono",/g)).toHaveLength(1);
    expect(css.match(/"IBM Plex Mono"/g)).toHaveLength(1);
  });

  it("repeats no query of the old 639 and 640 px and keeps no data: URL or gradient", () => {
    for (const file of [
      "src/styles/theme.css",
      "src/components/layout/AppShell.vue",
      "src/components/layout/TopBar.vue",
      "src/components/layout/BrandLogo.vue",
    ]) {
      // comments may name what the code avoids
      const text = readFileSync(join(process.cwd(), file), "utf8")
        .replace(/\/\*[^]*?\*\//g, "")
        .replace(/<!--[^]*?-->/g, "");
      expect(text, file).not.toMatch(/\b(639|640)px/);
      expect(text, file).not.toMatch(/data:|gradient\(/);
    }
  });
});

describe("the provider palette (A, jewel tones)", () => {
  const TONES = {
    dola: "#087f98",
    meta: "#2c4aad",
    vibes: "#8e1b5c",
    flow: "#666b1f",
    qwen: "#0b5c80",
    gemini: "#1c70d3",
  };

  it("each provider has its tone in both themes, a tint at 9 % on white in light, and a class that names them", () => {
    for (const [name, tone] of Object.entries(TONES)) {
      expect(lightBlock[`--p-${name}`], name).toBe(tone);
      expect(darkBlock[`--p-${name}`], name).toBe(tone);
      expect(lightBlock[`--p-${name}-soft`], name).toBe(`color-mix(in oklab, var(--p-${name}) 9%, #ffffff)`);
      expect(lightBlock[`--p-${name}-ink`], name).toBe(`var(--p-${name})`);
      expect(css).toContain(`.p-${name} {\n  --pc: var(--p-${name});\n  --ps: var(--p-${name}-soft);`);
    }
  });
});

describe("the fonts", () => {
  const pkg = JSON.parse(readFileSync(join(process.cwd(), "package.json"), "utf8")) as {
    dependencies: Record<string, string>;
  };
  const main = readFileSync(join(process.cwd(), "src/main.ts"), "utf8");
  const vite = readFileSync(join(process.cwd(), "vite.config.ts"), "utf8");

  it("are two exact-pinned fontsource packages", () => {
    expect(pkg.dependencies["@fontsource/be-vietnam-pro"]).toBe("5.3.0");
    expect(pkg.dependencies["@fontsource/ibm-plex-mono"]).toBe("5.3.0");
  });

  it("load the latin and vietnamese subsets of the weights the design uses, and nothing else", () => {
    const imports = [...main.matchAll(/import "(@fontsource\/[^"]+)";/g)].map((m) => m[1]);
    const want = [
      ...["latin", "vietnamese"].flatMap((subset) =>
        [400, 500, 600, 700].map((w) => `@fontsource/be-vietnam-pro/${subset}-${w}.css`),
      ),
      ...["latin", "vietnamese"].flatMap((subset) =>
        [400, 500].map((w) => `@fontsource/ibm-plex-mono/${subset}-${w}.css`),
      ),
    ];
    expect(imports.sort()).toEqual(want.sort());
  });

  it("are files, never inlined: the CSP has no data:", () => {
    expect(vite).toContain("assetsInlineLimit: 0,");
  });

  it("are drawn as the canvas draws them: no style sets font smoothing, text rendering or font features", () => {
    // the canvas sets none of these; on a Mac the antialiased smoothing draws every glyph thinner than the canvas
    const RENDERING =
      /(-webkit-font-smoothing|-moz-osx-font-smoothing|text-rendering|font-synthesis|font-feature-settings|font-variation-settings|font-optical-sizing|font-kerning)\s*:/;
    const src = join(process.cwd(), "src");
    const styles = (readdirSync(src, { recursive: true }) as string[]).filter((f) => /\.(css|vue)$/.test(f));
    expect(styles.length).toBeGreaterThan(50);
    const setting = styles.filter((f) =>
      RENDERING.test(readFileSync(join(src, f), "utf8").replace(/\/\*[\s\S]*?\*\//g, "")),
    );
    expect(setting).toEqual([]);
  });
});

// ---- the one breakpoint composable ------------------------------------------------------------------------------------
type Listener = (event: { matches: boolean }) => void;

// A matchMedia stand-in: each query keeps its listeners, and `set` flips a query and tells them.
function fakeMedia(initial: Record<string, boolean>) {
  const state = { ...initial };
  const listeners = new Map<string, Set<Listener>>();
  const removed: string[] = [];
  const asked: string[] = [];
  vi.stubGlobal("matchMedia", (query: string) => {
    asked.push(query);
    return {
      media: query,
      get matches() {
        return state[query] ?? false;
      },
      addEventListener: (_: string, fn: Listener) => {
        listeners.set(query, (listeners.get(query) ?? new Set()).add(fn));
      },
      removeEventListener: (_: string, fn: Listener) => {
        removed.push(query);
        listeners.get(query)?.delete(fn);
      },
    };
  });
  return {
    asked,
    removed,
    live: (query: string) => listeners.get(query)?.size ?? 0,
    set(query: string, matches: boolean) {
      state[query] = matches;
      for (const fn of listeners.get(query) ?? []) fn({ matches });
    },
  };
}

function mountProbe() {
  return mount(
    defineComponent({
      setup() {
        const { isPhone, isTablet } = useBreakpoint();
        return () => h("p", `${isPhone.value ? "phone" : ""}|${isTablet.value ? "tablet" : ""}`);
      },
    }),
  );
}

describe("useBreakpoint", () => {
  it("asks for phone below 768 px and tablet from 768 to 1279 px, and nothing else", () => {
    const media = fakeMedia({});
    mountProbe();
    expect(PHONE_QUERY).toBe("(max-width: 767px)");
    expect(TABLET_QUERY).toBe("(min-width: 768px) and (max-width: 1279px)");
    expect(media.asked.sort()).toEqual([PHONE_QUERY, TABLET_QUERY].sort());
  });

  it("is the desktop (neither) when no query matches", () => {
    fakeMedia({});
    expect(mountProbe().text()).toBe("|");
  });

  it("is phone only for a phone width and tablet only for a tablet width", () => {
    fakeMedia({ [PHONE_QUERY]: true });
    expect(mountProbe().text()).toBe("phone|");
    fakeMedia({ [TABLET_QUERY]: true });
    expect(mountProbe().text()).toBe("|tablet");
  });

  it("follows a change of the window width", async () => {
    const media = fakeMedia({});
    const w = mountProbe();
    media.set(TABLET_QUERY, true);
    await w.vm.$nextTick();
    expect(w.text()).toBe("|tablet");
    media.set(TABLET_QUERY, false);
    media.set(PHONE_QUERY, true);
    await w.vm.$nextTick();
    expect(w.text()).toBe("phone|");
  });

  it("stops listening when the component is gone, and not before", () => {
    const media = fakeMedia({});
    const w = mountProbe();
    expect(media.live(PHONE_QUERY)).toBe(1);
    expect(media.live(TABLET_QUERY)).toBe(1);
    expect(media.removed).toEqual([]);
    w.unmount();
    expect(media.live(PHONE_QUERY)).toBe(0);
    expect(media.live(TABLET_QUERY)).toBe(0);
    expect(media.removed.sort()).toEqual([PHONE_QUERY, TABLET_QUERY].sort());
  });

  it("is the desktop where matchMedia does not exist", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(mountProbe().text()).toBe("|");
  });
});

// ---- the canvas's global rules (its `.gd` stylesheet), rule by rule -------------------------------------------------
const ruleOf = (selector: string): string => {
  const at = css.indexOf(`\n${selector} {`);
  return at < 0 ? "" : css.slice(at, css.indexOf("}", at));
};

describe("the canvas's global rules", () => {
  it("gives controls and links the page font, and nothing else of the browser's control font", () => {
    expect(ruleOf("button,\ninput,\nselect,\ntextarea,\na")).toMatch(/\{\s*font-family: inherit;\s*$/);
  });

  it("draws every scrollbar as a line-strong thumb on a transparent track", () => {
    expect(ruleOf("*")).toMatch(/\{\s*scrollbar-color: var\(--rl-line-strong\) transparent;\s*$/);
  });

  it("gives Safari the same thumb through its own scrollbar pseudo-elements, rounded, on a transparent track", () => {
    const thumb = ruleOf("::-webkit-scrollbar-thumb");
    expect(thumb).toContain("background: var(--rl-line-strong) padding-box;");
    expect(thumb).toContain("border: 3px solid transparent;");
    expect(thumb).toContain("border-radius: 8px;");
    expect(ruleOf("::-webkit-scrollbar-track,\n::-webkit-scrollbar-corner")).toContain("background: transparent;");
    expect(ruleOf("::-webkit-scrollbar")).toMatch(/width: 15px;\s*height: 15px;/);
  });

  it("gives a number its tabular figures and nothing else: a number column aligns its own cells", () => {
    expect(ruleOf(".num")).toMatch(/\{\s*font-variant-numeric: tabular-nums;\s*$/);
  });

  it("selects text on the soft primary and draws bare links in the link colour, the hover step in light only", () => {
    expect(ruleOf("::selection")).toContain("background: var(--rl-primary-soft);");
    expect(ruleOf(":where(a)")).toContain("color: var(--rl-link);");
    expect(ruleOf(":where(a:hover)")).toContain("color: var(--rl-primary-hover);");
    expect(ruleOf("html.dark :where(a:hover)")).toContain("color: var(--rl-link);");
  });
});

describe("the multiple-select tick", () => {
  it("is drawn with borders and ranks over the Element Plus mask, which is a blocked data: image", () => {
    const rule = css.match(
      /html \.el-select-dropdown\.is-multiple \.el-select-dropdown__item\.is-selected::after \{([^}]*)\}/,
    );
    expect(rule?.[1]).toContain("mask-image: none !important;");
    expect(rule?.[1]).toContain("-webkit-mask-image: none !important;");
    expect(rule?.[1]).toContain("border-width: 0 2px 2px 0;");
  });
});
