<script setup lang="ts">
// One section of the Settings page (B Ledger): over a rule, its heading and one muted sentence in a label column at the
// left, its card at the right; where the two do not fit side by side the label stacks above the card (SettingsTablet).
// `pad`: the card lays its content out itself with 20 x 24 px of padding; `bare`: no card, the content is cards of its
// own. `id` is the in-page anchor the General links point at.
withDefaults(defineProps<{ id: string; title: string; note?: string; pad?: boolean; bare?: boolean }>(), {
  note: "",
  pad: false,
  bare: false,
});
</script>

<template>
  <section :id="id" class="ssec" :aria-labelledby="`${id}-title`">
    <div class="ssec__head">
      <h2 :id="`${id}-title`">{{ title }}</h2>
      <p v-if="note">{{ note }}</p>
      <slot name="head" />
    </div>
    <div class="ssec__card" :class="{ 'ssec__card--pad': pad, 'ssec__card--bare': bare }"><slot /></div>
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
/* stacked: the label runs the full width above the card */
@media (max-width: 967px) {
  .ssec__head {
    max-width: none;
  }
}
@media (max-width: 767px) {
  .ssec {
    gap: 12px;
    padding-top: 20px;
  }
  .ssec__card--pad {
    padding: 16px;
    gap: 16px;
  }
}
</style>
