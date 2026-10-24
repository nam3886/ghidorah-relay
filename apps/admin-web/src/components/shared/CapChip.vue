<script setup lang="ts">
import { computed } from "vue";
import { providerClass } from "@/lib/providers";

// A provider chip with an optional daily cap (JobsTablet `capChip`, ScrollerSpec notes): one pill in the provider's
// fill and ink, no ring. A capped chip shows the figure after the name and a 2 px bar along its bottom edge (its track
// the same tone at 22 %). At 80 % or more a static amber dot comes before the figure and figure and bar turn amber; at
// 100 % they turn red, the figure reads "· full" and the dot pulses (ring 1 to 2.2, opacity 0.6 to 0, 1.8 s ease-out),
// static under reduced motion, so a long list never twinkles. The tooltip says the numbers in words.
const props = withDefaults(defineProps<{ provider: string; used?: number; cap?: number | null; resets?: string }>(), {
  used: 0,
  cap: null,
  resets: "",
});

const n = (x: number): string => x.toLocaleString("en-US");
const capped = computed(() => !!props.cap && props.cap > 0);
const share = computed(() => (capped.value ? props.used / (props.cap as number) : 0));
const level = computed<"ok" | "near" | "full">(() =>
  !capped.value ? "ok" : share.value >= 1 ? "full" : share.value >= 0.8 ? "near" : "ok",
);
const figure = computed(() =>
  capped.value ? `${n(props.used)} / ${n(props.cap as number)}${level.value === "full" ? " · full" : ""}` : "",
);
const tip = computed(() =>
  capped.value
    ? `${props.provider}: ${n(props.used)} of ${n(props.cap as number)} points used today${props.resets ? ` · resets ${props.resets}` : ""}`
    : `${props.provider}: no daily cap`,
);
const barWidth = computed(() => ({ width: `${Math.min(100, Math.round(share.value * 100))}%` }));
</script>

<template>
  <el-tooltip :content="tip" placement="bottom-start" effect="dark" popper-class="rl-cap-tip" :show-after="400">
    <span
      class="cap num"
      :class="[providerClass(props.provider), `cap--${level}`]"
      tabindex="0"
      :aria-label="tip"
      data-testid="cap-chip"
    >
      {{ props.provider }}
      <span
        v-if="level !== 'ok'"
        class="cap__dot"
        :class="{ 'cap__dot--pulse': level === 'full' }"
        aria-hidden="true"
      />
      <span v-if="capped" class="cap__fig">{{ figure }}</span>
      <span v-if="capped" class="cap__track" aria-hidden="true" />
      <span v-if="capped" class="cap__bar" :style="barWidth" aria-hidden="true" />
    </span>
  </el-tooltip>
</template>

<style scoped>
.cap {
  --tone: var(--pi);
  position: relative;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
  background: var(--ps);
  color: var(--pi);
}
.cap--near {
  --tone: var(--rl-warn);
}
.cap--full {
  --tone: var(--rl-bad);
}
.cap__fig {
  color: var(--tone);
}
.cap__dot {
  position: relative;
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--tone);
  color: var(--tone);
}
.cap__track,
.cap__bar {
  position: absolute;
  left: 0;
  bottom: 0;
  height: 2px;
  background: var(--tone);
}
.cap__track {
  right: 0;
  opacity: 0.22;
}
@keyframes cap-pulse {
  from {
    transform: scale(1);
    opacity: 0.6;
  }
  to {
    transform: scale(2.2);
    opacity: 0;
  }
}
@media (prefers-reduced-motion: no-preference) {
  .cap__dot--pulse::after {
    content: "";
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: currentColor;
    animation: cap-pulse 1.8s ease-out infinite;
  }
}
</style>

<!-- Not scoped: the tooltip is teleported to <body>. -->
<style>
html:root .el-popper.is-dark.rl-cap-tip {
  padding: 6px 10px;
  border: 0;
  border-radius: 8px;
  background: var(--rl-tip);
  color: var(--rl-tip-fg);
  font: 600 12px/16px var(--rl-font);
  white-space: nowrap;
  box-shadow: 0 8px 24px rgba(16, 24, 40, 0.22);
}
html:root .el-popper.is-dark.rl-cap-tip .el-popper__arrow::before {
  border: 0;
  background: var(--rl-tip);
}
</style>
