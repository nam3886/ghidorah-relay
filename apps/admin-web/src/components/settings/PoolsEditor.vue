<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { SettingItem } from "@/api/settings";
import type { ProviderSummary } from "@/api/shell";
import IconButton from "@/components/shared/IconButton.vue";
import {
  budgetText,
  nextResetText,
  poolsOf,
  poolsTz,
  resetText,
  type Tier,
  tzForReset,
  withBudget,
  withTz,
} from "./settings-pools";

// Pools and budgets of one provider (Settings): what one account may spend per day, as the structured form of
// `<p>.pools`. A field per pool for the free tier's daily budget in the pool's unit, the daily reset on the GMT+7 clock
// (a whole hour: the zone it stands for is stored), the pro and ultra budgets in small rows, and today's spend across
// the provider's accounts from the summary. An emptied budget is no budget: the pool allows.
const props = defineProps<{
  item: SettingItem;
  draft: string | undefined;
  error?: string;
  busy: boolean;
  summary: ProviderSummary | null;
}>();
const emit = defineEmits<{ draft: [text: string]; reset: [] }>();

const pools = computed(() => poolsOf(props.draft, props.item));
const names = computed(() => Object.keys(pools.value));
const unitOf = (pool: string): string => props.summary?.pools.find((p) => p.pool === pool)?.unit ?? "units";
const title = (pool: string): string => `${pool.charAt(0).toUpperCase()}${pool.slice(1)}`;
const idOf = (pool: string, tier: string) => `pool-${props.item.group}-${pool}-${tier}`;

const OTHER_TIERS: Tier[] = ["pro", "ultra"];
const setBudget = (pool: string, tier: Tier, text: string) =>
  emit("draft", withBudget(props.draft, props.item, pool, tier, text));

// the reset input keeps what was typed until it is a whole hour
const tz = computed(() => poolsTz(pools.value));
const resetTyped = ref(resetText(tz.value));
const resetBad = ref(false);
watch(tz, (zone) => {
  if (!resetBad.value) resetTyped.value = resetText(zone);
});
function setReset(text: string): void {
  resetTyped.value = text;
  const zone = tzForReset(text);
  resetBad.value = zone === null;
  if (zone) emit("draft", withTz(props.draft, props.item, zone));
}
const zones = computed(() => [
  ...new Set(
    Object.values(pools.value)
      .map((c) => c.windows?.[0]?.tz)
      .filter(Boolean),
  ),
]);

// today across the accounts: what is left of each pool's budget
const today = computed(() =>
  (props.summary?.pools ?? [])
    .filter((p) => p.budget > 0)
    .map((p) => ({ ...p, share: Math.max(0, Math.min(100, Math.round((p.left / p.budget) * 100))) })),
);
const n = (x: number): string => x.toLocaleString("en-US");
</script>

<template>
  <div class="pools" :data-testid="`pools-${item.group}`">
    <div class="pools__grid">
      <div v-for="pool in names" :key="pool" class="pfield">
        <label :for="idOf(pool, 'free')" class="pfield__label">{{ title(pool) }} {{ unitOf(pool) }} per day</label>
        <span class="suffixed">
          <input
            :id="idOf(pool, 'free')"
            class="num"
            :value="budgetText(pools, pool, 'free')"
            inputmode="numeric"
            :aria-label="`${title(pool)} ${unitOf(pool)} per day`"
            :disabled="busy"
            autocomplete="off"
            @input="setBudget(pool, 'free', ($event.target as HTMLInputElement).value)"
          /><span class="suffix">{{ unitOf(pool) }}</span>
        </span>
        <span class="help">Per account, free tier. Empty: no limit.</span>
      </div>
      <div class="pfield">
        <label :for="`pool-${item.group}-reset`" class="pfield__label">Daily reset</label>
        <span class="suffixed" :class="{ 'is-bad': resetBad }">
          <input
            :id="`pool-${item.group}-reset`"
            class="num"
            :value="resetTyped"
            inputmode="numeric"
            aria-label="Daily reset"
            :aria-invalid="resetBad ? 'true' : undefined"
            :disabled="busy"
            autocomplete="off"
            @input="setReset(($event.target as HTMLInputElement).value)"
          /><span class="suffix">GMT+7</span>
        </span>
        <span v-if="resetBad" class="error" role="alert">A whole hour, 00:00 to 23:00.</span>
        <span v-else-if="zones.length > 1" class="help"
          >The pools reset at different times; a new time sets them all.</span
        >
        <span v-else class="help">{{ nextResetText(tz) }}</span>
      </div>
    </div>

    <div class="pools__rule"></div>
    <div class="pools__tiers">
      <span class="pfield__label">Other tiers</span>
      <div class="pools__weights">
        <template v-for="pool in names" :key="pool">
          <label v-for="tier in OTHER_TIERS" :key="tier" class="weight"
            ><span class="weight__name">{{ title(pool) }} · {{ tier }}</span
            ><span class="suffixed suffixed--sm"
              ><input
                class="num"
                :value="budgetText(pools, pool, tier)"
                inputmode="numeric"
                :aria-label="`${title(pool)} ${unitOf(pool)} per day, ${tier} tier`"
                :disabled="busy"
                autocomplete="off"
                @input="setBudget(pool, tier, ($event.target as HTMLInputElement).value)"
              /><span class="suffix">{{ unitOf(pool) }}</span></span
            ></label
          >
        </template>
      </div>
    </div>

    <div v-if="today.length" class="pools__today">
      <div class="today__head">
        <span class="today__title">Today across {{ n(summary?.accounts.total ?? 0) }} accounts</span>
        <span class="help num"
          ><template v-for="(p, i) in today" :key="p.pool"
            >{{ i ? " · " : "" }}{{ p.pool }} <b>{{ n(p.left) }}</b> of {{ n(p.budget) }} {{ p.unit }} left</template
          ></span
        >
      </div>
      <div class="today__bars">
        <template v-for="p in today" :key="p.pool">
          <span class="help">{{ p.pool }}</span>
          <span class="track"
            ><span class="fill" :class="{ 'is-low': p.share < 50 }" :style="{ width: `${p.share}%` }"
          /></span>
          <span class="num today__pct" :class="{ 'is-low': p.share < 50 }">{{ p.share }}%</span>
        </template>
      </div>
    </div>

    <div v-if="item.source === 'db' || error" class="pools__key">
      <span class="mono">{{ item.key }}</span>
      <IconButton
        v-if="item.source === 'db'"
        icon="reset"
        :label="`Reset ${item.key}`"
        :disabled="busy"
        @click="emit('reset')"
      />
      <span v-if="error" class="error" role="alert">{{ error }}</span>
    </div>
  </div>
</template>

<style scoped>
.pools {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.pools__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr));
  gap: 20px 24px;
}
.pfield {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.pfield__label {
  font-weight: 700;
}
.suffixed {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  height: 40px;
  overflow: hidden;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
}
.suffixed:focus-within {
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.suffixed.is-bad {
  border-color: var(--rl-bad);
}
.suffixed input {
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
.suffix {
  display: flex;
  align-items: center;
  height: 100%;
  padding: 0 10px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  color: var(--rl-muted);
  background: var(--rl-head);
  border-left: 1px solid var(--rl-line-strong);
}
.suffixed--sm {
  flex: none;
  display: inline-flex;
  height: 34px;
  border-radius: 8px;
}
.suffixed--sm input {
  width: 52px;
  flex: none;
  padding: 0;
  text-align: center;
}
.help {
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--rl-faint);
}
.help b {
  color: var(--rl-fg);
  font-weight: 700;
}
.error {
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--rl-bad);
  overflow-wrap: anywhere;
}
.num {
  font-variant-numeric: tabular-nums;
}
.mono {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-body);
  overflow-wrap: anywhere;
}
.pools__rule {
  height: 1px;
  background: var(--rl-rule);
}
.pools__tiers {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pools__weights {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr));
  gap: 12px 24px;
}
.weight {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 8px 8px 14px;
  border: 1px solid var(--rl-rule);
  border-radius: 10px;
}
.weight__name {
  font-family: var(--rl-mono);
  font-size: 13px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.pools__today {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
  background: var(--rl-head);
}
.today__head {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 4px 16px;
}
.today__title {
  font-weight: 700;
}
.today__bars {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) 44px;
  align-items: center;
  gap: 8px 10px;
  font-size: 12px;
}
.track {
  display: block;
  height: 8px;
  overflow: hidden;
  border-radius: 4px;
  background: var(--rl-track);
}
.fill {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--pc, var(--rl-brand));
}
.fill.is-low {
  background: var(--rl-amber);
}
.today__pct {
  text-align: right;
  font-weight: 700;
}
.today__pct.is-low {
  color: var(--rl-warn);
}
.pools__key {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 10px;
}
.pools__key .error {
  flex-basis: 100%;
}
@media (max-width: 1279px) {
  .suffixed {
    height: 44px;
  }
}
</style>
