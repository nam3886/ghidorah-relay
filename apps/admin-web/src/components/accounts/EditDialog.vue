<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Account, AccountPatch } from "@/api/accounts";
import IconButton from "@/components/shared/IconButton.vue";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { IB_ICONS } from "@/components/shared/icons/ledger-icons";
import { providerClass } from "@/lib/providers";
import AccountDialog from "./AccountDialog.vue";
import { accountChanges, accountSub, type ChipState, healthOf, hhmm, proxyStatus, proxyText } from "./account-model";

// Edit one account (AccountEdit): label, proxy and Taking jobs. The dialog only asks; the view sends. `save` carries the
// changed fields only (nothing changed just closes the dialog); `recheck` asks for the stored proxy to be probed again,
// and the dialog stays open: under the proxy row a chip says the check is running (`proxyChip`), then how it ended;
// beside it the result of the last stored check. A changed label is marked "edited" and says what it was. An account in
// use has no switch (the server refuses the change while a worker holds it).
const open = defineModel<boolean>({ required: true });
// `busy`: the page is running some command (Save waits); `saving`: this drawer's own write is in flight (every way
// out of it is inert)
const props = defineProps<{ account: Account | null; busy: boolean; saving: boolean; proxyChip?: ChipState | null }>();
const emit = defineEmits<{
  save: [patch: AccountPatch];
  recheck: [account: Account];
  "update:dirty": [dirty: boolean];
  closed: [];
}>();

const label = ref("");
const proxy = ref("");
const enabled = ref(true);
// What the fields held when the drawer opened. The guard compares against this copy, never against `props.account`:
// a new copy of the account (the 10 s read, the end of a Check proxy) must not change what counts as edited.
const snap = ref({ label: "", proxy: "", enabled: true });
const typed = (): boolean => label.value !== snap.value.label || proxy.value !== snap.value.proxy;
const dirty = computed(() => typed() || enabled.value !== snap.value.enabled);
const changes = computed(
  () =>
    [label.value !== snap.value.label, proxy.value !== snap.value.proxy, enabled.value !== snap.value.enabled].filter(
      Boolean,
    ).length,
);

// The fields fill when the dialog opens on an account, and only then: a read of the list while it is open (the end of
// a Check proxy, the 10 s read) brings a new copy of the same account and must not wipe what was typed.
watch(
  [() => open.value, () => props.account?.id],
  ([isOpen]) => {
    const account = props.account;
    if (!isOpen) {
      // nothing typed (a new proxy password included) outlives the drawer
      label.value = proxy.value = "";
      snap.value = { label: "", proxy: "", enabled: true };
      enabled.value = true;
      return;
    }
    if (!account) return;
    label.value = account.label;
    proxy.value = proxyText(account);
    enabled.value = account.state !== "disabled";
    snap.value = { label: label.value, proxy: proxy.value, enabled: enabled.value };
  },
  { immediate: true },
);

watch(dirty, (d) => emit("update:dirty", d), { immediate: true });

// The stored result as AccountEdit words it: "Last check 14:27 · connect timeout after 8 s", "Last check 14:27 · ok",
// "Not checked yet".
const status = computed(() => {
  const a = props.account;
  if (!a?.proxy_host) return null;
  const { tone, text } = proxyStatus(a);
  if (a.proxy_ok === null || a.proxy_ok === undefined) return { tone, text: "Not checked yet" };
  const at = a.proxy_checked_ms ? ` ${hhmm(a.proxy_checked_ms)}` : "";
  const reason = text.startsWith("failed · ") && !/ ago$/.test(text) ? text.slice("failed · ".length) : "";
  return { tone, text: `Last check${at} · ${reason || (a.proxy_ok ? "ok" : "failed")}` };
});
const health = computed(() => (props.account ? healthOf(props.account) : null));
const labelEdited = computed(() => label.value !== snap.value.label);
// the switch is the form's only while no worker holds the account
const canSwitch = computed(() => !!props.account && !props.account.in_use);

function save() {
  if (!props.account) return;
  const patch = accountChanges(props.account, label.value, proxy.value, canSwitch.value ? enabled.value : undefined);
  if (!patch) {
    open.value = false;
    return;
  }
  emit("save", patch);
}

function recheck() {
  if (!props.account || props.proxyChip?.state === "running") return;
  emit("recheck", props.account);
}
</script>

<template>
  <AccountDialog
    v-model="open"
    :title="`Edit account ${props.account?.label ?? ''}`"
    testid="edit-account-drawer"
    width="min(520px, 100%)"
    discard-text="Discard your changes to this account?"
    :dirty="dirty"
    :busy="props.saving"
    @closed="emit('closed')"
  >
    <template #title
      ><span class="acct-title"
        >Edit account <span class="acct-title-mono">{{ props.account?.label ?? "" }}</span></span
      ></template
    >
    <template v-if="props.account && health" #sub>
      <span class="acct-pill" :class="`tone-${health.tone}`">{{ health.label }}</span>
      <span class="acct-pchip" :class="providerClass(props.account.provider)">{{ props.account.provider }}</span>
      <span v-if="accountSub(props.account)" class="acct-field__help">{{ accountSub(props.account) }}</span>
    </template>

    <div class="acct-field">
      <label class="acct-field__label" for="acct-edit-label"
        >Label<span v-if="labelEdited" class="acct-edited">edited</span></label
      >
      <input
        id="acct-edit-label"
        v-model="label"
        class="acct-input"
        :class="{ 'is-edited': labelEdited }"
        maxlength="100"
        aria-label="Label"
      />
      <span class="acct-field__help"
        >Shown in the Accounts list and in job details.<template v-if="labelEdited">
          Was {{ snap.label }}.</template
        ></span
      >
    </div>

    <div class="acct-field">
      <label class="acct-field__label" for="acct-edit-proxy">Proxy</label>
      <div class="acct-proxy-row">
        <input
          id="acct-edit-proxy"
          v-model="proxy"
          class="acct-input acct-input--mono"
          autocomplete="off"
          aria-label="Proxy"
        />
        <!-- while a check runs a click asks nothing more (recheck); its chip says it is running -->
        <IconButton icon="proxy" label="Check proxy" class="acct-proxy-check" @click="recheck" />
      </div>
      <div v-if="props.proxyChip || status" class="acct-chips">
        <span
          v-if="props.proxyChip?.state === 'running'"
          class="acct-chip is-running"
          role="status"
          data-state="running"
          ><span class="acct-chip__spin" aria-hidden="true" />Checking{{
            props.proxyChip.time ? ` · started ${props.proxyChip.time}` : ""
          }}</span
        >
        <span v-else-if="props.proxyChip?.state === 'ok'" class="acct-chip is-ok" role="status" data-state="ok"
          ><PathIcon :paths="IB_ICONS.check" :size="12" :stroke="2.6" />Check passed · {{ props.proxyChip.time }}</span
        >
        <span v-else-if="props.proxyChip?.state === 'error'" class="acct-chip is-bad" role="status" data-state="error"
          ><PathIcon :paths="IB_ICONS.close" :size="12" :stroke="2.6" />Check failed ·
          {{ props.proxyChip.message || "no reason given" }}</span
        >
        <span
          v-if="status"
          class="acct-chip"
          :class="`is-${status.tone}`"
          :data-tone="status.tone"
          :role="props.proxyChip ? undefined : 'status'"
          ><PathIcon
            v-if="status.tone !== 'neutral'"
            :paths="status.tone === 'ok' ? IB_ICONS.check : IB_ICONS.close"
            :size="12"
            :stroke="2.6"
          />{{ status.text }}</span
        >
      </div>
      <span class="acct-field__help"
        >Leave empty to remove the proxy. The stored password is never shown; type a new one as host:port:user:pass to
        replace it.</span
      >
    </div>

    <fieldset v-if="canSwitch" class="acct-field">
      <legend class="acct-field__label">Taking jobs</legend>
      <div class="acct-switch-row">
        <button
          type="button"
          role="switch"
          class="acct-switch"
          :aria-checked="enabled ? 'true' : 'false'"
          :aria-label="`Enable ${props.account?.label ?? ''}`"
          @click="enabled = !enabled"
        >
          <span class="acct-switch__knob" />
        </button>
        <span class="acct-field__help">{{
          enabled ? "On. It claims jobs once its checks pass." : "Off. It takes no jobs until it is switched on."
        }}</span>
      </div>
    </fieldset>

    <template #footer="{ close }">
      <span v-if="changes" class="acct-foot-note"
        >{{ changes }} unsaved {{ changes === 1 ? "change" : "changes" }}</span
      >
      <span class="acct-foot-btns">
        <button type="button" class="acct-btn" :disabled="props.saving" @click="close">Cancel</button>
        <button
          type="button"
          class="acct-btn acct-btn--primary"
          :aria-disabled="props.busy || props.saving ? 'true' : undefined"
          @click="!props.busy && !props.saving && save()"
        >
          Save
        </button>
      </span>
    </template>
  </AccountDialog>
</template>

<style scoped>
.acct-proxy-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.acct-proxy-row .acct-input {
  flex: 1;
  min-width: 0;
}
/* the Check proxy icon is 36 px beside the 40 px input (AccountEdit) */
.acct-proxy-row :deep(.ib) {
  width: 36px;
  height: 36px;
}
</style>
