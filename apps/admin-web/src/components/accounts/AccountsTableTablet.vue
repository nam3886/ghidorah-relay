<script setup lang="ts">
import type { Account } from "@/api/accounts";
import StatusChip from "@/components/shared/StatusChip.vue";
import {
  accountSub,
  ago,
  asleepInfo,
  extraError,
  pointsFigure,
  proxyCheck,
  proxyChipText,
  proxyText,
  type RowAction,
  rowMenu,
} from "./account-model";
import AccountSwitch from "./AccountSwitch.vue";
import MenuPopover from "./MenuPopover.vue";
import MiniChip from "./MiniChip.vue";

// The account table from 768 to 1279 px (T-Accounts): five columns that fit the width; the counts, the proxy and the
// last error fold into the Account, Status and Points cells.
defineProps<{ rows: Account[]; checking: ReadonlySet<string> }>();
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
    <tbody>
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
          <span v-if="a.state_reason" class="line mono bad">{{ a.state_reason }}</span>
          <span class="line faint">Checked {{ ago(a.last_checked_ms) }}</span>
          <span v-if="extraError(a) && extraError(a) !== '—'" class="line mono bad">{{ extraError(a) }}</span>
        </td>
        <td class="tnum">
          <template v-if="a.capacity.length">
            <template v-for="c in a.capacity" :key="c.pool">
              <span class="block"
                ><span class="word">{{ c.pool }}</span> <span class="mono figure">{{ pointsFigure(c) }}</span></span
              >
              <MiniChip v-if="c.drift === true" tone="warn" class="drift">drift</MiniChip>
            </template>
          </template>
          <span v-else class="faint">—</span>
          <span class="made">{{ a.made.video }} videos · {{ a.made.image }} images</span>
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
            <AccountSwitch
              v-if="!a.in_use"
              size="tablet"
              :on="a.state !== 'disabled'"
              :label="`Enable ${a.label}`"
              @change="(on: boolean) => emit('toggle', a, on)"
            />
            <span v-else class="spacer" />
            <button
              type="button"
              class="check-btn"
              :aria-label="`Check ${a.label}`"
              :aria-disabled="checking.has(a.id) ? 'true' : undefined"
              @click="!checking.has(a.id) && emit('action', 'check', a)"
            >
              Check
            </button>
            <MenuPopover
              :items="rowMenu(a, false)"
              :trigger-label="`More actions for ${a.label}`"
              menu-label="Account actions"
              :size="32"
              @select="(key: string) => emit('action', key as RowAction, a)"
            />
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
.actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
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
  font: inherit;
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
