<script setup lang="ts">
import { ref } from "vue";
import CopyButton from "@/components/shared/CopyButton.vue";

// The raw key of a key just created, shown once. The view holds it in its own local state and passes it here: this
// component keeps nothing of its own, so Dismiss (the view clearing it) removes the key from the page.
defineProps<{ rawKey: string }>();
defineEmits<{ dismiss: [] }>();

const field = ref<HTMLInputElement | null>(null);
</script>

<template>
  <section class="callout" aria-label="One-time key" data-testid="new-key">
    <p class="callout__title">Copy this key now, it is not shown again.</p>
    <div class="callout__row">
      <input
        ref="field"
        class="callout__field"
        :value="rawKey"
        readonly
        aria-label="New API key"
        autocomplete="off"
        spellcheck="false"
        @focus="field?.select()"
      />
      <CopyButton :text="() => rawKey" size="default" :select-from="() => field" />
      <el-button @click="$emit('dismiss')">Dismiss</el-button>
    </div>
    <p class="callout__note">
      Only a hash is stored: once this box is gone (a reload, another tab) the key cannot be shown again.
    </p>
  </section>
</template>

<style scoped>
.callout {
  margin-bottom: 12px;
  padding: 12px 16px;
  color: var(--rl-warn);
  background: var(--rl-warn-bg);
  border-radius: var(--el-border-radius-base, 6px);
}
.callout__title {
  margin: 0 0 8px;
  font-weight: 600;
}
.callout__row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.callout__field {
  flex: 1;
  min-width: 0;
  height: 32px;
  padding: 0 10px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 13px;
  color: var(--rl-fg);
  background: var(--rl-surface);
  border: 1px solid var(--rl-line-strong);
  border-radius: var(--el-border-radius-base, 6px);
}
.callout__note {
  margin: 8px 0 0;
  font-size: 12px;
}
</style>
