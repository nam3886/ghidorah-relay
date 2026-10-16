<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useBreakpoint } from "@/composables/use-breakpoint";

// The one pager of every list, over el-pagination. The total is always known (offset paging). The view owns `offset`
// and `limit`: `move` carries the new offset, `size` the new page size (the view stores it and goes back to offset 0).
//   from 768 px: "Showing 1-10 of 1,321", a page-size select, Previous, numbered pages, Next (no jumper)
//   phone:       Previous, "1 / 133", Next, 44 px targets
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
// the page size in use stays in the select even when it is not in the list
const pageSizes = computed(() =>
  props.sizes.includes(props.limit) ? props.sizes : [...props.sizes, props.limit].sort((a, b) => a - b),
);
const showing = computed(() => {
  if (props.total === 0) return "Showing 0 of 0";
  const from = props.offset + 1;
  const to = Math.min(props.offset + props.limit, props.total);
  return `Showing ${from}–${to} of ${props.total.toLocaleString("en-US")}`;
});
// a size change can make Element Plus clamp the page and report it too: only the size event is real
const resizing = ref(false);

function onPage(p: number) {
  if (resizing.value) return;
  emit("move", (p - 1) * props.limit);
}
function onSize(s: number) {
  resizing.value = true;
  emit("size", s);
  void nextTick(() => (resizing.value = false));
}
</script>

<template>
  <nav class="pager" aria-label="Pages" :data-phone="isPhone">
    <span v-if="!isPhone" class="pager__text">{{ showing }}</span>
    <el-pagination
      background
      :layout="isPhone ? 'prev, slot, next' : 'sizes, prev, pager, next'"
      prev-text="Previous"
      next-text="Next"
      :total="props.total"
      :page-size="props.limit"
      :page-sizes="pageSizes"
      :current-page="currentPage"
      @update:current-page="onPage"
      @size-change="onSize"
    >
      <span class="pager__text pager__count">{{ currentPage }} / {{ pageCount }}</span>
    </el-pagination>
  </nav>
</template>

<style scoped>
.pager {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 4px 12px;
  padding: 14px 16px;
  border-top: 1px solid var(--rl-line);
  --el-pagination-button-height: 32px;
}
.pager[data-phone="true"] {
  justify-content: space-between;
  padding: 8px 16px;
  --el-pagination-button-height: 44px;
}
.pager__text {
  color: var(--rl-muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.pager__count {
  padding: 0 8px;
}
</style>
