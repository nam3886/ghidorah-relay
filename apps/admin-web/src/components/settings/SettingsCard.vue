<script setup lang="ts">
import { useStacked } from "./settings-media";

// One section of the Settings page (B Ledger): over a rule, its heading and one muted sentence in a label column at the
// left, its card at the right. Below 968 px (SettingsTablet, SettingsPhone) the label column goes: the heading sits
// inside the card at its top, with `short` beside it when given (the boards' few words: "poll, back-off, timeouts");
// `headless` keeps that heading for screen readers only (SettingsPhone's lone "Show 9 advanced settings").
// `pad`: the card lays its content out itself with 20 x 24 px of padding; `bare`: no card, the content is cards of its
// own. `id` is the in-page anchor the General links point at.
withDefaults(
  defineProps<{
    id: string;
    title: string;
    note?: string;
    short?: string;
    pad?: boolean;
    bare?: boolean;
    headless?: boolean;
  }>(),
  { note: "", short: "", pad: false, bare: false, headless: false },
);
const stacked = useStacked();
</script>

<template>
  <section :id="id" class="ssec" :class="{ 'is-stacked': stacked }" :aria-labelledby="`${id}-title`">
    <div v-if="!stacked" class="ssec__head">
      <h2 :id="`${id}-title`">{{ title }}</h2>
      <p v-if="note">{{ note }}</p>
      <slot name="head" />
    </div>
    <div class="ssec__card" :class="{ 'ssec__card--pad': pad, 'ssec__card--bare': bare }">
      <div v-if="stacked" class="ssec__in" :class="{ 'is-flush': !pad && !bare, 'sr-only': headless }">
        <span class="ssec__in-line">
          <h2 :id="`${id}-title`">{{ title }}</h2>
          <span v-if="short" class="ssec__short">{{ short }}</span>
        </span>
        <slot name="head" />
      </div>
      <slot />
    </div>
  </section>
</template>

<style scoped>
.ssec {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 40px;
  padding-top: 28px;
  border-top: 1px solid var(--rl-line);
  scroll-margin-top: var(--settings-anchor, 16px);
}
.ssec__head {
  flex: 1 1 240px;
  max-width: 300px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.ssec__head h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 700;
}
.ssec__head p {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-muted);
}
.ssec__card {
  flex: 999 1 640px;
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.ssec__card--pad {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 20px 24px;
}
.ssec__card--bare {
  overflow: visible;
  border-radius: 0;
  background: none;
  box-shadow: none;
}
/* stacked (SettingsTablet, SettingsPhone): no rule between the sections, the title inside the card */
.ssec.is-stacked {
  padding-top: 0;
  border-top: 0;
}
.ssec__in {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ssec__in.is-flush {
  padding: 18px 20px 4px;
}
.ssec__in-line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 10px;
}
.ssec__in h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 700;
}
.ssec__short {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
@media (max-width: 767px) {
  .ssec {
    gap: 12px;
  }
  .ssec__card--pad {
    padding: 16px;
    gap: 16px;
  }
  .ssec__in.is-flush {
    padding: 16px 16px 4px;
  }
}
</style>
