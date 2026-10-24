<script setup lang="ts">
import { computed, onMounted, reactive, ref, shallowRef } from "vue";
import { type AlertRule, changeAlertRule, listAlertRules } from "@/api/alerts";
import { putSetting } from "@/api/settings";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import { useBusy } from "@/composables/use-busy";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { MUTE_MINUTES, mutedText, SEVERITY_CARD, severityOf } from "./alerts-model";

// Alert rules (the head's sliders button): every kind the relay raises, stored groups first, then the live conditions.
// Each can be turned off (left out of the list and the counts; its rows are still stored) or muted for an hour (listed
// with its end, never counted). A threshold that is a setting is edited here through the Settings route; a figure
// fixed in code is only shown. Not a rule engine: what raises each kind does not change.
const emit = defineEmits<{ changed: [] }>();
const { run } = useBusy();
const rules = shallowRef<AlertRule[]>([]);
const error = ref("");
const writing = ref(false);
// the threshold being typed, per setting key
const typed = reactive<Record<string, string>>({});

const sections = computed(() => [
  {
    title: "Stored alerts",
    note: "Rows kept until acknowledged",
    items: rules.value.filter((r) => r.source === "stored"),
  },
  {
    title: "Live conditions",
    note: "Computed now, clear by themselves",
    items: rules.value.filter((r) => r.source === "live"),
  },
]);

async function load(): Promise<void> {
  try {
    rules.value = (await listAlertRules()).items;
    for (const r of rules.value) for (const t of r.thresholds) typed[t.setting] = String(t.value);
    error.value = "";
  } catch (err) {
    error.value = `Could not load the rules: ${errorText(err)}`;
  }
}
onMounted(load);

async function write(action: () => Promise<unknown>, said: string): Promise<void> {
  await run(async () => {
    writing.value = true;
    try {
      await action();
      // the page reloads at once: the drawer may be closed (and this panel gone) before its own read returns
      emit("changed");
      notify.success(said);
      await load();
    } finally {
      writing.value = false;
    }
  });
}

const toggle = (r: AlertRule, on: boolean) =>
  write(() => changeAlertRule(r.source, r.kind, { enabled: on }), `${r.kind} turned ${on ? "on" : "off"}`);
const mute = (r: AlertRule) =>
  write(() => changeAlertRule(r.source, r.kind, { mute_minutes: MUTE_MINUTES }), `${r.kind} muted for 1 h`);
const unmute = (r: AlertRule) =>
  write(() => changeAlertRule(r.source, r.kind, { mute_minutes: 0 }), `${r.kind} unmuted`);

const changedValue = (key: string, value: unknown): boolean => typed[key] !== String(value);
function saveThreshold(key: string): void {
  const text = (typed[key] ?? "").trim();
  const n = Number(text);
  if (text === "" || !Number.isFinite(n)) {
    notify.error(`${key}: a number is needed`);
    return;
  }
  void write(() => putSetting(key, n), `${key} set to ${n}`);
}
</script>

<template>
  <div class="ar" data-testid="alert-rules">
    <p v-if="error" class="ar-error" role="alert">{{ error }}</p>
    <p v-else-if="!rules.length" class="ar-help" role="status">Loading the rules…</p>
    <section v-for="s in sections" v-show="s.items.length" :key="s.title" class="ar-sec" :aria-label="s.title">
      <h3>
        {{ s.title }} <span class="ar-help">{{ s.note }}</span>
      </h3>
      <div
        v-for="r in s.items"
        :key="`${r.source}/${r.kind}`"
        class="ar-row"
        :class="{ 'is-off': !r.enabled }"
        data-testid="alert-rule"
      >
        <div class="ar-row__head">
          <span class="sev" :class="`tone-${SEVERITY_CARD[severityOf(r.severity)].tone}`">{{
            SEVERITY_CARD[severityOf(r.severity)].word
          }}</span>
          <b class="mono ar-kind">{{ r.kind }}</b>
          <ToggleSwitch
            class="ar-switch"
            :model-value="r.enabled"
            :label="`${r.kind} on`"
            :disabled="writing || r.money"
            :title="r.money ? 'A money alert is never turned off' : undefined"
            @change="(on: boolean) => toggle(r, on)"
          />
        </div>
        <span class="ar-text">{{ r.text }}</span>
        <span v-if="r.money" class="ar-help" data-testid="alert-rule-money"
          >Money alert: always on; a mute lasts at most a day and the banner still counts it.</span
        >
        <div v-for="t in r.thresholds" :key="t.setting" class="ar-threshold">
          <label :for="`ar-${t.setting}`" class="mono ar-help">{{ t.setting }}</label>
          <input :id="`ar-${t.setting}`" v-model="typed[t.setting]" class="ar-input num" inputmode="decimal" />
          <button
            type="button"
            class="ar-btn"
            :disabled="writing || !changedValue(t.setting, t.value)"
            @click="saveThreshold(t.setting)"
          >
            Save
          </button>
        </div>
        <span v-if="!r.thresholds.length" class="ar-help">Threshold: {{ r.fixed ?? "when it happens" }}</span>
        <div class="ar-row__mute">
          <template v-if="r.muted_until_ms">
            <span class="state" data-testid="alert-rule-muted">{{ mutedText(r.muted_until_ms) }}</span>
            <button type="button" class="ar-btn ar-btn--ghost" :disabled="writing" @click="unmute(r)">Unmute</button>
          </template>
          <button v-else type="button" class="ar-btn ar-btn--ghost" :disabled="writing || !r.enabled" @click="mute(r)">
            Mute 1 h
          </button>
          <span v-if="r.updated_by" class="ar-help">changed by {{ r.updated_by }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ar {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.ar-sec {
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  border: 1px solid var(--rl-rule);
  overflow: hidden;
}
.ar-sec h3 {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  margin: 0;
  padding: 10px 14px;
  font-size: 14px;
  font-weight: 700;
  background: var(--rl-head);
  border-bottom: 1px solid var(--rl-rule);
}
.ar-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border-top: 1px solid var(--rl-rule);
}
.ar-sec h3 + .ar-row {
  border-top: 0;
}
.ar-row.is-off .ar-text,
.ar-row.is-off .ar-kind {
  opacity: 0.55;
}
.ar-row__head,
.ar-row__mute,
.ar-threshold {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.ar-switch {
  margin-left: auto;
}
.ar-kind {
  font-size: 13px;
}
.ar-text {
  font-size: 13px;
  line-height: 19px;
  color: var(--rl-body);
}
.ar-help {
  font-size: 12px;
  font-weight: 500;
  color: var(--rl-faint);
}
.ar-error {
  margin: 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
  font-size: 13px;
}
.ar-input {
  width: 88px;
  height: 32px;
  box-sizing: border-box;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--rl-line);
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
}
.ar-btn {
  height: 32px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--rl-line);
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}
.ar-btn--ghost {
  border-color: transparent;
  background: transparent;
  color: var(--rl-muted);
  font-weight: 600;
}
.ar-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.sev,
.state {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.state {
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.tone-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.tone-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.tone-info {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
</style>
