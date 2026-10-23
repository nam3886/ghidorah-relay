<script setup lang="ts">
import { computed } from "vue";
import type { ProviderSummary, ProvidersSummary } from "@/api/shell";
import ProviderTile from "@/components/shared/ProviderTile.vue";
import { providerClass } from "@/lib/providers";
import { hourBars, pathText, poolShare, poolText, rowsOf, totalsOf } from "@/components/jobs/provider-table";

// "Jobs today" at the top of Jobs (option B, small multiples): one row per provider with its finished jobs per hour of
// the billing day, its open jobs, what finished today, its accounts, each pool's points left and how it sends; a
// totals row under them. A row picks that provider in the strip (the others dim); a second click clears it. In a
// container 860 px wide or less each row is a small card.
const props = defineProps<{ summary: ProvidersSummary | null; selected: string }>();
const emit = defineEmits<{ pick: [provider: string] }>();

const rows = computed(() => (props.summary ? rowsOf(props.summary) : []));
const totals = computed(() => totalsOf(rows.value));
const bars = computed(() =>
  Object.fromEntries(rows.value.map((p) => [p.provider, props.summary ? hourBars(props.summary, p.provider) : []])),
);
const rowClass = (p: ProviderSummary) => [
  providerClass(p.provider),
  { "is-on": props.selected === p.provider, "is-dim": !!props.selected && props.selected !== p.provider },
];
const pick = (name: string) => emit("pick", props.selected === name ? "" : name);
const finished = (p: ProviderSummary) => p.done_today + p.failed_today;
</script>

<template>
  <section v-if="summary && rows.length" class="ptable" aria-labelledby="ptable-title" data-testid="provider-table">
    <header class="ptable__head">
      <h2 id="ptable-title">Jobs today</h2>
      <span class="ptable__sub">one row per provider · bars are finished jobs per hour, 00–24</span>
      <span class="ptable__tot"
        ><b class="num">{{ totals.done + totals.failed }}</b> finished today ·
        <b class="num ptable__bad">{{ totals.failed }}</b> failed</span
      >
    </header>

    <div class="ptable__wide">
      <table class="ptable__table">
        <thead>
          <tr>
            <th>Provider</th>
            <th class="l">Per hour</th>
            <th>Running</th>
            <th>Done</th>
            <th>Failed</th>
            <th>Accounts</th>
            <th>Pool left</th>
            <th>Path</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(p, i) in rows"
            :key="p.provider"
            class="ptable__row"
            :class="rowClass(p)"
            :style="{ '--i': i }"
            :data-testid="`provider-row-${p.provider}`"
            @click="pick(p.provider)"
          >
            <td>
              <button
                type="button"
                class="ptable__who"
                :aria-pressed="selected === p.provider"
                :aria-label="`Show ${p.provider} only`"
                @click.stop="pick(p.provider)"
              >
                <ProviderTile :provider="p.provider" />{{ p.provider }}
              </button>
            </td>
            <td class="l">
              <span class="spark" role="img" :aria-label="`${p.provider}: ${finished(p)} finished today, by hour`">
                <i
                  v-for="(b, h) in bars[p.provider]"
                  :key="h"
                  :class="`spark__${b.kind}`"
                  :style="b.kind === 'bar' ? { height: `${b.pct}%`, '--h': h } : undefined"
                  :title="b.title"
                ></i>
              </span>
            </td>
            <td class="num">
              {{ p.running }}<span v-if="p.queued" class="faint"> +{{ p.queued }}</span>
            </td>
            <td class="num">{{ p.done_today }}</td>
            <td class="num">
              <span class="failn" :class="{ 'failn--none': !p.failed_today }"
                ><i v-if="p.failed_today"></i>{{ p.failed_today }}</span
              >
            </td>
            <td class="num">
              {{ p.accounts.usable }}<span class="faint">/{{ p.accounts.total }}</span>
            </td>
            <td>
              <span class="pools">
                <span v-for="pool in p.pools" :key="pool.pool" class="mini">
                  <span class="num">{{ poolText(pool) }}</span>
                  <span class="mini__bar"><i :style="{ width: `${poolShare(pool)}%` }"></i></span>
                </span>
              </span>
            </td>
            <td>
              <span class="via" :class="{ 'via--paused': !p.claims_enabled }">{{ pathText(p) }}</span>
            </td>
          </tr>
          <tr class="ptable__sum">
            <td>All</td>
            <td class="l">{{ totals.done + totals.failed }} finished</td>
            <td class="num">
              {{ totals.running }}<span v-if="totals.queued" class="faint"> +{{ totals.queued }}</span>
            </td>
            <td class="num">{{ totals.done }}</td>
            <td class="num">
              <span class="failn" :class="{ 'failn--none': !totals.failed }"
                ><i v-if="totals.failed"></i>{{ totals.failed }}</span
              >
            </td>
            <td class="num">{{ totals.usable }}/{{ totals.accounts }}</td>
            <td></td>
            <td></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="ptable__cards">
      <div
        v-for="(p, i) in rows"
        :key="p.provider"
        class="pcard"
        :class="rowClass(p)"
        :style="{ '--i': i }"
        role="button"
        tabindex="0"
        :aria-pressed="selected === p.provider"
        :aria-label="`Show ${p.provider} only`"
        :data-testid="`provider-card-${p.provider}`"
        @click="pick(p.provider)"
        @keydown.enter.prevent="pick(p.provider)"
        @keydown.space.prevent="pick(p.provider)"
      >
        <div class="pcard__top">
          <ProviderTile :provider="p.provider" />{{ p.provider }}
          <span class="via" :class="{ 'via--paused': !p.claims_enabled }">{{ pathText(p) }}</span>
        </div>
        <span class="spark spark--wide" aria-hidden="true">
          <i
            v-for="(b, h) in bars[p.provider]"
            :key="h"
            :class="`spark__${b.kind}`"
            :style="b.kind === 'bar' ? { height: `${b.pct}%`, '--h': h } : undefined"
          ></i>
        </span>
        <div class="pcard__grid">
          <span
            ><b class="num">{{ p.done_today }}</b
            >done</span
          >
          <span
            ><b class="num"
              >{{ p.running }}<span v-if="p.queued" class="faint"> +{{ p.queued }}</span></b
            >running</span
          >
          <span
            ><b class="failn" :class="{ 'failn--none': !p.failed_today }"
              ><i v-if="p.failed_today"></i>{{ p.failed_today }}</b
            >failed</span
          >
          <span
            ><b class="num">{{ p.accounts.usable }}/{{ p.accounts.total }}</b
            >accounts</span
          >
          <span class="pcard__pools">
            <span v-for="pool in p.pools" :key="pool.pool" class="mini">
              <span class="num">{{ poolText(pool) }}</span>
              <span class="mini__bar"><i :style="{ width: `${poolShare(pool)}%` }"></i></span>
            </span>
          </span>
        </div>
      </div>
      <div class="pcard pcard--sum">
        All · {{ totals.done + totals.failed }} finished · {{ totals.running }} running ·
        <span :class="{ ptable__bad: totals.failed }">{{ totals.failed }} failed</span> · {{ totals.usable }}/{{
          totals.accounts
        }}
        accounts
      </div>
    </div>
  </section>
</template>

<style scoped>
.ptable {
  container-type: inline-size;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
  overflow: hidden;
}
.ptable__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 12px;
  padding: 16px 20px 0;
}
.ptable__head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.ptable__sub {
  font-size: 12px;
  color: var(--rl-muted);
}
.ptable__tot {
  margin-left: auto;
  font-size: 12px;
  color: var(--rl-muted);
}
.ptable__tot b {
  color: var(--rl-fg);
  font: 600 13px var(--rl-mono);
}
.ptable__tot .ptable__bad,
.ptable__bad {
  color: var(--rl-bad);
}
.num {
  font-variant-numeric: tabular-nums;
}
.faint {
  color: var(--rl-faint);
}

/* the table */
.ptable__wide {
  margin-top: 6px;
  overflow-x: auto;
}
.ptable__table {
  width: 100%;
  border-collapse: collapse;
}
.ptable__table th {
  padding: 12px 14px 8px;
  border-bottom: 1px solid var(--rl-line);
  color: var(--rl-muted);
  font-size: 11px;
  font-weight: 500;
  text-align: right;
  white-space: nowrap;
}
.ptable__table td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--rl-line);
  font-size: 13px;
  text-align: right;
  white-space: nowrap;
}
.ptable__table th:first-child,
.ptable__table td:first-child,
.ptable__table .l {
  text-align: left;
}
.ptable__row {
  cursor: pointer;
  animation: ptable-fadeup 0.5s both;
  animation-delay: calc(var(--i) * 70ms);
}
.ptable__row:hover td {
  background: var(--rl-raised);
}
.ptable__row.is-on td {
  background: var(--ps);
}
.ptable__row.is-on td:first-child {
  box-shadow: inset 3px 0 0 var(--pc);
}
.ptable__row.is-dim {
  opacity: 0.5;
}
.ptable__sum td {
  border-bottom: 0;
  color: var(--rl-muted);
  font-size: 12px;
}
.ptable__who {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--pi);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

/* the hour bars: one per hour of the billing day, every row on one scale */
.spark {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  width: 168px;
  height: 30px;
}
.spark i {
  flex: 1;
  min-height: 2px;
  border-radius: 2px 2px 1px 1px;
}
.spark__bar {
  background: var(--pc);
  transform-origin: bottom;
  animation: ptable-rise 0.6s cubic-bezier(0.2, 0.7, 0.2, 1) both;
  animation-delay: calc(var(--h) * 18ms + var(--i) * 60ms);
}
.spark__zero {
  height: 2px;
  background: color-mix(in oklab, var(--pc) 25%, var(--rl-surface));
}
.spark__future {
  height: 2px;
  background: var(--rl-line);
}
.failn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.failn i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--rl-bad);
}
.failn--none {
  color: var(--rl-faint);
}
.pools {
  display: inline-grid;
  gap: 6px;
}
.mini {
  display: inline-grid;
  gap: 3px;
  min-width: 96px;
  font-size: 12px;
  text-align: right;
}
.mini__bar {
  height: 4px;
  border-radius: 2px;
  background: var(--rl-line);
  overflow: hidden;
}
.mini__bar i {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--pc);
  transform-origin: left;
  animation: ptable-grow 0.8s 0.3s both;
}
.via {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--rl-raised);
  color: var(--rl-muted);
  font: 500 11px var(--rl-mono);
}
.via--paused {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
  font-family: var(--rl-font);
  font-weight: 600;
}

/* a container 860 px wide or less: a card per provider */
.ptable__cards {
  display: none;
  margin-top: 10px;
}
@container (max-width: 860px) {
  .ptable__wide {
    display: none;
  }
  .ptable__cards {
    display: block;
  }
}
.pcard {
  display: grid;
  gap: 8px;
  padding: 14px 16px;
  border-top: 1px solid var(--rl-line);
  cursor: pointer;
  animation: ptable-fadeup 0.5s both;
  animation-delay: calc(var(--i) * 70ms);
}
.pcard.is-on {
  background: var(--ps);
  box-shadow: inset 3px 0 0 var(--pc);
}
.pcard.is-dim {
  opacity: 0.5;
}
.pcard__top {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--pi);
  font-weight: 600;
}
.pcard__top .via {
  margin-left: auto;
}
.spark--wide {
  width: 100%;
}
.pcard__grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px 6px;
  font-size: 12px;
  color: var(--rl-muted);
}
.pcard__grid b {
  display: flex;
  color: var(--rl-fg);
  font-size: 14px;
  font-weight: 600;
}
.pcard__grid b.failn--none {
  color: var(--rl-faint);
}
.pcard__pools {
  display: grid;
  grid-column: 1 / -1;
  gap: 6px;
}
.pcard__pools .mini {
  text-align: left;
}
.pcard--sum {
  cursor: default;
  font-size: 12px;
  color: var(--rl-muted);
  animation: none;
}

@keyframes ptable-rise {
  from {
    transform: scaleY(0);
  }
}
@keyframes ptable-grow {
  from {
    transform: scaleX(0);
  }
}
@keyframes ptable-fadeup {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
}
@media (prefers-reduced-motion: reduce) {
  .ptable__row,
  .pcard,
  .spark__bar,
  .mini__bar i {
    animation: none;
  }
}
</style>
