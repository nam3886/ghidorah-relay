<script setup lang="ts">
import { computed } from "vue";
import { useUiStore } from "@/stores/ui";
import logoDark from "@/assets/brand/ghidorah-logo-dark.svg";
import logoLight from "@/assets/brand/ghidorah-logo-light.svg";
import symbol from "@/assets/brand/ghidorah-symbol-purple.svg";

// The Ghidorah logo, never stretched or recoloured: the dark file has light letters (for the dark theme), the light
// file has dark letters. In the header, under 640 px, the symbol (32 px) and the word "Relay" replace the wide logo.
// The login page always shows the wide logo.
const props = withDefaults(defineProps<{ variant?: "header" | "login" }>(), { variant: "header" });
const ui = useUiStore();
const logo = computed(() => (ui.theme === "dark" ? logoDark : logoLight));
</script>

<template>
  <span class="brand" :class="`brand--${props.variant}`" data-testid="brand">
    <img class="brand__full" :src="logo" alt="Ghidorah Relay" />
    <template v-if="props.variant === 'header'">
      <img class="brand__symbol" :src="symbol" alt="Ghidorah" />
      <span class="brand__word">Relay</span>
    </template>
  </span>
</template>

<style scoped>
/* clear space: at least a fifth of the symbol's height on every side */
.brand {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px;
}
.brand img {
  display: block;
  width: auto;
}
.brand--header .brand__full {
  height: 30px; /* 186 px wide: the file is 930 by 150 */
}
.brand--login {
  padding: 0 0 8px;
}
.brand--login .brand__full {
  height: 44px;
}
.brand .brand__symbol,
.brand .brand__word {
  display: none;
}
.brand .brand__symbol {
  height: 32px;
}
.brand__word {
  font-size: 18px;
  font-weight: 600;
  color: var(--rl-fg);
}
@media (max-width: 639px) {
  .brand--header .brand__full {
    display: none;
  }
  .brand--header .brand__symbol {
    display: block;
  }
  .brand--header .brand__word {
    display: inline;
  }
  /* (same weight as the `.brand .brand__...` rules above and later in the file, so they win) */
}
</style>
