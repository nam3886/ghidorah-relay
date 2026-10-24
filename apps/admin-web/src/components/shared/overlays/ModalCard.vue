<script setup lang="ts">
import { useBreakpoint } from "@/composables/use-breakpoint";

// The Ledger modal (Dialogs, KeyDialogs; JobsTablet `modal`): one card, 16 px radius on every corner, the header inside
// it, 22 px of padding and 18 px between the parts, the lift shadow over the darker modal scrim. An optional 40 px badge
// (radius 12) leads the title: `danger` in the red pair, `calm` in the brand pair. Title 16/24 800, text 13/20 muted;
// the `footer` slot is right-aligned with 8 px gaps. On a phone the card is 16 px from each side, centred. Element Plus
// keeps the focus inside and closes it on Escape; `alert` makes it an alertdialog (a confirm).
const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    title: string;
    tone?: "none" | "danger" | "calm";
    alert?: boolean;
    width?: string;
    testid?: string;
  }>(),
  { tone: "none", alert: false, width: "440px", testid: undefined },
);
const emit = defineEmits<{ "update:modelValue": [open: boolean]; closed: [] }>();
const { isPhone } = useBreakpoint();
</script>

<template>
  <el-dialog
    :model-value="props.modelValue"
    class="rl-modal"
    modal-class="rl-modal-scrim"
    :width="isPhone ? 'calc(100% - 32px)' : props.width"
    :show-close="false"
    :role="props.alert ? 'alertdialog' : 'dialog'"
    :data-testid="props.testid"
    align-center
    append-to-body
    destroy-on-close
    @update:model-value="(open: boolean) => emit('update:modelValue', open)"
    @closed="emit('closed')"
  >
    <template #header="{ titleId }">
      <div class="rl-modal__head">
        <span v-if="props.tone !== 'none'" class="rl-modal__badge" :class="`is-${props.tone}`" aria-hidden="true">
          <svg
            v-if="props.tone === 'danger'"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
            <path d="M12 9v4" />
            <path d="M12 17h.01" />
          </svg>
          <svg
            v-else
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M12 11v5" />
            <path d="M12 8h.01" />
          </svg>
        </span>
        <div class="rl-modal__titles">
          <h2 :id="titleId" class="rl-modal__title">{{ props.title }}</h2>
          <div v-if="$slots.default" class="rl-modal__text"><slot /></div>
        </div>
      </div>
    </template>
    <slot name="body" />
    <template v-if="$slots.footer" #footer>
      <div class="rl-modal__foot"><slot name="footer" /></div>
    </template>
  </el-dialog>
</template>

<!-- Not scoped: the dialog is teleported to <body> and its parts are Element Plus's own. -->
<style>
html:root .el-dialog.rl-modal {
  display: flex;
  flex-direction: column;
  gap: 18px;
  max-width: 100%;
  padding: 22px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-drawer);
}
html:root .rl-modal-scrim {
  background: var(--rl-scrim-modal);
}
html:root .rl-modal .el-dialog__header,
html:root .rl-modal .el-dialog__body,
html:root .rl-modal .el-dialog__footer {
  padding: 0;
}
html:root .rl-modal .el-dialog__body:empty {
  display: none;
}
.rl-modal__head {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
.rl-modal__badge {
  display: grid;
  place-items: center;
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 12px;
}
.rl-modal__badge.is-danger {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.rl-modal__badge.is-calm {
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.rl-modal__titles {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.rl-modal__title {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
.rl-modal__text {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-muted);
}
.rl-modal__foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
