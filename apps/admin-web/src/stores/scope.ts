import { defineStore } from "pinia";
import { computed, ref, shallowRef } from "vue";
import { type CatalogModel, getModels } from "@/api/shell";
import { useAuthStore } from "@/stores/auth";

// The global provider filter ("" = All) and the model catalog it is built from. The filter is never persisted. It
// applies to the stats, Accounts, Check all / Refresh catalog, Jobs, Keys, Logs, Alerts, Settings (its tab: "" is General) and measured prices. The pages
// that work on one provider at a time (Test, Prompt frames) share `only` instead ("" until such a page picks one).
// Views read `provider` and pass it as the `provider` query (an empty one is dropped).
export const useScopeStore = defineStore("scope", () => {
  const provider = ref("");
  const only = ref("");
  // Whether the one-provider strip is off (a write of the page is in flight); the page that sets it clears it.
  const stripLocked = ref(false);
  const models = shallowRef<CatalogModel[] | null>(null);
  const providers = computed(() => [...new Set((models.value ?? []).map((m) => m.owned_by))].sort());

  // The catalog (every model of every provider) is read once and shared by the filter, the key form and Import.
  let loading: Promise<CatalogModel[]> | null = null;
  function loadCatalog(): Promise<CatalogModel[]> {
    if (models.value) return Promise.resolve(models.value);
    loading ??= getModels()
      .then((out) => (models.value = out.data))
      .finally(() => {
        loading = null;
      });
    return loading;
  }

  function reset(): void {
    provider.value = "";
    only.value = "";
    stripLocked.value = false;
    models.value = null;
  }
  useAuthStore().onSignOut(reset);

  return { provider, only, stripLocked, models, providers, loadCatalog, reset };
});
