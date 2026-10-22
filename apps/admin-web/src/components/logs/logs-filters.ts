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

  // The range picker needs both ends: a saved state with only one would leave the field empty while the search still
  // sent a bound, a filter nobody can see. Both ends are cleared then.
  function keepRangeWhole(): void {
    if (!from.value || !to.value) {
      from.value = "";
      to.value = "";
    }
  }

  return { q, status, from, to, reset, keepRangeWhole };
});
