<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Account, AccountPatch } from "@/api/accounts";
import StatusChip from "@/components/shared/StatusChip.vue";
import AccountDialog from "./AccountDialog.vue";
import { accountChanges, proxyStatus, proxyText } from "./account-model";

// Edit one account: label and proxy. The dialog only asks; the view sends. `save` carries the changed fields only
// (nothing changed just closes the dialog); `recheck` closes the dialog and asks for the proxy to be probed again.
// Beside the proxy field the result of its last check is shown as it stands.
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

const status = computed(() => (props.account?.proxy_host ? proxyStatus(props.account) : null));

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
  <AccountDialog v-model="open" variant="edit" :title="`Edit account — ${props.account?.label ?? ''}`">
    <label class="acct-field">
      <span class="acct-field__label">Label</span>
      <input v-model="label" class="acct-input" maxlength="100" aria-label="Label" />
      <span class="acct-field__help">Shown in the Accounts list and in job details.</span>
    </label>
    <div class="acct-field">
      <label class="acct-field__label" for="acct-edit-proxy">Proxy</label>
      <div class="proxy-row">
        <input
          id="acct-edit-proxy"
          v-model="proxy"
          class="acct-input acct-input--mono proxy-input"
          autocomplete="off"
          aria-label="Proxy"
        />
        <button type="button" class="acct-btn check" @click="recheck">Check proxy</button>
        <span v-if="status" class="status" role="status">
          <StatusChip :status="status.tone" :tone="status.tone" :label="status.text" />
        </span>
      </div>
      <span class="acct-field__help"
        >Leave empty to remove the proxy. The stored password is never shown; type a new one as host:port:user:pass to
        replace it.</span
      >
    </div>
    <template #footer>
      <button type="button" class="acct-btn" @click="open = false">Cancel</button>
      <button
        type="button"
        class="acct-btn acct-btn--primary"
        :aria-disabled="props.busy ? 'true' : undefined"
        @click="!props.busy && save()"
      >
        Save
      </button>
    </template>
  </AccountDialog>
</template>

<style scoped>
.proxy-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.proxy-input {
  flex: 1 1 260px;
  min-width: 0;
  width: auto;
}
.check {
  padding: 0 14px;
}
.status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
</style>
