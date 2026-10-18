<script setup lang="ts">
import { onBeforeUnmount, ref, watchEffect } from "vue";
import { IconCheck, IconCopy } from "@/components/shared/icons";
import { copyText, type CopyOutcome } from "@/components/shared/clipboard";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { notify } from "@/lib/notify";
import "@/components/logs/lane-controls.css";

// The raw key of a key just created, shown once. The view holds it in its own local state and passes it here: this
// component keeps nothing of its own, so Dismiss (the view clearing it) removes the key from the page. `label` is the
// name the operator gave the key.
const props = defineProps<{ rawKey: string; label: string }>();
const emit = defineEmits<{ dismiss: []; copied: [outcome: CopyOutcome] }>();
const { isPhone } = useBreakpoint();

const field = ref<HTMLInputElement | null>(null);
// The key goes into the field as its value property, never as a `value` attribute: nothing of it is then part of the
// page's HTML (a `:value` binding writes the attribute too).
watchEffect(
  () => {
    if (field.value) field.value.value = props.rawKey;
  },
  { flush: "post" },
);
const copied = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
// aborted on unmount: a copy still pending then must not touch the clipboard fallback, toast or emit
const abort = new AbortController();
onBeforeUnmount(() => {
  abort.abort();
  clearTimeout(timer);
});

async function copy(): Promise<void> {
  const outcome = await copyText(props.rawKey, () => field.value, abort.signal);
  if (outcome === "aborted") return;
  if (outcome === "select") notify.info("Press Ctrl+C to copy the selected text");
  else if (outcome === "failed") notify.error("Could not copy: select the text and copy it by hand");
  else {
    copied.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => (copied.value = false), 2000);
  }
  emit("copied", outcome);
}
</script>

<template>
  <section class="callout" role="status" aria-label="One-time key" data-testid="new-key">
    <div class="callout__head">
      <span class="callout__icon"><IconCheck :size="18" :stroke="2" /></span>
      <p class="callout__text">
        <strong>Key created for {{ label }}.</strong>
        <span class="callout__muted"> Copy it now: it is shown once and not stored.</span>
      </p>
    </div>
    <div class="callout__row">
      <input
        ref="field"
        class="callout__field"
        readonly
        :aria-label="`New API key for ${label}`"
        autocomplete="off"
        spellcheck="false"
        @focus="field?.select()"
      />
      <div class="callout__buttons">
        <button type="button" class="kla-btn kla-btn--primary kla-btn--fit callout__btn" @click="copy">
          <IconCopy :size="isPhone ? 16 : 15" />{{ copied ? "Copied" : "Copy" }}
        </button>
        <button type="button" class="kla-btn kla-btn--ghost kla-btn--fit callout__btn" @click="emit('dismiss')">
          Dismiss
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.callout {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  background: var(--rl-ok-bg);
}
.callout__head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.callout__icon {
  flex: none;
  margin-top: 1px;
  color: var(--rl-ok);
  line-height: 0;
}
.callout__text {
  flex: 1;
  min-width: 0;
  margin: 0;
}
.callout__text strong {
  font-weight: 600;
}
.callout__muted {
  color: var(--rl-muted);
}
.callout__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.callout__field {
  flex: 1 1 360px;
  min-width: 0;
  box-sizing: border-box;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-family: var(--rl-mono);
  font-size: 13px;
}
.callout__buttons {
  display: flex;
  gap: 8px;
}
/* as drawn: Copy padded 0 14 like Dismiss */
.callout__btn {
  padding: 0 14px;
}
@media (max-width: 1279px) {
  .callout__field {
    flex-basis: 300px;
  }
}
/* phone: the field on its own row and the two buttons sharing the next, both rows 12 px apart like the rest */
@media (max-width: 767px) {
  .callout__row {
    display: contents;
  }
  .callout__field {
    flex: none;
    width: 100%;
    height: 44px;
    font-size: 12px;
  }
  .callout__btn {
    flex: 1;
  }
}
</style>
