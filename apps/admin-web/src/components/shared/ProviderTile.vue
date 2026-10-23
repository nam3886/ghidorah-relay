<script setup lang="ts">
import { providerClass } from "@/lib/providers";

// A provider's mark: a square tile in its tone with the first letter of its name, white. Decorative: the name is
// always written beside it. A status is never drawn this way (that is StatusChip's pill with a dot).
defineProps<{ provider: string }>();
</script>

<template>
  <span class="ptile" :class="providerClass(provider)" aria-hidden="true">{{ provider.charAt(0).toUpperCase() }}</span>
</template>

<style scoped>
.ptile {
  display: inline-grid;
  place-items: center;
  flex: none;
  width: 20px;
  height: 20px;
  border-radius: 6px;
  background: var(--pc);
  color: var(--rl-on-primary);
  font: 700 11px/1 var(--rl-font);
}
</style>
