<script setup lang="ts">
import { computed } from "vue";
import type { SettingItem } from "@/api/settings";
import type { CatalogModel, ProviderSummary } from "@/api/shell";
import ProviderTile from "@/components/shared/ProviderTile.vue";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import { providerClass } from "@/lib/providers";
import SettingField from "./SettingField.vue";
import {
  type Card,
  otherPrices,
  pausedModels,
  priceInputs,
  priceTable,
  withModelOn,
  withPrice,
} from "./settings-cards";
import { valueText } from "./settings-value";

// One card of the Settings page (final.html "settings cards"): a provider's, or General ("core", the relay-wide
// settings). A provider's header carries its tile, how it sends and how many models it offers, and the "Taking jobs"
// switch (`<p>.claims_enabled`); "Models & prices" has a row per model of the catalog with its switch
// (`<p>.disabled_models`) and its price inputs (`<p>.prices`); then the basic settings, the rest folded under
// Advanced. Every control edits a draft: the card says how many settings changed, Discard puts the stored values back
// and Save sends each changed one.
const props = defineProps<{
  card: Card;
  drafts: Record<string, string>;
  errors: Record<string, string>;
  models: CatalogModel[];
  summary: ProviderSummary | null;
  busy: boolean;
  // General: the page's other sections, reached from its footer
  links?: ReadonlyArray<{ id: string; label: string }>;
  // the section last reached from a link
  current?: string;
}>();
const emit = defineEmits<{
  draft: [key: string, text: string];
  reset: [key: string];
  save: [keys: string[]];
  discard: [keys: string[]];
  go: [section: string];
  frames: [];
}>();

const general = computed(() => props.card.group === "core");
const own = computed(() => props.card.own);
const all = computed<SettingItem[]>(() => [
  ...Object.values(own.value).filter((i): i is SettingItem => !!i),
  ...props.card.basic,
  ...props.card.advanced,
]);
const isEdited = (item: SettingItem | undefined): boolean =>
  !!item && props.drafts[item.key] !== undefined && props.drafts[item.key] !== valueText(item);
const changed = computed(() => all.value.filter(isEdited).map((i) => i.key));

const claims = computed(() => own.value.claims_enabled);
const taking = computed(() => (claims.value ? props.drafts[claims.value.key] === "true" : true));
const paused = computed(
  () =>
    new Set(
      pausedModels(own.value.disabled_models && props.drafts[own.value.disabled_models.key], own.value.disabled_models),
    ),
);
const prices = computed(() =>
  own.value.prices ? priceTable(props.drafts[own.value.prices.key], own.value.prices) : {},
);
const others = computed(() => otherPrices(props.models, prices.value));
const onCount = computed(() => props.models.filter((m) => !paused.value.has(m.id)).length);
const kindOf = (m: CatalogModel) => (typeof m.kind === "string" ? m.kind : "");

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
// the settings drawn in the Models block: their edited marks, their errors and their Reset
const modelItems = computed(() => [own.value.disabled_models, own.value.prices].filter((i): i is SettingItem => !!i));
const changedWord = computed(() =>
  changed.value.length ? `${changed.value.length} ${changed.value.length === 1 ? "change" : "changes"} not saved` : "",
);
</script>

<template>
  <article
    class="scard"
    :class="general ? 'scard--general' : providerClass(card.group)"
    :aria-labelledby="`card-${card.group}-title`"
    :data-testid="`settings-card-${card.group}`"
  >
    <header class="scard__head">
      <span v-if="general" class="scard__mark" aria-hidden="true"
        ><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="3.2" />
          <path
            d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"
          /></svg
      ></span>
      <ProviderTile v-else :provider="card.group" class="scard__tile" />
      <div class="scard__title">
        <h2 :id="`card-${card.group}-title`">{{ general ? "General" : card.group }}</h2>
        <span class="scard__sub">
          <template v-if="general">relay-wide</template>
          <template v-else
            >{{ summary ? `${summary.execution} · ` : "" }}{{ onCount }}/{{ models.length }} models on</template
          >
        </span>
      </div>
      <div v-if="claims" class="scard__claims">
        <span class="claims__text">
          <b :class="{ 'is-paused': !taking }">{{ taking ? "Taking jobs" : "Paused" }}</b>
          <span v-if="isEdited(claims)" class="tag">edited</span>
          <span class="key">{{ claims.key }}</span>
        </span>
        <ToggleSwitch
          :model-value="taking"
          label="Taking jobs"
          :disabled="busy"
          @change="(on: boolean) => emit('draft', claims!.key, String(on))"
        />
      </div>
      <span v-if="claims && errors[claims.key]" class="error scard__err" role="alert">{{ errors[claims.key] }}</span>
    </header>

    <section v-if="!general && models.length" class="blk" :aria-label="`${card.group} models and prices`">
      <h3>Models &amp; prices</h3>
      <div v-for="m in models" :key="m.id" class="mrow" :class="{ 'is-off': paused.has(m.id) }" :data-model="m.id">
        <span class="mrow__name"
          >{{ m.name || m.id
          }}<small>{{ [kindOf(m), m.id, paused.has(m.id) ? "Paused" : ""].filter(Boolean).join(" · ") }}</small></span
        >
        <ToggleSwitch
          v-if="own.disabled_models"
          :model-value="!paused.has(m.id)"
          :label="`Offer ${m.id}`"
          :disabled="busy"
          @change="(on: boolean) => setModel(m.id, on)"
        />
        <span v-if="own.prices && priceInputs(m, prices).length" class="mrow__prices">
          <label v-for="p in priceInputs(m, prices)" :key="p.key"
            >{{ p.label
            }}<input
              class="price"
              :value="p.text"
              inputmode="numeric"
              :aria-label="`${m.id} ${p.label} price`"
              :disabled="busy"
              autocomplete="off"
              @input="setPrice(p.key, ($event.target as HTMLInputElement).value)"
          /></label>
        </span>
      </div>
      <div v-if="own.prices && others.length" class="mrow" data-model="">
        <span class="mrow__name">Other prices<small>keys no model of the catalog has</small></span>
        <span class="mrow__prices">
          <label v-for="p in others" :key="p.key"
            ><span class="mono">{{ p.label }}</span
            ><input
              class="price"
              :value="p.text"
              inputmode="numeric"
              :aria-label="`${p.key} price`"
              :disabled="busy"
              autocomplete="off"
              @input="setPrice(p.key, ($event.target as HTMLInputElement).value)"
          /></label>
        </span>
      </div>
      <div v-for="item in modelItems" :key="item.key" class="blk__key">
        <span class="key">{{ item.key }}</span>
        <span v-if="isEdited(item)" class="tag">edited</span>
        <button
          v-if="item.source === 'db'"
          type="button"
          class="reset"
          :disabled="busy"
          :aria-label="`Reset ${item.key}`"
          @click="emit('reset', item.key)"
        >
          Reset
        </button>
        <span v-if="errors[item.key]" class="error" role="alert">{{ errors[item.key] }}</span>
      </div>
    </section>

    <section v-if="card.basic.length" class="blk">
      <h3>{{ general ? "Settings" : "Basic" }}</h3>
      <div class="fields">
        <SettingField
          v-for="item in card.basic"
          :key="item.key"
          :item="item"
          :draft="drafts[item.key] ?? ''"
          :edited="isEdited(item)"
          :error="errors[item.key]"
          :busy="busy"
          @update:draft="(t: string) => emit('draft', item.key, t)"
          @reset="emit('reset', item.key)"
        />
      </div>
    </section>

    <details v-if="card.advanced.length" class="adv">
      <summary>
        Advanced <span class="adv__n">{{ card.advanced.length }} settings</span>
        <span v-if="card.advanced.some(isEdited)" class="tag">edited</span>
      </summary>
      <div class="fields">
        <SettingField
          v-for="item in card.advanced"
          :key="item.key"
          :item="item"
          :draft="drafts[item.key] ?? ''"
          :edited="isEdited(item)"
          :error="errors[item.key]"
          :busy="busy"
          @update:draft="(t: string) => emit('draft', item.key, t)"
          @reset="emit('reset', item.key)"
        />
      </div>
    </details>

    <footer class="scard__foot">
      <span class="scard__links">
        <template v-if="general">
          <template v-for="(l, n) in links ?? []" :key="l.id"
            ><span v-if="n" aria-hidden="true"> · </span
            ><a
              :href="`#${l.id}`"
              :aria-current="current === l.id ? 'true' : undefined"
              @click.prevent="emit('go', l.id)"
              >{{ l.label }}</a
            ></template
          >
        </template>
        <a v-else href="#/frames" @click.prevent="emit('frames')">Prompt frames →</a>
      </span>
      <span v-if="changedWord" class="scard__changed">{{ changedWord }}</span>
      <button
        type="button"
        class="btn"
        :disabled="busy || !changed.length"
        :aria-label="`Discard ${general ? 'General' : card.group} changes`"
        @click="emit('discard', changed)"
      >
        Discard
      </button>
      <button
        type="button"
        class="btn btn--primary"
        :disabled="busy || !changed.length"
        :aria-label="`Save ${general ? 'General' : card.group}`"
        @click="emit('save', changed)"
      >
        Save
      </button>
    </footer>
  </article>
</template>

<style scoped>
.scard {
  min-width: 0;
  overflow: hidden;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
}
.scard--general {
  --pc: var(--rl-primary);
  --ps: var(--rl-primary-soft);
  --pi: var(--rl-primary-hover);
}
.scard__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: var(--ps);
  box-shadow: inset 0 4px 0 var(--pc);
}
.scard__mark,
.scard__head .scard__tile {
  display: inline-grid;
  place-items: center;
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  font-size: 15px;
}
/* General is no provider: a gear on the brand colour, never a letter */
.scard__mark {
  background: var(--rl-primary);
  color: var(--rl-on-primary);
}
.scard__title {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.scard__title h2 {
  margin: 0;
  color: var(--pi);
  font-size: 16px;
  line-height: 22px;
}
.scard__sub {
  color: var(--rl-muted);
  font-size: 12px;
}
.scard__claims {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  font-size: 12px;
}
/* the state word (and its edited mark) over the key, right-aligned: as wide as the longer line */
.claims__text {
  display: grid;
  grid-template-columns: auto auto;
  justify-content: end;
  justify-items: end;
  align-items: center;
  gap: 0 6px;
  text-align: right;
}
.claims__text b {
  grid-column: 1;
}
.claims__text .key {
  grid-column: 1 / -1;
}
.is-paused {
  color: var(--rl-warn);
}
.scard__err {
  flex-basis: 100%;
}
.blk {
  padding: 12px 16px;
  border-top: 1px solid var(--rl-line);
}
.blk h3 {
  margin: 0 0 6px;
  color: var(--rl-muted);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}
.mrow {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 4px 12px;
  padding: 8px 0;
  border-top: 1px solid var(--rl-line);
}
.blk h3 + .mrow {
  border-top: 0;
}
.mrow__name {
  min-width: 0;
  color: var(--rl-fg);
  font-size: 13px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.mrow__name small {
  display: block;
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 400;
}
.mrow__prices {
  display: flex;
  flex-wrap: wrap;
  grid-column: 1 / -1;
  gap: 4px 12px;
}
.mrow__prices label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--rl-muted);
  font-size: 12px;
}
.price {
  box-sizing: border-box;
  width: 44px;
  height: 26px;
  padding: 0 4px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 6px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: 12px var(--rl-mono);
  text-align: center;
}
/* the focus ring hugs the box, clear of its label */
.price:focus-visible {
  outline-offset: 0;
}
.mrow.is-off .mrow__name,
.mrow.is-off .mrow__prices {
  opacity: 0.5;
}
.blk__key {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 8px;
  margin-top: 6px;
}
.fields {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 14px 16px;
}
.adv {
  padding: 12px 16px;
  border-top: 1px solid var(--rl-line);
  background: var(--rl-raised);
}
.adv > summary {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  list-style: none;
  cursor: pointer;
}
.adv > summary::-webkit-details-marker {
  display: none;
}
.adv > summary::before {
  content: "▸";
  color: var(--rl-muted);
  transition: transform 0.15s;
}
.adv[open] > summary::before {
  transform: rotate(90deg);
}
.adv__n {
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 400;
}
.adv .fields {
  margin-top: 12px;
}
.scard__foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--rl-line);
  font-size: 12px;
}
.scard__links {
  flex: 1 1 200px;
  min-width: 0;
  color: var(--rl-muted);
}
.scard__links a {
  color: var(--rl-primary);
  text-decoration: none;
}
.scard__changed {
  color: var(--rl-primary-hover);
  font-weight: 500;
}
.btn {
  box-sizing: border-box;
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.btn--primary {
  border-color: var(--rl-primary);
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font-weight: 600;
}
.btn:disabled {
  border-color: var(--rl-line-strong);
  background: transparent;
  color: var(--rl-faint);
  cursor: default;
}
.tag {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--rl-primary-soft);
  color: var(--rl-primary-hover);
  font-size: 11px;
  font-weight: 500;
}
.key {
  color: var(--rl-faint);
  font-family: var(--rl-mono);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.error {
  color: var(--rl-bad);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.reset {
  padding: 0;
  border: 0;
  background: none;
  color: var(--rl-primary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.reset:disabled {
  color: var(--rl-faint);
  cursor: default;
}
.mono {
  font-family: var(--rl-mono);
}
/* a phone: the footer's buttons and the price boxes are finger targets; the header is tile, name and switch on one
   row, the claims state and its key under them, left-aligned */
@media (max-width: 767px) {
  .scard__head {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    gap: 6px 12px;
  }
  .scard__claims {
    display: contents;
  }
  .scard__claims > :last-child {
    grid-row: 1;
    grid-column: 3;
  }
  .claims__text {
    grid-column: 2 / -1;
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-start;
    gap: 2px 8px;
    text-align: left;
  }
  .scard__err {
    grid-column: 1 / -1;
  }
  .btn {
    flex: 1;
    height: 44px;
    font-size: 14px;
  }
  .price {
    width: 52px;
    height: 36px;
    font-size: 14px;
  }
}
</style>
