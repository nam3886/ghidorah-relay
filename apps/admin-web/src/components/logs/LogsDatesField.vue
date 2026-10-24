<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import "./logs-date-field.css";
import { useLogsFilters } from "./logs-filters";
import { RANGE_SHORTCUTS } from "./log-window";

// The date range of the Request logs: one Element Plus range picker drawn as the Ledger field (calendar icon, From, to,
// To; MenusToasts "Request logs · date range" for the open panel). A range chosen, typed and confirmed, or cleared asks
// for a new search at once. `label` draws a visible name above it (the Filters sheet of a tablet or phone).
defineProps<{ label?: string }>();
const emit = defineEmits<{ search: [] }>();
const filters = useLogsFilters();
const { isPhone } = useBreakpoint();

// The range keeps the store's two `YYYY-MM-DD` texts as they are (the picker's `value-format`): never a Date, so the time
// zone cannot move a day. The picker reports `null` when cleared; the store holds "" for both then.
const range = computed<[string, string] | null>({
  get: () => (filters.from && filters.to ? ([filters.from, filters.to] as [string, string]) : null),
  set: (v: [string, string] | null) => {
    filters.from = v?.[0] ?? "";
    filters.to = v?.[1] ?? "";
  },
});
const shortcuts = RANGE_SHORTCUTS.map((s) => ({ text: s.text, value: s.value }));

// The picker gives both of its inputs the same attributes, so each is named here: From and To.
const dates = ref<HTMLElement>();
function nameInputs(): void {
  const inputs = dates.value?.querySelectorAll("input");
  inputs?.[0]?.setAttribute("aria-label", "From");
  inputs?.[1]?.setAttribute("aria-label", "To");
}
onMounted(nameInputs);
watch(isPhone, () => void nextTick(nameInputs));
</script>

<template>
  <div ref="dates" class="lf__date" :class="{ 'has-label': !!label }">
    <span v-if="label" class="lf__date-label">{{ label }}</span>
    <el-date-picker
      v-model="range"
      type="daterange"
      :id="['logs-from', 'logs-to']"
      value-format="YYYY-MM-DD"
      format="YYYY-MM-DD"
      range-separator="to"
      start-placeholder="From"
      end-placeholder="To"
      :shortcuts="shortcuts"
      clearable
      popper-class="rl-date-popper rl-range-popper"
      @change="emit('search')"
    />
  </div>
</template>

<style scoped>
.lf__date-label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 700;
}
</style>
