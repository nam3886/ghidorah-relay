<script setup lang="ts">
import "./account-dialog.css";
import { computed } from "vue";
import type { Account } from "@/api/accounts";
import ActionMenu from "@/components/shared/ActionMenu.vue";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import CommandChip from "@/components/shared/CommandChip.vue";
import IconButton from "@/components/shared/IconButton.vue";
import { IconMoreHorizontal } from "@/components/shared/icons";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { providerClass } from "@/lib/providers";
import {
  accountSub,
  type ChipState,
  chipProps,
  type Health,
  poolFigure,
  poolLeft,
  poolsInOrder,
  poolTone,
  proxyLine,
  proxyText,
  rowAction,
  type RowAction,
  rowMenu,
  tabletSub,
} from "./account-model";

// The account list (Accounts, AccountsTablet, AccountsPhone, AccountsStates): one row per account, what needs a look
// first. A desktop draws five columns (the account with its provider chip, its health and why, its pools as meters,
// its proxy and the last check, the row action and More); a tablet folds the proxy and the reason under the account
// name and shows the first pool as a figure; a phone draws a list of rows. The row action is Re-check for a dead,
// proxy-failing or low account and Wake for an asleep one; the other actions are in More (a phone's More starts with
// Check) and in the detail the account's name opens. A desktop and a tablet tick rows for the bulk bar. The states:
// a first read as flat bars, the list empty for the first time, empty under the filters, and a failed read. Every
// server string reaches the page as text; a long name or code ends in "…" with the whole text as its title.
export interface Row {
  a: Account;
  h: Health;
}
const props = defineProps<{
  rows: Row[];
  size: "desktop" | "tablet" | "phone";
  checking: ReadonlySet<string>;
  chips: Record<string, ChipState>;
  selected: ReadonlySet<string>;
  loading: boolean;
  error: string | null;
  // the box an empty list shows: the first run or no match for the filters
  empty: { kind: "first" | "filter"; title: string; text: string; primary: string } | null;
}>();
const emit = defineEmits<{
  open: [account: Account];
  action: [name: RowAction, account: Account];
  select: [ids: string[], on: boolean];
  retry: [];
  primary: [];
}>();

const phone = computed(() => props.size === "phone");
const first = computed(() => props.loading && props.rows.length === 0 && !props.error);
const allTicked = computed(() => props.rows.length > 0 && props.rows.every((r) => props.selected.has(r.a.id)));
const SKELETON = [0, 1, 2, 3, 4];

const ACTION = {
  check: { icon: "recheck", label: "Re-check account", shortcut: "R" },
  wake: { icon: "wake", label: "Wake account", shortcut: "" },
} as const;
const actionOf = (r: Row) => {
  const kind = rowAction(r.h);
  return kind ? { kind, ...ACTION[kind] } : null;
};
// the meter of a pool: what is left in %, its tone, and the figure
function meter(c: Account["capacity"][number], sep = " / ") {
  const left = poolLeft(c);
  const pct = left === null ? null : Math.round(left * 100);
  return {
    name: c.pool,
    pct: pct === null ? "—" : `${pct}%`,
    width: { width: `${Math.max(pct ?? 0, 2)}%` },
    tone: left === null ? "ok" : poolTone(left),
    fig: poolFigure(c, sep),
  };
}
// the tablet's one pool: the low one first, else the video pool, as `video 0 / 4`
function firstPool(a: Account) {
  const pools = poolsInOrder(a.capacity);
  const c = pools.find((p) => (poolLeft(p) ?? 1) < 0.1) ?? pools[0];
  if (!c) return null;
  const left = poolLeft(c);
  return { text: `${c.pool} ${poolFigure(c)}`, bad: left !== null && left < 0.1 };
}
const onAction = (r: Row): void => {
  const kind = rowAction(r.h);
  if (kind) emit("action", kind, r.a);
};

// inbox, search-x, cloud-off (JobsStates)
const ICONS = {
  inbox: ["M3 13h5l1.5 3h5L16 13h5", "M5 5h14l2 8v6H3v-6z"],
  search: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z", "m20 20-3.5-3.5", "m8.5 8.5 5 5", "m13.5 8.5-5 5"],
  cloud: ["M7 18a5 5 0 0 1-.9-9.9A6 6 0 0 1 17.7 8 4.5 4.5 0 0 1 18 18z", "m3 3 18 18"],
};
</script>

<template>
  <div class="at" :class="`at--${props.size}`" data-testid="accounts-table">
    <!-- a first read: flat bars in the real columns, no shimmer -->
    <template v-if="first">
      <div v-if="phone" class="at__list" aria-busy="true" aria-label="Loading accounts" data-state="loading">
        <div v-for="i in SKELETON" :key="i" class="p-row p-row--skel">
          <span class="bar bar--a" /><span class="bar bar--b" /><span class="bar bar--c" />
        </div>
      </div>
      <ArrowScroller v-else table label="Accounts table">
        <table class="at__table" aria-busy="true" aria-label="Loading accounts" data-state="loading">
          <thead>
            <tr>
              <th>Account</th>
              <th>Health</th>
              <th>Pools left</th>
              <th>Proxy</th>
              <th class="r">Action</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="i in SKELETON" :key="i" class="acct-row">
              <td><span class="bar bar--name" /></td>
              <td><span class="bar bar--pill" /></td>
              <td><span class="bar bar--pool" /></td>
              <td><span class="bar bar--proxy" /></td>
              <td class="r"><span class="bar bar--act" /></td>
            </tr>
          </tbody>
        </table>
      </ArrowScroller>
    </template>

    <!-- a failed read: the frame stays, Retry reads again -->
    <div v-else-if="props.error" class="at__box" role="alert" data-state="error">
      <span class="at__icon is-bad"><PathIcon :paths="ICONS.cloud" :size="22" :stroke="1.8" /></span>
      <b class="at__title">{{ props.error }}</b>
      <div class="at__btns"><button type="button" class="at__primary" @click="emit('retry')">Retry</button></div>
    </div>

    <!-- nothing yet, or nothing under the filters -->
    <div v-else-if="!props.rows.length && props.empty" class="at__box" role="status" data-state="empty">
      <span class="at__icon" :class="{ 'is-brand': props.empty.kind === 'first' }"
        ><PathIcon :paths="props.empty.kind === 'first' ? ICONS.inbox : ICONS.search" :size="22" :stroke="1.8"
      /></span>
      <b class="at__title">{{ props.empty.title }}</b>
      <span class="at__text">{{ props.empty.text }}</span>
      <div class="at__btns">
        <button type="button" class="at__primary" @click="emit('primary')">{{ props.empty.primary }}</button>
      </div>
    </div>

    <!-- a phone: one row per account -->
    <div v-else-if="phone" class="at__list" :data-state="props.loading ? 'refreshing' : 'ready'">
      <div
        v-for="r in props.rows"
        :key="r.a.id"
        class="p-row acct-row"
        :class="providerClass(r.a.provider)"
        :data-account="r.a.id"
      >
        <span class="p-row__top">
          <span class="acct-pill" :class="`tone-${r.h.tone}`">{{ r.h.label }}</span>
          <span class="acct-pchip" :class="providerClass(r.a.provider)">{{ r.a.provider }}</span>
        </span>
        <span class="p-row__who">
          <button type="button" class="at__open p-row__label" :title="r.a.label" @click="emit('open', r.a)">
            {{ r.a.label }}
          </button>
          <span v-if="accountSub(r.a)" class="p-row__ident clip" :title="accountSub(r.a)">{{ accountSub(r.a) }}</span>
        </span>
        <span v-if="r.h.why" class="mono why" :class="`tone-${r.h.tone}`">{{ r.h.why }}</span>
        <span v-if="r.a.capacity.length" class="p-row__pools">
          <span v-for="c in poolsInOrder(r.a.capacity)" :key="c.pool" class="p-meter">
            <span class="faint">{{ c.pool }}</span>
            <span class="track"><span class="fill" :class="`is-${meter(c).tone}`" :style="meter(c, '/').width" /></span>
            <span class="num pct" :class="meter(c).tone !== 'ok' && `is-${meter(c).tone}`">{{ meter(c).pct }}</span>
            <span class="num faint r">{{ meter(c, "/").fig }}</span>
          </span>
        </span>
        <span class="p-row__foot">
          <span class="p-row__proxy">
            <span class="mono proxy clip" :title="proxyText(r.a) || undefined">{{
              r.a.proxy_host ? proxyText(r.a) : "direct"
            }}</span>
            <span class="check-line" :class="{ 'is-bad': r.a.proxy_host && r.a.proxy_ok === false }">{{
              proxyLine(r.a)
            }}</span>
          </span>
          <IconButton
            v-if="actionOf(r)"
            :icon="actionOf(r)!.icon"
            :label="actionOf(r)!.label"
            :shortcut="actionOf(r)!.shortcut"
            :disabled="actionOf(r)!.kind === 'check' && props.checking.has(r.a.id)"
            placement="top-end"
            @click="onAction(r)"
          />
          <ActionMenu
            :items="rowMenu(r.a, true)"
            :trigger-label="`More actions for ${r.a.label}`"
            :tip="`More actions for ${r.a.label}`"
            menu-label="Account actions"
            variant="headerPhone"
            :icon="IconMoreHorizontal"
            :size="44"
            :icon-size="18"
            @select="(key: string) => emit('action', key as RowAction, r.a)"
          />
        </span>
        <span v-if="props.chips[`check:${r.a.id}`]" class="cmd-line">
          <CommandChip v-bind="chipProps(props.chips[`check:${r.a.id}`]!)" />
        </span>
      </div>
    </div>

    <!-- a tablet: the proxy and the reason fold under the account; one pool as a figure -->
    <ArrowScroller v-else-if="props.size === 'tablet'" table label="Accounts table">
      <table class="at__table at__table--tablet" :data-state="props.loading ? 'refreshing' : 'ready'">
        <thead>
          <tr>
            <th class="tick">
              <input
                type="checkbox"
                class="at__tick"
                :checked="allTicked"
                aria-label="Select every row on this page"
                @change="
                  emit(
                    'select',
                    props.rows.map((r) => r.a.id),
                    !allTicked,
                  )
                "
              />
            </th>
            <th>Health</th>
            <th>Account</th>
            <th>Provider</th>
            <th class="r">Pools left</th>
            <th class="r">Action</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in props.rows"
            :key="r.a.id"
            class="acct-row"
            :class="[providerClass(r.a.provider), { 'is-ticked': props.selected.has(r.a.id) }]"
            :data-account="r.a.id"
          >
            <td class="tick">
              <input
                type="checkbox"
                class="at__tick"
                :checked="props.selected.has(r.a.id)"
                :aria-label="`Select ${r.a.label}`"
                @change="emit('select', [r.a.id], !props.selected.has(r.a.id))"
              />
            </td>
            <td>
              <span class="acct-pill acct-pill--tab" :class="`tone-${r.h.tone}`">{{ r.h.short }}</span>
            </td>
            <td class="t-acct">
              <button type="button" class="at__open t-name" :title="r.a.label" @click="emit('open', r.a)">
                {{ r.a.label }}
              </button>
              <span
                class="mono t-sub"
                :class="(r.h.kind === 'dead' || r.h.kind === 'proxy') && 'tone-bad'"
                :title="tabletSub(r.a, r.h)"
                >{{ tabletSub(r.a, r.h) }}</span
              >
            </td>
            <td>
              <span class="acct-pchip" :class="providerClass(r.a.provider)">{{ r.a.provider }}</span>
            </td>
            <td class="r num t-pool" :class="{ 'tone-bad': firstPool(r.a)?.bad }">
              {{ firstPool(r.a)?.text ?? "—" }}
            </td>
            <td class="r t-act">
              <IconButton
                v-if="actionOf(r)"
                :icon="actionOf(r)!.icon"
                :label="actionOf(r)!.label"
                :shortcut="actionOf(r)!.shortcut"
                :disabled="actionOf(r)!.kind === 'check' && props.checking.has(r.a.id)"
                placement="top-end"
                @click="onAction(r)"
              />
              <span v-else class="num faint">—</span>
              <span v-if="props.chips[`check:${r.a.id}`]" class="cmd-line">
                <CommandChip v-bind="chipProps(props.chips[`check:${r.a.id}`]!)" />
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </ArrowScroller>

    <!-- a desktop: five columns -->
    <ArrowScroller v-else table label="Accounts table">
      <table class="at__table" :data-state="props.loading ? 'refreshing' : 'ready'">
        <thead>
          <tr>
            <th class="tick">
              <input
                type="checkbox"
                class="at__tick"
                :checked="allTicked"
                aria-label="Select every row on this page"
                @change="
                  emit(
                    'select',
                    props.rows.map((r) => r.a.id),
                    !allTicked,
                  )
                "
              />
            </th>
            <th>Account</th>
            <th>Health</th>
            <th>Pools left</th>
            <th>Proxy</th>
            <th class="r">Action</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in props.rows"
            :key="r.a.id"
            class="acct-row"
            :class="[providerClass(r.a.provider), { 'is-ticked': props.selected.has(r.a.id) }]"
            :data-account="r.a.id"
          >
            <td class="tick">
              <input
                type="checkbox"
                class="at__tick"
                :checked="props.selected.has(r.a.id)"
                :aria-label="`Select ${r.a.label}`"
                @change="emit('select', [r.a.id], !props.selected.has(r.a.id))"
              />
            </td>
            <td class="d-acct">
              <div class="d-acct__in">
                <span class="acct-pchip" :class="providerClass(r.a.provider)">{{ r.a.provider }}</span>
                <div class="d-acct__who">
                  <button type="button" class="at__open d-name" :title="r.a.label" @click="emit('open', r.a)">
                    {{ r.a.label }}
                  </button>
                  <span v-if="accountSub(r.a)" class="small clip" :title="accountSub(r.a)">{{ accountSub(r.a) }}</span>
                </div>
              </div>
            </td>
            <td class="d-health">
              <div class="d-health__in">
                <span class="acct-pill" :class="`tone-${r.h.tone}`">{{ r.h.label }}</span>
                <span
                  v-if="r.h.why"
                  class="mono why clip"
                  :class="(r.h.kind === 'dead' || r.h.kind === 'proxy') && 'tone-bad'"
                  :title="r.h.why"
                  >{{ r.h.why }}</span
                >
              </div>
            </td>
            <td class="d-pools">
              <div v-if="r.a.capacity.length" class="d-pools__in">
                <div v-for="c in poolsInOrder(r.a.capacity)" :key="c.pool" class="d-meter">
                  <span class="small clip" :title="c.pool">{{ c.pool }}</span>
                  <span class="track"
                    ><span class="fill" :class="`is-${meter(c).tone}`" :style="meter(c).width"
                  /></span>
                  <span class="num pct" :class="meter(c).tone !== 'ok' && `is-${meter(c).tone}`">{{
                    meter(c).pct
                  }}</span>
                  <span class="num small">{{ meter(c).fig }}</span>
                </div>
              </div>
              <span v-else class="faint">—</span>
            </td>
            <td class="d-proxy">
              <div class="d-proxy__in">
                <span class="mono proxy clip" :title="proxyText(r.a) || undefined">{{
                  r.a.proxy_host ? proxyText(r.a) : "direct"
                }}</span>
                <span class="check-line" :class="{ 'is-bad': r.a.proxy_host && r.a.proxy_ok === false }">{{
                  proxyLine(r.a)
                }}</span>
              </div>
            </td>
            <td class="r d-act">
              <span class="d-act__btns">
                <IconButton
                  v-if="actionOf(r)"
                  :icon="actionOf(r)!.icon"
                  :label="actionOf(r)!.label"
                  :shortcut="actionOf(r)!.shortcut"
                  :disabled="actionOf(r)!.kind === 'check' && props.checking.has(r.a.id)"
                  @click="onAction(r)"
                />
                <ActionMenu
                  :items="rowMenu(r.a, false)"
                  :trigger-label="`More actions for ${r.a.label}`"
                  :tip="`More actions for ${r.a.label}`"
                  menu-label="Account actions"
                  :icon="IconMoreHorizontal"
                  :size="32"
                  @select="(key: string) => emit('action', key as RowAction, r.a)"
                />
              </span>
              <span v-if="props.chips[`check:${r.a.id}`]" class="cmd-line">
                <CommandChip v-bind="chipProps(props.chips[`check:${r.a.id}`]!)" />
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </ArrowScroller>
  </div>
</template>

<style scoped>
.at {
  min-width: 0;
}
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}
.faint {
  color: var(--rl-faint);
}
.r {
  text-align: right;
}
.clip {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.small {
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-faint);
}
.tone-bad {
  color: var(--rl-bad);
}
.tone-warn {
  color: var(--rl-warn);
}

/* the table: 12 px head on the head fill, rows ruled */
.at__table {
  width: 100%;
  min-width: 1100px;
  border-collapse: collapse;
  table-layout: fixed;
}
.at__table--tablet {
  min-width: 700px;
}
.at__table th {
  padding: 10px 8px;
  background: var(--rl-head);
  color: var(--rl-faint);
  font-size: 12px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}
.at__table th.r {
  text-align: right;
}
.at__table td {
  padding: 12px 8px;
  border-top: 1px solid var(--rl-rule);
  vertical-align: middle;
}
.at__table th:last-child,
.at__table td:last-child {
  padding-right: 20px;
}
.at__table th.tick,
.at__table td.tick {
  width: 20px;
  padding: 0 0 0 20px;
}
.at__table tr.is-ticked td {
  background: var(--rl-primary-soft);
}
.at__tick {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--rl-primary);
  cursor: pointer;
}
/* the desktop column widths: the account, the health and the pools in parts, the action fixed, and the rest for
   the proxy, so a host:port is never cut */
.at__table:not(.at__table--tablet) th:nth-child(2) {
  width: 23%;
  padding-left: 12px;
}
.at__table:not(.at__table--tablet) td.d-acct {
  padding-left: 12px;
}
.at__table:not(.at__table--tablet) th:nth-child(3) {
  width: 20%;
}
.at__table:not(.at__table--tablet) th:nth-child(4) {
  width: 23%;
}
.at__table:not(.at__table--tablet) th:nth-child(6) {
  width: 104px;
}
.at__table--tablet th:nth-child(2) {
  width: 130px;
  padding-left: 12px;
}
.at__table--tablet td:nth-child(2) {
  padding-left: 12px;
}
.at__table--tablet th:nth-child(4) {
  width: 76px;
}
.at__table--tablet th:nth-child(5) {
  width: 130px;
}
.at__table--tablet th:nth-child(6) {
  width: 96px;
}

/* the account name opens its detail: plain text, a link on hover */
.at__open {
  display: block;
  max-width: 100%;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-weight: 700;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.at__open:hover {
  color: var(--rl-link);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.d-acct__in {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.d-acct__who {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.d-health__in {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
}
.why {
  max-width: 100%;
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-faint);
}
.why.tone-bad {
  color: var(--rl-bad);
}
.d-pools__in {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.d-meter {
  display: grid;
  grid-template-columns: 44px 96px 40px auto;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 16px;
}
.track {
  display: block;
  height: 6px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--rl-track);
}
.fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--pc);
}
.fill.is-warn {
  background: var(--rl-warn);
}
.fill.is-bad {
  background: var(--rl-bad);
}
.pct {
  text-align: right;
  font-weight: 700;
  color: var(--rl-fg);
}
.pct.is-warn {
  color: var(--rl-warn);
}
.pct.is-bad {
  color: var(--rl-bad);
}
.d-proxy__in,
.p-row__proxy {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.proxy {
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-fg);
}
.check-line {
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-faint);
}
.check-line.is-bad {
  color: var(--rl-bad);
}
.d-act,
.t-act {
  white-space: nowrap;
}
.d-act__btns {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.cmd-line {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}
.t-name {
  font-weight: 600;
}
.t-sub {
  display: block;
  overflow: hidden;
  font-size: 11px;
  line-height: 16px;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--rl-faint);
}
/* the account column takes the room left and its lines end in "…" (AccountsTablet: one line each) */
.t-acct {
  width: 100%;
  max-width: 0;
}
.t-sub.tone-bad {
  color: var(--rl-bad);
}
.t-pool {
  white-space: nowrap;
  font-weight: 500;
  color: var(--rl-body);
}
.t-pool.tone-bad {
  font-weight: 700;
  color: var(--rl-bad);
}
.acct-pill--tab {
  height: 22px;
  padding: 0 9px;
}

/* the phone list */
.at__list {
  display: flex;
  flex-direction: column;
}
.p-row {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 12px 10px 16px;
  border-top: 1px solid var(--rl-rule);
}
.at__list > .p-row:first-child {
  border-top: 0;
}
.p-row__top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.p-row__who {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.p-row__label {
  font-size: 15px;
  line-height: 21px;
}
.p-row__ident {
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-faint);
}
.p-row__pools {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.p-meter {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) 36px 64px;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 16px;
}
.p-row__foot {
  display: flex;
  align-items: center;
  gap: 8px;
}
.p-row__proxy {
  flex: 1;
}
/* the row's icon buttons are the 44 px touch size on a phone at every pointer (AccountsPhone `ib t`) */
.p-row__foot :deep(.ib) {
  width: 44px;
  height: 44px;
  border-radius: 10px;
}

/* the flat bars of a first read */
.bar {
  display: block;
  height: 12px;
  max-width: 100%;
  border-radius: 6px;
  background: var(--rl-skel);
}
.at__table td:has(.bar) {
  padding-top: 18px;
  padding-bottom: 18px;
}
.bar--name {
  width: 160px;
}
.bar--pill {
  width: 90px;
}
.bar--pool {
  width: 110px;
}
.bar--proxy {
  width: 120px;
}
.bar--act {
  width: 60px;
  margin-left: auto;
}
.p-row--skel .bar--a {
  width: 120px;
}
.p-row--skel .bar--b {
  width: 70%;
}
.p-row--skel .bar--c {
  width: 50%;
}

/* empty and failed: a box in the card's frame */
.at__box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 40px 24px;
  text-align: center;
}
.at__icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.at__icon.is-brand {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.at__icon.is-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.at__title {
  font-size: 15px;
}
.at__text {
  max-width: 420px;
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-muted);
}
.at__btns {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.at__primary {
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
.at__primary:hover {
  background: var(--rl-primary-hover);
}
.at--phone .at__primary {
  height: 44px;
}
</style>
