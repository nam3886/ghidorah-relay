<script setup lang="ts">
import { computed, ref } from "vue";
import type { SettingItem } from "@/api/settings";
import type { CatalogModel } from "@/api/shell";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import IconButton from "@/components/shared/IconButton.vue";
import BottomSheet from "@/components/shared/overlays/BottomSheet.vue";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import {
  type Card,
  costLabel,
  kindsText,
  marginOf,
  otherPrices,
  pausedModels,
  type PriceInput,
  priceInputs,
  priceTable,
  tableKeys,
  tierColumns,
  tierHead,
  withModelOn,
  withPrice,
} from "./settings-cards";
import { useStacked } from "./settings-media";

// Models and prices of one provider (Settings, "Models and prices"): a Video table and an Image table, a row per model
// of the catalog with its Offered switch (`<p>.disabled_models`), its sale points per tier (`<p>.prices`) and its
// upstream cost per tier (`<p>.costs`, what one job takes from the account, in the pool's unit), and the margin (1 credit
// or 1 use counts as 1 point). Wide tables scroll sideways in the arrow scroller. A phone lists one row per model and
// edits a model's prices in a bottom sheet. Keys no model of the catalog has get rows of their own.
const props = defineProps<{
  card: Card;
  models: CatalogModel[];
  drafts: Record<string, string>;
  errors: Record<string, string>;
  busy: boolean;
  // the pool unit of each kind ("credits", "uses", "jobs"), from the provider summary
  units: Record<string, string>;
}>();
const emit = defineEmits<{ draft: [key: string, text: string]; reset: [key: string] }>();
const { isPhone } = useBreakpoint();
// SettingsTablet: one read-only table (On/Off, the sale points, the credits joined); a tap on a model's name opens the
// same sheet the phone edits in. The board draws no way to edit there: the name as a button is the least that adds one.
const stacked = useStacked();
const readOnly = computed(() => stacked.value && !isPhone.value);

const own = computed(() => props.card.own);
const prices = computed(() =>
  own.value.prices ? priceTable(props.drafts[own.value.prices.key], own.value.prices) : {},
);
// every option the provider declares gets an input, a blank one for an option its table does not hold yet
const priceKeys = computed(() => tableKeys(own.value.prices, prices.value));
const costs = computed(() => (own.value.costs ? priceTable(props.drafts[own.value.costs.key], own.value.costs) : {}));
const costKeys = computed(() => tableKeys(own.value.costs, costs.value));
const paused = computed(
  () =>
    new Set(
      pausedModels(own.value.disabled_models && props.drafts[own.value.disabled_models.key], own.value.disabled_models),
    ),
);

const costHead = (kind: string): string => {
  const unit = props.units[kind];
  if (!unit) return "Upstream cost";
  return kind === "image" && unit === "uses" ? "Image uses" : `Upstream ${unit}`;
};
const unitWord = (kind: string): string => props.units[kind] ?? "units";
// "4 uses", "1 use", "— uses": a count with its word, singular for one
const counted = (text: string | undefined, word: string): string =>
  `${text || "—"} ${text === "1" ? word.replace(/s$/, "") : word}`;

interface Row {
  m: CatalogModel;
  on: boolean;
  sale: Array<PriceInput | undefined>;
  cost: Array<PriceInput | undefined>;
  margin: { text: string; tone: string };
}
const tables = computed(() => {
  const kinds: Array<[string, string, string, CatalogModel[]]> = [
    ["video", "Video", "Per job, by length", props.models.filter((m) => m.kind === "video")],
    ["image", "Image", "Per image", props.models.filter((m) => m.kind !== "video")],
  ];
  return kinds
    .filter(([, , , ms]) => ms.length)
    .map(([kind, title, note, ms]) => {
      const cols = tierColumns(ms, [
        [prices.value, priceKeys.value],
        [costs.value, costKeys.value],
      ]);
      const rows: Row[] = ms.map((m) => {
        const sale = priceInputs(m, prices.value, priceKeys.value);
        const cost = priceInputs(m, costs.value, costKeys.value);
        const at = (list: PriceInput[]) => cols.map((c) => list.find((p) => p.label === c));
        const s = at(sale);
        const c = at(cost);
        const pairs = s.map((x, i): [string | undefined, string | undefined] => [x?.text, c[i]?.text]);
        return { m, on: !paused.value.has(m.id), sale: s, cost: c, margin: marginOf(pairs) };
      });
      return { kind, title, note, cols, rows, single: cols.length === 1 };
    });
});
// the keys no model of the catalog has, a row each with its sale and cost inputs
const others = computed(() => {
  const sale = otherPrices(props.models, prices.value, priceKeys.value);
  const cost = otherPrices(props.models, costs.value, costKeys.value);
  const keys = [...new Set([...sale, ...cost].map((p) => p.key))];
  return keys.map((key) => ({ key, sale: sale.find((p) => p.key === key), cost: cost.find((p) => p.key === key) }));
});

// a price or cost input whose text differs from the stored table
const storedText = (item: SettingItem | undefined, key: string): string => {
  const v = (item?.value as Record<string, unknown> | undefined)?.[key];
  return v === undefined || v === null ? "" : String(v);
};
const changedPrice = (p: PriceInput) => p.text !== storedText(own.value.prices, p.key);
const changedCost = (p: PriceInput) => p.text !== storedText(own.value.costs, p.key);

function setModel(id: string, on: boolean): void {
  const item = own.value.disabled_models;
  if (!item) return;
  emit(
    "draft",
    item.key,
    withModelOn(
      props.drafts[item.key],
      item,
      props.models.map((m) => m.id),
      id,
      on,
    ),
  );
}
function setPrice(key: string, text: string): void {
  const item = own.value.prices;
  if (item) emit("draft", item.key, withPrice(props.drafts[item.key], item, key, text));
}
function setCost(key: string, text: string): void {
  const item = own.value.costs;
  if (item) emit("draft", item.key, withPrice(props.drafts[item.key], item, key, text));
}
const valueOf = (e: Event): string => (e.target as HTMLInputElement).value;

// the settings drawn here: their refusals and their Reset
const modelItems = computed(() =>
  [own.value.disabled_models, own.value.prices, own.value.costs].filter((i): i is SettingItem => !!i),
);
const footItems = computed(() => modelItems.value.filter((i) => i.source === "db" || props.errors[i.key]));

// the phone's sheet: the model whose prices it edits
const editingAt = ref<{ kind: string; id: string } | null>(null);
const sheetOpen = ref(false);
// read again from the tables, so the sheet shows each draft as it is typed
const editing = computed(() => {
  const at = editingAt.value;
  const table = at && tables.value.find((t) => t.kind === at.kind);
  return table && at ? { table, id: at.id } : null;
});
const sheetRow = computed(() => editing.value?.table.rows.find((r) => r.m.id === editing.value?.id) ?? null);
function edit(table: (typeof tables.value)[number], id: string): void {
  editingAt.value = { kind: table.kind, id };
  sheetOpen.value = true;
}
const join = (list: Array<PriceInput | undefined>): string => list.map((p) => p?.text || "—").join(" · ");

// the tablet's one table: the video tiers as its columns; an image model shows its one price in the first and its uses
const flat = computed(() => {
  const lead = tables.value.find((t) => t.kind === "video") ?? tables.value[0];
  const cols = lead?.cols ?? [];
  const unit = lead ? unitWord(lead.kind) : "credits";
  const rows = tables.value.flatMap((t) =>
    t.rows.map((r) => ({
      t,
      r,
      sale: t === lead ? r.sale : cols.map((_c, i) => (i === 0 ? r.sale[0] : undefined)),
      credits: t === lead ? join(r.cost) : counted(r.cost[0]?.text, unitWord(t.kind)),
    })),
  );
  return { cols, rows, head: unit.charAt(0).toUpperCase() + unit.slice(1) };
});
function phoneSub(t: (typeof tables.value)[number], r: Row): string {
  if (t.single) {
    const per = t.kind === "video" ? "job" : "image";
    return `${counted(r.sale[0]?.text, "points")} per ${per} · ${counted(r.cost[0]?.text, unitWord(t.kind))}`;
  }
  // the model's own tiers only: one it has no price or cost for is left out, not drawn as a dash
  const own = t.cols.map((_c, i) => i).filter((i) => r.sale[i] || r.cost[i]);
  const heads = own.map((i) => tierHead(t.cols[i]!, "job").replace(/ s$/, ""));
  const pick = (list: Array<PriceInput | undefined>) => join(own.map((i) => list[i]));
  return `Points ${pick(r.sale)} for ${heads.join("/")} s · ${unitWord(t.kind)} ${pick(r.cost)}`;
}
</script>

<template>
  <div class="mp" :data-testid="`model-prices-${card.group}`">
    <ArrowScroller v-if="readOnly && flat.rows.length" :label="`${card.group} prices`" table>
      <table class="mp__table mp__table--ro">
        <thead>
          <tr>
            <th class="first">Model</th>
            <th class="center">Offered</th>
            <th v-for="c in flat.cols" :key="c" class="tier num">{{ tierHead(c, "job") }}</th>
            <th class="last right">{{ flat.head }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="x in flat.rows" :key="x.r.m.id" :class="{ 'is-off': !x.r.on }" :data-model="x.r.m.id">
            <td class="first">
              <button
                type="button"
                class="mp__open mono mp__name"
                :aria-label="`Edit ${x.r.m.id} prices`"
                :disabled="busy"
                @click="edit(x.t, x.r.m.id)"
              >
                {{ x.r.m.id }}
              </button>
              <div class="mono mp__kinds">{{ kindsText(x.r.m) }}</div>
            </td>
            <td class="center">
              <span class="pill" :class="x.r.on ? 'pill--ok' : 'pill--neutral'">{{ x.r.on ? "On" : "Off" }}</span>
            </td>
            <td v-for="(s, i) in x.sale" :key="i" class="tier num" :class="{ 'is-changed-ro': s && changedPrice(s) }">
              {{ s?.text || "—" }}
            </td>
            <td class="last right num">{{ x.credits }}</td>
          </tr>
        </tbody>
      </table>
    </ArrowScroller>
    <template v-for="(t, n) in readOnly ? [] : tables" :key="t.kind">
      <div class="mp__head" :class="{ 'is-ruled': n > 0 }">
        <h3>{{ t.title }}</h3>
        <span class="help">{{ t.note }}</span>
      </div>

      <ul v-if="isPhone" class="mp__rows" :aria-label="`${card.group} ${t.kind} models`">
        <li v-for="r in t.rows" :key="r.m.id" class="mp__item" :class="{ 'is-off': !r.on }" :data-model="r.m.id">
          <span class="mp__line">
            <span class="pill" :class="r.on ? 'pill--ok' : 'pill--neutral'">{{ r.on ? "On" : "Off" }}</span>
            <span class="mp__right num" :class="r.margin.tone ? `tone--${r.margin.tone}` : ''">{{
              r.margin.text
            }}</span>
          </span>
          <span class="mono mp__name">{{ r.m.id }}</span>
          <span class="help num">{{ phoneSub(t, r) }}</span>
          <span class="mp__acts">
            <IconButton
              v-if="r.on"
              icon="edit"
              :label="`Edit ${r.m.id} prices`"
              :disabled="busy"
              @click="edit(t, r.m.id)"
            />
            <button
              v-else-if="own.disabled_models"
              type="button"
              class="btn"
              :disabled="busy"
              :aria-label="`Offer ${r.m.id}`"
              @click="setModel(r.m.id, true)"
            >
              Offer
            </button>
          </span>
        </li>
      </ul>

      <ArrowScroller v-else :label="`${t.title} prices`" table>
        <table class="mp__table" :style="{ minWidth: `${Math.max(820, 360 + t.cols.length * 2 * 56)}px` }">
          <thead>
            <tr>
              <th :rowspan="t.single ? 1 : 2" class="first bottom">Model</th>
              <th :rowspan="t.single ? 1 : 2" class="bottom">Offered</th>
              <template v-if="t.single">
                <th>Sale points</th>
                <th>{{ costHead(t.kind) }}</th>
              </template>
              <template v-else>
                <th :colspan="t.cols.length" class="group">Sale points</th>
                <th :colspan="t.cols.length" class="group">{{ costHead(t.kind) }}</th>
              </template>
              <th :rowspan="t.single ? 1 : 2" class="last bottom right">Margin</th>
            </tr>
            <tr v-if="!t.single">
              <th v-for="c in t.cols" :key="`s${c}`" class="tier num">{{ tierHead(c, "job") }}</th>
              <th v-for="c in t.cols" :key="`c${c}`" class="tier num">{{ tierHead(c, "job") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in t.rows" :key="r.m.id" :class="{ 'is-off': !r.on }" :data-model="r.m.id">
              <td class="first">
                <div class="mono mp__name">{{ r.m.id }}</div>
                <div class="help">{{ kindsText(r.m) }}</div>
              </td>
              <td class="offer">
                <ToggleSwitch
                  v-if="own.disabled_models"
                  size="md"
                  :model-value="r.on"
                  :label="`Offer ${r.m.id}`"
                  :disabled="busy"
                  @change="(on: boolean) => setModel(r.m.id, on)"
                />
              </td>
              <td v-for="(p, i) in r.sale" :key="`s${i}`" class="cell" :class="{ 'cell--left': t.single }">
                <input
                  v-if="p"
                  class="num-in price num"
                  :class="{ 'is-changed': changedPrice(p) }"
                  :value="p.text"
                  inputmode="numeric"
                  :aria-label="`${r.m.id} ${p.label} price`"
                  :disabled="busy"
                  autocomplete="off"
                  @input="setPrice(p.key, valueOf($event))"
                />
                <span v-else class="num-in is-none" aria-hidden="true">—</span>
              </td>
              <td v-for="(c, i) in r.cost" :key="`c${i}`" class="cell" :class="{ 'cell--left': t.single }">
                <input
                  v-if="c"
                  class="num-in cost num"
                  :class="{ 'is-changed': changedCost(c) }"
                  :value="c.text"
                  inputmode="numeric"
                  :aria-label="`${r.m.id} ${costLabel(c)}`"
                  :disabled="busy"
                  autocomplete="off"
                  @input="setCost(c.key, valueOf($event))"
                />
                <span v-else class="num-in is-none" aria-hidden="true">—</span>
              </td>
              <td class="last right">
                <span class="pill num" :class="r.margin.tone ? `pill--${r.margin.tone}` : 'pill--none'">{{
                  r.margin.text
                }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </ArrowScroller>
    </template>

    <template v-if="others.length">
      <div class="mp__head is-ruled">
        <h3>Other keys</h3>
        <span class="help">Keys no model of the catalog has</span>
      </div>
      <div class="mp__others">
        <div v-for="o in others" :key="o.key" class="mp__other" data-model="">
          <span class="mono mp__key">{{ o.key }}</span>
          <label v-if="o.sale" class="mp__pair"
            ><span class="help">Sale points</span
            ><input
              class="num-in price num"
              :class="{ 'is-changed': changedPrice(o.sale) }"
              :value="o.sale.text"
              inputmode="numeric"
              :aria-label="`${o.key} price`"
              :disabled="busy"
              autocomplete="off"
              @input="setPrice(o.key, valueOf($event))"
          /></label>
          <label v-if="o.cost" class="mp__pair"
            ><span class="help">Upstream cost</span
            ><input
              class="num-in cost num"
              :class="{ 'is-changed': changedCost(o.cost) }"
              :value="o.cost.text"
              inputmode="numeric"
              :aria-label="`${o.key} cost`"
              :disabled="busy"
              autocomplete="off"
              @input="setCost(o.key, valueOf($event))"
          /></label>
        </div>
      </div>
    </template>

    <div v-if="footItems.length" class="mp__foot">
      <div v-for="item in footItems" :key="item.key" class="mp__key-line">
        <span class="mono mp__key">{{ item.key }}</span>
        <IconButton
          v-if="item.source === 'db'"
          icon="reset"
          :label="`Reset ${item.key}`"
          :disabled="busy"
          @click="emit('reset', item.key)"
        />
        <span v-if="errors[item.key]" class="error" role="alert">{{ errors[item.key] }}</span>
      </div>
    </div>

    <BottomSheet
      v-model="sheetOpen"
      :title="sheetRow?.m.id ?? ''"
      :sub="sheetRow ? kindsText(sheetRow.m) : ''"
      testid="model-prices-sheet"
    >
      <template v-if="sheetRow && editing">
        <div v-if="own.disabled_models" class="sheet__switch">
          <span class="sheet__label">Offered</span>
          <ToggleSwitch
            size="md"
            :model-value="sheetRow.on"
            :label="`Offer ${sheetRow.m.id}`"
            :disabled="busy"
            @change="(on: boolean) => setModel(sheetRow!.m.id, on)"
          />
        </div>
        <div class="sheet__grid">
          <span class="help">{{ editing.table.single ? "" : "Tier" }}</span>
          <span class="help">Sale points</span>
          <span class="help">{{ costHead(editing.table.kind) }}</span>
          <template v-for="(c, i) in editing.table.cols" :key="c">
            <span class="sheet__tier num">{{ editing.table.single ? editing.table.note : tierHead(c, "job") }}</span>
            <input
              v-if="sheetRow.sale[i]"
              class="num-in price num"
              :class="{ 'is-changed': changedPrice(sheetRow.sale[i]!) }"
              :value="sheetRow.sale[i]!.text"
              inputmode="numeric"
              :aria-label="`${sheetRow.m.id} ${sheetRow.sale[i]!.label} price`"
              :disabled="busy"
              autocomplete="off"
              @input="setPrice(sheetRow.sale[i]!.key, valueOf($event))"
            />
            <span v-else class="num-in is-none" aria-hidden="true">—</span>
            <input
              v-if="sheetRow.cost[i]"
              class="num-in cost num"
              :class="{ 'is-changed': changedCost(sheetRow.cost[i]!) }"
              :value="sheetRow.cost[i]!.text"
              inputmode="numeric"
              :aria-label="`${sheetRow.m.id} ${costLabel(sheetRow.cost[i]!)}`"
              :disabled="busy"
              autocomplete="off"
              @input="setCost(sheetRow.cost[i]!.key, valueOf($event))"
            />
            <span v-else class="num-in is-none" aria-hidden="true">—</span>
          </template>
        </div>
        <p class="help">Margin {{ sheetRow.margin.text }}. 1 credit or 1 image use counts as 1 point.</p>
      </template>
      <template #footer>
        <button type="button" class="sheet__done" @click="sheetOpen = false">Done</button>
      </template>
    </BottomSheet>
  </div>
</template>

<style scoped>
.mp__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  padding: 14px 20px;
}
.mp__head.is-ruled {
  border-top: 1px solid var(--rl-line);
}
.mp__head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
}
.help {
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--rl-faint);
}
.mono {
  font-family: var(--rl-mono);
}
.mp__name {
  font-size: 13px;
  font-weight: 500;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
.is-off .mp__name {
  color: var(--rl-muted);
}
.mp__table {
  width: 100%;
  border-collapse: collapse;
}
.mp__table thead tr {
  text-align: left;
  font-size: 12px;
  color: var(--rl-faint);
  background: var(--rl-head);
}
.mp__table th {
  padding: 10px 8px;
  font-weight: 600;
  white-space: nowrap;
}
.mp__table th.bottom {
  vertical-align: bottom;
}
.mp__table th.group {
  padding: 10px 8px 2px;
  font-weight: 700;
  text-align: center;
  color: var(--rl-muted);
}
.mp__table th.tier {
  padding: 2px 4px 10px;
  text-align: center;
}
.mp__table .first {
  padding-left: 20px;
}
.mp__table .last {
  padding-right: 20px;
}
.mp__table .right {
  text-align: right;
}
.mp__table tbody tr {
  border-top: 1px solid var(--rl-rule);
}
.mp__table td {
  padding: 12px 4px;
  vertical-align: middle;
}
.mp__table td.first {
  padding: 12px 8px 12px 20px;
  min-width: 180px;
}
/* SettingsTablet: the read-only table, its figures plain, an edited one bold */
.mp__table--ro {
  min-width: 640px;
}
.mp__table--ro th.tier {
  padding: 10px 6px;
}
.mp__table--ro th.center,
.mp__table--ro td.center {
  text-align: center;
}
.mp__table--ro td.tier {
  padding: 12px 6px;
  text-align: center;
  color: var(--rl-body);
}
.mp__table--ro td.last {
  color: var(--rl-body);
}
.mp__table--ro .is-changed-ro {
  font-weight: 800;
  color: var(--rl-fg);
}
.mp__open {
  display: block;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  font-family: var(--rl-mono);
  text-align: left;
  cursor: pointer;
}
.mp__open:hover {
  color: var(--rl-brand-ink);
  text-decoration: underline;
}
.mp__open:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.mp__kinds {
  font-size: 11px;
  line-height: 16px;
  color: var(--rl-faint);
}
.mp__table td.offer {
  padding: 12px 8px;
}
.mp__table td.last {
  padding: 12px 20px 12px 8px;
  white-space: nowrap;
}
.cell {
  text-align: center;
}
.cell--left {
  padding: 12px 8px;
  text-align: left;
}
.num {
  font-variant-numeric: tabular-nums;
}
.num-in {
  box-sizing: border-box;
  display: inline-grid;
  place-items: center;
  width: 48px;
  height: 34px;
  padding: 0 4px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
  text-align: center;
}
.cell--left .num-in {
  width: 56px;
}
.num-in:focus-visible {
  outline: none;
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.num-in.is-changed {
  border-color: var(--rl-brand);
  background: var(--rl-primary-soft);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.num-in.is-none {
  color: var(--rl-faint);
  background: var(--rl-head);
}
.num-in:disabled {
  opacity: 0.6;
  cursor: wait;
}
.pill {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.pill--ok {
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.pill--warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.pill--bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.pill--neutral {
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.pill--none {
  color: var(--rl-faint);
}
.mp__item .pill {
  height: 22px;
  padding: 0 9px;
}
.mp__right.tone--ok {
  color: var(--rl-ok);
}
.mp__right.tone--warn {
  color: var(--rl-warn);
}
.mp__right.tone--bad {
  color: var(--rl-bad);
}

/* the phone list: a row per model */
.mp__rows {
  margin: 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--rl-rule);
}
.mp__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 16px;
  border-top: 1px solid var(--rl-rule);
}
.mp__item:first-child {
  border-top: 0;
}
.mp__line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.mp__right {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-muted);
}
.mp__acts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.btn {
  box-sizing: border-box;
  height: 44px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

/* keys no model has */
.mp__others {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--rl-rule);
}
.mp__other {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  padding: 12px 20px;
  border-top: 1px solid var(--rl-rule);
}
.mp__other:first-child {
  border-top: 0;
}
.mp__key {
  flex: 1 1 200px;
  min-width: 0;
  font-size: 12px;
  color: var(--rl-body);
  overflow-wrap: anywhere;
}
.mp__pair {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

/* the keys of the three settings, with Reset, and their refusals */
.mp__foot {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 20px;
  border-top: 1px solid var(--rl-line);
  background: var(--rl-head);
}
.mp__key-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 10px;
}
.mp__key-line .mp__key {
  flex: 0 1 auto;
}
.error {
  flex-basis: 100%;
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--rl-bad);
  overflow-wrap: anywhere;
}

/* the phone sheet */
.sheet__switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
}
.sheet__label {
  font-weight: 700;
}
.sheet__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px 72px;
  align-items: center;
  gap: 8px 12px;
}
.sheet__grid .num-in {
  width: 72px;
  height: 44px;
  border-radius: 10px;
}
.sheet__tier {
  font-weight: 600;
}
.sheet__done {
  box-sizing: border-box;
  width: 100%;
  height: 44px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
</style>
