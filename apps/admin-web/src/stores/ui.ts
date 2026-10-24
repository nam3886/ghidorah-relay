import { defineStore } from "pinia";
import { ref } from "vue";
import { THEME_KEY } from "@/lib/session";
import { useAuthStore } from "@/stores/auth";

export type Theme = "light" | "dark";

const OS_DARK = "(prefers-color-scheme: dark)";

function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
}

export const useUiStore = defineStore("ui", () => {
  // public/theme-init.js has already put the class on <html> (stored choice, else the OS setting); read it back.
  const theme = ref<Theme>(document.documentElement.classList.contains("dark") ? "dark" : "light");

  function apply(next: Theme): void {
    theme.value = next;
    document.documentElement.classList.toggle("dark", next === "dark");
  }

  // The theme follows the system until the user picks one (the manual override, remembered).
  function setTheme(next: Theme): void {
    apply(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* storage blocked: the choice lasts until reload */
    }
  }
  const toggleTheme = (): void => setTheme(theme.value === "dark" ? "light" : "dark");

  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    const os = window.matchMedia(OS_DARK);
    os.addEventListener?.("change", (event: { matches: boolean }) => {
      if (storedTheme() === null) apply(event.matches ? "dark" : "light");
    });
  }

  // The one job-detail dialog is mounted in the shell; Jobs and Alerts open it through openJob(id).
  const openJobId = ref<string | null>(null);
  const openJob = (id: string): void => {
    openJobId.value = id;
  };
  const closeJob = (): void => {
    openJobId.value = null;
  };

  useAuthStore().onSignOut(() => {
    openJobId.value = null;
  });

  return { theme, setTheme, toggleTheme, openJobId, openJob, closeJob };
});
