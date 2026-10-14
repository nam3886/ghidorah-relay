<script setup lang="ts">
import { ref, watch } from "vue";
import type { Account, AccountPatch } from "@/api/accounts";
import { accountChanges, proxyText } from "./account-model";

// Edit one account: label and proxy. The dialog only asks; the view sends. `save` carries the changed fields only
// (nothing changed just closes the dialog); `recheck` asks for the proxy to be probed again.
const open = defineModel<boolean>({ required: true });
const props = defineProps<{ account: Account | null; busy: boolean }>();
const emit = defineEmits<{ save: [patch: AccountPatch]; recheck: [account: Account] }>();

const label = ref("");
const proxy = ref("");

watch(
  () => [open.value, props.account] as const,
  ([isOpen, account]) => {
    if (!isOpen || !account) return;
    label.value = account.label;
    proxy.value = proxyText(account);
  },
  { immediate: true },
);

function save() {
  if (!props.account) return;
  const patch = accountChanges(props.account, label.value, proxy.value);
  if (!patch) {
    open.value = false;
    return;
  }
  emit("save", patch);
}

function recheck() {
  if (!props.account) return;
  open.value = false;
  emit("recheck", props.account);
}
</script>

<template>
  <el-dialog v-model="open" title="Edit account" width="min(480px, 94vw)">
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item label="Label">
        <el-input v-model="label" aria-label="Label" />
      </el-form-item>
      <el-form-item label="Proxy (empty removes it; the old password is never shown)">
        <el-input v-model="proxy" autocomplete="off" aria-label="Proxy" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="recheck">Re-check proxy</el-button>
      <el-button type="primary" :loading="props.busy" @click="save">Save</el-button>
    </template>
  </el-dialog>
</template>
