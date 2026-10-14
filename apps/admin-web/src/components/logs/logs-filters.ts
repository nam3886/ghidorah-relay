import { defineStore } from "pinia";
import { ref } from "vue";
import { useAuthStore } from "@/stores/auth";
import type { LogFilters } from "./log-window";

// The filters of the Request logs outlive tab switches, in memory only: a search text is session data, so it goes on
// sign-out and a reload starts clean.
export const useLogsFilters = defineStore("logs-filters", () => {
  const q = ref("");
  const status = ref<LogFilters["status"]>("all");
  const from = ref("");
  const to = ref("");

  function reset(): void {
    q.value = "";
    status.value = "all";
    from.value = "";
    to.value = "";
  }
  useAuthStore().onSignOut(reset);

  return { q, status, from, to, reset };
});
