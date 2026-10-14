<script setup lang="ts">
import { reactive, watch } from "vue";
import type { ApiKey, KeyBody } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import { type CreateError, keyBody, keyForm, type KeyForm } from "./key-model";

// Create or edit a key. The dialog only asks: it builds the body from what was typed and the view sends it.
const open = defineModel<boolean>({ required: true });
// `error`: why a create failed, already reduced to fixed text and a field name (see createError).
const props = defineProps<{
  editing: ApiKey | null;
  models: CatalogModel[];
  busy: boolean;
  error: CreateError | null;
}>();
// The highlighted field gets Element Plus's error look; the text itself is one line above the form.
const flagged = (name: CreateError["field"]) => ({ "is-error": props.error?.field === name });
const emit = defineEmits<{ submit: [body: KeyBody] }>();

const form = reactive<KeyForm>(keyForm([], null));

// Each opening starts from the key being edited (or a new one) and the catalog as it is now.
watch(
  () => open.value,
  (isOpen) => {
    if (isOpen) Object.assign(form, keyForm(props.models, props.editing));
  },
  { immediate: true },
);

const MODES = ["closed", "limit", "unlimited"] as const;
</script>

<template>
  <el-dialog v-model="open" :title="props.editing ? 'Edit API key' : 'New API key'" width="min(640px, 94vw)">
    <el-form label-position="top" @submit.prevent="emit('submit', keyBody(form))">
      <p v-if="props.error" class="form-error" role="alert">{{ props.error.text }}</p>
      <el-form-item label="Label" :class="flagged('label')">
        <el-input v-model="form.label" aria-label="Label" maxlength="100" />
      </el-form-item>
      <el-form-item label="Max unfinished jobs (empty = default, 0 = unlimited)" :class="flagged('max_unfinished')">
        <el-input
          v-model="form.maxUnfinished"
          type="number"
          min="0"
          aria-label="Max unfinished jobs (empty = default, 0 = unlimited)"
        />
      </el-form-item>
      <el-form-item label="Allowed models" :class="flagged('models')">
        <el-checkbox v-model="form.any">Any model</el-checkbox>
        <el-checkbox-group v-model="form.models" :disabled="form.any" class="model-list">
          <el-checkbox v-for="m in props.models" :key="m.id" :value="m.id">
            {{ m.id }} <span class="muted">{{ m.owned_by }}</span>
          </el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <el-form-item label="Wallets (a wallet left closed refuses that kind)" :class="flagged('wallets')">
        <div class="wallets">
          <div class="wallet-row wallet-head" aria-hidden="true">
            <span>Wallet</span><span>Mode</span><span>Per day</span><span>Total</span>
          </div>
          <div v-for="w in form.wallets" :key="`${w.provider}/${w.kind}`" class="wallet-row" data-wallet>
            <span>{{ w.provider }} {{ w.kind }}</span>
            <el-select v-model="w.mode" :aria-label="`${w.provider} ${w.kind} mode`" size="small">
              <el-option v-for="m in MODES" :key="m" :label="m" :value="m" />
            </el-select>
            <el-input
              v-model="w.day"
              type="number"
              min="1"
              size="small"
              placeholder="per day"
              :disabled="w.mode !== 'limit'"
              :aria-label="`${w.provider} ${w.kind} per day`"
            />
            <el-input
              v-model="w.total"
              type="number"
              min="1"
              size="small"
              placeholder="total"
              :disabled="w.mode !== 'limit'"
              :aria-label="`${w.provider} ${w.kind} total`"
            />
          </div>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="open = false">Cancel</el-button>
      <el-button type="primary" :loading="props.busy" @click="emit('submit', keyBody(form))">{{
        props.editing ? "Save" : "Create"
      }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-error {
  margin: 0 0 12px;
  color: var(--rl-bad);
  font-size: 13px;
}
.model-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  width: 100%;
  max-height: 160px;
  overflow-y: auto;
}
.muted {
  color: var(--rl-muted);
  font-size: 12px;
}
.wallets {
  width: 100%;
}
.wallet-row {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1fr 1fr;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.wallet-head {
  color: var(--rl-muted);
  font-size: 12px;
}
</style>
