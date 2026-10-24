<script setup lang="ts">
import { computed } from "vue";
import type { JobDetail } from "@/api/jobs-detail";
import { fmtDayClock, plural } from "@/components/jobs/job-text";

import JobLedger from "./JobLedger.vue";

// The Technical tab's facts as JobDetailTabs draws them: one ledger, the label at 160 px and the value in mono; a fact
// whose value the server does not have is left out. Worded as drawn: `dola / video` (provider and pool), `dola video`
// (the wallet), `Studio Saigon · rk_live_3f9a` (label and prefix), `1 credit` (an account cost of 0 is no cost: left
// out), `video v4` (the frame version, tagged as the Frames page tags it), `delivered · 1 try` style callbacks: the
// answer's HTTP code, a delivery that did not go through says its state first.
const props = defineProps<{ job: JobDetail; phone?: boolean }>();

type Fact = [label: string, value: string];
const text = (v: unknown): string => (v === null || v === undefined || v === "" ? "" : String(v));
const words = (...parts: unknown[]): string => parts.map(text).filter(Boolean).join(" · ");

function callback(c: JobDetail["callback"]): string {
  if (!c.status) return "";
  const code = /^HTTP (\d{3})/.exec(text(c.last))?.[1];
  return words(c.status === "sent" ? "" : c.status, code, plural(c.attempts ?? 0, "attempt"));
}

const facts = computed(() => {
  const j = props.job;
  const stamp = (ms: number | null): string => (ms ? fmtDayClock(ms, true) : "");
  const all: Fact[] = [
    ["Job id", text(j.id)],
    ["Provider / pool", [j.provider, j.pool].filter(Boolean).join(" / ")],
    ["Wallet", j.wallet_kind ? `${j.provider} ${j.wallet_kind}` : ""],
    ["Key", words(j.key_label?.trim(), j.key_prefix)],
    ["Account", text(j.account_label)],
    ["External ref", text(j.external_ref)],
    ["Attempts / claims", `${j.attempts} / ${j.claims}`],
    ["Account cost", j.account_cost ? plural(j.account_cost, "credit") : ""],
    ["Upstream model", text(j.upstream_model)],
    ["Prompt frame", text(j.frame_id) && `${j.media_type} v${j.frame_id}`],
    ["Callback", callback(j.callback)],
    ["Upstream price", text(j.upstream_price)],
    ["Completion", text(j.completion)],
    ["Sent", stamp(j.sent_ms)],
    ["Updated", stamp(j.updated_ms)],
    ["Params", j.params === null || j.params === undefined ? "" : JSON.stringify(j.params)],
  ];
  return all.filter(([, value]) => value !== "").map(([label, value]) => ({ label, value, mono: true }));
});
</script>

<template>
  <JobLedger :items="facts" wide small :stacked="props.phone" testid="job-facts" />
</template>
