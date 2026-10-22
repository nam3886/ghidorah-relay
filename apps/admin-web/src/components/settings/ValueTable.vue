<script setup lang="ts">
import { computed } from "vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import SectionState from "./SectionState.vue";
import type { ValueRow } from "./settings-value";
import "./settings-grid.css";
import "./settings-skeleton.css";

// The read-only list of a card as a table: Name | Value, and Reason when `reasons`. A row with a `group` sits under a
// band of that name (a new band whenever the group changes, in the order given). A phone gets one block per row.
// The card's failure and its wait are this card's too: it shows the error with Retry, "None" when empty and bars while
// the first read runs.
const props = defineProps<{
  rows: ValueRow[];
  loading: boolean;
  error: string | null;
  label: string;
  reasons?: boolean;
}>();
const emit = defineEmits<{ retry: [] }>();
const { isPhone } = useBreakpoint();

const groups = computed(() => {
  const out: { name: string; rows: ValueRow[] }[] = [];
  for (const row of props.rows) {
    const name = row.group ?? "";
    let block = out[out.length - 1];
    if (!block || block.name !== name) {
      block = { name, rows: [] };
      out.push(block);
    }
    block.rows.push(row);
  }
  return out;
});
const skeleton = computed(() => props.loading && !props.rows.length && !props.error);
const SKELETON = [
  { name: "120px", value: "72px", why: "220px" },
  { name: "168px", value: "56px", why: "168px" },
  { name: "96px", value: "88px", why: "240px" },
  { name: "144px", value: "64px", why: "192px" },
  { name: "104px", value: "80px", why: "208px" },
];
const columns = computed(() => (props.reasons ? 3 : 2));
</script>

<template>
  <div class="set-value" :aria-busy="loading">
    <SectionState :error="error" :empty="!rows.length && !loading" empty-text="None" @retry="emit('retry')" />

    <ul v-if="skeleton && isPhone" class="set-list" aria-hidden="true">
      <li v-for="w in SKELETON" :key="w.name">
        <i class="set-skel" :style="{ width: w.name }" />
        <i class="set-skel" :style="{ width: w.value }" />
      </li>
    </ul>
    <template v-else-if="!error && rows.length && isPhone">
      <template v-for="g in groups" :key="g.name">
        <h3 v-if="g.name" class="set-list__group">{{ g.name }}</h3>
        <ul class="set-list" :aria-label="g.name ? `${label} of ${g.name}` : label">
          <li v-for="row in g.rows" :key="row.name">
            <span class="set-list__name">{{ row.name }}</span>
            <span class="set-list__value">{{ row.value }}</span>
            <span v-if="reasons" class="set-list__why">{{ row.reason }}</span>
          </li>
        </ul>
      </template>
    </template>

    <div v-else-if="!error && (rows.length || skeleton)" class="set-scroll">
      <table class="set-table" :aria-label="label">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Value</th>
            <th v-if="reasons" scope="col">Reason</th>
          </tr>
        </thead>
        <tbody v-if="skeleton" aria-hidden="true">
          <tr v-for="w in SKELETON" :key="w.name" class="set-skel-row">
            <td><i class="set-skel" :style="{ width: w.name }" /></td>
            <td><i class="set-skel" :style="{ width: w.value }" /></td>
            <td v-if="reasons"><i class="set-skel" :style="{ width: w.why }" /></td>
          </tr>
        </tbody>
        <tbody v-else>
          <template v-for="g in groups" :key="g.name">
            <tr v-if="g.name" class="set-table__group">
              <th scope="rowgroup" :colspan="columns">{{ g.name }}</th>
            </tr>
            <tr v-for="row in g.rows" :key="row.name">
              <td class="set-table__name">{{ row.name }}</td>
              <td>{{ row.value }}</td>
              <td v-if="reasons" class="set-table__why">{{ row.reason }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
