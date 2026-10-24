<script setup lang="ts">
import { onBeforeUnmount, ref, watchEffect } from "vue";
import { copyText, type CopyOutcome } from "@/components/shared/clipboard";
import { IconCopy } from "@/components/shared/icons";
import ModalCard from "@/components/shared/overlays/ModalCard.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { notify } from "@/lib/notify";

// The raw key of a key just created, shown once in a blocking modal (KeyDialogs, KeySecretPhone). The view holds the
// key in its own local state and passes it here; this component keeps nothing of its own, so Done (the view clearing
// it) removes the key from the page. Done unlocks once the key was copied (the Copy button, or a copy from the field
// itself); until then Escape and the scrim do nothing. `label` is the key's name, or its prefix when it has none.
const props = defineProps<{ rawKey: string; label: string }>();
const emit = defineEmits<{ dismiss: []; copied: [outcome: CopyOutcome] }>();
const { isPhone } = useBreakpoint();

const field = ref<HTMLInputElement | null>(null);
// Puts the cursor in the key's field (the view calls it once the form drawer is gone).
function reveal(): void {
  field.value?.focus({ preventScroll: true });
}
defineExpose({ reveal });
// The key goes into the field as its value property, never as a `value` attribute: nothing of it is then part of the
// page's HTML (a `:value` binding writes the attribute too).
watchEffect(
  () => {
    if (field.value) field.value.value = props.rawKey;
  },
  { flush: "post" },
);

const unlocked = ref(false);
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
  // a copy the page could not make itself leaves the key selected for Ctrl+C: Done unlocks either way
  unlocked.value = true;
  if (outcome === "select") notify.info("Press Ctrl+C to copy the selected text");
  else if (outcome === "failed") notify.error("Could not copy: select the text and copy it by hand");
  else {
    copied.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => (copied.value = false), 2000);
  }
  emit("copied", outcome);
}

function closed(open: boolean): void {
  if (!open && unlocked.value) emit("dismiss");
}
</script>

<template>
  <ModalCard
    :model-value="true"
    :title="`Key created for ${props.label}`"
    tone="ok"
    :locked="!unlocked"
    width="480px"
    testid="new-key"
    @update:model-value="closed"
  >
    Copy it now. It is shown once and not stored; if it is lost, delete the key and make a new one.
    <template #body>
      <div class="ks" :class="{ 'is-phone': isPhone }">
        <div class="ks__field">
          <label class="ks__label" for="new-key-field">API key</label>
          <div class="ks__row">
            <input
              id="new-key-field"
              ref="field"
              class="ks__secret mono"
              readonly
              autocomplete="off"
              spellcheck="false"
              @focus="field?.select()"
              @copy="unlocked = true"
            />
            <button type="button" class="ks__copy" @click="copy">
              <IconCopy :size="15" />{{ copied ? "Copied" : isPhone ? "Copy key" : "Copy" }}
            </button>
          </div>
          <span class="ks__help">Send it in the header <span class="mono">Authorization: Bearer …</span></span>
        </div>
        <p v-if="!unlocked" class="ks__lock" role="note">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M6 11h12a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z" />
            <path d="M8 11V7a4 4 0 0 1 8 0v4" />
          </svg>
          <span v-if="isPhone">Done unlocks after you copy the key. Back, swipe and tapping outside do nothing until then.</span>
          <span v-else>Done unlocks after you copy the key. Esc and clicking outside do nothing until then.</span>
        </p>
      </div>
    </template>
    <template #footer>
      <button type="button" class="ks__done" :class="{ 'is-phone': isPhone }" :disabled="!unlocked" @click="emit('dismiss')">
        Done
      </button>
    </template>
  </ModalCard>
</template>

<!-- not scoped: the modal is teleported to <body> -->
<style>
.ks {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.ks .mono {
  font-family: var(--rl-mono);
}
.ks__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ks__label {
  font-size: 13px;
  line-height: 20px;
  font-weight: 700;
  color: var(--rl-fg);
}
.ks__row {
  display: flex;
  gap: 8px;
}
.ks__secret {
  flex: 1;
  min-width: 0;
  box-sizing: border-box;
  height: 44px;
  padding: 0 12px;
  border: 1px solid var(--rl-brand);
  border-radius: 10px;
  background: var(--rl-head);
  color: var(--rl-fg);
  font-size: 13px;
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
  outline: none;
}
.ks__copy,
.ks__done {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  box-sizing: border-box;
  border: 0;
  border-radius: 10px;
  font: inherit;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}
.ks__copy {
  height: 44px;
  padding: 0 16px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
}
.ks__copy:hover {
  background: var(--rl-primary-hover);
}
.ks__help {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.ks__lock {
  display: flex;
  gap: 10px;
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-head);
  color: var(--rl-muted);
  font-size: 13px;
  line-height: 19px;
}
.ks__lock svg {
  flex: none;
  margin-top: 2px;
}
.ks__done {
  height: 40px;
  padding: 0 18px;
  margin-top: 4px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
}
.ks__done:disabled {
  background: var(--rl-track);
  color: var(--rl-faint);
  cursor: not-allowed;
}
.ks__copy:focus-visible,
.ks__done:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
/* a phone: the field, then Copy key and Done each on its own full-width 44 px line */
.ks.is-phone .ks__row {
  flex-direction: column;
}
.ks.is-phone .ks__copy {
  width: 100%;
}
.ks__done.is-phone {
  width: 100%;
  height: 44px;
  margin-top: 0;
}
</style>
