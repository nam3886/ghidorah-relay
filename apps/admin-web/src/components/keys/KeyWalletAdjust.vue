<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import type { ApiKey, WalletBody } from "@/api/keys";
import IconButton from "@/components/shared/IconButton.vue";
import BottomSheet from "@/components/shared/overlays/BottomSheet.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { placePopup } from "@/lib/popup-place";
import { keyName, planChange, pointsWallet, type WalletChange } from "./key-model";

// Adjust wallet (KeyWallet, KeyWalletPhone): the points wallet of one key, its balance now and after the change, Top up
// or Deduct, and the amount. A desktop and a tablet draw it as a dialog hanging from the row's wallet button (fixed
// under it, its arrow at the button); a phone as a bottom sheet. The change is the wallet's total limit (planChange):
// the server keeps no ledger note and no refund of its own here, so the design's Refund and Note are not drawn. The
// view sends what `submit` carries; `keyRow` null closes it.
const props = defineProps<{ keyRow: ApiKey | null; anchor: HTMLElement | null; busy: boolean }>();
const emit = defineEmits<{ submit: [body: WalletBody]; close: [] }>();
const { isPhone } = useBreakpoint();

const change = ref<WalletChange>("top");
const amount = ref("");
const CHANGES: ReadonlyArray<{ value: WalletChange; label: string }> = [
  { value: "top", label: "Top up" },
  { value: "deduct", label: "Deduct" },
];
const n = (x: number): string => x.toLocaleString("en-US");
// "5,000" and "5000" are the same amount
const typedAmount = computed(() => (amount.value.trim() === "" ? NaN : Number(amount.value.replace(/[,\s]/g, ""))));
const plan = computed(() =>
  planChange(props.keyRow ? pointsWallet(props.keyRow) : undefined, change.value, typedAmount.value),
);
const unlimited = computed(() => props.keyRow !== null && pointsWallet(props.keyRow)?.mode === "unlimited");
const primaryText = computed(() => {
  const what = change.value === "top" ? "Top up" : "Deduct";
  return Number.isFinite(typedAmount.value) && typedAmount.value > 0 ? `${what} ${n(typedAmount.value)}` : what;
});
// the problem shows once something was typed (an empty box is not an error yet)
const shownProblem = computed(() => (amount.value.trim() || unlimited.value ? plan.value.problem : ""));

const open = computed(() => props.keyRow !== null);
watch(open, async (isOpen) => {
  if (!isOpen) return stopFollowing();
  change.value = "top";
  amount.value = "";
  if (isPhone.value) return;
  await nextTick();
  place();
  follow();
  amountField.value?.focus();
});

function send(): void {
  if (props.busy || !plan.value.body) return;
  emit("submit", plan.value.body);
}

// ---- the desktop dialog: fixed under its button, following it on a scroll or a resize ---------------------------
const panel = ref<HTMLElement | null>(null);
const amountField = ref<HTMLInputElement | null>(null);
const at = ref({ left: 0, top: 0, arrow: 24 });
const WIDTH = 330;
function place(): void {
  const el = props.anchor;
  if (!el || !panel.value) return;
  const r = el.getBoundingClientRect();
  const view = { width: window.innerWidth, height: window.innerHeight };
  const box = placePopup(r, { width: WIDTH, height: panel.value.offsetHeight }, view, "start");
  const arrow = Math.min(Math.max(r.left + Math.min(r.width / 2, 40) - box.left - 6, 16), WIDTH - 28);
  at.value = { left: box.left, top: box.top + 4, arrow };
}
function onOutside(event: Event): void {
  const t = event.target as Node | null;
  if (t && (panel.value?.contains(t) || props.anchor?.contains(t))) return;
  // a press in a toast or a box over the page is not a press beside the dialog
  if (t instanceof Element && t.closest(".el-message, .el-overlay")) return;
  emit("close");
}
const onWindow = (): void => place();
function follow(): void {
  document.addEventListener("pointerdown", onOutside, true);
  window.addEventListener("resize", onWindow);
  window.addEventListener("scroll", onWindow, true);
}
function stopFollowing(): void {
  document.removeEventListener("pointerdown", onOutside, true);
  window.removeEventListener("resize", onWindow);
  window.removeEventListener("scroll", onWindow, true);
}
onBeforeUnmount(stopFollowing);
function escape(): void {
  if (props.busy) return;
  emit("close");
  props.anchor?.focus();
}
const panelStyle = computed(() => ({ left: `${at.value.left}px`, top: `${at.value.top}px` }));
const arrowStyle = computed(() => ({ left: `${at.value.arrow}px` }));
</script>

<template>
  <BottomSheet
    v-if="isPhone"
    :model-value="open"
    :title="keyRow ? `Adjust wallet · ${keyName(keyRow)}` : 'Adjust wallet'"
    :sub="keyRow && keyRow.label.trim() ? keyRow.prefix : ''"
    testid="wallet-sheet"
    @update:model-value="(v: boolean) => !v && !busy && emit('close')"
  >
    <form class="wa wa--sheet" @submit.prevent="send">
      <dl class="wa__ledger num">
        <div class="wa__lrow">
          <dt>Balance now</dt>
          <dd>{{ unlimited ? "unlimited" : `${n(plan.now)} pts` }}</dd>
        </div>
        <div class="wa__lrow">
          <dt>After this change</dt>
          <dd :class="change === 'top' ? 'is-ok' : 'is-bad'">{{ unlimited ? "unlimited" : `${n(plan.after)} pts` }}</dd>
        </div>
      </dl>
      <div class="wa__field">
        <span class="wa__label" id="wa-kind-sheet">Kind of change</span>
        <div class="wa__seg" role="group" aria-labelledby="wa-kind-sheet">
          <button
            v-for="c in CHANGES"
            :key="c.value"
            type="button"
            class="wa__segbtn"
            :class="{ 'is-on': change === c.value }"
            :aria-pressed="change === c.value"
            @click="change = c.value"
          >
            {{ c.label }}
          </button>
        </div>
      </div>
      <label class="wa__field">
        <span class="wa__label">Amount</span>
        <span class="wa__amt" :class="{ 'is-bad': !!shownProblem }">
          <span class="wa__sign" :class="change === 'top' ? 'is-ok' : 'is-bad'" aria-hidden="true">{{
            change === "top" ? "+" : "−"
          }}</span>
          <input v-model="amount" class="wa__bare num" inputmode="numeric" autocomplete="off" aria-label="Amount" />
          <span class="wa__suffix">pts</span>
        </span>
        <span v-if="shownProblem" class="wa__problem" role="alert">{{ shownProblem }}</span>
      </label>
    </form>
    <template #footer>
      <button type="button" class="wa__btn wa__btn--line" :disabled="busy" @click="emit('close')">Cancel</button>
      <button type="button" class="wa__btn wa__btn--primary" :disabled="busy || !plan.body" @click="send">
        {{ primaryText }}
      </button>
    </template>
  </BottomSheet>

  <Teleport v-else to="body">
    <section
      v-if="open && keyRow"
      ref="panel"
      class="wa wa--pop"
      role="dialog"
      aria-labelledby="wa-title"
      data-testid="wallet-pop"
      :style="panelStyle"
      @keydown.esc.stop.prevent="escape"
    >
      <span class="wa__arrow" aria-hidden="true" :style="arrowStyle" />
      <div class="wa__head">
        <h2 id="wa-title" class="wa__title">Adjust wallet</h2>
        <IconButton icon="close" label="Close" shortcut="Esc" placement="top-end" :disabled="busy" @click="escape" />
      </div>
      <div class="wa__now num">
        <span class="wa__nowcol"
          ><span class="wa__help">Now</span><b>{{ unlimited ? "unlimited" : `${n(plan.now)} pts` }}</b></span
        >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
          class="wa__to"
        >
          <path d="M5 12h14" />
          <path d="m13 6 6 6-6 6" />
        </svg>
        <span class="wa__nowcol"
          ><span class="wa__help">After</span
          ><b :class="change === 'top' ? 'is-ok' : 'is-bad'">{{
            unlimited ? "unlimited" : `${n(plan.after)} pts`
          }}</b></span
        >
      </div>
      <form class="wa__form" @submit.prevent="send">
        <div class="wa__seg" role="group" aria-label="Kind of change">
          <button
            v-for="c in CHANGES"
            :key="c.value"
            type="button"
            class="wa__segbtn"
            :class="{ 'is-on': change === c.value }"
            :aria-pressed="change === c.value"
            @click="change = c.value"
          >
            {{ c.label }}
          </button>
        </div>
        <label class="wa__field">
          <span class="wa__label">Amount</span>
          <span class="wa__amt" :class="{ 'is-bad': !!shownProblem }">
            <span class="wa__sign" :class="change === 'top' ? 'is-ok' : 'is-bad'" aria-hidden="true">{{
              change === "top" ? "+" : "−"
            }}</span>
            <input
              ref="amountField"
              v-model="amount"
              class="wa__bare num"
              inputmode="numeric"
              autocomplete="off"
              aria-label="Amount"
            />
            <span class="wa__suffix">pts</span>
          </span>
          <span v-if="shownProblem" class="wa__problem" role="alert">{{ shownProblem }}</span>
        </label>
        <div class="wa__foot">
          <button type="button" class="wa__btn wa__btn--link" :disabled="busy" @click="escape">Cancel</button>
          <button type="button" class="wa__btn wa__btn--primary" :disabled="busy || !plan.body" @click="send">
            {{ primaryText }}
          </button>
        </div>
      </form>
    </section>
  </Teleport>
</template>

<!-- not scoped: the dialog and the sheet are teleported to <body> -->
<style>
.wa {
  color: var(--rl-fg);
  font-size: 14px;
  line-height: 20px;
}
.wa .num {
  font-variant-numeric: tabular-nums;
}
.wa--pop {
  position: fixed;
  z-index: 2050;
  box-sizing: border-box;
  width: 330px;
  max-width: calc(100vw - 16px);
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border-radius: 14px;
  background: var(--rl-pop);
  box-shadow: var(--rl-shadow-pop);
}
.wa__arrow {
  position: absolute;
  top: -6px;
  width: 12px;
  height: 12px;
  transform: rotate(45deg);
  background: var(--rl-pop);
  box-shadow: -1px -1px 0 var(--rl-line-strong);
}
.wa__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.wa__title {
  flex: 1;
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 800;
}
.wa__now {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 12px;
  border: 1px solid var(--rl-rule);
  border-radius: 10px;
  background: var(--rl-head);
}
.wa__nowcol {
  display: flex;
  flex-direction: column;
}
.wa__to {
  color: var(--rl-faint);
}
.wa__help {
  font-size: 12px;
  color: var(--rl-faint);
}
.wa .is-ok {
  color: var(--rl-ok);
}
.wa .is-bad {
  color: var(--rl-bad);
}
.wa__form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.wa__seg {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 10px;
  background: var(--rl-track);
}
.wa__segbtn {
  flex: 1;
  min-width: 0;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.wa__segbtn.is-on {
  background: var(--rl-seg-on);
  color: var(--rl-fg);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.1);
}
.wa__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.wa__label {
  font-size: 13px;
  font-weight: 700;
}
.wa__amt {
  display: flex;
  align-items: center;
  box-sizing: border-box;
  height: 36px;
  overflow: hidden;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: var(--rl-input);
}
.wa__amt:focus-within {
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.wa__amt.is-bad {
  border-color: var(--rl-bad);
}
.wa__sign {
  padding-left: 10px;
  font-weight: 800;
}
.wa__bare {
  flex: 1;
  min-width: 0;
  height: 100%;
  padding: 0 8px;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-weight: 700;
}
.wa__suffix {
  display: flex;
  align-items: center;
  height: 100%;
  padding: 0 10px;
  border-left: 1px solid var(--rl-line-strong);
  background: var(--rl-head);
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 600;
}
.wa__problem {
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--rl-bad);
}
.wa__foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.wa__btn {
  box-sizing: border-box;
  height: 34px;
  padding: 0 12px;
  border: 0;
  border-radius: 8px;
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.wa__btn--link {
  background: transparent;
  color: var(--rl-fg);
}
.wa__btn--link:hover {
  background: var(--rl-track);
}
.wa__btn--primary {
  padding: 0 14px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font-weight: 700;
}
.wa__btn--primary:hover {
  background: var(--rl-primary-hover);
}
.wa__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.wa__btn:focus-visible,
.wa__segbtn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}

/* the phone sheet: the ledger rows, the touch height, two equal buttons in the footer */
.wa--sheet {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.wa__ledger {
  display: flex;
  flex-direction: column;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.wa__lrow {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  box-sizing: border-box;
  min-height: 40px;
  padding: 10px 14px;
  border-top: 1px solid var(--rl-rule);
}
.wa__lrow:first-child {
  border-top: 0;
}
.wa__lrow dt {
  flex: none;
  font-size: 13px;
  color: var(--rl-faint);
}
.wa__lrow dd {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  text-align: right;
}
.wa--sheet .wa__seg {
  border-radius: 12px;
}
.wa--sheet .wa__segbtn {
  height: 38px;
}
.wa--sheet .wa__amt {
  height: 44px;
  border-radius: 10px;
}
.wa--sheet .wa__suffix {
  padding: 0 12px;
}
.rl-sheet .wa__btn {
  flex: 1 1 0;
  min-width: 0;
  height: 44px;
  padding: 0 16px;
  border-radius: 10px;
  font-weight: 700;
}
.rl-sheet .wa__btn--line {
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  color: var(--rl-fg);
}
</style>
