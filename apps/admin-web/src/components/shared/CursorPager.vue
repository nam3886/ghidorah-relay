<script setup lang="ts">
// "Page N" with Prev and Next for a keyset list. The view keeps the cursor stack; this only reports the clicks.
// Prev is disabled on page 1, Next when the server gave no `next_cursor`.
const props = defineProps<{ page: number; hasNext: boolean }>();
const emit = defineEmits<{ prev: []; next: [] }>();
</script>

<template>
  <nav class="pager" aria-label="Pages">
    <span class="pager__text">Page {{ props.page }}</span>
    <el-button size="small" :disabled="props.page <= 1" @click="emit('prev')">Prev</el-button>
    <el-button size="small" :disabled="!props.hasNext" @click="emit('next')">Next</el-button>
  </nav>
</template>

<style scoped>
.pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
}
.pager__text {
  color: var(--rl-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
</style>
