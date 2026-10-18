<script setup lang="ts">
import "./account-skeleton.css";
import type { Account } from "@/api/accounts";
import StatusChip from "@/components/shared/StatusChip.vue";
import { fmtTime } from "@/lib/format";
import {
  accountSub,
  ago,
  asleepInfo,
  asleepLine,
  type ChipState,
  chipProps,
  pointsFigure,
  poolsInOrder,
  proxyCheck,
  proxyChipText,
  proxyText,
  type RowAction,
  rowMenu,
  statusReason,
  barWidth,
  SKELETON_ROWS,
} from "./account-model";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import ActionMenu from "@/components/shared/ActionMenu.vue";
import CommandChip from "@/components/shared/CommandChip.vue";
import MiniChip from "./MiniChip.vue";

// The account table from 1280 px (B-Accounts): nine columns inside a box that scrolls sideways below 1280 px. A row's
// Check has its command chip under it while it runs and until the next read (`chips`, by `check:<id>`).
defineProps<{
  rows: Account[];
  checking: ReadonlySet<string>;
  chips: Record<string, ChipState>;
  skeleton?: boolean;
}>();
const emit = defineEmits<{ toggle: [account: Account, on: boolean]; action: [name: RowAction, account: Account] }>();
</script>

<template>
  <div class="acct-scroll">
    <table class="acct-table">
      <thead>
        <tr>
          <th scope="col" class="first">Account</th>
          <th scope="col">Status</th>
          <th scope="col" class="nowrap">Points left / budget</th>
          <th scope="col">Asleep</th>
          <th scope="col" class="right">Videos made</th>
          <th scope="col" class="right">Images made</th>
          <th scope="col">Proxy</th>
          <th scope="col">Last error</th>
          <th scope="col" class="right last">Actions</th>
        </tr>
      </thead>
      <!-- loading: the header above, flat bars in each column (B-States loading) -->
      <tbody v-if="skeleton" aria-hidden="true">
        <tr v-for="(s, n) in SKELETON_ROWS" :key="n">
          <td class="first two-line">
            <i class="acct-skel" :style="barWidth(s.label)" /><i
              class="acct-skel acct-skel--sub"
              :style="barWidth(s.sub)"
            />
          </td>
          <td class="two-line"><i class="acct-skel acct-skel--pill" :style="barWidth(s.pill)" /></td>
          <td><i class="acct-skel" :style="barWidth(64)" /></td>
          <td><i class="acct-skel" :style="barWidth(24)" /></td>
          <td class="right"><i class="acct-skel acct-skel--end" :style="barWidth(22)" /></td>
          <td class="right"><i class="acct-skel acct-skel--end" :style="barWidth(22)" /></td>
          <td><i class="acct-skel" :style="barWidth(100)" /></td>
          <td><i class="acct-skel" :style="barWidth(120)" /></td>
          <td class="last"><i class="acct-skel acct-skel--end" :style="barWidth(96)" /></td>
        </tr>
      </tbody>
      <tbody v-else>
        <tr v-for="a in rows" :key="a.id" class="acct-row" :class="{ 'is-dead': a.state === 'dead' }">
          <td class="first two-line nowrap cap-account">
            <span class="label clip" :title="a.label">{{ a.label }}</span>
            <span class="sub clip" :title="accountSub(a)">{{ accountSub(a) }}</span>
          </td>
          <td class="two-line nowrap cap-status">
            <!-- a space between the pills, as the canvas's markup puts one there -->
            <StatusChip :status="a.state" /><template v-if="a.in_use">{{ " " }}</template
            ><StatusChip v-if="a.in_use" status="in use" class="gap-chip" />
            <span class="status-note clip" :title="fmtTime(a.last_checked_ms)"
              ><template v-if="statusReason(a)"
                ><span class="mono bad clip" :title="statusReason(a)!">{{ statusReason(a) }}</span> · </template
              >Checked {{ ago(a.last_checked_ms) }}</span
            >
          </td>
          <td class="tnum nowrap">
            <template v-if="a.capacity.length">
              <div v-for="c in poolsInOrder(a.capacity)" :key="c.pool" class="points">
                <span class="word">{{ c.pool }}</span> <span class="mono figure">{{ pointsFigure(c) }}</span
                ><template v-if="c.drift === true">{{ " " }}</template
                ><MiniChip v-if="c.drift === true" tone="warn" class="gap-chip">drift</MiniChip>
              </div>
            </template>
            <span v-else class="faint">—</span>
          </td>
          <td class="two-line nowrap cap-asleep">
            <template v-if="asleepInfo(a)">
              <span class="tnum asleep clip" :title="asleepLine(asleepInfo(a)!)">{{ asleepLine(asleepInfo(a)!) }}</span>
              <span v-if="asleepInfo(a)!.why" class="mono why clip" :title="asleepInfo(a)!.why ?? undefined">{{
                asleepInfo(a)!.why
              }}</span>
            </template>
            <span v-else class="faint">—</span>
          </td>
          <td class="tnum right mono">{{ a.made.video }}</td>
          <td class="tnum right mono">{{ a.made.image }}</td>
          <td class="two-line nowrap">
            <template v-if="a.proxy_host">
              <span class="mono proxy">{{ proxyText(a) }}</span
              ><template v-if="a.has_proxy_auth">{{ " " }}</template
              ><span v-if="a.has_proxy_auth" class="auth">auth</span>
              <span class="proxy-chip"
                ><MiniChip :tone="proxyCheck(a).tone">{{ proxyChipText(a) }}</MiniChip></span
              >
            </template>
            <span v-else class="faint">direct</span>
          </td>
          <td class="err-cell">
            <span v-if="a.last_error" class="mono err" :title="a.last_error">{{ a.last_error }}</span>
            <span v-else class="mono err">—</span>
          </td>
          <td class="last">
            <div class="actions">
              <ToggleSwitch
                v-if="!a.in_use"
                size="desktop"
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
  </div>
</template>

<style scoped>
.acct-scroll {
  overflow-x: auto;
}
.acct-table {
  width: 100%;
  min-width: 1280px;
  border-collapse: separate;
  border-spacing: 0;
}
th {
  height: 38px;
  padding: 0 12px;
  background: var(--rl-raised);
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 500;
  text-align: left;
}
th.first {
  padding: 0 12px 0 16px;
}
th.last {
  padding: 0 16px 0 12px;
}
.right {
  text-align: right;
}
td {
  padding: 0 12px;
  border-bottom: 1px solid var(--rl-line);
  background: var(--row-bg, transparent);
}
.acct-row.is-dead {
  --row-bg: var(--rl-bad-bg);
}
.acct-row:hover td {
  background: var(--rl-raised);
}
td.two-line {
  padding-top: 8px;
  padding-bottom: 8px;
}
td.first {
  height: 64px;
  box-sizing: border-box;
  padding-left: 16px;
}
td.last {
  padding-right: 16px;
}
.nowrap {
  white-space: nowrap;
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
.label {
  display: block;
  font-weight: 600;
}
/* A long value never widens its column past the canvas's (Jobs' rule, design-deviations 17): the Account, Status and
   Asleep lines end in "…" within the widest line B-Accounts draws in that column at 1440 (195.22, 226.63 and
   156.70 px), the full text as their title; Proxy and Last error stay where the board shows them. */
.clip {
  max-width: var(--cap);
  overflow: hidden;
  text-overflow: ellipsis;
}
.cap-account {
  --cap: 195.25px;
}
.cap-status {
  --cap: 226.75px;
}
.cap-asleep {
  --cap: 156.75px;
}
/* in the status line the dead reason gives way first, so "· Checked … ago" stays whole: a row of the reason and the
   rest of the line on one baseline, as the inline line sets them (`pre` keeps the space before the dot) */
.status-note.clip {
  display: flex;
  align-items: baseline;
  white-space: pre;
}
.status-note .clip {
  flex: 0 1 auto;
  min-width: 0;
}
.sub {
  display: block;
  color: var(--rl-muted);
  font-size: 12px;
}
.gap-chip {
  margin-left: 4px;
}
.status-note {
  display: block;
  margin-top: 3px;
  color: var(--rl-faint);
  font-size: 11px;
  line-height: 16px;
}
.bad {
  color: var(--rl-bad);
}
.word {
  color: var(--rl-muted);
  font-size: 12px;
}
.figure {
  font-size: 13px;
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
td.right.mono {
  font-size: 13px;
}
.proxy {
  font-size: 12px;
}
.auth {
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
  font-size: 11px;
}
.proxy-chip {
  display: block;
  margin-top: 3px;
}
.err-cell {
  max-width: 200px;
}
.err {
  display: block;
  max-width: 200px;
  overflow: hidden;
  color: var(--rl-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* switch, Check, More in one row; a command chip on a second line, from under Check to the end */
.actions {
  display: grid;
  grid-template-columns: auto auto auto;
  justify-content: end;
  align-items: center;
  gap: 8px 8px;
}
.cmd-chip {
  display: flex;
  grid-column: 2 / 4;
  min-width: 0;
}
.spacer {
  display: block;
  width: 36px;
}
.check-btn {
  height: 30px;
  padding: 0 12px;
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
