<script setup lang="ts">
import { Close } from "@element-plus/icons-vue";
import { useBreakpoint } from "@/composables/use-breakpoint";

// The one right-hand drawer of every detail (job, request, alert...). A 620 px panel; a full-screen sheet on a phone.
// The body is destroyed on close, so nothing of a closed detail lingers (its state, timers, media). The dialog's
// accessible name is `title`. Slots: `chips` (status chip and one line of context under the title), `tabs` (a tab
// row under the header), default (the scrolling body) and `footer` (sticky at the bottom).
const props = defineProps<{ modelValue: boolean; title: string; testid?: string }>();
const emit = defineEmits<{ "update:modelValue": [open: boolean]; closed: [] }>();
const { isPhone } = useBreakpoint();
</script>

<template>
  <el-drawer
    :model-value="props.modelValue"
    class="detail-drawer"
    direction="rtl"
    :size="isPhone ? '100%' : '620px'"
    :title="props.title"
    :show-close="false"
    :with-header="true"
    :data-testid="props.testid"
    destroy-on-close
    append-to-body
    @update:model-value="(open: boolean) => emit('update:modelValue', open)"
    @closed="emit('closed')"
  >
    <template #header>
      <div class="detail-drawer__head">
        <div class="detail-drawer__top">
          <div class="detail-drawer__titles">
            <h2 class="detail-drawer__title">{{ props.title }}</h2>
            <div v-if="$slots.chips" class="detail-drawer__chips"><slot name="chips" /></div>
          </div>
          <button
            type="button"
            class="detail-drawer__close"
            aria-label="Close"
            @click="emit('update:modelValue', false)"
          >
            <el-icon :size="18"><Close /></el-icon>
          </button>
        </div>
        <div v-if="$slots.tabs" class="detail-drawer__tabs"><slot name="tabs" /></div>
      </div>
    </template>
    <div class="detail-drawer__body"><slot /></div>
    <template v-if="$slots.footer" #footer>
      <div class="detail-drawer__foot"><slot name="footer" /></div>
    </template>
  </el-drawer>
</template>

<!-- not scoped: the drawer is teleported to <body> and its panel is Element Plus's own element -->
<style>
.detail-drawer.el-drawer {
  --el-drawer-padding-primary: 0px;
  background: var(--rl-surface);
  box-shadow: -16px 0 48px rgba(10, 6, 20, 0.28);
}
.detail-drawer .el-drawer__header {
  display: block;
  margin: 0;
  padding: 0;
}
.detail-drawer .el-drawer__body {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}
.detail-drawer .el-drawer__footer {
  padding: 0;
}
.detail-drawer__head {
  padding: 20px 24px 0;
  border-bottom: 1px solid var(--rl-line);
}
.detail-drawer__top {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.detail-drawer__titles {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.detail-drawer__title {
  margin: 0;
  font-size: 20px;
  line-height: 28px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.detail-drawer__chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
  color: var(--rl-muted);
}
.detail-drawer__close {
  flex: none;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.detail-drawer__close:hover {
  background: var(--rl-raised);
}
.detail-drawer__tabs {
  margin-top: 12px;
}
.detail-drawer__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 24px 24px;
}
.detail-drawer__foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 24px;
  background: var(--rl-surface);
  border-top: 1px solid var(--rl-line-strong);
}
@media (max-width: 767.98px) {
  .detail-drawer__head {
    padding: 12px 12px 0;
  }
  .detail-drawer__close {
    width: 44px;
    height: 44px;
  }
  .detail-drawer__body {
    padding: 16px 16px 24px;
  }
  .detail-drawer__foot {
    padding: 10px 16px;
  }
}
</style>
