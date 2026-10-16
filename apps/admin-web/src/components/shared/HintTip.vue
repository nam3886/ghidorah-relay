<script setup lang="ts">
import { ref } from "vue";

// A short note on a piece of text, drawn on the menu surface (D125) instead of the browser's own `title` tooltip. It
// shows on hover AND when the text takes the keyboard focus (the span is a tab stop), and Escape closes it while the
// focus stays where it is. `text` is plain text, never HTML. The note is also the text's description for a screen
// reader (Element Plus links it).
const props = withDefaults(defineProps<{ text: string; placement?: "top" | "bottom" }>(), { placement: "top" });
const tip = ref<{ hide: () => void } | null>(null);
</script>

<template>
  <el-tooltip
    ref="tip"
    :content="props.text"
    :placement="props.placement"
    effect="light"
    :trigger="['hover', 'focus']"
    popper-class="rl-tip"
    :show-after="150"
    :show-arrow="false"
  >
    <span class="hint-tip" tabindex="0" @keydown.esc="tip?.hide()"><slot /></span>
  </el-tooltip>
</template>

<style scoped>
.hint-tip {
  border-radius: 4px;
  cursor: help;
}
</style>
