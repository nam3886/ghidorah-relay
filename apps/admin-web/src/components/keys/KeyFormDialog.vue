<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type { ApiKey, KeyBody, WalletMode } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import { IconAlertCircle, IconClose } from "@/components/shared/icons";
import "@/components/logs/lane-controls.css";
import { type CreateError, keyBody, keyForm, type KeyForm } from "./key-model";

// Create or edit a key. The dialog only asks: it builds the body from what was typed and the view sends it.
const open = defineModel<boolean>({ required: true });
// `error`: why a create failed, already reduced to fixed text and a field name (see createError).
const props = defineProps<{
  editing: ApiKey | null;
  models: CatalogModel[];
  busy: boolean;
  error: CreateError | null;
}>();
const emit = defineEmits<{ submit: [body: KeyBody] }>();
// The highlighted field draws its edge in the bad colour; the text itself is one line above the form.
const flagged = (name: CreateError["field"]): boolean => props.error?.field === name;

const form = reactive<KeyForm>(keyForm([], null));

// A new key needs a label (B-States): until it has one, Create key stays off with its reason, and a try (a click, Enter,
// leaving the field empty) marks the field and says so. A key that already has no label stays valid: its edit is not
// held back. A label of spaces is no label.
const labelMissing = computed(() => !props.editing && form.label.trim() === "");
const labelTried = ref(false);
const labelError = computed(() => labelMissing.value && labelTried.value);
function send(): void {
  if (labelMissing.value) {
    labelTried.value = true;
    return;
  }
  emit("submit", keyBody(form));
}

// Each opening starts from the key being edited (or a new one) and the catalog as it is now.
watch(
  () => open.value,
  (isOpen) => {
    if (!isOpen) return;
    Object.assign(form, keyForm(props.models, props.editing));
    labelTried.value = false;
  },
  { immediate: true },
);

const title = computed(() =>
  props.editing ? `Edit API key — ${props.editing.label || props.editing.prefix}` : "New API key",
);

// What a wallet has used so far, from the key being edited ("3 used today · 14 total", "9 used"); nothing for a new key.
function usedText(provider: string, kind: string, mode: string): string {
  const w = props.editing?.wallets.find((x) => x.provider === provider && x.kind === kind);
  if (!w) return "";
  return mode === "limit" || w.mode === "limit"
    ? `${w.used_day} used today · ${w.used_total} total`
    : `${w.used_total} used`;
}

const MODE_OPTIONS = (["closed", "limit", "unlimited"] as const).map((m) => ({ value: m, label: m }));
const typed = (e: Event): string => (e.target as HTMLInputElement).value;
// The right side of a model row, as drawn: its kind and its display name ("video · Dreamina Seedance 2.5"); the id
// stands in for a model the catalog sent without a name.
const modelHint = (m: CatalogModel): string => `${String(m.kind ?? "")} · ${m.name || m.id}`;
</script>

<template>
  <el-dialog v-model="open" class="key-dialog" :show-close="false" width="min(640px, 94vw)">
    <template #header="{ titleId }">
      <div class="kd__head">
        <h2 :id="titleId" class="kd__title">{{ title }}</h2>
        <button type="button" class="kla-icon-btn kd__close" aria-label="Close" @click="open = false">
          <IconClose :size="18" :stroke="2" />
        </button>
      </div>
    </template>

    <form class="kd__form" @submit.prevent="send">
      <p v-if="props.error" class="kd__error" role="alert"><IconAlertCircle :size="18" />{{ props.error.text }}</p>

      <div class="kd__field">
        <label class="kd__label" for="key-label">Label</label>
        <input
          id="key-label"
          v-model="form.label"
          class="kd__input"
          :class="{ 'is-error': flagged('label') || labelError }"
          :aria-invalid="flagged('label') || labelError || undefined"
          :aria-describedby="labelError ? 'key-label-error' : undefined"
          maxlength="100"
          placeholder="Partner D"
          aria-label="Label"
          @blur="labelTried = true"
        />
        <p v-if="labelError" id="key-label-error" class="kd__field-error">
          <IconAlertCircle :size="14" />A label is required.
        </p>
        <p v-else class="kd__help">
          Up to 100 characters.<template v-if="props.editing">
            Key <span class="kla-mono">{{ props.editing.prefix }}</span></template
          >
        </p>
      </div>

      <div class="kd__field">
        <label class="kd__label" for="key-max">Max unfinished jobs</label>
        <input
          id="key-max"
          class="kd__input kd__input--num"
          :class="{ 'is-error': flagged('max_unfinished') }"
          type="number"
          min="0"
          step="1"
          :value="form.maxUnfinished"
          aria-label="Max unfinished jobs (empty = default, 0 = unlimited)"
          @input="form.maxUnfinished = typed($event)"
        />
        <p class="kd__help">empty = default 5, 0 = unlimited</p>
      </div>

      <fieldset class="kd__set" :class="{ 'is-error': flagged('models') }">
        <legend class="kd__label kd__legend">Allowed models</legend>
        <label class="kd__any"
          ><input v-model="form.any" type="checkbox" class="kd__check" /><span>Any model</span></label
        >
        <div
          class="kd__models"
          role="group"
          aria-label="Pick models"
          :aria-disabled="form.any"
          :class="{ 'is-off': form.any }"
        >
          <label v-for="m in props.models" :key="m.id" class="kd__model">
            <input v-model="form.models" type="checkbox" class="kd__check" :value="m.id" :disabled="form.any" />
            <span class="kla-mono kd__model-id">{{ m.id }}</span>
            <span class="kd__spacer" />
            <span class="kd__model-hint">{{ modelHint(m) }}</span>
          </label>
        </div>
      </fieldset>

      <fieldset class="kd__set" :class="{ 'is-error': flagged('wallets') }">
        <legend class="kd__label kd__legend kd__legend--tight">Wallets</legend>
        <p class="kd__help kd__help--wallets">Limits apply in limit mode only. Per day resets at midnight.</p>
        <!-- each wallet a group named after it: its Mode, Per day and Total read in its name -->
        <div
          v-for="w in form.wallets"
          :key="`${w.provider}/${w.kind}`"
          class="kd__wallet"
          role="group"
          :aria-label="`${w.provider} ${w.kind}`"
          data-wallet
        >
          <div class="kd__wallet-name">
            <span class="kd__wallet-kind">{{ w.provider }} {{ w.kind }}</span>
            <span class="kd__help kd__used">{{ usedText(w.provider, w.kind, w.mode) }}</span>
          </div>
          <FilterSelect
            class="kd__mode"
            variant="field"
            label="Mode"
            :aria-label="`${w.provider} ${w.kind} Mode`"
            :model-value="w.mode"
            :options="MODE_OPTIONS"
            @update:model-value="(v) => (w.mode = v as WalletMode)"
          />
          <label class="kd__mini"
            ><span class="kd__help">Per day</span>
            <input
              class="kd__input kd__input--wallet"
              :class="{ 'is-off': w.mode !== 'limit' }"
              type="number"
              min="1"
              step="1"
              placeholder="—"
              :value="w.mode === 'limit' ? w.day : ''"
              :disabled="w.mode !== 'limit'"
              :aria-label="`${w.provider} ${w.kind} per day`"
              @input="w.day = typed($event)"
          /></label>
          <label class="kd__mini"
            ><span class="kd__help">Total</span>
            <input
              class="kd__input kd__input--wallet"
              :class="{ 'is-off': w.mode !== 'limit' }"
              type="number"
              min="1"
              step="1"
              placeholder="—"
              :value="w.mode === 'limit' ? w.total : ''"
              :disabled="w.mode !== 'limit'"
              :aria-label="`${w.provider} ${w.kind} total`"
              @input="w.total = typed($event)"
          /></label>
        </div>
      </fieldset>
    </form>

    <template #footer>
      <div class="kd__foot">
        <button type="button" class="kla-btn" @click="open = false">Cancel</button>
        <span class="kd__create">
          <span v-if="labelMissing" id="key-create-why" role="tooltip" class="kd__tip">Fill in the label first</span>
          <button
            type="button"
            class="kla-btn kla-btn--primary"
            :class="{ 'is-new': !props.editing }"
            :disabled="props.busy"
            :aria-disabled="labelMissing || undefined"
            :aria-describedby="labelMissing ? 'key-create-why' : undefined"
            @click="send"
          >
            {{ props.editing ? "Save" : "Create key" }}
          </button>
        </span>
      </div>
    </template>
  </el-dialog>
</template>

<!-- not scoped: the dialog is teleported to <body>, and its chrome is Element Plus's own markup -->
<style>
.key-dialog.el-dialog {
  --el-dialog-padding-primary: 0px;
  --el-dialog-margin-top: 32px;
  padding: 0;
  border: 0;
  border-radius: 16px;
  max-height: calc(100% - 64px);
  display: flex;
  flex-direction: column;
}
/* no edge in either theme, as drawn (the dark theme gives every other dialog one) */
html:root .key-dialog.el-dialog {
  border: 0;
}
.key-dialog .el-dialog__header {
  margin: 0;
  padding: 20px 24px 16px;
  border-bottom: 1px solid var(--rl-line);
}
.key-dialog .el-dialog__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 24px;
}
.key-dialog .el-dialog__footer {
  padding: 16px 24px;
  border-top: 1px solid var(--rl-line);
}
.key-dialog .kd__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.key-dialog .kd__title {
  margin: 0;
  font-size: 20px;
  line-height: 28px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.key-dialog .kd__close {
  width: 36px;
  height: 36px;
}
.key-dialog .kd__form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
/* the B-States error alert */
.key-dialog .kd__error {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  color: var(--rl-fg);
}
.key-dialog .kd__error svg {
  flex: none;
  color: var(--rl-bad);
}
.key-dialog .kd__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.key-dialog .kd__label {
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  color: var(--rl-fg);
}
.key-dialog .kd__legend {
  margin-bottom: 8px;
  padding: 0;
}
.key-dialog .kd__legend--tight {
  margin-bottom: 4px;
}
.key-dialog .kd__help {
  margin: 0;
  font-size: 12px;
  line-height: 20px;
  color: var(--rl-muted);
}
.key-dialog .kd__help--wallets {
  margin-bottom: 8px;
}
.key-dialog .kd__input {
  box-sizing: border-box;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 14px;
}
.key-dialog .kd__input.is-error {
  border-color: var(--rl-bad);
}
.key-dialog .kd__input--num {
  width: 120px;
  font-family: var(--rl-mono);
  font-variant-numeric: tabular-nums;
}
.key-dialog .kd__set {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}
.key-dialog .kd__set.is-error {
  outline: 1px solid var(--rl-bad);
  outline-offset: 4px;
  border-radius: 6px;
}
.key-dialog .kd__any {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 32px;
  font-weight: 500;
}
.key-dialog .kd__check {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--rl-primary);
}
.key-dialog .kd__models {
  display: flex;
  flex-direction: column;
  margin-top: 8px;
  border-top: 1px solid var(--rl-line);
}
.key-dialog .kd__models.is-off {
  opacity: 0.5;
}
.key-dialog .kd__model {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 0 4px;
  border-bottom: 1px solid var(--rl-line);
}
.key-dialog .kd__model-id {
  font-size: 13px;
}
.key-dialog .kd__used {
  font-variant-numeric: tabular-nums;
}
.key-dialog .kd__spacer {
  flex: 1;
}
.key-dialog .kd__model-hint {
  font-size: 12px;
  color: var(--rl-muted);
}
.key-dialog .kd__wallet {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px 12px;
  padding: 12px 0;
  border-top: 1px solid var(--rl-line);
}
.key-dialog .kd__wallet-name {
  display: flex;
  flex-direction: column;
  flex: 1 1 130px;
  min-width: 120px;
  padding-bottom: 8px;
}
.key-dialog .kd__wallet-kind {
  font-weight: 500;
}
.key-dialog .kd__mini {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
/* the Mode select as B-KeyForm draws it: 140 wide and 36 high, the author padding 8 (the field's own value inset and
   arrow follow from it); on a phone it is the field's chip */
.key-dialog .kd__mode.filter-field {
  --field-pad: 8px;
}
.key-dialog .kd__mode.filter-field:not(.is-chip) .filter-select.is-field {
  width: 140px;
  height: 36px;
}
.key-dialog .kd__input--wallet {
  width: 100px;
  font-family: var(--rl-mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.key-dialog .kd__input.is-off {
  background: var(--rl-raised);
  color: var(--rl-faint);
  opacity: 0.7;
}
.key-dialog .kd__foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.key-dialog .kd__foot .kla-btn {
  padding: 0 16px;
}
.key-dialog .kd__foot .kla-btn--primary {
  padding: 0 18px;
}
/* B-States' form validation: Create key padded 0 16, its reason above it while the label is empty, on hover or focus;
   the error line under the field in the bad colour */
.key-dialog .kd__foot .kla-btn--primary.is-new {
  padding: 0 16px;
}
.key-dialog .kd__create {
  position: relative;
  display: inline-flex;
}
.key-dialog .kd__tip {
  position: absolute;
  right: 0;
  bottom: 44px;
  visibility: hidden;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--rl-fg);
  color: var(--rl-surface);
  font-size: 12px;
  line-height: 16px;
  white-space: nowrap;
  box-shadow: var(--rl-shadow-pop, 0 12px 32px rgb(10 6 20 / 18%));
  pointer-events: none;
}
.key-dialog .kd__create:hover .kd__tip,
.key-dialog .kd__create:focus-within .kd__tip {
  visibility: visible;
}
.key-dialog .kd__field-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-bad);
}
.key-dialog .kd__field-error svg {
  flex: none;
}
@media (max-width: 767px) {
  .key-dialog.el-dialog {
    border-radius: 0;
  }
  .key-dialog .el-dialog__header {
    padding: 12px 12px 12px 16px;
  }
  .key-dialog .kd__close {
    width: 44px;
    height: 44px;
  }
  .key-dialog .el-dialog__body {
    padding: 16px;
  }
  .key-dialog .el-dialog__footer {
    padding: 12px 16px;
  }
}
</style>
