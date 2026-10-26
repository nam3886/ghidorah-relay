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
  it("follows a change of the OS setting while nothing is stored, and not after a manual choice", () => {
    let listener: ((event: { matches: boolean }) => void) | undefined;
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      addEventListener: (_: string, fn: (event: { matches: boolean }) => void) => {
        if (query === "(prefers-color-scheme: dark)") listener = fn;
      },
      removeEventListener: () => undefined,
    }));
    const ui = useUiStore();
    listener?.({ matches: true });
    expect(ui.theme).toBe("dark");
    expect(isDark()).toBe(true);
    listener?.({ matches: false });
    expect(ui.theme).toBe("light");
    ui.setTheme("dark");
    listener?.({ matches: false });
    expect(ui.theme).toBe("dark");
    expect(isDark()).toBe(true);
  });
});

// ---- the Ledger tokens, pinned: the values of the canvas token objects (K, S, PF, PB) --------------------------------
const css = readFileSync(join(process.cwd(), "src/styles/theme.css"), "utf8");

function declarations(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2]?.trim()]));
}
const lightBlock = declarations(css.match(/\nhtml:root \{([^}]*)\}/)?.[1] ?? "");
const darkBlock = declarations(css.match(/\nhtml\.dark:root \{([^}]*)\}/)?.[1] ?? "");

// [light, dark], copied from the canvas
const TOKENS: Record<string, [string, string]> = {
  "--rl-bg": ["#f5f6fa", "#0d0e14"],
  "--rl-surface": ["#ffffff", "#15161f"],
  "--rl-pop": ["#ffffff", "#1b1d28"],
  "--rl-head": ["#fafbfd", "#181a24"],
  "--rl-track": ["#eceef4", "#262838"],
  "--rl-input": ["#ffffff", "#101119"],
  "--rl-line": ["#e6e8ef", "#262838"],
  "--rl-rule": ["#eef0f5", "#1f2130"],
  "--rl-line-strong": ["#e0e3ea", "#2e3142"],
  "--rl-fg": ["#1a1b25", "#ecedf3"],
  "--rl-body": ["#4a4d5c", "#c3c6d4"],
  "--rl-muted": ["#5d6070", "#a3a6b8"],
  "--rl-faint": ["#6b6e7f", "#8d90a3"],
  "--rl-brand": ["#6a35e8", "#8b5cf6"],
  "--rl-primary": ["#6a35e8", "#7c3aed"],
  "--rl-primary-soft": ["#efe9ff", "#2a1f4d"],
  "--rl-brand-ink": ["#5b2fd6", "#c4b2ff"],
  "--rl-focus": ["#6a35e8", "#8b5cf6"],
  "--rl-link": ["#5b2fd6", "#c4b2ff"],
  "--rl-ok": ["#067647", "#7ee2b8"],
  "--rl-ok-bg": ["#dcfae6", "#0f2c1d"],
  "--rl-warn": ["#b54708", "#fcd34d"],
  "--rl-warn-bg": ["#fef0c7", "#3a2a0a"],
  "--rl-bad": ["#b42318", "#ff9c92"],
  "--rl-bad-bg": ["#fee4e2", "#3d1512"],
  "--rl-bad-solid": ["#c8322a", "#c8322a"],
  "--rl-neutral": ["#475467", "#a3a6b8"],
  "--rl-neutral-bg": ["#f2f4f7", "#22242f"],
  "--rl-amber": ["#f79009", "#fbbf24"],
  "--rl-alert-badge": ["#93370d", "#fcd34d"],
  "--rl-alert-badge-bg": ["#fef0c7", "#3a2a0a"],
  "--rl-tip": ["#1a1b25", "#ecedf3"],
  "--rl-tip-fg": ["#ffffff", "#15161f"],
  "--rl-scroll-thumb": ["#d0d4de", "#3a3d50"],
  "--rl-scroll-thumb-hover": ["#a9afbf", "#565b74"],
  "--rl-scrim": ["rgba(16, 18, 28, 0.42)", "rgba(5, 6, 10, 0.66)"],
  "--rl-scrim-modal": ["rgba(16, 18, 28, 0.55)", "rgba(5, 6, 10, 0.72)"],
  "--rl-shadow-card": ["0 1px 2px rgba(16, 24, 40, 0.05), 0 0 0 1px #e6e8ef", "0 0 0 1px #262838"],
  "--rl-shadow-pop": [
    "0 0 0 1px rgba(16, 24, 40, 0.08), 0 16px 40px rgba(16, 24, 40, 0.14)",
    "0 0 0 1px #2e3142, 0 16px 40px rgba(0, 0, 0, 0.5)",
  ],
  "--rl-shadow-drawer": [
    "0 0 0 1px rgba(16, 24, 40, 0.06), 0 24px 64px rgba(16, 24, 40, 0.22)",
    "0 0 0 1px #2e3142, 0 24px 64px rgba(0, 0, 0, 0.55)",
  ],
};
const PF = {
  dola: ["#0f766e", "#5eead4"],
  meta: ["#1d4ed8", "#93b4ff"],
  vibes: ["#be185d", "#f9a8d4"],
  flow: ["#4d7c0f", "#bef264"],
  gemini: ["#c2410c", "#fdba74"],
  qwen: ["#8a6700", "#fde047"],
  muse: ["#0369a1", "#7dd3fc"],
};
const PB = {
  dola: ["#ddf6f2", "#0f2e2b"],
  meta: ["#e3ecfd", "#16234a"],
  vibes: ["#fce4f0", "#3a1428"],
  flow: ["#ebf5d6", "#22300c"],
  gemini: ["#feebdc", "#3a1e0c"],
  qwen: ["#fbf1d0", "#33290a"],
  muse: ["#e0f2fe", "#0c2a3d"],
};

describe("the Ledger tokens", () => {
  it("light and dark have the canvas value of every token", () => {
    for (const [name, [light, dark]] of Object.entries(TOKENS)) {
      expect(lightBlock[name], `light ${name}`).toBe(light);
      expect(darkBlock[name], `dark ${name}`).toBe(dark);
    }
  });

  it("the two themes set the same names, so a token can never exist in one theme only", () => {
    const names = (block: Record<string, string>) => Object.keys(block).sort();
    expect(names(darkBlock)).toEqual(names(lightBlock));
  });

  it("each provider has its PF ink and PB fill in both themes, and a class that names them", () => {
    for (const name of Object.keys(PF) as (keyof typeof PF)[]) {
      expect([lightBlock[`--p-${name}`], darkBlock[`--p-${name}`]], name).toEqual(PF[name]);
      expect([lightBlock[`--p-${name}-ink`], darkBlock[`--p-${name}-ink`]], name).toEqual(PF[name]);
      expect([lightBlock[`--p-${name}-soft`], darkBlock[`--p-${name}-soft`]], name).toEqual(PB[name]);
      expect(css).toContain(`.p-${name} {\n  --pc: var(--p-${name});\n  --ps: var(--p-${name}-soft);`);
    }
  });

  it("maps the Element Plus colours onto the tokens, with error as an alias of danger", () => {
    const mapped = declarations(css.match(/\nhtml:root,\nhtml\.dark:root \{([^}]*)\}/)?.[1] ?? "");
    expect(mapped["--el-color-primary"]).toBe("var(--rl-primary)");
    expect(mapped["--el-color-success"]).toBe("var(--rl-ok)");
    expect(mapped["--el-color-warning"]).toBe("var(--rl-warn)");
    expect(mapped["--el-color-danger"]).toBe("var(--rl-bad)");
    expect(mapped["--el-color-error"]).toBe("var(--rl-bad)");
    expect(mapped["--el-color-error-light-9"]).toBe("var(--el-color-danger-light-9)");
    expect(mapped["--el-color-info"]).toBe("var(--rl-neutral)");
    expect(mapped["--el-font-family"]).toBe("var(--rl-font)");
    expect(mapped["--el-font-size-base"]).toBe("14px");
    expect(mapped["--el-border-radius-base"]).toBe("10px");
    expect(mapped["--el-card-border-radius"]).toBe("16px");
    expect(mapped["--el-table-header-bg-color"]).toBe("var(--rl-head)");
    expect(mapped["--el-bg-color-overlay"]).toBe("var(--rl-pop)");
    // every ladder step comes from the theme's own colours, so it is right in both themes
    for (const hue of ["primary", "success", "warning", "danger", "info"]) {
      for (const step of ["light-3", "light-5", "light-7", "light-8", "light-9", "dark-2"]) {
        expect(mapped[`--el-color-${hue}-${step}`], `${hue} ${step}`).toMatch(/var\(--rl-/);
      }
    }
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

  it("a focused field takes the brand edge and the 3 px halo; everything else the 2 px ring", () => {
    expect(css).toMatch(/0 0 0 1px var\(--rl-brand\) inset,\s*0 0 0 3px var\(--rl-focus-halo\) !important;/);
    expect(lightBlock["--rl-focus-halo"]).toBe("rgba(106, 53, 232, 0.1)");
    expect(darkBlock["--rl-focus-halo"]).toBe("rgba(139, 92, 246, 0.22)");
    expect(css).toMatch(/:focus-visible \{\s*outline: 2px solid var\(--rl-focus\);\s*outline-offset: 2px;/);
  });

  it("a solid destructive button is the solid red with white text", () => {
    expect(css).toMatch(
      /--el-button-bg-color: var\(--rl-bad-solid\);[^}]*--el-button-text-color: var\(--rl-on-primary\);/,
    );
  });

  it("overlays carry the lift and a dialog the 16 px radius", () => {
    expect(css).toMatch(/html \.el-drawer \{\s*box-shadow: var\(--rl-shadow-drawer\);/);
    expect(css).toMatch(/html \.el-message-box \{\s*border-radius: 16px;\s*box-shadow: var\(--rl-shadow-drawer\);/);
  });

  it("reduced motion stops the animations", () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{[^]*animation-duration: 0\.01ms !important;/);
  });

  it("the two fonts come from one variable each", () => {
    expect(css.match(/--rl-font: Manrope, system-ui, -apple-system, sans-serif;/g)).toHaveLength(1);
    expect(css.match(/--rl-mono: "JetBrains Mono", ui-monospace, Menlo, monospace;/g)).toHaveLength(1);
    expect(css).not.toMatch(/Be Vietnam Pro|IBM Plex Mono/);
  });

  it("keeps no data: URL or gradient in the shell's own style sheets", () => {
    for (const file of [
      "src/styles/theme.css",
      "src/components/layout/AppShell.vue",
      "src/components/layout/TopBar.vue",
      "src/components/layout/BottomNav.vue",
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

describe("the fonts", () => {
  const pkg = JSON.parse(readFileSync(join(process.cwd(), "package.json"), "utf8")) as {
    dependencies: Record<string, string>;
  };
  const main = readFileSync(join(process.cwd(), "src/main.ts"), "utf8");
  const vite = readFileSync(join(process.cwd(), "vite.config.ts"), "utf8");

  it("are two exact-pinned fontsource packages", () => {
    expect(pkg.dependencies["@fontsource/manrope"]).toBe("5.3.0");
    expect(pkg.dependencies["@fontsource/jetbrains-mono"]).toBe("5.3.0");
    expect(
      Object.keys(pkg.dependencies)
        .filter((d) => d.startsWith("@fontsource/"))
        .sort(),
    ).toEqual(["@fontsource/jetbrains-mono", "@fontsource/manrope"]);
  });

  it("load the latin and vietnamese subsets of the weights the design uses, and nothing else", () => {
    const imports = [...main.matchAll(/import "(@fontsource\/[^"]+)";/g)].map((m) => m[1]);
    const want = [
      ...["latin", "vietnamese"].flatMap((subset) =>
        [400, 500, 600, 700, 800].map((w) => `@fontsource/manrope/${subset}-${w}.css`),
      ),
      ...["latin", "vietnamese"].flatMap((subset) =>
        [400, 500].map((w) => `@fontsource/jetbrains-mono/${subset}-${w}.css`),
      ),
    ];
    expect(imports.sort()).toEqual(want.sort());
  });

  it("are files, never inlined: the CSP has no data:", () => {
    expect(vite).toContain("assetsInlineLimit: 0,");
  });

  it("are drawn as the canvas draws them: no style sets font smoothing, text rendering or font features", () => {
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

// ---- the canvas's global rules, rule by rule ---------------------------------------------------------------------------
const ruleOf = (selector: string): string => {
  const at = css.indexOf(`\n${selector} {`);
  return at < 0 ? "" : css.slice(at, css.indexOf("}", at));
};

describe("the canvas's global rules", () => {
  it("gives controls and links the page font, and nothing else of the browser's control font", () => {
    expect(ruleOf("button,\ninput,\nselect,\ntextarea,\na")).toMatch(/\{\s*font-family: inherit;\s*$/);
  });

  it("draws every scrollbar thin, the thumb on a transparent track (Firefox and current Chrome)", () => {
    expect(ruleOf("*")).toMatch(
      /\{\s*scrollbar-width: thin;\s*scrollbar-color: var\(--rl-scroll-thumb\) transparent;\s*$/,
    );
  });

  it("gives WebKit the 8 px bar: a rounded thumb with a 1 px clear edge, darker under the pointer", () => {
    expect(ruleOf("::-webkit-scrollbar")).toMatch(/width: 8px;\s*height: 8px;/);
    const thumb = ruleOf("::-webkit-scrollbar-thumb");
    expect(thumb).toContain("border: 1px solid transparent;");
    expect(thumb).toContain("border-radius: 8px;");
    expect(thumb).toContain("background-color: var(--rl-scroll-thumb);");
    expect(thumb).toContain("background-clip: padding-box;");
    expect(ruleOf("::-webkit-scrollbar-thumb:hover")).toContain("background-color: var(--rl-scroll-thumb-hover);");
    expect(ruleOf("::-webkit-scrollbar-track,\n::-webkit-scrollbar-corner")).toContain("background: transparent;");
  });

  it("gives a number its tabular figures and an id the mono face", () => {
    expect(ruleOf(".num")).toMatch(/\{\s*font-variant-numeric: tabular-nums;\s*$/);
    expect(ruleOf(".mono")).toMatch(/\{\s*font-family: var\(--rl-mono\);\s*$/);
  });

  it("selects text on the soft brand and draws bare links in the brand ink with its hover step", () => {
    expect(ruleOf("::selection")).toContain("background: var(--rl-primary-soft);");
    expect(ruleOf(":where(a)")).toContain("color: var(--rl-link);");
    expect(ruleOf(":where(a:hover)")).toContain("color: var(--rl-link-hover);");
    expect(lightBlock["--rl-link-hover"]).toBe("#43209f");
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
