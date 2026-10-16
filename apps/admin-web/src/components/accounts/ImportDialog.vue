<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { importAccounts, type ImportResult } from "@/api/accounts";
import { useBusy } from "@/composables/use-busy";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import AccountDialog from "./AccountDialog.vue";
import { IconChevronDown } from "@/components/shared/icons";
import { importBody } from "./account-model";

// Import accounts: one credential per line, an optional proxy for all of them, and the result of the answer. A
// rejected line is named by its number and reason only: the credential itself is never echoed back.
const open = defineModel<boolean>({ required: true });
const emit = defineEmits<{ imported: [] }>();

const scope = useScopeStore();
const { busy, run } = useBusy();
const provider = ref("");
const credentials = ref("");
const proxy = ref("");
const result = ref<ImportResult | null>(null);

const providers = computed(() => scope.providers);

// What was pasted never outlives its use: both fields are emptied when the dialog opens and when it closes, right
// after the payload is copied for the request, and on every failure.
function wipe(): void {
  credentials.value = "";
  proxy.value = "";
}

// Each opening starts from the filter (the provider the operator is looking at), else the first provider.
watch(open, (isOpen) => {
  wipe();
  if (!isOpen) return;
  provider.value = scope.provider || providers.value[0] || "";
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
    variant="import"
    title="Import accounts"
    subtitle="One credential per line. Lines that fail are rejected; the rest are imported."
  >
    <label class="acct-field">
      <span class="acct-field__label">Provider</span>
      <span class="acct-select-wrap">
        <select v-model="provider" class="acct-select" aria-label="Provider">
          <option v-for="name in providers" :key="name" :value="name">{{ name }}</option>
        </select>
        <IconChevronDown :size="14" :stroke="2" />
      </span>
    </label>
    <label class="acct-field">
      <span class="acct-field__label">Credentials</span>
      <textarea
        v-model="credentials"
        class="acct-textarea"
        rows="4"
        spellcheck="false"
        autocomplete="off"
        aria-label="Credentials"
      />
      <span class="acct-field__help">One per line. Secrets are stored encrypted and never shown again.</span>
    </label>
    <label class="acct-field">
      <span class="acct-field__label">Proxy for all <span>(optional)</span></span>
      <input
        v-model="proxy"
        class="acct-input acct-input--mono"
        placeholder="host:port:user:pass"
        autocomplete="off"
        aria-label="Proxy for all"
      />
    </label>
    <div v-if="result" class="result" role="status" aria-label="Result of the previous import">
      <div class="result__head">
        <strong>Imported {{ result.imported.length }}, rejected {{ result.rejected.length }}</strong>
        <span v-if="result.imported.length" class="pill tone-ok"
          ><i class="pill__dot" />{{ result.imported.length }} imported</span
        >
        <span v-if="result.rejected.length" class="pill tone-bad"
          ><i class="pill__dot" />{{ result.rejected.length }} rejected</span
        >
      </div>
      <ul v-if="result.rejected.length" class="result__list">
        <li v-for="r in result.rejected" :key="r.index">
          <span class="mono faint">line {{ r.index + 1 }}</span>
          <span class="bad">{{ r.reason }}</span>
        </li>
      </ul>
    </div>
    <template #footer>
      <button type="button" class="acct-btn" @click="open = false">Cancel</button>
      <button
        type="button"
        class="acct-btn acct-btn--primary"
        :aria-disabled="busy ? 'true' : undefined"
        @click="!busy && submit()"
      >
        Import
      </button>
    </template>
  </AccountDialog>
</template>

<style scoped>
.result {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-raised);
}
.result__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.result__head strong {
  font-weight: 600;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 10px;
  border-radius: 11px;
  font-size: 12px;
  font-weight: 500;
  color: var(--pill-fg);
  background: var(--pill-bg);
}
.pill__dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
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
.result__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.result__list li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  font-size: 13px;
}
.mono {
  font-family: var(--rl-mono);
  font-size: 12px;
}
.faint {
  color: var(--rl-faint);
}
.bad {
  color: var(--rl-bad);
}
</style>
