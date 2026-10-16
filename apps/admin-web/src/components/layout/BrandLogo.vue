<script setup lang="ts">
import { computed } from "vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useUiStore } from "@/stores/ui";
import logoDark from "@/assets/brand/ghidorah-logo-dark.svg";
import logoLight from "@/assets/brand/ghidorah-logo-light.svg";

// The Ghidorah logo, never stretched or recoloured: the dark file has light letters (for the dark theme), the light
// file has dark letters. Always the wide logo: 26 px high in the top bar (24 px on a phone), 28 px on the login page.
const props = withDefaults(defineProps<{ variant?: "header" | "login" }>(), { variant: "header" });
const ui = useUiStore();
const { isPhone } = useBreakpoint();
const logo = computed(() => (ui.theme === "dark" ? logoDark : logoLight));
</script>

<template>
  <span class="brand" :class="[`brand--${props.variant}`, { 'brand--phone': isPhone }]" data-testid="brand">
    <img class="brand__full" :src="logo" alt="Ghidorah" />
  </span>
</template>

<style scoped>
.brand {
  display: inline-flex;
  align-items: center;
}
.brand img {
  display: block;
  width: auto;
  height: 26px; /* 161 px wide: the file is 930 by 150 */
}
.brand--phone img {
  height: 24px;
}
.brand--login img {
  height: 28px;
}
</style>
