<script setup lang="ts">
import { computed, reactive, ref, useTemplateRef, watch } from "vue";
import type { ApiKey, KeyBody, WalletMode } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import CapChip from "@/components/shared/CapChip.vue";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import { IconAlertCircle } from "@/components/shared/icons";
import SideDrawer from "@/components/shared/overlays/SideDrawer.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { byProviderOrder, providerClass } from "@/lib/providers";
import {
  balanceOf,
  balanceText,
  capLine,
  changedCount,
  type CreateError,
  createdText,
  jobsText,
  keyBody,
  keyForm,
  type KeyForm,
  keyName,
  POINTS,
  scopeName,
  todayText,
} from "./key-model";

// Create or edit a key, in the right drawer (KeyForm, KeyFormDark; a full screen on a phone, KeyCreatePhone). The form
// only asks: it builds the body from what was typed and the view sends it. `dirty` (something typed since it opened)
// is what the drawer's close guard and the view's leave guard read. The parts: the label and the max unfinished, the
// points wallet (its mode and limits: its balance is its total less what it used), the allowed models by provider, and
// one cap per provider.
const open = defineModel<boolean>({ required: true });
// `error`: why a create failed, already reduced to fixed text and a field name (see createError).
const props = defineProps<{
  editing: ApiKey | null;
  models: CatalogModel[];
  busy: boolean;
  error: CreateError | null;
}>();
const emit = defineEmits<{ submit: [body: KeyBody]; "update:dirty": [dirty: boolean]; closed: [] }>();
const { isPhone } = useBreakpoint();
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
const changes = computed(() => (dirty.value ? changedCount(form, JSON.parse(baseline.value) as KeyForm) : 0));

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

const title = computed(() => (props.editing ? `Edit key · ${keyName(props.editing)}` : "New API key"));
const drawer = useTemplateRef<InstanceType<typeof SideDrawer>>("drawer");

// the points wallet first, then one cap per provider
const points = computed(() => form.wallets.find((w) => w.scope === POINTS));
const caps = computed(() => form.wallets.filter((w) => w.scope !== POINTS));
const saved = (scope: string) => props.editing?.wallets.find((w) => w.scope === scope);
const balance = computed(() => balanceOf(saved(POINTS)));

// the catalog by provider, in the drawn order; a paused model can be taken off a key but not put on it
const groups = computed(() => {
  const by = new Map<string, CatalogModel[]>();
  for (const m of props.models) by.set(m.owned_by, [...(by.get(m.owned_by) ?? []), m]);
  return byProviderOrder([...by.keys()]).map((p) => ({ provider: p, models: by.get(p)! }));
});
const picked = (id: string): boolean => form.models.includes(id);

const MODE_OPTIONS = (["closed", "limit", "unlimited"] as const).map((m) => ({ value: m, label: m }));
const typed = (e: Event): string => (e.target as HTMLInputElement).value;
</script>

<template>
  <SideDrawer
    ref="drawer"
    v-model="open"
    :title="title"
    testid="key-drawer"
    :back-label="isPhone ? '' : 'Back to API keys'"
    :dirty="dirty"
    :busy="props.busy"
    full
    @back="drawer?.close()"
    @closed="emit('closed')"
  >
    <template #sub>
      <span v-if="props.editing" class="kf__sub"
        ><span class="mono">{{ props.editing.prefix }}••••</span> · created
        {{ createdText(props.editing.created_ms) }} · {{ jobsText(props.editing) }}</span
      >
      <span v-else class="kf__sub">One points wallet, shared by every provider</span>
    </template>

    <form class="kf" :class="{ 'is-phone': isPhone }" @submit.prevent="send">
      <p v-if="props.error" class="kf__error" role="alert"><IconAlertCircle :size="16" />{{ props.error.text }}</p>

      <div class="kf__grid">
        <div class="kf__field">
          <label class="kf__label" for="key-label">Label</label>
          <input
            id="key-label"
            v-model="form.label"
            class="kf__input"
            :class="{ 'is-error': flagged('label') }"
            :aria-invalid="flagged('label') || undefined"
            maxlength="100"
            placeholder="Partner D"
          />
          <span class="kf__help">Up to 100 characters.</span>
        </div>
        <div class="kf__field">
          <label class="kf__label" for="key-max">Max unfinished jobs</label>
          <input
            id="key-max"
            class="kf__input num"
            :class="{ 'is-error': flagged('max_unfinished') }"
            type="number"
            min="0"
            step="1"
            :value="form.maxUnfinished"
            @input="form.maxUnfinished = typed($event)"
          />
          <span class="kf__help">empty = default 5, 0 = unlimited</span>
        </div>
      </div>

      <section
        v-if="points"
        class="kf__wallet"
        :class="{ 'is-error': flagged('wallets') }"
        role="group"
        aria-label="points"
        aria-describedby="kf-wallet-help"
        data-wallet
      >
        <div class="kf__whead">
          <h3 class="kf__h3">Points wallet</h3>
          <span v-if="props.editing" class="kf__help num"
            >balance <b class="kf__strong">{{ balanceText(balance) }}{{ balance.left === null ? "" : " pts" }}</b
            ><template v-if="todayText(saved(POINTS))"> · {{ todayText(saved(POINTS)) }}</template></span
          >
        </div>
        <div class="kf__wgrid">
          <FilterSelect
            class="kf__mode"
            variant="field"
            block
            label="Mode"
            aria-label="points Mode"
            :model-value="points.mode"
            :options="MODE_OPTIONS"
            @update:model-value="(v) => (points!.mode = v as WalletMode)"
          />
          <label class="kf__field"
            ><span class="kf__label">Per day</span>
            <span class="kf__suffix" :class="{ 'is-off': points.mode !== 'limit' }"
              ><input
                class="kf__bare num"
                type="number"
                min="1"
                step="1"
                placeholder="—"
                :value="points.mode === 'limit' ? points.day : ''"
                :disabled="points.mode !== 'limit'"
                aria-label="points per day"
                @input="points.day = typed($event)"
              /><span class="kf__unit">pts</span></span
            ></label
          >
          <label class="kf__field"
            ><span class="kf__label">{{ props.editing ? "Total" : "Starting points" }}</span>
            <span class="kf__suffix" :class="{ 'is-off': points.mode !== 'limit' }"
              ><input
                class="kf__bare num"
                type="number"
                min="1"
                step="1"
                placeholder="—"
                :value="points.mode === 'limit' ? points.total : ''"
                :disabled="points.mode !== 'limit'"
                aria-label="points total"
                @input="points.total = typed($event)"
              /><span class="kf__unit">pts</span></span
            ></label
          >
        </div>
        <span id="kf-wallet-help" class="kf__help"
          >The balance is the total less what was used. Limits apply in limit mode; empty = no limit on that side.
          <template v-if="props.editing">Use Adjust wallet on the key row for a top-up or a deduction.</template></span
        >
      </section>

      <fieldset class="kf__set" :class="{ 'is-error': flagged('models') }">
        <legend class="kf__legend">Allowed models</legend>
        <label class="kf__any"
          ><input v-model="form.any" type="checkbox" class="kf__check" /><span class="kf__anyword">Any model</span
          ><span class="kf__help">includes models added later</span></label
        >
        <div
          class="kf__models"
          role="group"
          aria-label="Pick models"
          :aria-disabled="form.any"
          :class="{ 'is-off': form.any }"
        >
          <div v-for="g in groups" :key="g.provider" class="kf__group">
            <span class="kf__ghead" :class="providerClass(g.provider)"
              ><span class="kf__gdot" aria-hidden="true" />{{ g.provider }}</span
            >
            <div class="kf__gmodels">
              <label
                v-for="m in g.models"
                :key="m.id"
                class="kf__model"
                :class="{ 'is-on': picked(m.id), 'is-paused': m.disabled === true }"
              >
                <input
                  v-model="form.models"
                  type="checkbox"
                  class="kf__check"
                  :value="m.id"
                  :disabled="form.any || (m.disabled === true && !picked(m.id))"
                />
                <span class="mono">{{ m.id }}</span>
                <span v-if="m.disabled === true" class="kf__tag">paused</span>
              </label>
            </div>
          </div>
        </div>
      </fieldset>

      <fieldset class="kf__set" :class="{ 'is-error': flagged('wallets') }">
        <legend class="kf__legend">Daily caps per provider</legend>
        <span class="kf__help"
          >A job needs the points wallet and its provider's cap both open. Caps apply in limit mode. Per day resets at
          midnight.</span
        >
        <div class="kf__caps">
          <div v-if="!isPhone" class="kf__crow kf__chead" aria-hidden="true">
            <span>Wallet</span><span>Mode</span><span>Per day</span><span>Total</span>
          </div>
          <div
            v-for="w in caps"
            :key="w.scope"
            class="kf__crow"
            role="group"
            :aria-label="scopeName(w.scope)"
            data-wallet
          >
            <span class="kf__cname">
              <span class="kf__cline">
                <CapChip
                  :provider="w.scope"
                  :used="saved(w.scope)?.used_day ?? 0"
                  :cap="saved(w.scope)?.mode === 'limit' ? saved(w.scope)!.day_limit : null"
                />
              </span>
              <span class="kf__help num">{{ props.editing ? capLine(saved(w.scope)) : "new key: nothing used" }}</span>
            </span>
            <FilterSelect
              class="kf__cmode"
              variant="field"
              block
              :aria-label="`${scopeName(w.scope)} Mode`"
              :model-value="w.mode"
              :options="MODE_OPTIONS"
              @update:model-value="(v) => (w.mode = v as WalletMode)"
            />
            <input
              class="kf__small num"
              type="number"
              min="1"
              step="1"
              placeholder="—"
              :value="w.mode === 'limit' ? w.day : ''"
              :disabled="w.mode !== 'limit'"
              :aria-label="`${scopeName(w.scope)} per day`"
              @input="w.day = typed($event)"
            />
            <input
              class="kf__small num"
              type="number"
              min="1"
              step="1"
              placeholder="—"
              :value="w.mode === 'limit' ? w.total : ''"
              :disabled="w.mode !== 'limit'"
              :aria-label="`${scopeName(w.scope)} total`"
              @input="w.total = typed($event)"
            />
          </div>
        </div>
      </fieldset>
    </form>

    <template #footer="{ close }">
      <span v-if="!isPhone && changes" class="kf__help num"
        >{{ changes }} unsaved {{ changes === 1 ? "change" : "changes" }}</span
      >
      <span class="kf__buttons" :class="{ 'is-phone': isPhone }">
        <button type="button" class="kf__btn kf__btn--link" :disabled="props.busy" @click="close">Cancel</button>
        <button type="button" class="kf__btn kf__btn--primary" :disabled="props.busy" @click="send">
          {{ props.editing ? "Save" : "Create key" }}
        </button>
      </span>
    </template>
  </SideDrawer>
</template>

<!-- not scoped: the drawer is teleported to <body> -->
<style>
.kf {
  display: flex;
  flex-direction: column;
  gap: 22px;
  margin: 0;
  color: var(--rl-fg);
}
.kf .mono,
.kf__sub .mono {
  font-family: var(--rl-mono);
}
.kf .num {
  font-variant-numeric: tabular-nums;
}
.kf__sub {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.kf__error {
  display: flex;
  gap: 10px;
  margin: 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
  font-size: 13px;
  line-height: 19px;
}
.kf__error svg {
  flex: none;
  margin-top: 2px;
}
.kf__grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 12px;
}
.kf__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.kf__label {
  font-size: 13px;
  line-height: 20px;
  font-weight: 700;
}
.kf__help {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.kf__strong {
  color: var(--rl-fg);
}
.kf__input {
  box-sizing: border-box;
  width: 100%;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
}
.kf__input:focus,
.kf__small:focus {
  outline: none;
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.kf__input.is-error {
  border-color: var(--rl-bad);
}

/* the points wallet box */
.kf__wallet {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
  background: var(--rl-head);
}
.kf__wallet.is-error {
  border-color: var(--rl-bad);
}
.kf__whead {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
}
.kf__h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
}
.kf__wgrid {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px;
  align-items: end;
}
.kf__suffix {
  display: flex;
  align-items: center;
  box-sizing: border-box;
  height: 40px;
  overflow: hidden;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
}
.kf__suffix:focus-within {
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.kf__suffix.is-off {
  background: var(--rl-head);
}
.kf__bare {
  flex: 1;
  min-width: 0;
  height: 100%;
  padding: 0 12px;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
}
.kf__bare:disabled {
  color: var(--rl-faint);
}
.kf__unit {
  display: flex;
  align-items: center;
  height: 100%;
  padding: 0 10px;
  border-left: 1px solid var(--rl-line-strong);
  background: var(--rl-head);
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

/* the field selects take the drawn 40 px (36 in the caps table) */
.kf .kf__mode .filter-select.is-field {
  height: 40px;
}
.kf .kf__cmode .filter-select.is-field {
  height: 36px;
}

.kf__set {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}
.kf__set.is-error .kf__models,
.kf__set.is-error .kf__caps {
  border-color: var(--rl-bad);
}
.kf__legend {
  margin-bottom: 4px;
  padding: 0;
  font-size: 14px;
  font-weight: 700;
}
.kf__any {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  cursor: pointer;
}
.kf__anyword {
  font-weight: 600;
}
.kf__check {
  flex: none;
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--rl-primary);
}
.kf__models {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.kf__models.is-off {
  opacity: 0.5;
}
.kf__group {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  padding: 10px 12px;
  border-top: 1px solid var(--rl-rule);
}
.kf__group:first-child {
  border-top: 0;
}
/* the provider's name in its ink; the model names stay plain */
.kf__ghead {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  font-size: 13px;
  font-weight: 700;
  color: var(--pi);
  overflow-wrap: anywhere;
}
.kf__gdot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--pc);
}
.kf__gmodels {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.kf__model {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-size: 12px;
  cursor: pointer;
  overflow-wrap: anywhere;
}
.kf__model.is-on {
  border-color: var(--rl-brand);
  background: var(--rl-primary-soft);
}
.kf__model.is-paused:not(.is-on) {
  background: var(--rl-head);
  color: var(--rl-faint);
  cursor: not-allowed;
}
.kf__tag {
  padding: 0 6px;
  border-radius: 999px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
  font-size: 11px;
  font-weight: 700;
}

/* the caps table */
.kf__caps {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.kf__crow {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 120px 96px 96px;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  border-top: 1px solid var(--rl-rule);
}
.kf__crow:first-child {
  border-top: 0;
}
.kf__chead {
  padding: 8px 12px;
  background: var(--rl-head);
  color: var(--rl-faint);
  font-size: 12px;
  font-weight: 600;
}
.kf__cname {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.kf__cline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.kf__small {
  box-sizing: border-box;
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
}
.kf__small:disabled {
  background: var(--rl-head);
  color: var(--rl-faint);
}

/* the footer: the count of changes at the left, Cancel and the primary at the right */
.kf__buttons {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.kf__btn {
  box-sizing: border-box;
  height: 36px;
  padding: 0 14px;
  border: 0;
  border-radius: 10px;
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.kf__btn--link {
  background: transparent;
  color: var(--rl-fg);
}
.kf__btn--link:hover {
  background: var(--rl-track);
}
.kf__btn--primary {
  padding: 0 18px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font-weight: 700;
}
.kf__btn--primary:hover {
  background: var(--rl-primary-hover);
}
.kf__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.kf__btn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}

/* a phone: one column, each cap a block (its chip, then mode and limits), two equal 44 px buttons in the footer */
.kf.is-phone .kf__grid,
.kf.is-phone .kf__wgrid {
  grid-template-columns: minmax(0, 1fr);
}
.kf.is-phone .kf__input,
.kf.is-phone .kf__suffix {
  height: 44px;
}
.kf.is-phone .kf__group {
  grid-template-columns: minmax(0, 1fr);
  gap: 4px;
}
.kf.is-phone .kf__model {
  min-height: 44px;
}
.kf.is-phone .kf__crow {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr);
}
.kf.is-phone .kf__cname {
  grid-column: 1 / -1;
}
.kf.is-phone .kf__small,
.kf.is-phone .kf__cmode .filter-select.is-field {
  height: 44px;
}
.kf__buttons.is-phone {
  flex: 1;
  margin-left: 0;
}
/* the phone footer: 10 px over and under the two buttons, 16 px at the sides (KeyCreatePhone) */
.rl-drawer__foot:has(.kf__buttons.is-phone) {
  padding: 10px 16px;
}
.kf__buttons.is-phone .kf__btn {
  flex: 1 1 0;
  min-width: 0;
  height: 44px;
  font-weight: 700;
}
.kf__buttons.is-phone .kf__btn--link {
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
}
</style>
