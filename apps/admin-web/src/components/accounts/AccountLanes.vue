<script setup lang="ts">
import "./account-skeleton.css";
import { computed } from "vue";
import type { Account } from "@/api/accounts";
import type { ProvidersSummary } from "@/api/shell";
import ActionMenu from "@/components/shared/ActionMenu.vue";
import CommandChip from "@/components/shared/CommandChip.vue";
import ProviderTile from "@/components/shared/ProviderTile.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import { fmtTime } from "@/lib/format";
import { byProviderOrder, providerClass } from "@/lib/providers";
import {
  accountSub,
  ago,
  asleepInfo,
  asleepLine,
  barWidth,
  type ChipState,
  chipProps,
  madeLine,
  pointsFigure,
  poolShare,
  poolsInOrder,
  proxyCheck,
  proxyChipText,
  proxyText,
  type RowAction,
  rowMenu,
  SKELETON_ROWS,
  statusReason,
} from "./account-model";
import MiniChip from "./MiniChip.vue";

// The accounts of the page grouped per provider (final.html, "accounts board"): one lane per provider with a header in
// its colours (tile, name, usable / total from the provider summary, paused), then one row per account: label, status
// pill, "in use", the enable switch; who it is; its pools as meters in the provider's tone; the proxy and its check;
// why it sleeps and its last error; when it was checked and what it made, on a line of its own; Check and the row
// menu on a compact action row. One provider (or one
// lane) lays its rows out side by side. A phone has no Check button: Check is the first item of the row menu.
const props = defineProps<{
  rows: Account[];
  checking: ReadonlySet<string>;
  chips: Record<string, ChipState>;
  summary: ProvidersSummary | null;
  // the provider filter ("" = All): a lane's footer offers to show that provider alone
  selected: string;
  size: "desktop" | "tablet" | "phone";
  skeleton?: boolean;
}>();
const emit = defineEmits<{
  toggle: [account: Account, on: boolean];
  action: [name: RowAction, account: Account];
  pick: [provider: string];
}>();

const lanes = computed(() => {
  const by = new Map<string, Account[]>();
  for (const a of props.rows) by.set(a.provider, [...(by.get(a.provider) ?? []), a]);
  const info = new Map((props.summary?.providers ?? []).map((p) => [p.provider, p]));
  return byProviderOrder([...by.keys()]).map((name) => ({ name, rows: by.get(name)!, info: info.get(name) ?? null }));
});
const phone = computed(() => props.size === "phone");
</script>

<template>
  <!-- loading: one lane of five rows of flat bars (B-States loading) -->
  <div v-if="skeleton" class="board board--single" aria-hidden="true">
    <section class="lane p-other">
      <header class="lane__head"><i class="acct-skel" :style="barWidth(120)" /></header>
      <ul class="lane__rows">
        <li v-for="(s, n) in SKELETON_ROWS" :key="n" class="acct-row">
          <div class="acct-row__top">
            <i class="acct-skel" :style="barWidth(s.label)" /><i
              class="acct-skel acct-skel--pill acct-skel--end"
              :style="barWidth(s.pill)"
            />
          </div>
          <i class="acct-skel acct-skel--sub" :style="barWidth(s.sub)" />
        </li>
      </ul>
    </section>
  </div>
  <div v-else class="board" :class="{ 'board--single': lanes.length === 1, 'board--phone': phone }">
    <section
      v-for="lane in lanes"
      :key="lane.name"
      class="lane"
      :class="providerClass(lane.name)"
      :aria-label="`${lane.name} accounts`"
      :data-testid="`lane-${lane.name}`"
    >
      <header class="lane__head">
        <ProviderTile :provider="lane.name" class="lane__tile" />
        <h2 class="lane__name">{{ lane.name }}</h2>
        <span class="lane__meta tnum">
          <template v-if="lane.info">{{ lane.info.accounts.usable }}/{{ lane.info.accounts.total }} usable</template>
          <template v-else>{{ lane.rows.length }} {{ lane.rows.length === 1 ? "account" : "accounts" }}</template>
          <span v-if="lane.info && !lane.info.claims_enabled" class="paused">paused</span>
        </span>
      </header>
      <ul class="lane__rows">
        <li
          v-for="a in lane.rows"
          :key="a.id"
          class="acct-row"
          :class="{ 'is-dead': a.state === 'dead' }"
          :data-account="a.id"
        >
          <div class="acct-row__top">
            <span class="label clip" :title="a.label">{{ a.label }}</span>
            <StatusChip v-if="a.in_use" status="in use" />
            <StatusChip :status="a.state" />
            <ToggleSwitch
              v-if="!a.in_use"
              :size="size"
              :model-value="a.state !== 'disabled'"
              :label="`Enable ${a.label}`"
              @change="(on: boolean) => emit('toggle', a, on)"
            />
          </div>
          <span class="sub clip" :title="accountSub(a)">{{ accountSub(a) }}</span>
          <span v-if="statusReason(a)" class="mono bad reason clip" :title="statusReason(a)!">{{
            statusReason(a)
          }}</span>

          <div v-if="a.capacity.length" class="meters">
            <div v-for="c in poolsInOrder(a.capacity)" :key="c.pool" class="meter">
              <span class="meter__name">{{ c.pool }}</span>
              <span class="meter__track"><i :style="{ width: `${poolShare(c)}%` }" /></span>
              <span class="mono tnum meter__figure">{{ pointsFigure(c) }}</span>
              <MiniChip v-if="c.drift === true" tone="warn">drift</MiniChip>
            </div>
          </div>

          <div class="proxy-line">
            <template v-if="a.proxy_host">
              <span class="mono proxy clip" :title="proxyText(a)">{{ proxyText(a) }}</span>
              <span v-if="a.has_proxy_auth" class="auth">auth</span>
              <MiniChip :tone="proxyCheck(a).tone">{{ proxyChipText(a) }}</MiniChip>
            </template>
            <span v-else class="faint">direct, no proxy</span>
          </div>

          <div v-if="asleepInfo(a) || a.last_error" class="why">
            <span v-if="asleepInfo(a)" class="tnum asleep">{{ asleepLine(asleepInfo(a)!) }}</span>
            <span v-if="asleepInfo(a)?.why" class="mono faint clip" :title="asleepInfo(a)!.why ?? undefined">{{
              asleepInfo(a)!.why
            }}</span>
            <span v-if="a.last_error" class="mono bad err" :title="a.last_error">{{ a.last_error }}</span>
          </div>

          <span class="foot-note tnum clip" :title="fmtTime(a.last_checked_ms)"
            >Checked {{ ago(a.last_checked_ms) }} · {{ madeLine(a.made) }}</span
          >
          <div class="acct-row__foot">
            <button
              v-if="!phone"
              type="button"
              class="check-btn"
              :aria-label="`Check ${a.label}`"
              :aria-disabled="checking.has(a.id) ? 'true' : undefined"
              @click="emit('action', 'check', a)"
            >
              Check
            </button>
            <ActionMenu
              :items="rowMenu(a, phone)"
              :trigger-label="`More actions for ${a.label}`"
              menu-label="Account actions"
              :variant="phone ? 'headerPhone' : undefined"
              :size="phone ? 44 : undefined"
              :icon-size="phone ? 18 : undefined"
              @select="(key: string) => emit('action', key as RowAction, a)"
            />
          </div>
          <div v-if="chips[`check:${a.id}`]" class="cmd-line">
            <CommandChip v-bind="chipProps(chips[`check:${a.id}`]!)" />
          </div>
        </li>
      </ul>
      <button
        v-if="!selected && lane.info && lanes.length > 1"
        type="button"
        class="lane__more"
        @click="emit('pick', lane.name)"
      >
        All {{ lane.info.accounts.total }} {{ lane.name }} accounts →
      </button>
    </section>
  </div>
</template>

<style scoped>
.board {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  align-items: start;
  gap: 14px;
}
.board--phone {
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}
.lane {
  min-width: 0;
  overflow: hidden;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
  box-shadow: inset 0 4px 0 var(--pc);
}
.lane__head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 14px 12px;
  background: var(--ps);
  border-bottom: 1px solid var(--rl-line);
}
.lane__head .lane__tile {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  font-size: 15px;
}
.lane__name {
  margin: 0;
  color: var(--pi);
  font-size: 15px;
  font-weight: 600;
}
.lane__meta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  color: var(--rl-muted);
  font-size: 12px;
  white-space: nowrap;
}
.paused {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
  font-size: 11px;
  font-weight: 600;
}
.lane__rows {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
}
/* one lane: its rows side by side, as many as fit */
.board--single:not(.board--phone) .lane__rows {
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
}
.acct-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding: 10px 14px;
  border-top: 1px solid var(--rl-line);
  box-shadow: inset 3px 0 0 color-mix(in srgb, var(--pc) 45%, transparent);
  background: var(--row-bg, transparent);
}
.lane__rows > .acct-row:first-child {
  border-top: 0;
}
.board--single:not(.board--phone) .lane__rows > .acct-row {
  border-top: 0;
  border-bottom: 1px solid var(--rl-line);
}
.acct-row.is-dead {
  --row-bg: var(--rl-bad-bg);
}
.acct-row__top {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.label {
  flex: 1;
  min-width: 0;
  font-weight: 600;
}
.clip {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sub {
  color: var(--rl-muted);
  font-size: 12px;
}
.reason {
  font-size: 12px;
}
.mono {
  font-family: var(--rl-mono);
}
.tnum {
  font-variant-numeric: tabular-nums;
}
.faint {
  color: var(--rl-faint);
  font-size: 12px;
}
.bad {
  color: var(--rl-bad);
}
.meters {
  display: grid;
  gap: 4px;
  font-size: 12px;
}
.meter {
  display: grid;
  grid-template-columns: 44px minmax(40px, 1fr) auto auto;
  align-items: center;
  gap: 8px;
}
.meter__name {
  color: var(--rl-muted);
}
.meter__track {
  height: 6px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--rl-line);
}
.meter__track i {
  display: block;
  height: 100%;
  background: var(--pc);
}
.meter__figure {
  font-size: 12px;
}
.proxy-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
  min-width: 0;
}
.proxy {
  min-width: 0;
  font-size: 12px;
}
.auth {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
  font-size: 11px;
}
.why {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  font-size: 12px;
}
.asleep {
  color: var(--rl-warn);
  font-weight: 500;
}
.err {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.acct-row__foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.foot-note {
  min-width: 0;
  color: var(--rl-faint);
  font-size: 12px;
}
.check-btn {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}
.check-btn:hover {
  background: var(--rl-raised);
}
.check-btn[aria-disabled="true"] {
  background: var(--rl-raised);
  color: var(--rl-faint);
  cursor: default;
}
.cmd-line {
  display: flex;
  justify-content: flex-end;
}
.lane__more {
  display: block;
  width: 100%;
  padding: 10px 14px;
  border: 0;
  border-top: 1px solid var(--rl-line);
  background: transparent;
  color: var(--pi);
  font: inherit;
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
}
.lane__more:hover {
  background: var(--ps);
}
</style>
