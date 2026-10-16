<script setup lang="ts">
import { computed } from "vue";
import { RefreshLeft } from "@element-plus/icons-vue";

// Points charged and points kept, from the list API's `price` and `net_charged`. A refund shows as the struck charge,
// a refund icon and the net (`1 -> 0`), never a hidden flag. A missing net (a job not charged yet) is a dash.
const props = defineProps<{ price: number | null | undefined; net: number | null | undefined }>();

const refunded = computed(
  () => typeof props.price === "number" && typeof props.net === "number" && props.net < props.price,
);
const netText = computed(() => (typeof props.net === "number" ? String(props.net) : "-"));
</script>

<template>
  <span class="money" data-testid="money-cell" :data-refunded="refunded">
    <template v-if="refunded">
      <s class="money__price">{{ props.price }}</s>
      <el-icon class="money__icon" role="img" aria-label="refunded"><RefreshLeft /></el-icon>
    </template>
    <span class="money__net">{{ netText }}</span>
  </span>
</template>

<style scoped>
.money {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.money__price {
  color: var(--rl-faint);
  text-decoration: line-through;
}
.money__icon {
  color: var(--rl-ok);
  font-size: 13px;
}
</style>
