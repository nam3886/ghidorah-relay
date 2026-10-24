<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import type { LogDetail } from "@/api/logs";
import { copyText } from "@/components/shared/clipboard";
import IconButton from "@/components/shared/IconButton.vue";
import SideDrawer from "@/components/shared/overlays/SideDrawer.vue";
import { notify } from "@/lib/notify";
import { durationText, errorTone, fullTime, logKey, splitRoute, statusTone } from "./log-window";
import { highlight } from "./log-json";

// One logged request in the right-hand drawer (LogDetail.dc.html): the title is the prompt it carried, else its job's
// title; under it the status pill and the route; then the facts as a two-column ledger, the redacted request and the
// answer, each with a Copy icon, and the upstream exchange. The job id is a link: it asks the view to close this drawer
// and open the job. Everything the server sent reaches the page as text. The API keeps no headers, no client address
// and no upstream exchange yet: those parts say so.
const open = defineModel<boolean>({ required: true });
const props = defineProps<{ log: LogDetail | null }>();
const emit = defineEmits<{ "open-job": [id: string] }>();

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
const requestLines = computed(() => highlight(requestText.value));
const answerLines = computed(() => highlight(answerText.value ?? ""));

const prompt = computed(() => {
  const body = props.log?.request;
  const p = body && typeof body === "object" ? (body as Record<string, unknown>).prompt : undefined;
  return typeof p === "string" && p.trim() ? p.trim() : "";
});
const title = computed(() => prompt.value || props.log?.job_title || `Request ${props.log?.id ?? ""}`.trim());
const route = computed(() => splitRoute(props.log?.route ?? ""));
const keyText = computed(() => {
  const l = props.log;
  if (!l) return "";
  return l.key_id && l.key_prefix && logKey(l) !== l.key_prefix ? `${logKey(l)} · ${l.key_prefix}` : logKey(l);
});

const part = ref<"headers" | "body">("body");
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
    notify.success(`Copied the ${which === "request" ? "request" : "answer"} body`);
    clearTimeout(timer);
    timer = setTimeout(() => (copied.value = null), 2000);
  }
}
</script>

<template>
  <SideDrawer
    v-model="open"
    :title="title"
    testid="request-drawer"
    back-label="Back to Request logs"
    @back="open = false"
  >
    <template v-if="log" #sub>
      <span class="rd__pill num" :class="`tone-${statusTone(log.status_code)}`">{{ log.status_code }}</span>
      <span class="rd__route mono"
        ><span v-if="route.method" class="rd__method" :class="{ post: route.method === 'POST' }">{{
          route.method
        }}</span>
        {{ route.path }}</span
      >
    </template>
    <template v-if="log">
      <dl class="rd__ledger" data-testid="request-facts">
        <div class="rd__row">
          <dt>Time</dt>
          <dd class="mono num">{{ fullTime(log.at_ms) }}</dd>
        </div>
        <div class="rd__row">
          <dt>Duration</dt>
          <dd class="num">{{ durationText(log.duration_ms) }}</dd>
        </div>
        <div class="rd__row">
          <dt>Provider</dt>
          <dd>{{ log.provider || "—" }}</dd>
        </div>
        <div class="rd__row">
          <dt>Key</dt>
          <dd>{{ keyText }}</dd>
        </div>
        <div class="rd__row">
          <dt>Job</dt>
          <dd v-if="log.job_id">
            <button type="button" class="rd__link mono" @click="emit('open-job', log.job_id)">
              {{ log.job_id.slice(0, 8) }}
            </button>
          </dd>
          <dd v-else class="faint">no job</dd>
        </div>
        <div v-if="log.error_code" class="rd__row">
          <dt>Error</dt>
          <dd class="mono" :class="`tone-${errorTone(log.status_code)}`">{{ log.error_code }}</dd>
        </div>
      </dl>

      <section class="rd__block" aria-labelledby="lg-req">
        <div class="rd__block-head">
          <h3 id="lg-req">Request</h3>
          <div class="rd__seg" role="tablist" aria-label="Request parts">
            <button type="button" role="tab" :aria-selected="part === 'headers'" @click="part = 'headers'">
              Headers
            </button>
            <button type="button" role="tab" :aria-selected="part === 'body'" @click="part = 'body'">Body</button>
          </div>
          <IconButton
            v-if="part === 'body'"
            :icon="copied === 'request' ? 'check' : 'copy'"
            label="Copy request body"
            @click="copy('request')"
          />
        </div>
        <pre
          v-if="part === 'body'"
          ref="requestPre"
          class="rd__pre mono"
          tabindex="0"
        ><template v-for="(line, i) in requestLines" :key="i"><span v-for="(p, j) in line" :key="j" :class="p.kind">{{ p.text }}</span>{{ "\n" }}</template></pre>
        <p v-else class="rd__none">The API does not keep request headers yet.</p>
      </section>

      <section class="rd__block" aria-labelledby="lg-ans">
        <div class="rd__block-head">
          <h3 id="lg-ans">Answer</h3>
          <span class="rd__help num mono">{{ log.status_code }} · {{ durationText(log.duration_ms) }}</span>
          <IconButton
            v-if="answerText !== null"
            :icon="copied === 'answer' ? 'check' : 'copy'"
            label="Copy answer body"
            @click="copy('answer')"
          />
        </div>
        <pre
          v-if="answerText !== null"
          ref="answerPre"
          class="rd__pre mono"
          tabindex="0"
        ><template v-for="(line, i) in answerLines" :key="i"><span v-for="(p, j) in line" :key="j" :class="p.kind">{{ p.text }}</span>{{ "\n" }}</template></pre>
        <p v-else class="rd__none">(none kept)</p>
      </section>

      <section class="rd__block" aria-labelledby="lg-up">
        <h3 id="lg-up">Upstream exchange</h3>
        <p v-if="!log.job_id" class="rd__none">
          (none kept) The request ended at the relay: no job was made, so nothing reached
          {{ log.provider || "a provider" }}.
        </p>
        <p v-else class="rd__none">The API does not keep the upstream exchange yet.</p>
      </section>
    </template>
  </SideDrawer>
</template>

<style scoped>
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}
.faint {
  color: var(--rl-faint);
}
/* under the title: the pill and the route */
.rd__pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-width: 44px;
  height: 24px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}
.rd__pill.tone-ok {
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.rd__pill.tone-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.rd__pill.tone-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.rd__route {
  font-size: 13px;
  overflow-wrap: anywhere;
}
.rd__method {
  color: var(--rl-muted);
  font-weight: 500;
}
.rd__method.post {
  color: var(--rl-brand-ink);
}

/* the facts: two columns of cells ruled on the grid */
.rd__ledger {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.rd__row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  margin: -1px 0 0 -1px;
  padding: 10px 14px;
  border-top: 1px solid var(--rl-rule);
  border-left: 1px solid var(--rl-rule);
}
.rd__row dt {
  font-size: 12px;
  color: var(--rl-faint);
}
.rd__row dd {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
.rd__row dd.faint {
  color: var(--rl-faint);
}
.rd__row dd.tone-warn {
  color: var(--rl-warn);
}
.rd__row dd.tone-bad {
  color: var(--rl-bad);
}
/* a plain link: the link colour, underlined, on the line of the row */
.rd__link {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--rl-link);
  font-size: 13px;
  font-weight: 600;
  line-height: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.rd__link:hover {
  color: var(--rl-link-hover);
}
.rd__link:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}

.rd__block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rd__block h3 {
  margin: 0;
  font-size: 14px;
  line-height: 20px;
  font-weight: 700;
}
.rd__block-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rd__block-head .ib-wrap:last-child {
  margin-left: auto;
}
.rd__help {
  font-size: 12px;
  color: var(--rl-faint);
}
.rd__seg {
  display: flex;
  gap: 2px;
  margin-left: 6px;
  padding: 2px;
  border-radius: 8px;
  background: var(--rl-track);
}
.rd__seg button {
  height: 26px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.rd__seg button[aria-selected="true"] {
  background: var(--rl-seg-on);
  color: var(--rl-fg);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.08);
}
.rd__seg button:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}

/* the code block: JSON in its own colours, on the code fill */
.rd__pre {
  margin: 0;
  max-height: 300px;
  padding: 12px 14px;
  overflow: hidden;
  border: 1px solid var(--rl-line-strong);
  border-radius: 12px;
  background: var(--rl-code);
  color: var(--rl-body);
  font-size: 12px;
  line-height: 20px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  --rl-json-num: #c2410c;
}
:global(html.dark) .rd__pre {
  --rl-json-num: #fdba74;
}
.rd__pre .key {
  color: var(--rl-brand-ink);
}
.rd__pre .str {
  color: var(--rl-ok);
}
.rd__pre .num {
  color: var(--rl-json-num);
}
.rd__pre .lit {
  color: var(--rl-neutral);
}
.rd__pre .pun {
  color: var(--rl-faint);
}
.rd__none {
  margin: 0;
  padding: 12px 14px;
  border: 1px dashed var(--rl-line-strong);
  border-radius: 12px;
  font-size: 13px;
  color: var(--rl-faint);
}
/* a phone: the Copy icons are the touch size and the job link has a 44 px hit area (negative margins give the
   height back) */
@media (max-width: 767px) {
  .rd__block-head :deep(.ib) {
    width: 44px;
    height: 44px;
    border-radius: 10px;
  }
  .rd__link {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    margin: -14px 0;
  }
}
</style>

<!-- the drawer's title is the prompt as drawn: 18/26, 800 -->
<style>
[data-testid="request-drawer"] .rl-drawer__title {
  font-weight: 800;
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
