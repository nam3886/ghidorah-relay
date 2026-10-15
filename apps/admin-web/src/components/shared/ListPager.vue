<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";

// One pager over el-pagination, in two modes.
//   offset: the total is known. Total, a page-size select, numbered pages and a jumper. The view owns `offset` and
//           `limit`: `move` carries the new offset, `size` the new page size (the view stores it and goes back to
//           offset 0). Under 640 px a compact layout (small, 5 page buttons) with the total as text above it.
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
  { mode: "offset", offset: 0, limit: 10, total: 0, sizes: () => [10, 20, 50, 100], page: 1, hasNext: false },
);
const emit = defineEmits<{ move: [offset: number]; size: [limit: number]; prev: []; next: [] }>();

const currentPage = computed(() => Math.floor(props.offset / props.limit) + 1);
// the page size in use stays in the select even when it is not in the list
const pageSizes = computed(() =>
  props.sizes.includes(props.limit) ? props.sizes : [...props.sizes, props.limit].sort((a, b) => a - b),
);
// a size change can make Element Plus clamp the page and report it too: only the size event is real
const resizing = ref(false);

const FULL_LAYOUT = "total, sizes, prev, pager, next, jumper";
const COMPACT_LAYOUT = "prev, pager, next";
const narrow = ref(false);
let query: MediaQueryList | undefined;
const onQuery = (e: { matches: boolean }) => (narrow.value = e.matches);
onMounted(() => {
  if (typeof window.matchMedia !== "function") return;
  query = window.matchMedia("(max-width: 639px)");
  narrow.value = query.matches;
  query.addEventListener("change", onQuery);
});
onBeforeUnmount(() => query?.removeEventListener("change", onQuery));

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
  <nav class="pager" aria-label="Pages" :data-mode="props.mode" :data-narrow="narrow">
    <span v-if="narrow && props.mode === 'offset'" class="pager__text">{{ props.total }} total</span>
    <el-pagination
      v-if="props.mode === 'offset'"
      background
      :small="narrow"
      :pager-count="narrow ? 5 : 7"
      :layout="narrow ? COMPACT_LAYOUT : FULL_LAYOUT"
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
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 4px 12px;
  padding-top: 12px;
}
.pager__text {
  color: var(--rl-muted);
  font-variant-numeric: tabular-nums;
}
</style>
