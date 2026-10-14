import { defineStore } from "pinia";
import { ref } from "vue";
import { THEME_KEY } from "@/lib/session";
import { useAuthStore } from "@/stores/auth";

export type Theme = "light" | "dark";

export const useUiStore = defineStore("ui", () => {
  // public/theme-init.js has already put the class on <html> (stored choice, else the OS setting); read it back.
  const theme = ref<Theme>(document.documentElement.classList.contains("dark") ? "dark" : "light");

  function setTheme(next: Theme): void {
    theme.value = next;
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* storage blocked: the choice lasts until reload */
    }
  }
  const toggleTheme = (): void => setTheme(theme.value === "dark" ? "light" : "dark");

  // Accounts auto-refresh: its state outlives tab switches.
  const accountsAutoRefresh = ref(false);

  // The one job-detail dialog is mounted in the shell; Jobs and Alerts open it through openJob(id).
  const openJobId = ref<string | null>(null);
  const openJob = (id: string): void => {
    openJobId.value = id;
  };
  const closeJob = (): void => {
    openJobId.value = null;
  };

  useAuthStore().onSignOut(() => {
    accountsAutoRefresh.value = false;
    openJobId.value = null;
  });

  return { theme, setTheme, toggleTheme, accountsAutoRefresh, openJobId, openJob, closeJob };
});
