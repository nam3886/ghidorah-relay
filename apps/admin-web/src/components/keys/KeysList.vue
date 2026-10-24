<script setup lang="ts">
import { computed } from "vue";
import type { ApiKey } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import ActionMenu, { type MenuItem } from "@/components/shared/ActionMenu.vue";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import CapChip from "@/components/shared/CapChip.vue";
import IconButton from "@/components/shared/IconButton.vue";
import { IconMoreHorizontal, IconPencilMenu, IconPower, IconTrashMenu } from "@/components/shared/icons";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import {
  balanceOf,
  balanceText,
  capChips,
  jobsText,
  keyName,
  keyState,
  pointsWallet,
  STATE_PILL,
  todayText,
  unfinishedText,
} from "./key-model";

// The key list, drawn as Keys.dc.html (a desktop: Key, Wallet, Providers and caps today, Jobs and the row menu),
// KeysTablet (the same table, scrolling sideways in its box) and KeysPhone (one block per key with its pill, balance,
// caps and row buttons), with the states of KeysStates: a first read as flat bars, no key yet, none under the filters,
// and a failed read. The wallet figure is a button that opens Adjust wallet. Server strings reach the page as text.
const props = defineProps<{
  rows: ApiKey[];
  loading: boolean;
  error: string | null;
  // the list is empty because of the Show chips, the search or the provider strip
  filtered: boolean;
  emptyText: string;
  catalog: readonly CatalogModel[];
  only: string;
  createdId: string | null;
  // the key whose Adjust wallet is open
  walletId: string | null;
  busy: boolean;
}>();
const emit = defineEmits<{
  edit: [k: ApiKey];
  wallet: [k: ApiKey, anchor: HTMLElement];
  toggle: [k: ApiKey, enabled: boolean];
  remove: [k: ApiKey];
  retry: [];
  "clear-filters": [];
  create: [];
}>();
const { isPhone, isTablet } = useBreakpoint();

const SKELETON = [0, 1, 2, 3, 4];
const first = computed(() => props.loading && props.rows.length === 0);
const bare = computed(() => !first.value && !props.error && props.rows.length === 0);
const state = computed(() =>
  props.error ? "error" : first.value ? "loading" : props.loading ? "refreshing" : bare.value ? "empty" : "ready",
);

const balance = (k: ApiKey) => balanceOf(pointsWallet(k));
const caps = (k: ApiKey) => capChips(k, props.catalog, props.only);
const walletLabel = (k: ApiKey) => {
  const b = balance(k);
  return `Adjust wallet of ${keyName(k)}, ${b.left === null ? b.word : `${balanceText(b)} points`}`;
};
// the switch says what a click does
const switchTitle = (enabled: boolean): string => (enabled ? "Disable key" : "Enable key");
const menu = (k: ApiKey): MenuItem[] => [
  { key: "edit", label: "Edit key", icon: IconPencilMenu },
  { key: "toggle", label: switchTitle(k.enabled), icon: IconPower },
  { key: "delete", label: "Delete key", icon: IconTrashMenu, danger: true, separated: true },
];
function pick(k: ApiKey, what: string): void {
  if (what === "edit") emit("edit", k);
  else if (what === "toggle") emit("toggle", k, !k.enabled);
  else if (what === "delete") emit("remove", k);
}
const openWallet = (k: ApiKey, event: MouseEvent) => emit("wallet", k, event.currentTarget as HTMLElement);

// inbox, search-x, cloud-off (JobsStates)
const ICONS = {
  inbox: ["M3 13h5l1.5 3h5L16 13h5", "M5 5h14l2 8v6H3v-6z"],
  search: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z", "m20 20-3.5-3.5", "m8.5 8.5 5 5", "m13.5 8.5-5 5"],
  cloud: ["M7 18a5 5 0 0 1-.9-9.9A6 6 0 0 1 17.7 8 4.5 4.5 0 0 1 18 18z", "m3 3 18 18"],
};
</script>

<template>
  <div class="kl" :class="{ 'kl--tablet': isTablet, 'kl--phone': isPhone }" :data-state="state" data-testid="keys-list">
    <!-- a first read: flat bars in the real columns, no shimmer -->
    <template v-if="first">
      <div v-if="isPhone" class="kl__list" aria-busy="true" aria-label="Loading keys">
        <div v-for="i in SKELETON" :key="i" class="p-key p-key--skel">
          <span class="bar bar--a" /><span class="bar bar--b" /><span class="bar bar--c" />
        </div>
      </div>
      <ArrowScroller v-else table label="Keys table">
        <table class="kl__table" aria-busy="true" aria-label="Loading keys">
          <thead>
            <tr>
              <th>Key</th>
              <th class="r">Wallet</th>
              <th class="t-provs">Providers and caps today</th>
              <th>Jobs</th>
              <th class="t-act" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="i in SKELETON" :key="i">
              <td><span class="bar bar--name" /></td>
              <td class="r"><span class="bar bar--bal" /></td>
              <td class="t-provs"><span class="bar bar--caps" /></td>
              <td><span class="bar bar--jobs" /></td>
              <td class="t-act" />
            </tr>
          </tbody>
        </table>
      </ArrowScroller>
    </template>

    <!-- a failed read: the frame stays, Retry reads again -->
    <div v-else-if="error" class="kl__box" role="alert">
      <span class="kl__icon is-bad"><PathIcon :paths="ICONS.cloud" :size="22" :stroke="1.8" /></span>
      <b class="kl__title">{{ error }}</b>
      <div class="kl__btns"><button type="button" class="kl__primary" @click="emit('retry')">Retry</button></div>
    </div>

    <!-- nothing yet, or nothing under the filters -->
    <div v-else-if="bare" class="kl__box" role="status">
      <template v-if="filtered">
        <span class="kl__icon"><PathIcon :paths="ICONS.search" :size="22" :stroke="1.8" /></span>
        <b class="kl__title">{{ emptyText }}</b>
        <span class="kl__text">Change the Show chips or the search, or clear them to see every key.</span>
        <div class="kl__btns">
          <button type="button" class="kl__primary" @click="emit('clear-filters')">Clear filters</button>
        </div>
      </template>
      <template v-else>
        <span class="kl__icon is-brand"><PathIcon :paths="ICONS.inbox" :size="22" :stroke="1.8" /></span>
        <b class="kl__title">No API keys yet.</b>
        <span class="kl__text"
          >A key lets a customer call the relay. Each key has one points wallet and per-provider caps.</span
        >
        <div class="kl__btns">
          <button type="button" class="kl__primary" :disabled="busy" @click="emit('create')">New key</button>
        </div>
      </template>
    </div>

    <!-- the phone: one block per key -->
    <div v-else-if="isPhone" class="kl__list">
      <div
        v-for="k in rows"
        :key="k.id"
        class="p-key"
        :class="{ 'is-new': k.id === createdId, 'is-off': !k.enabled }"
        data-testid="key-row"
      >
        <span class="p-key__top">
          <span class="pill" :class="`tone-${STATE_PILL[keyState(k)].tone}`">{{ STATE_PILL[keyState(k)].text }}</span>
          <span class="p-key__right num">{{
            balance(k).left === null ? balance(k).word : `${balanceText(balance(k))} pts`
          }}</span>
        </span>
        <span class="p-key__title k-label" :class="{ 'k-label--prefix mono': !k.label.trim() }">{{ keyName(k) }}</span>
        <span class="p-key__sub"
          ><span v-if="k.label.trim()" class="mono">{{ k.prefix }}••••</span
          ><template v-if="k.label.trim()"> · </template>{{ jobsText(k) }}</span
        >
        <span v-if="caps(k).length" class="p-key__caps">
          <CapChip v-for="c in caps(k)" :key="c.provider" :provider="c.provider" :used="c.used" :cap="c.cap" />
        </span>
        <span class="p-key__actions">
          <button
            v-if="!k.enabled"
            type="button"
            class="kbtn"
            :aria-label="`Enable key ${keyName(k)}`"
            @click="emit('toggle', k, true)"
          >
            Enable
          </button>
          <button
            v-else-if="keyState(k) === 'empty' || keyState(k) === 'closed'"
            type="button"
            class="kbtn kbtn--primary"
            @click="openWallet(k, $event)"
          >
            Top up
          </button>
          <button v-else type="button" class="kbtn" @click="openWallet(k, $event)">Adjust wallet</button>
          <IconButton icon="edit" :label="`Edit key ${keyName(k)}`" :disabled="busy" @click="emit('edit', k)" />
          <ActionMenu
            :items="menu(k)"
            :trigger-label="`More actions for ${keyName(k)}`"
            menu-label="Key actions"
            :icon="IconMoreHorizontal"
            :size="44"
            tip
            @select="(what) => pick(k, what)"
          />
        </span>
      </div>
    </div>

    <!-- a desktop and a tablet: the table scrolls sideways inside its own box -->
    <ArrowScroller v-else table label="Keys table">
      <table class="kl__table">
        <thead>
          <tr>
            <th>Key</th>
            <th class="r">Wallet</th>
            <th class="t-provs">Providers and caps today</th>
            <th>Jobs</th>
            <th class="t-act"><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="k in rows"
            :key="k.id"
            :class="{ 'is-new': k.id === createdId, 'is-off': !k.enabled }"
            data-testid="key-row"
          >
            <td class="t-key">
              <div class="t-key__line">
                <span class="k-label" :class="{ 'k-label--prefix mono': !k.label.trim() }">{{ keyName(k) }}</span>
                <span v-if="!k.enabled" class="pill tone-neutral">Paused</span>
              </div>
              <div v-if="k.label.trim()" class="meta mono">{{ k.prefix }}••••</div>
            </td>
            <td class="r t-wallet">
              <button
                type="button"
                class="wbtn"
                :aria-label="walletLabel(k)"
                aria-haspopup="dialog"
                :aria-expanded="walletId === k.id"
                @click="openWallet(k, $event)"
              >
                <span class="wbtn__bal num" :class="`tone-${balance(k).tone}`"
                  ><svg
                    class="wbtn__pen"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M4 20h4L19 9l-4-4L4 16z" /></svg
                  >{{ balanceText(balance(k)) }}<span v-if="balance(k).left !== null" class="unit"> pts</span></span
                >
                <span v-if="todayText(pointsWallet(k))" class="meta num">{{ todayText(pointsWallet(k)) }}</span>
              </button>
            </td>
            <td class="t-provs">
              <div v-if="caps(k).length" class="t-caps">
                <CapChip v-for="c in caps(k)" :key="c.provider" :provider="c.provider" :used="c.used" :cap="c.cap" />
              </div>
              <span v-else class="meta">no provider open</span>
            </td>
            <td class="t-jobs">
              <div class="t-jobs__n num">{{ jobsText(k) }}</div>
              <div class="meta num">{{ unfinishedText(k) }} unfinished</div>
            </td>
            <td class="t-act">
              <ActionMenu
                :items="menu(k)"
                :trigger-label="`More actions for ${keyName(k)}`"
                menu-label="Key actions"
                :icon="IconMoreHorizontal"
                :size="32"
                tip
                @select="(what) => pick(k, what)"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </ArrowScroller>
  </div>
</template>

<style scoped>
.kl {
  min-width: 0;
}
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}
.r {
  text-align: right;
}
.meta {
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-faint);
}

/* the table: 12 px head on the head fill, rows ruled */
.kl__table {
  width: 100%;
  min-width: 820px;
  border-collapse: collapse;
}
.kl--tablet .kl__table {
  min-width: 900px;
}
.kl__table th {
  padding: 10px 8px;
  background: var(--rl-head);
  color: var(--rl-faint);
  font-size: 12px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}
.kl__table th.r {
  text-align: right;
}
.kl__table th:first-child,
.kl__table td:first-child {
  padding-left: 20px;
}
.kl__table td {
  padding: 14px 8px;
  border-top: 1px solid var(--rl-rule);
  vertical-align: middle;
}
.kl__table .t-provs {
  padding-left: 24px;
}
.kl__table .t-act {
  width: 32px;
  padding: 10px 16px 10px 0;
  text-align: right;
}
.kl__table td.t-act {
  padding: 14px 16px 14px 0;
}
tr.is-off td:not(.t-act) {
  opacity: 0.72;
}
tr.is-new td {
  background: var(--rl-primary-soft);
}
.t-key {
  width: 30%;
}
.t-key__line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.t-key .meta {
  margin-top: 2px;
}
.k-label {
  min-width: 0;
  font-weight: 700;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
/* a key with no label: its prefix, muted and in mono */
.k-label--prefix {
  font-size: 13px;
  font-weight: 500;
  color: var(--rl-muted);
}
.t-wallet {
  white-space: nowrap;
}
.t-caps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.t-jobs {
  white-space: nowrap;
}
.t-jobs__n {
  font-weight: 600;
  color: var(--rl-body);
}

/* the wallet figure: a quiet button, its pencil shown on hover and focus */
.wbtn {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-end;
  margin: -6px -10px;
  padding: 6px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  text-align: right;
  cursor: pointer;
}
.wbtn:hover {
  border-color: var(--rl-line-strong);
  background: var(--rl-head);
}
.wbtn[aria-expanded="true"] {
  border-color: var(--rl-brand);
  background: var(--rl-primary-soft);
}
.wbtn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.wbtn__bal {
  display: inline-flex;
  align-items: baseline;
  gap: 0;
  font-size: 15px;
  line-height: 20px;
  font-weight: 800;
}
.wbtn__pen {
  align-self: center;
  margin-right: 6px;
  color: var(--rl-faint);
  opacity: 0;
}
.wbtn:hover .wbtn__pen,
.wbtn:focus-visible .wbtn__pen {
  opacity: 1;
}
.unit {
  white-space: pre;
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-faint);
}
.tone-ok {
  color: var(--rl-fg);
}
.tone-warn {
  color: var(--rl-warn);
}
.tone-bad {
  color: var(--rl-bad);
}

/* the pill of a state */
.pill {
  display: inline-flex;
  align-items: center;
  flex: none;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.pill.tone-ok {
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.pill.tone-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.pill.tone-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.pill.tone-neutral {
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}

/* the phone list */
.kl__list {
  display: flex;
  flex-direction: column;
}
.p-key {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 44px;
  padding: 14px 16px;
  border-top: 1px solid var(--rl-rule);
}
.kl__list > .p-key:first-child {
  border-top: 0;
}
.p-key.is-new {
  background: var(--rl-primary-soft);
}
.p-key__top {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.p-key__top .pill {
  height: 22px;
  padding: 0 9px;
}
.p-key__right {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-muted);
}
.p-key__title {
  font-size: 15px;
  line-height: 21px;
}
.p-key__title.k-label--prefix {
  font-size: 13px;
}
.p-key__sub {
  font-size: 12px;
  line-height: 17px;
  color: var(--rl-faint);
  overflow-wrap: anywhere;
}
.p-key__caps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.p-key__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.kbtn {
  flex: none;
  box-sizing: border-box;
  height: 44px;
  padding: 0 16px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}
.kbtn--primary {
  border: 0;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
}
.kbtn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}

/* the flat bars of a first read */
.bar {
  display: block;
  height: 12px;
  max-width: 100%;
  border-radius: 6px;
  background: var(--rl-skel);
}
.kl__table td:has(.bar) {
  padding-top: 18px;
  padding-bottom: 18px;
}
.bar--name {
  width: 150px;
}
.bar--bal {
  width: 70px;
  margin-left: auto;
}
.bar--caps {
  width: 140px;
}
.bar--jobs {
  width: 80px;
}
.p-key--skel .bar--a {
  width: 120px;
}
.p-key--skel .bar--b {
  width: 70%;
}
.p-key--skel .bar--c {
  width: 50%;
}

/* empty and failed: a box in the card's frame */
.kl__box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 40px 24px;
  text-align: center;
}
.kl__icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.kl__icon.is-brand {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.kl__icon.is-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.kl__title {
  font-size: 15px;
}
.kl__text {
  max-width: 420px;
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-muted);
}
.kl__btns {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.kl__primary {
  height: 36px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.kl__primary:hover {
  background: var(--rl-primary-hover);
}
.kl--phone .kl__primary {
  height: 44px;
}
</style>
