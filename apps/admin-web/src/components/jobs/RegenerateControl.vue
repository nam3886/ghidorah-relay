<script setup lang="ts">
import { computed, ref } from "vue";
import { confirmDialog, type ConfirmOptions } from "@/components/shared/confirm-dialog";
import { hasKeptKey } from "@/components/jobs/kept-key";
import { useBusy } from "@/composables/use-busy";
import { notify } from "@/lib/notify";
import { abandonOperation } from "@/lib/operation-key";

// The Regenerate button of the bulk bar and of the job detail. A regenerate makes a NEW job and charges it, so:
// - the button is off while a call (or its confirm) is open, and the retry key logic is operation-key.ts's;
// - after a failure that kept the key, a second control offers "Start a new regenerate", behind a confirm that warns
//   of the double charge. Nothing else drops a kept key.
const props = defineProps<{
  // the operation-key action: bulkAction(ids) or jobAction(id)
  action: string;
  label: string;
  disabled?: boolean;
  confirm: ConfirmOptions;
  // makes the call and toasts its success; a thrown error is toasted here
  perform: () => Promise<void>;
}>();

const { busy, run } = useBusy();
// operation-key.ts does not announce its kept keys, so the record is read again after every call and every abandon.
const reads = ref(0);
const kept = computed(() => {
  void reads.value;
  return hasKeptKey(props.action);
});

async function start(): Promise<void> {
  if (busy.value) return;
  await run(async () => {
    if (!(await confirmDialog(props.confirm))) return;
    try {
      await props.perform();
    } finally {
      reads.value++;
    }
  });
}

const WARNING =
  "The earlier attempt may already have made jobs. Starting a new one can charge again. Check the Jobs list first.";

async function startNew(): Promise<void> {
  await run(async () => {
    const ok = await confirmDialog({
      title: "Start a new regenerate",
      message: WARNING,
      confirmText: "Start a new one",
      danger: true,
    });
    if (!ok) return;
    if (abandonOperation(props.action)) notify.info("Done. The next Regenerate starts a new attempt.");
    else notify.error("That regenerate is still running. Wait for its answer first.");
    reads.value++;
  });
}
</script>

<template>
  <span class="regenerate">
    <el-button :disabled="props.disabled || busy" :loading="busy" @click="start">{{ props.label }}</el-button>
    <el-button v-if="kept && !busy" link type="warning" @click="startNew">Start a new regenerate</el-button>
  </span>
</template>

<style scoped>
.regenerate {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
</style>
