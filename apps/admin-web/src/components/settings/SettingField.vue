<script setup lang="ts">
import { computed } from "vue";
import type { SettingItem } from "@/api/settings";
import HintTip from "@/components/shared/HintTip.vue";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import { fieldLabel } from "./settings-cards";
import { fullDefault, isLongDefault } from "./settings-value";

// One setting on a card: its label and "edited" while the draft differs from the stored value, the input (a switch
// for a true/false one), the description, what it allows, then its key, where the value comes from and the default,
// with Reset when the database holds a value. A refusal of the last Save is written under it.
const props = defineProps<{
  item: SettingItem;
  draft: string;
  edited: boolean;
  error?: string;
  busy: boolean;
}>();
const emit = defineEmits<{ "update:draft": [text: string]; reset: [] }>();

const shownDefault = computed(() => JSON.stringify(props.item.default));
const longDefault = computed(() => isLongDefault(shownDefault.value));
const id = computed(() => `set-${props.item.key.replace(/[^\w-]/g, "-")}`);
</script>

<template>
  <div class="field" :class="{ 'is-edited': edited, 'has-error': !!error }" :data-key="item.key">
    <label class="field__label" :for="item.type === 'bool' ? undefined : id"
      >{{ fieldLabel(item) }}<span v-if="edited" class="tag">edited</span></label
    >
    <div class="field__input">
      <ToggleSwitch
        v-if="item.type === 'bool'"
        :model-value="draft === 'true'"
        :label="`Value of ${item.key}`"
        :disabled="busy"
        @change="(on: boolean) => emit('update:draft', String(on))"
      />
      <input
        v-else
        :id="id"
        class="input"
        :class="{ 'input--wide': item.type === 'str' || item.type === 'list' || item.type === 'dict' }"
        :value="draft"
        :aria-label="`Value of ${item.key}`"
        :aria-invalid="error ? 'true' : undefined"
        :disabled="busy"
        autocomplete="off"
        spellcheck="false"
        @input="emit('update:draft', ($event.target as HTMLInputElement).value)"
      />
    </div>
    <span class="help">{{ item.description }}</span>
    <span v-if="item.allowed" class="help allowed">Allowed: {{ item.allowed }}</span>
    <span v-if="error" class="error" role="alert">{{ error }}</span>
    <span class="key">
      <span class="key__name">{{ item.key }}</span>
      <span class="key__default" :class="{ 'is-long': longDefault }"
        >{{ item.source }} · default
        <HintTip v-if="longDefault" mono :text="fullDefault(item.default)">{{ shownDefault }}</HintTip
        ><template v-else>{{ shownDefault }}</template></span
      >
      <button
        v-if="item.source === 'db'"
        type="button"
        class="reset"
        :disabled="busy"
        :aria-label="`Reset ${item.key}`"
        @click="emit('reset')"
      >
        Reset
      </button>
    </span>
  </div>
</template>

<style scoped>
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.field__label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
}
.tag {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--rl-primary-soft);
  color: var(--rl-primary-hover);
  font-size: 11px;
  font-weight: 500;
}
.field__input {
  display: flex;
  align-items: center;
  min-height: 34px;
}
.input {
  box-sizing: border-box;
  width: 100%;
  max-width: 160px;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-family: var(--rl-mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
@media (max-width: 767px) {
  .input {
    max-width: none;
    height: 44px;
    font-size: 14px;
  }
}
.input--wide {
  max-width: none;
}
.is-edited .input {
  border-color: var(--rl-primary);
}
.has-error .input {
  border-color: var(--rl-bad);
}
.help {
  color: var(--rl-muted);
  font-size: 12px;
  line-height: 17px;
  overflow-wrap: anywhere;
}
.error {
  color: var(--rl-bad);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.key {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 8px;
  min-width: 0;
  color: var(--rl-faint);
  font-family: var(--rl-mono);
  font-size: 11px;
}
.key__name {
  overflow-wrap: anywhere;
}
.key__default {
  font-variant-numeric: tabular-nums;
}
/* a default longer than the line (a price table) ends in "…"; the whole value is its note */
.key__default.is-long {
  display: flex;
  gap: 1ch;
  min-width: 0;
  max-width: 100%;
  white-space: nowrap;
}
.key__default.is-long :deep(.hint-tip) {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.reset {
  padding: 0;
  border: 0;
  background: none;
  color: var(--rl-primary);
  font: inherit;
  font-family: var(--rl-font);
  font-size: 12px;
  cursor: pointer;
}
.reset:disabled {
  color: var(--rl-faint);
  cursor: default;
}
</style>
