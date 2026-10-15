<script setup lang="ts">
import { reactive, watch } from "vue";
import type { ApiKey, KeyBody } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import { fmtTime } from "@/lib/format";
import { type CreateError, keyBody, keyForm, type KeyForm, unfinishedText } from "./key-model";

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

// The form keeps numbers as text (an empty box and a 0 differ); the number field speaks number or nothing.
const asNumber = (text: string): number | undefined => (text === "" ? undefined : Number(text));
const asText = (value: number | null | undefined): string =>
  value === null || value === undefined ? "" : String(value);

const MODES = ["closed", "limit", "unlimited"] as const;
</script>

<template>
  <el-dialog v-model="open" :title="props.editing ? 'Edit API key' : 'New API key'" width="min(640px, 94vw)">
    <el-form label-position="top" @submit.prevent="emit('submit', keyBody(form))">
      <el-descriptions v-if="props.editing" :column="2" border class="key-facts" aria-label="Key details">
        <el-descriptions-item label="Prefix"
          ><span class="mono">{{ props.editing.prefix }}</span></el-descriptions-item
        >
        <el-descriptions-item label="Created">{{ fmtTime(props.editing.created_ms) }}</el-descriptions-item>
        <el-descriptions-item label="Jobs total">{{ props.editing.jobs }}</el-descriptions-item>
        <el-descriptions-item label="Unfinished / max">{{ unfinishedText(props.editing) }}</el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-if="props.error"
        type="error"
        show-icon
        :closable="false"
        :title="props.error.text"
        class="form-error"
      />
      <el-form-item label="Label" :class="flagged('label')">
        <el-input v-model="form.label" aria-label="Label" maxlength="100" />
      </el-form-item>
      <el-form-item label="Max unfinished jobs (empty = default, 0 = unlimited)" :class="flagged('max_unfinished')">
        <el-input-number
          :model-value="asNumber(form.maxUnfinished)"
          :min="0"
          :precision="0"
          controls-position="right"
          aria-label="Max unfinished jobs (empty = default, 0 = unlimited)"
          @update:model-value="(v) => (form.maxUnfinished = asText(v))"
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
            <el-select v-model="w.mode" :aria-label="`${w.provider} ${w.kind} mode`">
              <el-option v-for="m in MODES" :key="m" :label="m" :value="m" />
            </el-select>
            <el-input-number
              :model-value="asNumber(w.day)"
              :min="1"
              :precision="0"
              :controls="false"
              placeholder="per day"
              :disabled="w.mode !== 'limit'"
              :aria-label="`${w.provider} ${w.kind} per day`"
              @update:model-value="(v) => (w.day = asText(v))"
            />
            <el-input-number
              :model-value="asNumber(w.total)"
              :min="1"
              :precision="0"
              :controls="false"
              placeholder="total"
              :disabled="w.mode !== 'limit'"
              :aria-label="`${w.provider} ${w.kind} total`"
              @update:model-value="(v) => (w.total = asText(v))"
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
.form-error,
.key-facts {
  margin: 0 0 12px;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
.wallet-row :deep(.el-input-number) {
  width: 100%;
}
@media (max-width: 520px) {
  .wallet-row {
    grid-template-columns: 1fr 1fr;
  }
  .wallet-head {
    display: none;
  }
}
.model-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  width: 100%;
  max-height: 160px;
  overflow-y: auto;
}
.muted {
  margin-left: 6px;
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
