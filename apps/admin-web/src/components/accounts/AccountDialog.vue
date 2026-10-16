<script setup lang="ts">
import "./account-dialog.css";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { IconArrowLeft, IconClose } from "@/components/shared/icons";

// The frame of the Import and Edit dialogs (specs/accounts.md sections 7 and 8): a header with the title and Close,
// the body, the footer. From 768 px a 640 px box under the scrim; below 768 px a full-screen sheet whose header has a
// 44 px Back and whose footer stays at the bottom. The two dialogs differ as drawn: Import has an edge and a quiet
// header, Edit has a divider under its header and no edge.
const open = defineModel<boolean>({ required: true });
const props = defineProps<{ title: string; variant: "import" | "edit"; subtitle?: string }>();
const { isPhone } = useBreakpoint();
</script>

<template>
  <el-dialog
    v-model="open"
    :class="['acct-dialog', `acct-dialog--${props.variant}`, { 'acct-dialog--sheet': isPhone }]"
    :show-close="false"
    append-to-body
    destroy-on-close
    width="640px"
  >
    <template #header="{ titleId }">
      <div class="acct-dialog__head">
        <button v-if="isPhone" type="button" class="acct-dialog__icon" aria-label="Close" @click="open = false">
          <IconArrowLeft :size="20" />
        </button>
        <div class="acct-dialog__titles">
          <h2 :id="titleId" class="acct-dialog__title">{{ props.title }}</h2>
          <p v-if="props.subtitle" class="acct-dialog__sub">{{ props.subtitle }}</p>
        </div>
        <button v-if="!isPhone" type="button" class="acct-dialog__icon" aria-label="Close" @click="open = false">
          <IconClose :size="18" :stroke="2" />
        </button>
      </div>
    </template>
    <div class="acct-dialog__body"><slot /></div>
    <template #footer>
      <div class="acct-dialog__foot"><slot name="footer" /></div>
    </template>
  </el-dialog>
</template>
