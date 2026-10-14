import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runInThisContext } from "node:vm";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
