<script setup lang="ts">
import { computed } from "vue";
import type { RawExchange } from "@/api/jobs-detail";
import IconButton from "@/components/shared/IconButton.vue";

// One raw exchange of the drawer (JobDetail: Raw request, Raw response): the heading, the route and status in mono,
// Copy, then the JSON body pretty-printed and coloured by token (keys brand, strings green, numbers orange, literals
// grey). The server has redacted it; it is drawn as text. A job with nothing stored says so in the box.
const props = defineProps<{ title: string; raw: RawExchange | null | undefined; empty: string }>();
const emit = defineEmits<{ copy: [text: string] }>();

const text = computed(() => (props.raw ? JSON.stringify(props.raw.body, null, 2) : ""));
const where = computed(() =>
  props.raw?.route ? [props.raw.route, props.raw.status_code].filter((v) => v !== null).join(" · ") : "",
);

type Tok = { text: string; kind: string };
const TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(null|true|false)|([{}[\],:])|(\s+)|(.)/g;
const lines = computed<Tok[][]>(() =>
  text.value.split("\n").map((line) => {
    const out: Tok[] = [];
    for (const m of line.matchAll(TOKEN)) {
      if (m[1] && m[2]) out.push({ text: m[1], kind: "key" }, { text: m[2], kind: "pun" });
      else if (m[1]) out.push({ text: m[1], kind: "str" });
      else if (m[3]) out.push({ text: m[3], kind: "num" });
      else if (m[4]) out.push({ text: m[4], kind: "lit" });
      else if (m[5]) out.push({ text: m[5], kind: "pun" });
      else out.push({ text: m[0], kind: "" });
    }
    return out;
  }),
);
</script>

<template>
  <div class="jr">
    <div class="jr__head">
      <h3 class="jr__h">{{ title }}</h3>
      <span v-if="where" class="mono jr__where">{{ where }}</span>
      <IconButton
        v-if="raw"
        icon="copy"
        :label="`Copy ${title.toLowerCase()}`"
        placement="top-end"
        @click="emit('copy', text)"
      />
    </div>
    <pre v-if="raw" class="mono jr__code"><div v-for="(l, i) in lines" :key="i"><span
      v-for="(t, j) in l" :key="j" :class="t.kind && `t-${t.kind}`">{{ t.text }}</span></div></pre>
    <p v-else class="jr__empty">{{ empty }}</p>
  </div>
</template>

<style scoped>
.mono {
  font-family: var(--rl-mono);
}
.jr {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.jr__head {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.jr__h {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
}
.jr__where {
  min-width: 0;
  overflow: hidden;
  color: var(--rl-faint);
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.jr__code {
  margin: 0;
  max-height: 220px;
  padding: 12px 14px;
  overflow: auto;
  border: 1px solid var(--rl-line-strong);
  border-radius: 12px;
  background: var(--rl-code);
  color: var(--rl-body);
  font-size: 12px;
  line-height: 20px;
  white-space: pre;
}
.jr__empty {
  margin: 0;
  padding: 12px 14px;
  border: 1px dashed var(--rl-line-strong);
  border-radius: 12px;
  color: var(--rl-faint);
  font-size: 13px;
}
.t-key {
  color: var(--rl-brand-ink);
}
.t-str {
  color: var(--rl-ok);
}
.t-num {
  color: var(--p-gemini-ink);
}
.t-lit {
  color: var(--rl-neutral);
}
.t-pun {
  color: var(--rl-faint);
}
</style>
