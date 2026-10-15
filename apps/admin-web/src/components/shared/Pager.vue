<script setup lang="ts">
import { computed, nextTick, ref } from "vue";

// One pager over el-pagination, in two modes.
//   offset: the total is known. Total, a page-size select and numbered pages. The view owns `offset` and `limit`:
//           `move` carries the new offset, `size` the new page size (the view stores it and goes back to offset 0).
//   cursor: a keyset list with no total. Only Prev and "Page N" and Next, no page numbers. The view keeps the cursor
//           stack: `prev` and `next` only report the clicks. Next is disabled when `hasNext` is false.
const props = withDefaults(
  defineProps<{
    mode?: "offset" | "cursor";
    // offset mode
    offset?: number;
    limit?: number;
    total?: number;
    sizes?: number[];
    // cursor mode
    page?: number;
    hasNext?: boolean;
  }>(),
  { mode: "offset", offset: 0, limit: 50, total: 0, sizes: () => [25, 50, 100, 200], page: 1, hasNext: false },
);
const emit = defineEmits<{ move: [offset: number]; size: [limit: number]; prev: []; next: []; }>();

const currentPage = computed(() => Math.floor(props.offset / props.limit) + 1);
// the page size in use stays in the select even when it is not in the list
const pageSizes = computed(() => (props.sizes.includes(props.limit) ? props.sizes : [...props.sizes, props.limit].sort((a, b) => a - b)));
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
  <nav class="pager" aria-label="Pages" :data-mode="props.mode">
    <el-pagination
      v-if="props.mode === 'offset'"
      background
      layout="total, sizes, prev, pager, next"
      :total="props.total"
      :page-size="props.limit"
      :page-sizes="pageSizes"
      :current-page="currentPage"
      @update:current-page="onPage"
      @size-change="onSize"
    />
    <el-pagination
      v-else
      background
      layout="prev, slot, next"
      :page-size="1"
      :page-count="props.hasNext ? props.page + 1 : props.page"
      :current-page="props.page"
      @update:current-page="() => {}"
      @prev-click="emit('prev')"
      @next-click="emit('next')"
    >
      <span class="pager__text">Page {{ props.page }}</span>
    </el-pagination>
  </nav>
</template>

<style scoped>
.pager {
  display: flex;
  justify-content: flex-end;
  padding-top: 12px;
  overflow-x: auto;
}
.pager__text {
  color: var(--rl-muted);
  font-variant-numeric: tabular-nums;
}
</style>
