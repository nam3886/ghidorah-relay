import { defineStore } from "pinia";
import { ref, shallowRef } from "vue";
import { getOpenAlertGroups, getStats, type Stats } from "@/api/shell";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";

// The stats strip and the attention banner. The strip follows the provider filter; the live alerts and the open
// alert groups cover the whole deployment. Only the newest refresh draws (or toasts a failure): an older, slower
// answer must not bring back an old count.
export const useStatsStore = defineStore("stats", () => {
  const scope = useScopeStore();
  const data = shallowRef<Stats | null>(null);
  const openGroups = ref(0);
  const loading = ref(false);
  const latest = useLatest();

  async function refresh(): Promise<void> {
    const ticket = latest.next();
    loading.value = true;
    try {
      const [stats, groups] = await Promise.all([getStats(scope.provider), getOpenAlertGroups()]);
      if (!latest.isCurrent(ticket)) return;
      data.value = stats;
      openGroups.value = groups.items.length;
    } catch (err) {
      if (!latest.isCurrent(ticket)) return;
      notify.error(errorText(err));
    } finally {
      if (latest.isCurrent(ticket)) loading.value = false;
    }
  }

  useAuthStore().onSignOut(() => {
    latest.invalidate();
    data.value = null;
    openGroups.value = 0;
    loading.value = false;
  });

  return { data, openGroups, loading, refresh };
});
