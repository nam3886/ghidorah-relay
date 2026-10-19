<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type { ApiKey, KeyBody, WalletMode } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import DetailDrawer from "@/components/shared/DetailDrawer.vue";
import { IconAlertCircle } from "@/components/shared/icons";
import "@/components/logs/lane-controls.css";
import { type CreateError, keyBody, keyForm, type KeyForm } from "./key-model";

// Create or edit a key, in a right drawer. The form only asks: it builds the body from what was typed and the view
// sends it. `dirty` (something typed since it opened) is what the drawer's close guard and the view's leave guard read.
const open = defineModel<boolean>({ required: true });
// `error`: why a create failed, already reduced to fixed text and a field name (see createError).
const props = defineProps<{
  editing: ApiKey | null;
  models: CatalogModel[];
  busy: boolean;
  error: CreateError | null;
}>();
const emit = defineEmits<{ submit: [body: KeyBody]; "update:dirty": [dirty: boolean]; closed: [] }>();
// The highlighted field draws its edge in the bad colour; the text itself is one line above the form.
const flagged = (name: CreateError["field"]): boolean => props.error?.field === name;

const form = reactive<KeyForm>(keyForm([], null));

// The label is optional: a key with none is named by its prefix (a label of spaces is no label, the server strips it).
function send(): void {
  emit("submit", keyBody(form));
}

// What the form held when it opened: the drawer asks before it closes only when the form no longer equals it.
const baseline = ref(JSON.stringify(form));
const dirty = computed(() => JSON.stringify(form) !== baseline.value);
watch(dirty, (d) => emit("update:dirty", d), { immediate: true });

// Each opening starts from the key being edited (or a new one) and the catalog as it is now.
watch(
  () => open.value,
  (isOpen) => {
    // a closed form holds nothing that a close or a leave could lose
    if (isOpen) Object.assign(form, keyForm(props.models, props.editing));
    baseline.value = JSON.stringify(form);
  },
  { immediate: true },
);

const title = computed(() =>
  props.editing ? `Edit API key — ${props.editing.label.trim() || props.editing.prefix}` : "New API key",
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
  <DetailDrawer
    v-model="open"
    :title="title"
    testid="key-drawer"
    back-label="Back to API keys"
    :dirty="dirty"
    :busy="props.busy"
    @closed="emit('closed')"
  >
    <form class="kd__form key-dialog" @submit.prevent="send">
      <p v-if="props.error" class="kd__error" role="alert"><IconAlertCircle :size="18" />{{ props.error.text }}</p>

      <div class="kd__field">
        <label class="kd__label" for="key-label">Label</label>
        <input
          id="key-label"
          v-model="form.label"
          class="kd__input"
          :class="{ 'is-error': flagged('label') }"
          :aria-invalid="flagged('label') || undefined"
          maxlength="100"
          placeholder="Partner D"
          aria-label="Label"
        />
        <p class="kd__help">
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

    <template #footer="{ close }">
      <button type="button" class="kla-btn" :disabled="props.busy" @click="close">Cancel</button>
      <button
        type="button"
        class="kla-btn kla-btn--primary"
        :class="{ 'is-new': !props.editing }"
        :disabled="props.busy"
        @click="send"
      >
        {{ props.editing ? "Save" : "Create key" }}
      </button>
    </template>
  </DetailDrawer>
</template>

<!-- not scoped: the drawer is teleported to <body> -->
<style>
.key-dialog.kd__form {
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
  padding: 0;
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
/* the Mode select as B-KeyForm draws it: 140 wide and 36 high; on a phone it is the field chip */
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
/* Cancel and Save as B-KeyForm draws them (padding 0 16 and 0 18); Create key is 0 16, as B-States draws it */
.detail-drawer__foot .kla-btn {
  padding: 0 16px;
}
.detail-drawer__foot .kla-btn--primary {
  padding: 0 18px;
}
.detail-drawer__foot .kla-btn--primary.is-new {
  padding: 0 16px;
}

/* a phone: Cancel and the primary share the footer row as two equal 44 px buttons, as the Accounts import footer draws them */
@media (max-width: 767px) {
  .detail-drawer__foot .kla-btn,
  .detail-drawer__foot .kla-btn--primary,
  .detail-drawer__foot .kla-btn--primary.is-new {
    box-sizing: border-box;
    flex: 1 1 0;
    min-width: 0;
    height: 44px;
    padding: 0;
    font-size: 14px;
    line-height: normal;
  }
  /* the primary has no edge: a transparent one makes both buttons the same size to the flex algorithm */
  .detail-drawer__foot .kla-btn--primary {
    border: 1px solid transparent;
  }
}
</style>
