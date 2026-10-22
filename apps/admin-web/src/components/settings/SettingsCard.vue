<script setup lang="ts">
// One card of the Settings page: a heading with one muted sentence, then its content. `id` is the in-page anchor the
// sub-navigation points at.
defineProps<{ id: string; title: string; note?: string }>();
</script>

<template>
  <section :id="id" class="card" :aria-labelledby="`${id}-title`">
    <div class="card__head">
      <h2 :id="`${id}-title`">{{ title }}</h2>
      <p v-if="note">{{ note }}</p>
    </div>
    <slot />
  </section>
</template>

<style scoped>
.card {
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
  overflow: hidden;
  scroll-margin-top: var(--settings-anchor, 16px);
}
.card__head {
  padding: 16px 16px 14px;
}
.card h2 {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
}
.card__head p {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--rl-muted);
}
</style>
