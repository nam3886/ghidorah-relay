<script setup lang="ts">
import { ref } from "vue";
import type { InputInstance } from "element-plus";
import CopyButton from "@/components/shared/CopyButton.vue";

// The raw key of a key just created, shown once. The view holds it in its own local state and passes it here: this
// component keeps nothing of its own, so Dismiss (the view clearing it) removes the key from the page.
defineProps<{ rawKey: string }>();
defineEmits<{ dismiss: [] }>();

const field = ref<InputInstance | null>(null);
</script>

<template>
  <el-alert
    type="warning"
    show-icon
    :closable="false"
    title="Copy this key now, it is not shown again."
    class="callout"
    aria-label="One-time key"
    data-testid="new-key"
  >
    <div class="callout__row">
      <el-input
        ref="field"
        class="callout__field"
        :model-value="rawKey"
        readonly
        aria-label="New API key"
        autocomplete="off"
        spellcheck="false"
        @focus="field?.select()"
      />
      <CopyButton :text="() => rawKey" :select-from="() => field?.input" />
      <el-button @click="$emit('dismiss')">Dismiss</el-button>
    </div>
    <p class="callout__note">
      Only a hash is stored: once this box is gone (a reload, another tab) the key cannot be shown again.
    </p>
  </el-alert>
</template>

<style scoped>
.callout {
  margin-bottom: 12px;
}
.callout__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.callout__field {
  flex: 1;
  min-width: 12rem;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
.callout__note {
  margin: 8px 0 0;
  font-size: 12px;
}
</style>
