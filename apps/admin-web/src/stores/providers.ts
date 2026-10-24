import { defineStore } from "pinia";
import { ref, shallowRef } from "vue";
import { getProvidersSummary, type ProvidersSummary } from "@/api/shell";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";

// The provider summary (GET /admin/providers/summary) behind the provider strip: the page that shows the strip reads
// it again with its own refresh. Only the newest answer draws; a failed read keeps the last one and toasts.
export const useProvidersStore = defineStore("providers", () => {
  const data = shallowRef<ProvidersSummary | null>(null);
  // the range of the figures: Jobs switches it (Today, 7 days, 30 days) and puts it back to today when it leaves
  const range = ref("today");
  const latest = useLatest();

  async function refresh(): Promise<void> {
    const ticket = latest.next();
    try {
      const out = await getProvidersSummary(range.value);
      if (latest.isCurrent(ticket)) data.value = out;
    } catch (err) {
      if (latest.isCurrent(ticket)) notify.error(errorText(err));
    }
  }

  useAuthStore().onSignOut(() => {
    latest.invalidate();
    data.value = null;
  });

  return { data, range, refresh };
});
