<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { SettingItem } from "@/api/settings";
import HintTip from "@/components/shared/HintTip.vue";
import IconButton from "@/components/shared/IconButton.vue";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import { fieldLabel } from "./settings-cards";
import { fullDefault, isLongDefault } from "./settings-value";

// One setting (SettingsForms). `card`: its own card, the label with "edited" while the draft differs from the stored
// value, the input (a switch for a true/false one), the description, what it allows, a refusal of the last Save, then
// its key, where the value comes from and the default, with Reset (an icon button) when the database holds a value.
// `row`: one line of a section card (Settings, "Taking jobs"): the label and its help at the left, "Changed", the input
// and Reset at the right.
// A setting with `choices` is an Element Plus select (a list setting: a multiple select, the tags in the order chosen:
// that order is the setting's, so a source is moved last by removing it and choosing it again). A secret shows only
// "Set · …abcd" or "Not set", with Replace (a password input whose text is the draft Save sends) and Clear (a delete).
const props = withDefaults(
  defineProps<{
    item: SettingItem;
    draft: string;
    edited: boolean;
    error?: string;
    busy: boolean;
    layout?: "card" | "row";
  }>(),
  { error: "", layout: "card" },
);
const emit = defineEmits<{ "update:draft": [text: string]; reset: [] }>();

const shownDefault = computed(() => JSON.stringify(props.item.default));
const longDefault = computed(() => isLongDefault(shownDefault.value));
const id = computed(() => `set-${props.item.key.replace(/[^\w-]/g, "-")}`);
const bool = computed(() => props.item.type === "bool");
// Reset is offered for a value the database holds
const resettable = computed(() => props.item.source === "db" && !props.item.secret);
const wide = computed(() => ["str", "list", "dict"].includes(props.item.type));
const ALERT = ["M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z", "M12 8v4", "M12 16h.01"];

const choices = computed(() => props.item.choices ?? null);
const multiple = computed(() => props.item.type === "list");
// the select's value: the draft parsed (a list, or one string), the stored value when the draft is not JSON
const chosen = computed<string | string[]>(() => {
  try {
    return JSON.parse(props.draft) as string | string[];
  } catch {
    return multiple.value ? [] : props.draft;
  }
});
const choose = (v: string | string[]): void => emit("update:draft", multiple.value ? JSON.stringify(v) : String(v));

const secret = computed(() => !!props.item.secret);
const isSet = computed(() => props.item.source === "db");
const secretState = computed(() =>
  !isSet.value ? "Not set" : props.item.hint ? `Set · ends in ${props.item.hint}` : "Set",
);
// Replace opens the input; a fresh read of the settings (after Save, Clear or Discard) closes it
const replacing = ref(false);
watch(
  () => props.item,
  () => (replacing.value = false),
);
function cancelReplace(): void {
  replacing.value = false;
  emit("update:draft", "");
}
</script>

<template>
  <div
    class="field"
    :class="[`field--${layout}`, { 'is-edited': edited, 'has-error': !!error, 'is-busy': busy, 'is-switch': bool }]"
    :data-key="item.key"
  >
    <div class="field__text">
      <label class="field__label" :for="bool ? undefined : id"
        >{{ fieldLabel(item) }}<span v-if="edited && layout === 'card'" class="tag">edited</span></label
      >
      <span v-if="layout === 'row'" class="help">{{ item.description }}</span>
      <span v-if="layout === 'row' && item.allowed" class="help allowed">Allowed: {{ item.allowed }}</span>
      <span v-if="layout === 'row' && error" class="error" role="alert"
        ><PathIcon :paths="ALERT" :size="14" :stroke="2.2" />{{ error }}</span
      >
    </div>
    <span v-if="layout === 'row' && edited" class="changed">Changed</span>
    <div class="field__input" :class="{ 'is-bool': bool }">
      <ToggleSwitch
        v-if="bool"
        size="md"
        :model-value="draft === 'true'"
        :label="`Value of ${item.key}`"
        :disabled="busy"
        @change="(on: boolean) => emit('update:draft', String(on))"
      />
      <span v-if="bool && layout === 'card'" class="help">{{ draft === "true" ? "On" : "Off" }}</span>
      <template v-if="secret">
        <span class="secret-state" :class="{ 'is-set': isSet }" :data-testid="`secret-state-${item.key}`">{{
          secretState
        }}</span>
        <input
          v-if="replacing || draft"
          :id="id"
          class="input input--wide mono"
          type="password"
          :value="draft"
          :aria-label="`New value of ${item.key}`"
          :aria-invalid="error ? 'true' : undefined"
          :disabled="busy"
          placeholder="Paste the new key"
          autocomplete="new-password"
          spellcheck="false"
          @input="emit('update:draft', ($event.target as HTMLInputElement).value)"
        />
        <button v-if="replacing || draft" type="button" class="sbtn" :disabled="busy" @click="cancelReplace">
          Cancel
        </button>
        <button
          v-else
          type="button"
          class="sbtn"
          :disabled="busy"
          :aria-label="`${isSet ? 'Replace' : 'Set'} ${item.key}`"
          @click="replacing = true"
        >
          {{ isSet ? "Replace" : "Set" }}
        </button>
        <el-popconfirm
          v-if="isSet && !replacing && !draft"
          :title="`Clear the ${fieldLabel(item).toLowerCase()}? Flow stops using it at its next settings read.`"
          confirm-button-text="Clear"
          confirm-button-type="danger"
          cancel-button-text="Cancel"
          :hide-icon="true"
          width="260"
          @confirm="emit('reset')"
        >
          <template #reference>
            <button type="button" class="sbtn sbtn--bad" :disabled="busy" :aria-label="`Clear ${item.key}`">
              Clear
            </button>
          </template>
        </el-popconfirm>
      </template>
      <el-select
        v-else-if="choices"
        :id="id"
        class="select"
        :class="{ 'select--wide': multiple }"
        :model-value="chosen"
        :multiple="multiple"
        :disabled="busy"
        :aria-label="`Value of ${item.key}`"
        @update:model-value="choose"
      >
        <el-option v-for="c in choices" :key="c" :label="c" :value="c" />
      </el-select>
      <input
        v-else-if="!bool"
        :id="id"
        class="input"
        :class="{ 'input--wide': wide, mono: layout === 'card' || wide }"
        :value="draft"
        :aria-label="`Value of ${item.key}`"
        :aria-invalid="error ? 'true' : undefined"
        :disabled="busy"
        inputmode="text"
        autocomplete="off"
        spellcheck="false"
        @input="emit('update:draft', ($event.target as HTMLInputElement).value)"
      />
      <IconButton
        v-if="layout === 'row' && resettable"
        icon="reset"
        :label="`Reset ${item.key}`"
        :disabled="busy"
        placement="top-end"
        @click="emit('reset')"
      />
    </div>
    <template v-if="layout === 'card'">
      <span class="desc">{{ item.description }}</span>
      <span v-if="item.allowed" class="help allowed mono">Allowed: {{ item.allowed }}</span>
      <span v-if="error" class="error" role="alert"
        ><PathIcon :paths="ALERT" :size="14" :stroke="2.2" />{{ error }}</span
      >
      <div class="key">
        <span class="key__name mono">{{ item.key }}</span>
        <span class="key__default help" :class="{ 'is-long': longDefault }"
          >{{ item.source }} · default
          <HintTip v-if="longDefault" mono :text="fullDefault(item.default)"
            ><span class="mono key__value">{{ shownDefault }}</span></HintTip
          ><span v-else class="mono key__value">{{ shownDefault }}</span></span
        >
        <IconButton
          v-if="resettable"
          icon="reset"
          :label="`Reset ${item.key}`"
          :disabled="busy"
          placement="top-end"
          @click="emit('reset')"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.field {
  min-width: 0;
}
.field--card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 18px 20px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.field--row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 24px;
  border-top: 1px solid var(--rl-rule);
}
.field--row:first-child {
  border-top: 0;
}
.field__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.field--row .field__text {
  flex: 1 1 auto;
  margin-right: auto;
}
.field__label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.field--row .field__label {
  font-size: 14px;
}
.tag {
  flex: none;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
  font-size: 11px;
  font-weight: 700;
}
.changed {
  flex: none;
  font-size: 12px;
  font-weight: 700;
  color: var(--rl-brand-ink);
}
.field__input {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.field--row .field__input {
  flex: none;
  gap: 8px;
}
.input {
  box-sizing: border-box;
  width: 100%;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.input:focus-visible {
  outline: none;
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.field--row .input {
  width: 72px;
  height: 34px;
  border-radius: 8px;
  padding: 0 8px;
  text-align: center;
  font-size: 14px;
  font-weight: 600;
}
.field--row .input--wide {
  width: 220px;
  text-align: left;
  font-size: 13px;
  font-weight: 400;
}
.secret-state {
  flex: none;
  font-size: 13px;
  font-weight: 600;
  color: var(--rl-faint);
  white-space: nowrap;
}
.secret-state.is-set {
  color: var(--rl-ok);
}
.sbtn {
  flex: none;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.sbtn--bad {
  color: var(--rl-bad);
}
.sbtn:disabled {
  opacity: 0.6;
  cursor: wait;
}
.sbtn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.select {
  width: 220px;
}
.select--wide {
  width: 320px;
}
.is-edited .input {
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.field--row.is-edited .input {
  background: var(--rl-primary-soft);
}
.has-error .input {
  border-color: var(--rl-bad);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--rl-bad) 12%, transparent);
}
.is-busy .input {
  opacity: 0.6;
  cursor: wait;
}
.desc {
  font-size: 13px;
  line-height: 19px;
  color: var(--rl-muted);
  overflow-wrap: anywhere;
}
.help {
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--rl-faint);
  overflow-wrap: anywhere;
}
.error {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--rl-bad);
  overflow-wrap: anywhere;
}
.error :deep(svg) {
  flex: none;
}
.key {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  min-width: 0;
  margin-top: 2px;
  padding-top: 8px;
  border-top: 1px solid var(--rl-rule);
}
.key__name {
  font-size: 12px;
  color: var(--rl-body);
  overflow-wrap: anywhere;
}
.key__value {
  font-size: 12px;
  color: var(--rl-body);
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
.key__default.is-long .key__value {
  border-bottom: 1px dashed var(--rl-faint);
}
.mono {
  font-family: var(--rl-mono);
}

/* stacked (below 968 px): the row's label stacks over its input (JobsTablet `field`), the inputs at the touch height; a
   narrow desktop keeps the row (SettingsNarrow) */
@media (max-width: 967px) {
  .field--row {
    flex-wrap: wrap;
    gap: 6px 12px;
    padding: 14px 20px;
  }
  .field--row .field__text {
    flex-basis: 100%;
    order: 0;
  }
  .field--row .field__input {
    flex: 1 1 auto;
    order: 2;
  }
  .field--row .changed {
    order: 3;
  }
  .field--row .select {
    flex: 1;
    width: auto;
  }
  .field--row .input,
  .field--row .input--wide {
    flex: 1;
    width: auto;
    height: 44px;
    border-radius: 10px;
    text-align: left;
    padding: 0 12px;
  }
  .field--row .field__input.is-bool {
    flex: none;
  }
}
/* stacked (SettingsTablet, SettingsPhone): the label, then the input the full width, then its help; a switch stays at
   the right of its label */
@media (max-width: 967px) {
  .field--row .field__text {
    display: contents;
  }
  .field--row .field__label {
    flex-basis: 100%;
    order: 0;
  }
  .field--row.is-switch .field__label {
    flex: 1 1 0;
    flex-basis: auto;
  }
  .field--row .field__input {
    order: 1;
  }
  .field--row .changed {
    order: 1;
  }
  .field--row .help,
  .field--row .error {
    flex-basis: 100%;
    order: 2;
  }
}
@media (max-width: 767px) {
  .field--row {
    padding: 14px 16px;
  }
  .input {
    height: 44px;
    font-size: 14px;
  }
}
</style>
