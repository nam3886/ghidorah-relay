<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import type { LogDetail } from "@/api/logs";
import { copyText } from "@/components/shared/clipboard";
import DetailDrawer from "@/components/shared/DetailDrawer.vue";
import { IconCopy } from "@/components/shared/icons";
import StatusChip from "@/components/shared/StatusChip.vue";
import { notify } from "@/lib/notify";
import "./lane-controls.css";
import { dateClock, logKey, thousands } from "./log-window";

// One logged request in the right-hand drawer: the facts as a plain ledger, then the redacted request and the answer,
// each with Copy. The job id is a link: it asks the view to close this drawer and open the job. Everything the server
// sent reaches the page as text.
const open = defineModel<boolean>({ required: true });
const props = defineProps<{ log: LogDetail | null }>();
const emit = defineEmits<{ "open-job": [id: string] }>();

const title = computed(() => `Request ${props.log?.id ?? ""}`.trim());

// A body is shown as the server sent it when it is already text, otherwise as indented JSON.
function bodyText(value: unknown): string {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}
const requestText = computed(() => bodyText(props.log?.request));
const answerText = computed(() => (props.log?.response == null ? null : bodyText(props.log.response)));

const requestPre = ref<HTMLElement | null>(null);
const answerPre = ref<HTMLElement | null>(null);
const copied = ref<"request" | "answer" | null>(null);
let timer: ReturnType<typeof setTimeout> | undefined;
// aborted when the drawer goes: a copy still pending then must not touch the clipboard fallback
const abort = new AbortController();
onBeforeUnmount(() => {
  abort.abort();
  clearTimeout(timer);
});

async function copy(which: "request" | "answer"): Promise<void> {
  const text = which === "request" ? requestText.value : (answerText.value ?? "");
  const from = which === "request" ? requestPre : answerPre;
  const outcome = await copyText(text, () => from.value, abort.signal);
  if (outcome === "aborted") return;
  if (outcome === "select") notify.info("Press Ctrl+C to copy the selected text");
  else if (outcome === "failed") notify.error("Could not copy: select the text and copy it by hand");
  else {
    copied.value = which;
    clearTimeout(timer);
    timer = setTimeout(() => (copied.value = null), 2000);
  }
}
</script>

<template>
  <DetailDrawer v-model="open" :title="title" testid="request-drawer" back-label="Back to Request logs">
    <template v-if="log" #chips>
      <div class="rd__chips">
        <StatusChip :status="log.status_code" class="rd__pill" />
        <span class="rd__route">{{ log.route }}</span>
      </div>
    </template>
    <template v-if="log">
      <dl class="rd__ledger" data-testid="request-facts">
        <div class="rd__row">
          <dt>Time</dt>
          <span class="rd__leader" aria-hidden="true" />
          <dd class="kla-mono">{{ dateClock(log.at_ms) }}</dd>
        </div>
        <div class="rd__row">
          <dt>Provider</dt>
          <span class="rd__leader" aria-hidden="true" />
          <dd>{{ log.provider || "—" }}</dd>
        </div>
        <div class="rd__row">
          <dt>Key</dt>
          <span class="rd__leader" aria-hidden="true" />
          <dd>{{ logKey(log) }}</dd>
        </div>
        <div class="rd__row">
          <dt>Job</dt>
          <span class="rd__leader" aria-hidden="true" />
          <dd v-if="log.job_id">
            <button type="button" class="rd__link kla-mono" @click="emit('open-job', log.job_id)">
              {{ log.job_id.slice(0, 8) }}
            </button>
          </dd>
          <dd v-else>no job</dd>
        </div>
        <!-- red on any error, a client error too (the canvas's 422): the list keeps red for a server error only -->
        <div v-if="log.error_code" class="rd__row">
          <dt>Error</dt>
          <span class="rd__leader" aria-hidden="true" />
          <dd class="kla-mono rd__bad">{{ log.error_code }}</dd>
        </div>
        <div class="rd__row">
          <dt>Duration</dt>
          <span class="rd__leader" aria-hidden="true" />
          <dd class="kla-mono">{{ thousands(log.duration_ms) }} ms</dd>
        </div>
      </dl>

      <section class="rd__block">
        <div class="rd__block-head">
          <h3>Request</h3>
          <button type="button" class="kla-btn kla-btn--tiny" aria-label="Copy Request body" @click="copy('request')">
            <IconCopy :size="12" />{{ copied === "request" ? "Copied" : "Copy" }}
          </button>
        </div>
        <pre ref="requestPre" class="rd__pre" tabindex="0">{{ requestText }}</pre>
      </section>
      <section class="rd__block">
        <div class="rd__block-head">
          <h3>Answer</h3>
          <button
            v-if="answerText !== null"
            type="button"
            class="kla-btn kla-btn--tiny"
            aria-label="Copy Answer body"
            @click="copy('answer')"
          >
            <IconCopy :size="12" />{{ copied === "answer" ? "Copied" : "Copy" }}
          </button>
        </div>
        <pre v-if="answerText !== null" ref="answerPre" class="rd__pre" tabindex="0">{{ answerText }}</pre>
        <p v-else class="rd__none">(none kept)</p>
      </section>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.rd__chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding-bottom: 16px;
}
.rd__pill {
  font-variant-numeric: tabular-nums;
}
/* mono without tabular figures, as drawn */
.rd__route {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
/* the drawer's body spaces its blocks (20) */
.rd__ledger {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  font-size: 13px;
}
.rd__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.rd__row dt {
  color: var(--rl-muted);
  white-space: nowrap;
}
.rd__leader {
  flex: 1;
  min-width: 8px;
  border-bottom: 1px dotted var(--rl-line-strong);
  transform: translateY(-4px);
}
.rd__row dd {
  margin: 0;
  color: var(--rl-fg);
  font-weight: 400;
  text-align: right;
  overflow-wrap: anywhere;
  font-variant-numeric: tabular-nums;
}
.rd__row dd.rd__bad {
  color: var(--rl-bad);
}
/* a plain link, as drawn: the link colour, underlined, on the line of the row; the hover colour in light (the canvas's
   `a:hover`), none in dark */
.rd__link {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--rl-link, var(--rl-primary));
  font-size: 13px;
  line-height: inherit;
  font-variant-numeric: tabular-nums;
  text-decoration: underline;
  cursor: pointer;
}
html:not(.dark) .rd__link:hover {
  color: var(--rl-primary-hover);
}
.rd__block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rd__block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.rd__block-head h3 {
  margin: 0;
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
}
.rd__pre {
  margin: 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-raised);
  font-family: var(--rl-mono);
  font-size: 12px;
  line-height: 20px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  overflow-x: auto;
}
/* a phone: every action has a 44 px hit area (the Copy buttons are drawn that high; the job link keeps its look and
   gets the height from padding that negative margins give back) */
@media (max-width: 767px) {
  /* the sheet's own chip row ends 4 px above its hairline: 16 in all, as on the desktop */
  .rd__chips {
    padding-bottom: 12px;
  }
  .rd__block-head .kla-btn--tiny {
    height: 44px;
    padding: 0 12px;
    border-radius: 10px;
    font-size: 13px;
  }
  .rd__link {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    margin: -14px 0;
  }
}
.rd__none {
  margin: 0;
  color: var(--rl-muted);
}
</style>
