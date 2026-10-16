<script setup lang="ts">
import { computed, watch } from "vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import FilterSelect from "./FilterSelect.vue";
import { pageSlots } from "./page-list";

// The one pager of every list, drawn exactly as the canvas draws it. The total is always known (offset paging). The
// view owns `offset` and `limit`: `move` carries the new offset, `size` the new page size (the view stores it and goes
// back to offset 0).
//   from 768 px: "Showing 1-10 of 1,321", a page-size select, Previous, numbered pages (`1 2 3 … 133`), Next
//   phone:       Previous, "1 / 133", Next, 44 px targets
// Element Plus's el-pagination cannot draw `1 2 3 … 133` (its pager-count is odd and at least 5), so the page list is
// ours (page-list.ts). A disabled arrow is `aria-disabled`, not `disabled`, as the canvas draws it.
const props = withDefaults(defineProps<{ offset?: number; limit?: number; total?: number; sizes?: number[] }>(), {
  offset: 0,
  limit: 10,
  total: 0,
  sizes: () => [10, 20, 50, 100],
});
const emit = defineEmits<{ move: [offset: number]; size: [limit: number] }>();
const { isPhone } = useBreakpoint();

const currentPage = computed(() => Math.floor(props.offset / props.limit) + 1);
const pageCount = computed(() => Math.max(1, Math.ceil(props.total / props.limit)));
const slots = computed(() => pageSlots(currentPage.value, pageCount.value));
const hasPrev = computed(() => currentPage.value > 1);
const hasNext = computed(() => currentPage.value < pageCount.value);
// the page size in use stays in the select even when it is not in the list
const sizeOptions = computed(() =>
  (props.sizes.includes(props.limit) ? props.sizes : [...props.sizes, props.limit].sort((a, b) => a - b)).map((s) => ({
    value: s,
    label: `${s} / page`,
  })),
);
const showing = computed(() => {
  if (props.total === 0) return "Showing 0 of 0";
  const from = props.offset + 1;
  const to = Math.min(props.offset + props.limit, props.total);
  return `Showing ${from}–${to} of ${props.total.toLocaleString("en-US")}`;
});

// A list that shrank under the page on screen (a reload, a filter): the pager moves itself to the last page.
watch(
  () => props.total,
  () => {
    if (currentPage.value > pageCount.value) emit("move", (pageCount.value - 1) * props.limit);
  },
);

function goto(page: number): void {
  if (page < 1 || page > pageCount.value || page === currentPage.value) return;
  emit("move", (page - 1) * props.limit);
}
</script>

<template>
  <nav class="pager" aria-label="Pages" :data-phone="isPhone">
    <span v-if="!isPhone" class="pager__text">{{ showing }}</span>
    <div class="pager__group">
      <FilterSelect
        v-if="!isPhone"
        class="pager__size"
        size="pager"
        aria-label="Page size"
        :chevron="false"
        :model-value="props.limit"
        :options="sizeOptions"
        @update:model-value="(v) => emit('size', Number(v))"
      />
      <button
        type="button"
        class="pager__step btn-prev"
        :class="{ 'is-disabled': !hasPrev }"
        :aria-disabled="!hasPrev"
        aria-label="Previous page"
        @click="goto(currentPage - 1)"
      >
        Previous
      </button>
      <span v-if="isPhone" class="pager__text pager__count">{{ currentPage }} / {{ pageCount }}</span>
      <template v-else>
        <template v-for="(slot, i) in slots" :key="`${i}-${slot}`">
          <span v-if="slot === '…'" class="pager__gap">…</span>
          <button
            v-else
            type="button"
            class="pager__page number"
            :class="{ 'is-active': slot === currentPage, 'is-wide': slot === pageCount && String(slot).length >= 3 }"
            :aria-current="slot === currentPage ? 'page' : undefined"
            @click="goto(slot)"
          >
            {{ slot }}
          </button>
        </template>
      </template>
      <button
        type="button"
        class="pager__step btn-next"
        :class="{ 'is-disabled': !hasNext }"
        :aria-disabled="!hasNext"
        aria-label="Next page"
        @click="goto(currentPage + 1)"
      >
        Next
      </button>
    </div>
  </nav>
</template>

<style scoped>
.pager {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-top: 1px solid var(--rl-line);
}
.pager__group {
  display: flex;
  align-items: center;
  gap: 4px;
}
.pager__size {
  margin-right: 8px;
}
.pager__text {
  color: var(--rl-muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.pager__step,
.pager__page {
  height: 32px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}
.pager__step {
  padding: 0 10px;
}
.pager__step.is-disabled {
  color: var(--rl-faint);
  cursor: default;
}
.pager__page {
  min-width: 32px;
  padding: 0;
}
.pager__page.is-wide {
  min-width: 40px;
}
.pager__page:not(.is-active):hover,
.pager__step:not(.is-disabled):hover {
  background: var(--rl-raised);
}
.pager__page.is-active {
  background: var(--rl-primary);
  color: #ffffff;
  font-weight: 600;
  cursor: default;
}
.pager__gap {
  padding: 0 4px;
  color: var(--rl-faint);
}
.pager[data-phone="true"] {
  gap: 8px;
  padding: 8px 16px;
}
.pager[data-phone="true"] .pager__group {
  flex: 1;
  justify-content: space-between;
  gap: 8px;
}
.pager[data-phone="true"] .pager__step {
  min-width: 88px;
  height: 44px;
  padding: 0 12px;
  font-size: 14px;
}
.pager[data-phone="true"] .pager__step:not(.is-disabled) {
  font-weight: 500;
}
</style>
