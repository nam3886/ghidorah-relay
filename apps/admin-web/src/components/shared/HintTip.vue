<script setup lang="ts">
import { ref } from "vue";

// A short note on a piece of text, drawn on the menu surface (D125) instead of the browser's own `title` tooltip. It
// shows on hover AND when the text takes the keyboard focus (the span is a tab stop), and Escape closes it while the
// focus stays where it is. `text` is plain text, never HTML. The note is also the text's description for a screen
// reader (Element Plus links it).
// `mono`: the note is a value (a JSON table, an id), drawn in the mono font with its own line breaks kept.
const props = withDefaults(defineProps<{ text: string; placement?: "top" | "bottom"; mono?: boolean }>(), {
  placement: "top",
  mono: false,
});
const tip = ref<{ hide: () => void } | null>(null);
// The note is up: its Escape is its own and goes no further, so a drawer around it stays open. With no note up the
// Escape goes on to whatever is listening (a drawer asks to close).
const shown = ref(false);
function onEscape(e: KeyboardEvent): void {
  if (shown.value) e.stopPropagation();
  tip.value?.hide();
}
</script>

<template>
  <el-tooltip
    ref="tip"
    :content="props.text"
    :placement="props.placement"
    effect="light"
    :trigger="['hover', 'focus']"
    :popper-class="props.mono ? 'rl-tip rl-tip--mono' : 'rl-tip'"
    :show-after="150"
    :show-arrow="false"
    :visible="shown"
    @update:visible="(open: boolean) => (shown = open)"
  >
    <span class="hint-tip" tabindex="0" @keydown.esc="onEscape"><slot /></span>
  </el-tooltip>
</template>

<style scoped>
.hint-tip {
  border-radius: 4px;
  cursor: help;
}
</style>
