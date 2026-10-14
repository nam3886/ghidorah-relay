<script setup lang="ts">
import { computed } from "vue";

// "from-to of total" with Prev and Next. The view owns the offset: it gets `move` with the new offset.
const props = defineProps<{ offset: number; limit: number; total: number }>();
const emit = defineEmits<{ move: [offset: number] }>();

const from = computed(() => (props.total === 0 ? 0 : props.offset + 1));
const to = computed(() => Math.min(props.offset + props.limit, props.total));
const hasPrev = computed(() => props.offset > 0);
const hasNext = computed(() => props.offset + props.limit < props.total);
</script>

<template>
  <nav class="pager" aria-label="Pages">
    <span class="pager__text">{{ from }}-{{ to }} of {{ props.total }}</span>
    <el-button size="small" :disabled="!hasPrev" @click="emit('move', Math.max(0, props.offset - props.limit))"
      >Prev</el-button
    >
    <el-button size="small" :disabled="!hasNext" @click="emit('move', props.offset + props.limit)">Next</el-button>
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
