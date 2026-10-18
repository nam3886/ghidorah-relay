<script setup lang="ts">
import "./account-skeleton.css";
import type { Account } from "@/api/accounts";
import StatusChip from "@/components/shared/StatusChip.vue";
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
  barWidth,
  SKELETON_ROWS,
} from "./account-model";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import ActionMenu from "@/components/shared/ActionMenu.vue";
import CommandChip from "@/components/shared/CommandChip.vue";
import MiniChip from "./MiniChip.vue";

// The account list below 768 px (P-Accounts): one stacked row per account. There is no Check button: Check is the
// first item of the row menu.
defineProps<{ rows: Account[]; chips: Record<string, ChipState>; skeleton?: boolean }>();
const emit = defineEmits<{ toggle: [account: Account, on: boolean]; action: [name: RowAction, account: Account] }>();
</script>

<template>
  <!-- loading: the rows' own lines as flat bars (B-States loading) -->
  <ul v-if="skeleton" class="acct-list" aria-hidden="true">
    <li v-for="(s, n) in SKELETON_ROWS" :key="n" class="acct-item">
      <div class="head">
        <i class="acct-skel" :style="barWidth(s.label)" /><i
          class="acct-skel acct-skel--pill"
          :style="barWidth(s.pill)"
        />
      </div>
      <i class="acct-skel acct-skel--sub" :style="barWidth(s.sub)" />
      <i class="acct-skel" :style="barWidth(180)" />
      <div class="foot">
        <i class="acct-skel" :style="barWidth(110)" /><i class="acct-skel acct-skel--pill" :style="barWidth(36)" />
      </div>
    </li>
  </ul>
  <ul v-else class="acct-list">
    <li v-for="a in rows" :key="a.id" class="acct-item" :class="{ 'is-dead': a.state === 'dead' }">
      <div class="head">
        <span class="label">{{ a.label }}</span>
        <span class="pills">
          <StatusChip :status="a.state" />
          <StatusChip v-if="a.in_use" status="in use" />
        </span>
      </div>
      <span class="sub">{{ accountSub(a) }}</span>
      <div class="meta">
        <template v-if="a.capacity.length">
          <span v-for="c in poolsInOrder(a.capacity)" :key="c.pool" class="tnum points">
            <span class="word">{{ c.pool }}</span
            ><span class="mono figure">{{ pointsFigure(c) }}</span>
            <MiniChip v-if="c.drift === true" tone="warn">drift</MiniChip>
          </span>
        </template>
        <span v-if="a.proxy_host" class="points">
          <span class="mono proxy">{{ proxyText(a) }}</span>
          <MiniChip :tone="proxyCheck(a).tone">{{ proxyChipText(a) }}</MiniChip>
        </span>
        <span v-else class="direct">direct, no proxy</span>
      </div>
      <span v-if="asleepInfo(a)" class="tnum asleep">{{ asleepLine(asleepInfo(a)!) }}</span>
      <span v-if="a.last_error" class="mono err">{{ a.last_error }}</span>
      <div class="foot">
        <span class="checked">Checked {{ ago(a.last_checked_ms) }}</span>
        <div class="controls">
          <ToggleSwitch
            v-if="!a.in_use"
            size="phone"
            :model-value="a.state !== 'disabled'"
            :label="`Enable ${a.label}`"
            @change="(on: boolean) => emit('toggle', a, on)"
          />
          <span v-else class="spacer" />
          <ActionMenu
            :items="rowMenu(a, true)"
            :trigger-label="`More actions for ${a.label}`"
            menu-label="Account actions"
            variant="headerPhone"
            :size="44"
            :icon-size="18"
            @select="(key: string) => emit('action', key as RowAction, a)"
          />
        </div>
      </div>
      <!-- a phone's Check is in the row menu: its chip goes under that menu's trigger -->
      <div v-if="chips[`check:${a.id}`]" class="cmd-line">
        <CommandChip v-bind="chipProps(chips[`check:${a.id}`]!)" />
      </div>
    </li>
  </ul>
</template>

<style scoped>
.acct-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.acct-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 16px 4px;
  border-bottom: 1px solid var(--rl-line);
  background: var(--row-bg, transparent);
}
.acct-item.is-dead {
  --row-bg: var(--rl-bad-bg);
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 4px 8px;
}
.label {
  font-size: 15px;
  font-weight: 600;
}
.pills {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.sub {
  color: var(--rl-muted);
  font-size: 13px;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 14px;
}
.points {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tnum {
  font-variant-numeric: tabular-nums;
}
.mono {
  font-family: var(--rl-mono);
}
.word {
  color: var(--rl-muted);
  font-size: 12px;
}
.figure {
  font-size: 13px;
}
.proxy {
  font-size: 12px;
}
.direct {
  color: var(--rl-faint);
  font-size: 12px;
}
.asleep {
  color: var(--rl-warn);
  font-size: 13px;
  font-weight: 500;
}
.err {
  color: var(--rl-bad);
  font-size: 12px;
  line-height: 16px;
  overflow-wrap: anywhere;
}
.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.checked {
  color: var(--rl-faint);
  font-size: 12px;
}
.controls {
  display: flex;
  align-items: center;
}
.cmd-line {
  display: flex;
  justify-content: flex-end;
  margin-top: 2px;
  padding-bottom: 8px;
}
.spacer {
  display: block;
  width: 52px;
}
</style>
