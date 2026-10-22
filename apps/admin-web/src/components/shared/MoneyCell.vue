<script setup lang="ts">
import { computed } from "vue";
import { IconUndo } from "./icons";

// Points charged and points kept, from the list API's `price` and `net_charged`. A refund shows as the struck charge,
// a refund icon and the net (`1 -> 0`), never a hidden flag. A missing net (a job not charged yet) is a dash.
const props = defineProps<{ price: number | null | undefined; net: number | null | undefined }>();

const refunded = computed(
  () => typeof props.price === "number" && typeof props.net === "number" && props.net < props.price,
);
// a net not known yet is an em dash
const netText = computed(() => (typeof props.net === "number" ? String(props.net) : "—"));
</script>

<template>
  <span class="money" data-testid="money-cell" :data-refunded="refunded">
    <template v-if="refunded">
      <s class="money__price">{{ props.price }}</s>
      {{ " "
      }}<IconUndo class="money__icon" :size="13" :stroke="2" role="img" aria-hidden="false" aria-label="refunded" />{{
        " "
      }}
    </template>
    <span class="money__net">{{ netText }}</span>
  </span>
</template>

<style scoped>
.money {
  white-space: nowrap;
  color: var(--rl-fg);
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
  vertical-align: -2px;
}
</style>
