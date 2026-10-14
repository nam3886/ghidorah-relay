<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { importAccounts } from "@/api/accounts";
import { useBusy } from "@/composables/use-busy";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import { importBody, importSummary } from "./account-model";

// Import accounts: one credential per line, an optional proxy for all of them, and the result line of the answer.
const open = defineModel<boolean>({ required: true });
const emit = defineEmits<{ imported: [] }>();

const scope = useScopeStore();
const { busy, run } = useBusy();
const provider = ref("");
const credentials = ref("");
const proxy = ref("");
const result = ref("");

const providers = computed(() => scope.providers);

// Each opening starts from the filter (the provider the operator is looking at), else the first provider.
watch(open, (isOpen) => {
  if (!isOpen) return;
  provider.value = scope.provider || providers.value[0] || "";
  result.value = "";
});

async function submit() {
  const body = importBody(provider.value, credentials.value, proxy.value);
  if (!body) {
    notify.error("Paste at least one credential");
    return;
  }
  await run(async () => {
    const out = await importAccounts(body);
    credentials.value = "";
    result.value = importSummary(out);
    emit("imported");
  });
}
</script>

<template>
  <el-dialog v-model="open" title="Import accounts" width="min(520px, 94vw)">
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="Provider">
        <el-select v-model="provider" aria-label="Provider">
          <el-option v-for="name in providers" :key="name" :label="name" :value="name" />
        </el-select>
      </el-form-item>
      <el-form-item label="Credentials (one per line)">
        <el-input
          v-model="credentials"
          type="textarea"
          :rows="8"
          class="mono-field"
          aria-label="Credentials (one per line)"
        />
      </el-form-item>
      <el-form-item label="Proxy for all (optional, host:port:user:pass)">
        <el-input v-model="proxy" autocomplete="off" aria-label="Proxy for all" />
      </el-form-item>
      <p v-if="result" class="import-result" role="status">{{ result }}</p>
    </el-form>
    <template #footer>
      <el-button @click="open = false">Close</el-button>
      <el-button type="primary" :loading="busy" @click="submit">Import</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.mono-field :deep(textarea) {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}
.import-result {
  margin: 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}
</style>
