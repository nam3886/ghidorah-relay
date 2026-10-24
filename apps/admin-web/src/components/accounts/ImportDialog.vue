<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { importAccounts, type ImportResult } from "@/api/accounts";
import { useBusy } from "@/composables/use-busy";
import { notify } from "@/lib/notify";
import { byProviderOrder, providerClass } from "@/lib/providers";
import { useScopeStore } from "@/stores/scope";
import AccountDialog from "./AccountDialog.vue";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import { importBody, rejectedText } from "./account-model";

// Import accounts (AccountImport, ImportPhone): one credential per line beside a line-number gutter, an optional proxy
// for all of them, and the result of the answer. A rejected line is named by its number and reason only: the
// credential itself is never echoed back; the imported ones are named by the label the server gave them.
const open = defineModel<boolean>({ required: true });
const emit = defineEmits<{
  imported: [];
  "update:dirty": [dirty: boolean];
  "update:busy": [busy: boolean];
  closed: [];
}>();

const scope = useScopeStore();
const { busy, run } = useBusy();
const provider = ref("");
const credentials = ref("");
const proxy = ref("");
const result = ref<ImportResult | null>(null);

const providers = computed(() => scope.providers);
// something pasted that a close would lose (a submitted import has been wiped, so it is clean)
const dirty = computed(() => credentials.value.trim() !== "" || proxy.value.trim() !== "");
watch(dirty, (d) => emit("update:dirty", d), { immediate: true });
watch(busy, (b) => emit("update:busy", b));
const providerOptions = computed(() => byProviderOrder(providers.value).map((name) => ({ value: name, label: name })));
// the gutter numbers the lines typed (a trailing empty line is a line, as an editor counts it); the count over the box
// counts the credentials
const lineCount = computed(() => Math.max(1, credentials.value.split("\n").length));
const filled = computed(() => credentials.value.split("\n").filter((l) => l.trim()).length);
const importedNames = computed(() => {
  const names = (result.value?.imported ?? []).map((i) => i.label);
  if (!names.length) return "";
  const one = names.length === 1;
  const list = one ? names[0] : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
  return `${list} ${one ? "was" : "were"} added and ${one ? "is" : "are"} being checked now.`;
});

// What was pasted never outlives its use: both fields are emptied when the drawer opens and when it closes (by any
// path, a close from outside included), right after the payload is copied for the request, and on every failure.
function wipe(): void {
  credentials.value = "";
  proxy.value = "";
}

// Each opening starts from the filter (the provider the operator is looking at), else the first provider.
watch(open, (isOpen) => {
  wipe();
  if (!isOpen) return;
  provider.value = scope.provider || String(providerOptions.value[0]?.value ?? "");
  result.value = null;
});

async function submit() {
  const body = importBody(provider.value, credentials.value, proxy.value);
  if (!body) {
    notify.error("Paste at least one credential");
    return;
  }
  wipe();
  await run(async () => {
    const out = await importAccounts(body);
    result.value = out;
    emit("imported");
  });
}
</script>

<template>
  <AccountDialog
    v-model="open"
    title="Import accounts"
    testid="import-drawer"
    width="min(560px, 100%)"
    discard-text="Discard the pasted credentials?"
    :dirty="dirty"
    :busy="busy"
    @closed="emit('closed')"
  >
    <template #title><span class="acct-title">Import accounts</span></template>
    <template #sub>
      <p class="acct-sub-line">One credential per line. Lines that fail are rejected; the rest are imported.</p>
    </template>
    <div class="acct-stack">
      <FilterSelect
        :model-value="provider"
        class="acct-provider"
        :class="providerClass(provider)"
        variant="field"
        block
        label="Provider"
        :options="providerOptions"
        @update:model-value="(v) => (provider = String(v))"
      />
      <div class="acct-field">
        <div class="acct-field__head">
          <label class="acct-field__label" for="acct-import-cred">Credentials</label>
          <span class="acct-field__help num">{{ filled }} {{ filled === 1 ? "line" : "lines" }}</span>
        </div>
        <div class="acct-area-wrap">
          <div class="acct-gutter" aria-hidden="true">
            <span v-for="i in lineCount" :key="i">{{ i }}</span>
          </div>
          <textarea
            id="acct-import-cred"
            v-model="credentials"
            class="acct-area"
            rows="6"
            spellcheck="false"
            autocomplete="off"
            aria-label="Credentials"
          />
        </div>
        <span class="acct-field__help">Secrets are stored encrypted and never shown again.</span>
      </div>
      <div class="acct-field">
        <label class="acct-field__label" for="acct-import-proxy"
          >Proxy for all <span class="acct-field__opt">optional</span></label
        >
        <input
          id="acct-import-proxy"
          v-model="proxy"
          class="acct-input acct-input--mono"
          placeholder="host:port:user:pass"
          autocomplete="off"
          aria-label="Proxy for all"
        />
      </div>
      <div v-if="result" class="acct-result" role="status" aria-label="Result of the previous import">
        <div class="acct-result__head">
          <b>Imported {{ result.imported.length }}, rejected {{ result.rejected.length }}</b>
          <span v-if="result.imported.length" class="pill tone-ok"
            ><i class="pill__dot" />{{ result.imported.length }} imported</span
          >
          <span v-if="result.rejected.length" class="pill tone-bad"
            ><i class="pill__dot" />{{ result.rejected.length }} rejected</span
          >
        </div>
        <ul v-if="result.rejected.length" class="acct-result__list">
          <li v-for="r in result.rejected" :key="r.index">
            <span class="mono num acct-result__line">line {{ r.index + 1 }}</span>
            <span class="acct-result__bad">{{ rejectedText(r.reason) }}</span>
          </li>
        </ul>
        <span v-if="importedNames" class="acct-field__help">{{ importedNames }}</span>
      </div>
    </div>
    <template #footer="{ close }">
      <span class="acct-foot-btns">
        <button type="button" class="acct-btn" :disabled="busy" @click="close">Cancel</button>
        <button
          type="button"
          class="acct-btn acct-btn--primary"
          :aria-disabled="busy ? 'true' : undefined"
          @click="!busy && submit()"
        >
          Import
        </button>
      </span>
    </template>
  </AccountDialog>
</template>

<style scoped>
.acct-result {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
  background: var(--rl-head);
}
.acct-result__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  color: var(--pill-fg);
  background: var(--pill-bg);
}
.pill__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentcolor;
}
.tone-ok {
  --pill-fg: var(--rl-ok);
  --pill-bg: var(--rl-ok-bg);
}
.tone-bad {
  --pill-fg: var(--rl-bad);
  --pill-bg: var(--rl-bad-bg);
}
.acct-result__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.acct-result__list li {
  display: flex;
  gap: 10px;
  font-size: 13px;
}
.acct-result__line {
  flex: none;
  font-size: 12px;
  color: var(--rl-faint);
}
.acct-result__bad {
  min-width: 0;
  font-weight: 600;
  color: var(--rl-bad);
}
</style>
