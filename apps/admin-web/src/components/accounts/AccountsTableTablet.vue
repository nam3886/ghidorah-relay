<script setup lang="ts">
import "./account-skeleton.css";
import type { Account } from "@/api/accounts";
import StatusChip from "@/components/shared/StatusChip.vue";
import {
  accountSub,
  ago,
  asleepInfo,
  extraError,
  madeLine,
  pointsFigure,
  poolsInOrder,
  proxyCheck,
  proxyChipText,
  proxyText,
  type RowAction,
  rowMenu,
  statusReason,
  type ChipState,
  chipProps,
  barWidth,
  SKELETON_ROWS,
} from "./account-model";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import ActionMenu from "@/components/shared/ActionMenu.vue";
import CommandChip from "@/components/shared/CommandChip.vue";
import MiniChip from "./MiniChip.vue";

// The account table from 768 to 1279 px (T-Accounts): five columns that fit the width; the counts, the proxy and the
// last error fold into the Account, Status and Points cells.
defineProps<{
  rows: Account[];
  checking: ReadonlySet<string>;
  chips: Record<string, ChipState>;
  skeleton?: boolean;
}>();
const emit = defineEmits<{ toggle: [account: Account, on: boolean]; action: [name: RowAction, account: Account] }>();
</script>

<template>
  <table class="acct-table">
    <thead>
      <tr>
        <th scope="col" class="first">Account</th>
        <th scope="col" class="w-status">Status</th>
        <th scope="col" class="w-points">Points</th>
        <th scope="col" class="w-asleep">Asleep</th>
        <th scope="col" class="w-actions right">Actions</th>
      </tr>
    </thead>
    <!-- loading: the header above, flat bars in each column (B-States loading) -->
    <tbody v-if="skeleton" aria-hidden="true">
      <tr v-for="(s, n) in SKELETON_ROWS" :key="n" class="acct-skel-row">
        <td class="first">
          <i class="acct-skel" :style="barWidth(s.label)" /><i
            class="acct-skel acct-skel--sub"
            :style="barWidth(s.sub)"
          />
        </td>
        <td><i class="acct-skel acct-skel--pill" :style="barWidth(s.pill)" /></td>
        <td>
          <i class="acct-skel" :style="barWidth(64)" /><i class="acct-skel acct-skel--sub" :style="barWidth(80)" />
        </td>
        <td><i class="acct-skel" :style="barWidth(24)" /></td>
        <td class="last"><i class="acct-skel acct-skel--end" :style="barWidth(96)" /></td>
      </tr>
    </tbody>
    <tbody v-else>
      <tr v-for="a in rows" :key="a.id" class="acct-row" :class="{ 'is-dead': a.state === 'dead' }">
        <td class="first">
          <span class="label">{{ a.label }}</span>
          <span class="sub">{{ accountSub(a) }}</span>
          <span v-if="a.proxy_host" class="proxy-line">
            <span class="mono proxy">{{ proxyText(a) }}</span>
            <MiniChip :tone="proxyCheck(a).tone">{{ proxyChipText(a) }}</MiniChip>
          </span>
          <span v-else class="direct">direct, no proxy</span>
        </td>
        <td>
          <span class="chips">
            <StatusChip :status="a.state" />
            <StatusChip v-if="a.in_use" status="in use" />
          </span>
          <span v-if="statusReason(a)" class="line mono bad">{{ statusReason(a) }}</span>
          <span class="line faint">Checked {{ ago(a.last_checked_ms) }}</span>
          <span v-if="extraError(a) && extraError(a) !== '—'" class="line mono bad">{{ extraError(a) }}</span>
        </td>
        <td class="tnum">
          <template v-if="a.capacity.length">
            <template v-for="c in poolsInOrder(a.capacity)" :key="c.pool">
              <span class="block"
                ><span class="word">{{ c.pool }}</span> <span class="mono figure">{{ pointsFigure(c) }}</span></span
              >
              <MiniChip v-if="c.drift === true" tone="warn" class="drift">drift</MiniChip>
            </template>
          </template>
          <span v-else class="faint">—</span>
          <span class="made">{{ madeLine(a.made) }}</span>
        </td>
        <td>
          <template v-if="asleepInfo(a)">
            <span class="tnum asleep">until {{ asleepInfo(a)!.until }}</span>
            <span v-if="asleepInfo(a)!.kind || asleepInfo(a)!.why" class="mono why">{{
              asleepInfo(a)!.kind ?? asleepInfo(a)!.why
            }}</span>
          </template>
          <span v-else class="faint">—</span>
        </td>
        <td class="last">
          <div class="actions">
            <ToggleSwitch
              v-if="!a.in_use"
              size="tablet"
              :model-value="a.state !== 'disabled'"
              :label="`Enable ${a.label}`"
              @change="(on: boolean) => emit('toggle', a, on)"
            />
            <span v-else class="spacer" />
            <button
              type="button"
              class="check-btn"
              :aria-label="`Check ${a.label}`"
              :aria-disabled="checking.has(a.id) ? 'true' : undefined"
              @click="emit('action', 'check', a)"
            >
              Check
            </button>
            <ActionMenu
              :items="rowMenu(a, false)"
              :trigger-label="`More actions for ${a.label}`"
              menu-label="Account actions"
              :size="32"
              @select="(key: string) => emit('action', key as RowAction, a)"
            />
            <span v-if="chips[`check:${a.id}`]" class="cmd-chip">
              <CommandChip v-bind="chipProps(chips[`check:${a.id}`]!)" />
            </span>
          </div>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.acct-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: separate;
  border-spacing: 0;
}
th {
  height: 38px;
  padding: 0 8px;
  background: var(--rl-raised);
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 500;
  text-align: left;
}
th.first {
  padding: 0 8px 0 16px;
}
th.right {
  padding: 0 12px 0 8px;
  text-align: right;
}
.w-status {
  width: 138px;
}
.w-points {
  width: 104px;
}
.w-asleep {
  width: 84px;
}
.w-actions {
  width: 156px;
}
td {
  padding: 10px 8px;
  border-bottom: 1px solid var(--rl-line);
  background: var(--row-bg, transparent);
  vertical-align: top;
}
.acct-row.is-dead {
  --row-bg: var(--rl-bad-bg);
}
.acct-row:hover td {
  background: var(--rl-raised);
}
td.first {
  padding-left: 16px;
}
td.last {
  padding-right: 12px;
}
.tnum {
  font-variant-numeric: tabular-nums;
}
.mono {
  font-family: var(--rl-mono);
}
.faint {
  color: var(--rl-faint);
}
.bad {
  color: var(--rl-bad);
}
.label {
  display: block;
  font-weight: 600;
}
.sub {
  display: block;
  color: var(--rl-muted);
  font-size: 12px;
}
.proxy-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
  margin-top: 4px;
}
.proxy {
  font-size: 11px;
  line-height: 16px;
}
.direct {
  display: block;
  margin-top: 4px;
  color: var(--rl-faint);
  font-size: 12px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.line {
  display: block;
  margin-top: 3px;
  font-size: 11px;
  line-height: 16px;
  word-break: break-word;
}
.block {
  display: block;
}
.word {
  color: var(--rl-muted);
  font-size: 12px;
}
.figure {
  font-size: 13px;
}
.drift {
  margin-top: 3px;
}
.made {
  display: block;
  margin-top: 3px;
  color: var(--rl-muted);
  font-size: 12px;
  line-height: 16px;
}
.asleep {
  display: block;
  color: var(--rl-warn);
  font-size: 13px;
  font-weight: 500;
}
.why {
  display: block;
  color: var(--rl-faint);
  font-size: 11px;
}
/* switch, Check, More in one row; a command chip on a second line, from under Check to the end */
.actions {
  display: grid;
  grid-template-columns: auto auto auto;
  justify-content: end;
  align-items: center;
  gap: 8px 6px;
}
.cmd-chip {
  display: flex;
  grid-column: 2 / 4;
  min-width: 0;
}
.spacer {
  display: block;
  width: 44px;
}
.check-btn {
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  /* the page's family; the line height stays the button's own (normal), as drawn */
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
</style>
