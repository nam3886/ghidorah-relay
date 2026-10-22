<script setup lang="ts">
import { computed } from "vue";
import type { JobDetail } from "@/api/jobs-detail";
import { plural } from "@/components/jobs/job-text";
import { fmtTime } from "@/lib/format";

// The Technical tab's facts as the canvas draws them: a plain two-column grid (a single list on a phone), a fact
// whose value the server does not have is left out. The long or rarely used facts follow under "Diagnostics".
// Worded as drawn: `dola image` (the wallet is the provider's, of one kind), `Shop A · rk_…` (label and prefix),
// `2 credits` (an account cost of 0 is no cost: left out), `image v2` (the frame version, tagged as the Frames page
// tags it), `200 · 1 attempt` (the answer's HTTP code; a delivery that did not go through says its state first).
const props = defineProps<{ job: JobDetail; phone: boolean }>();

type Fact = [label: string, value: string];
const text = (v: unknown): string => (v === null || v === undefined || v === "" ? "" : String(v));
const kept = (facts: Fact[]): Fact[] => facts.filter(([, value]) => value !== "");
const words = (...parts: unknown[]): string => parts.map(text).filter(Boolean).join(" · ");

function callback(c: JobDetail["callback"]): string {
  if (!c.status) return "";
  const code = /^HTTP (\d{3})/.exec(text(c.last))?.[1];
  return words(c.status === "sent" ? "" : c.status, code, plural(c.attempts ?? 0, "attempt"));
}

const facts = computed<Fact[]>(() => {
  const j = props.job;
  return kept([
    ["Job id", text(j.id)],
    ["Provider / pool", [j.provider, j.pool].filter(Boolean).join(" / ")],
    ["Wallet", j.wallet_kind ? `${j.provider} ${j.wallet_kind}` : ""],
    ...(props.phone
      ? ([
          ["Key", words(j.key_label?.trim(), j.key_prefix)],
          ["Account", text(j.account_label)],
        ] as Fact[])
      : []),
    ["External ref", text(j.external_ref)],
    ["Attempts / claims", `${j.attempts} / ${j.claims}`],
    ["Account cost", j.account_cost ? plural(j.account_cost, "credit") : ""],
    ["Upstream model", text(j.upstream_model)],
    ["Prompt frame", text(j.frame_id) && `${j.media_type} v${j.frame_id}`],
    ["Callback", callback(j.callback)],
  ]);
});

const diagnostics = computed<Fact[]>(() => {
  const j = props.job;
  const stamp = (ms: number | null): string => (ms ? fmtTime(ms) : "");
  return kept([
    ["Upstream price", text(j.upstream_price)],
    ["Completion", text(j.completion)],
    ["Sent", stamp(j.sent_ms)],
    ["Updated", stamp(j.updated_ms)],
    ["Params", j.params === null || j.params === undefined ? "" : JSON.stringify(j.params)],
  ]);
});
</script>

<template>
  <div class="tech">
    <dl class="tech__grid" :class="{ 'is-phone': props.phone }" data-testid="job-facts">
      <div v-for="[label, value] in facts" :key="label" class="tech__fact">
        <dt>{{ label }}</dt>
        <dd>{{ value }}</dd>
      </div>
    </dl>
    <template v-if="diagnostics.length">
      <h3 class="tech__title">Diagnostics</h3>
      <dl class="tech__grid" :class="{ 'is-phone': props.phone }" data-testid="job-diagnostics">
        <div v-for="[label, value] in diagnostics" :key="label" class="tech__fact">
          <dt>{{ label }}</dt>
          <dd>{{ value }}</dd>
        </div>
      </dl>
    </template>
  </div>
</template>

<style scoped>
.tech {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.tech__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}
.tech__grid {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
  font-size: 12px;
}
.tech__grid.is-phone {
  display: flex;
  flex-direction: column;
  gap: 0;
  font-size: 13px;
}
.tech__fact {
  min-width: 0;
}
.is-phone .tech__fact {
  padding: 10px 0;
  border-bottom: 1px solid var(--rl-line);
}
.tech__fact dt {
  color: var(--rl-muted);
}
.is-phone .tech__fact dt {
  font-size: 12px;
}
.tech__fact dd {
  margin: 2px 0 0;
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  overflow-wrap: anywhere;
}
</style>
