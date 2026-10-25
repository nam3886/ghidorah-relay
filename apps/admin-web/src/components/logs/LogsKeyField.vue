<script setup lang="ts">
import { computed, onMounted, shallowRef } from "vue";
import { type ApiKey, listKeys } from "@/api/keys";
import { keyName } from "@/components/jobs/job-text";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import { useLogsFilters } from "./logs-filters";

// The Key filter of the Request logs (Logs.dc.html: `Key All keys`, the toolbar's select; a tablet and a phone have it
// in the Filters sheet as a labelled field). The keys are read once when it appears; a key is named as Jobs names it.
// A choice asks for a new search at once.
defineProps<{ field?: boolean }>();
const emit = defineEmits<{ search: [] }>();
const filters = useLogsFilters();

const keys = shallowRef<ApiKey[]>([]);
onMounted(async () => {
  try {
    keys.value = (await listKeys()).items;
  } catch {
    keys.value = []; // the filter keeps All keys; the list itself reports a server that does not answer
  }
});
const options = computed(() => [
  { value: "", label: "All keys" },
  ...keys.value.map((k) => ({ value: k.id, label: keyName(k) })),
]);

function pick(value: string | number): void {
  filters.key = String(value);
  emit("search");
}
</script>

<template>
  <FilterSelect
    :model-value="filters.key"
    :options="options"
    label="Key"
    :variant="field ? 'field' : 'chip'"
    :block="field"
    @update:model-value="pick"
  />
</template>
