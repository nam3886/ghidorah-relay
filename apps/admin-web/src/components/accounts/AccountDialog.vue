<script setup lang="ts">
import "./account-dialog.css";
import DetailDrawer from "@/components/shared/DetailDrawer.vue";

// The frame of the Import and Edit forms: the shared right drawer (a full-screen sheet with Back on a phone), named by
// its visible heading. The subtitle is the line under the heading. `dirty` and `busy` are the guard of the drawer: a
// close asks first while something typed would be lost, and is inert while a write runs. A scrim click never closes.
// The footer is given `close`, the guarded close, for Cancel.
const open = defineModel<boolean>({ required: true });
const props = defineProps<{
  title: string;
  subtitle?: string;
  testid: string;
  discardText: string;
  dirty?: boolean;
  busy?: boolean;
  // Import's fields sit 16 px apart as drawn; Edit's 20 (the drawer's own)
  tight?: boolean;
}>();
const emit = defineEmits<{ closed: [] }>();
</script>

<template>
  <DetailDrawer
    v-model="open"
    :title="props.title"
    :testid="props.testid"
    back-label="Back to Accounts"
    :dirty="props.dirty"
    :busy="props.busy"
    :discard-text="props.discardText"
    :close-on-scrim="false"
    @closed="emit('closed')"
  >
    <template v-if="props.subtitle" #chips>{{ props.subtitle }}</template>
    <div class="acct-form" :class="{ 'acct-form--tight': props.tight }"><slot /></div>
    <template #footer="{ close }"><slot name="footer" :close="close" /></template>
  </DetailDrawer>
</template>
