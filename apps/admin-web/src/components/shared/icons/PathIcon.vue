<script setup lang="ts">
// A stroke icon drawn from a list of path `d` strings (ledger-icons.ts): currentColor, round caps and joins, every
// size an attribute (the CSP allows no inline style).
withDefaults(defineProps<{ paths: readonly string[]; size?: number; stroke?: number }>(), { size: 16, stroke: 1.75 });
</script>

<template>
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    :width="size"
    :height="size"
    fill="none"
    stroke="currentColor"
    :stroke-width="stroke"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="(d, i) in paths" :key="i" :d="d" />
  </svg>
</template>
