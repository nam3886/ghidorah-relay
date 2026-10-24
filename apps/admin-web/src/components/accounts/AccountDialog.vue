<script setup lang="ts">
import "./account-dialog.css";
import SideDrawer from "@/components/shared/overlays/SideDrawer.vue";

// The frame of the Import and Edit forms (AccountImport, AccountEdit, ImportPhone): the Ledger right drawer, `width`
// wide on a desktop (Edit 520 px, Import 560), 48 px short of the left edge on a tablet and the whole screen on a phone
// (the sheet head: no Back, Close · Esc). The drawer is named by its visible heading (the `title` slot draws it when the
// heading has a mono part); `sub` is the line or the chips under it. `dirty` and `busy` are its guard: a way out asks
// first while something typed would be lost, and is inert while a write runs. The footer is given `close`, the guarded
// close, for Cancel.
const open = defineModel<boolean>({ required: true });
const props = defineProps<{
  title: string;
  testid: string;
  width: string;
  discardText: string;
  dirty?: boolean;
  busy?: boolean;
}>();
const emit = defineEmits<{ closed: [] }>();
</script>

<template>
  <SideDrawer
    v-model="open"
    :title="props.title"
    :testid="props.testid"
    :width="props.width"
    back-label="Back to Accounts"
    full
    panel-class="is-sheet-head"
    :dirty="props.dirty"
    :busy="props.busy"
    :discard-text="props.discardText"
    @back="open = false"
    @closed="emit('closed')"
  >
    <template v-if="$slots.title" #title><slot name="title" /></template>
    <template v-if="$slots.sub" #sub><slot name="sub" /></template>
    <slot />
    <template #footer="{ close }"><slot name="footer" :close="close" /></template>
  </SideDrawer>
</template>
