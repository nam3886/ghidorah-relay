import { defineStore } from "pinia";
import { shallowRef } from "vue";
import { getProvidersSummary, type ProvidersSummary } from "@/api/shell";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";

// The provider summary (GET /admin/providers/summary) behind the provider strip: the page that shows the strip reads
// it again with its own refresh. Only the newest answer draws; a failed read keeps the last one and toasts.
export const useProvidersStore = defineStore("providers", () => {
  const data = shallowRef<ProvidersSummary | null>(null);
  const latest = useLatest();

  async function refresh(): Promise<void> {
    const ticket = latest.next();
    try {
      const out = await getProvidersSummary();
      if (latest.isCurrent(ticket)) data.value = out;
    } catch (err) {
      if (latest.isCurrent(ticket)) notify.error(errorText(err));
    }
  }

  useAuthStore().onSignOut(() => {
    latest.invalidate();
    data.value = null;
  });

  return { data, refresh };
});
