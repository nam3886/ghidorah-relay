<script setup lang="ts">
import { ref, watch } from "vue";
import { getSettingsHistory, type SettingChange } from "@/api/settings";
import SideDrawer from "@/components/shared/overlays/SideDrawer.vue";
import { errorText } from "@/lib/errors";
import { SHOWN_OFFSET_MIN } from "./settings-pools";

// The change history of the settings (Settings, "Change history"): newest first, one row per change: when (GMT+7),
// who, which key, and from what to what. "default" is no stored value; a secret setting says only
// "changed", the server never keeps its values. More reads the older ones.
const open = defineModel<boolean>({ required: true });
const rows = ref<SettingChange[]>([]);
const next = ref<number | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

async function read(more = false): Promise<void> {
  loading.value = true;
  try {
    const page = await getSettingsHistory(more ? (next.value ?? undefined) : undefined);
    rows.value = more ? [...rows.value, ...page.items] : page.items;
    next.value = page.next;
    error.value = null;
  } catch (err) {
    error.value = errorText(err);
  } finally {
    loading.value = false;
  }
}
watch(open, (isOpen) => {
  if (isOpen) void read();
});

const shown = (v: unknown): string =>
  v === null || v === undefined ? "default" : typeof v === "string" ? v : JSON.stringify(v);
// "24 Oct 14:02 GMT+7": the page tells every time on the GMT+7 clock (settings-pools)
const when = (ms: number): string =>
  `${new Date(ms + SHOWN_OFFSET_MIN * 60_000).toLocaleString("en-GB", {
    timeZone: "UTC",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })} GMT+7`;
</script>

<template>
  <SideDrawer v-model="open" title="Change history" testid="settings-history" width="min(560px, 100%)">
    <template #sub><span class="sh__sub">Who changed which setting, from what to what.</span></template>
    <p v-if="error" class="sh__bad" role="alert">Could not load the history: {{ error }}</p>
    <p v-else-if="!rows.length && !loading" class="sh__sub">No setting has been changed yet.</p>
    <ul v-else class="sh__list">
      <li v-for="r in rows" :key="r.id" class="sh__row">
        <span class="sh__meta num"
          >{{ when(r.at_ms) }} · <b>{{ r.by ?? "the relay" }}</b></span
        >
        <span class="mono sh__key">{{ r.key }}</span>
        <span v-if="r.secret" class="sh__change">changed</span>
        <span v-else class="mono sh__change"
          ><span class="sh__old">{{ shown(r.old) }}</span> → <span>{{ shown(r.new) }}</span></span
        >
      </li>
    </ul>
    <button v-if="next !== null" type="button" class="sh__more" :disabled="loading" @click="read(true)">
      Older changes
    </button>
  </SideDrawer>
</template>

<style scoped>
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}
.sh__sub {
  font-size: 13px;
  line-height: 19px;
  color: var(--rl-faint);
}
.sh__bad {
  margin: 0;
  color: var(--rl-bad);
  font-size: 13px;
}
.sh__list {
  margin: 0;
  padding: 0;
  overflow: hidden;
  list-style: none;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.sh__row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 14px;
  border-top: 1px solid var(--rl-rule);
}
.sh__row:first-child {
  border-top: 0;
}
.sh__meta {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.sh__meta b {
  color: var(--rl-fg);
  font-weight: 600;
}
.sh__key {
  font-size: 13px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.sh__change {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-body);
  overflow-wrap: anywhere;
}
.sh__old {
  color: var(--rl-muted);
  text-decoration: line-through;
}
.sh__more {
  align-self: flex-start;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--rl-line);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
</style>
