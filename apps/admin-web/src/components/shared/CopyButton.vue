<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { notify } from "@/lib/notify";
import { copyText, type CopyOutcome } from "./clipboard";

const props = withDefaults(
  defineProps<{
    text: string | (() => string);
    label?: string;
    size?: "small" | "default";
    // the element the text sits in: selected when no copy path works, so the user can press Ctrl+C
    selectFrom?: () => HTMLElement | null | undefined;
  }>(),
  { label: "Copy", size: "small", selectFrom: undefined },
);
const emit = defineEmits<{ copied: [outcome: CopyOutcome] }>();

const copied = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;

onBeforeUnmount(() => clearTimeout(timer));

async function copy() {
  const value = typeof props.text === "function" ? props.text() : props.text;
  const outcome = await copyText(value, props.selectFrom);
  if (outcome === "select") notify.info("Press Ctrl+C to copy the selected text");
  else if (outcome === "failed") notify.error("Could not copy: select the text and copy it by hand");
  else {
    copied.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => (copied.value = false), 2000);
  }
  emit("copied", outcome);
}
</script>

<template>
  <el-button :size="size" class="copy-button" @click="copy">{{ copied ? "Copied" : label }}</el-button>
</template>
