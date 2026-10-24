<script setup lang="ts">
import { computed, ref } from "vue";
import {
  fetchTitle,
  type JobDetail,
  recheckJob,
  recoverJob,
  regenerateJob,
  resendWebhook,
  revokeLinks,
} from "@/api/jobs-detail";
import { announceJobChange } from "@/components/jobs/job-changes";
import { shortId } from "@/components/jobs/job-text";
import RegenerateControl from "@/components/jobs/RegenerateControl.vue";
import IconButton from "@/components/shared/IconButton.vue";
import { useBusy } from "@/composables/use-busy";
import { useCommandPoll } from "@/composables/use-command-poll";
import { notify } from "@/lib/notify";
import { jobAction } from "@/lib/operation-key";

// The buttons of one job. Each shows only when the job allows it (the same rules as the old admin). `reload` asks the
// drawer to read the job again; `announceJobChange` tells the list. The drawer places them in three zones (JobDetail,
// JobDetailTabs, JobDetailPhone): `row`, the Overview's action row (Re-check job · R as an icon, Regenerate… in words;
// the drawer's own Download and Copy share link go in the `before` and `after` slots); `footer`, the phone's foot
// (Regenerate… beside the drawer's Download all); `more`, the Technical tab's More actions (Recover from an account,
// Resend webhook, Revoke share links behind a confirm, Fetch title). A zone only moves a button; the rules are the same.
// `withRegenerate: false` leaves Regenerate… out of the row (the phone has it at its foot).
const props = withDefaults(
  defineProps<{ job: JobDetail; zone: "row" | "footer" | "more"; withRegenerate?: boolean }>(),
  {
    withRegenerate: true,
  },
);
const emit = defineEmits<{ reload: [] }>();

const { busy, run } = useBusy();
const { poll } = useCommandPoll();
const account = ref("");

const finished = computed(() => props.job.status === "done" || props.job.status === "failed");
const hasLinks = computed(() => props.job.media.some((m) => m.url));
const canFetchTitle = computed(() => Boolean(props.job.external_ref && props.job.account_label));
// the More actions block is drawn only when it has a button
const hasMore = computed(
  () => finished.value || props.job.callback.status === "failed" || hasLinks.value || canFetchTitle.value,
);

const recheck = () =>
  run(async () => {
    const out = await recheckJob(props.job.id);
    emit("reload");
    announceJobChange();
    notify.success(out.recharged ? "Re-checked: charged again" : "Re-checked");
  });
// R in the drawer runs the same Re-check, when the job can be re-checked
defineExpose({ recheck: () => (props.job.can_recheck && !busy.value ? recheck() : undefined) });

const recover = () => {
  const name = account.value.trim();
  if (!name) {
    notify.error("Enter the account id or label to recover from");
    return Promise.resolve();
  }
  return run(async () => {
    const out = await recoverJob(props.job.id, name);
    await poll(out.command_id, "Recover");
    emit("reload");
    announceJobChange();
  });
};

const getTitle = () =>
  run(async () => {
    const out = await fetchTitle(props.job.id);
    await poll(out.command_id, "Fetch title");
    emit("reload");
    announceJobChange();
  });

const resend = () =>
  run(async () => {
    await resendWebhook(props.job.id);
    emit("reload");
    notify.success("Webhook queued again");
  });

const revoke = () =>
  run(async () => {
    const out = await revokeLinks(props.job.id);
    emit("reload");
    notify.success(`Revoked ${out.revoked} link(s)`);
  });

async function regenerate(): Promise<void> {
  const out = await regenerateJob(props.job.id);
  announceJobChange();
  notify.success(`${out.replayed ? "Already made: job " : "New job "}${shortId(out.id)} (${out.status})`);
}
const confirm = {
  title: "Regenerate this job?",
  message: "Regenerate makes a NEW job for the same key at today’s price, and charges it.",
  confirmText: "Regenerate",
  danger: true,
};
</script>

<template>
  <div v-if="props.zone === 'row'" class="ja-row">
    <slot name="before" />
    <IconButton
      v-if="props.job.can_recheck"
      icon="recheck"
      label="Re-check job"
      shortcut="R"
      :disabled="busy"
      @click="recheck"
    />
    <RegenerateControl
      v-if="finished && props.withRegenerate"
      class="ja-regen"
      :action="jobAction(props.job.id)"
      label="Regenerate…"
      :confirm="confirm"
      :perform="regenerate"
    />
    <slot name="after" />
  </div>

  <RegenerateControl
    v-else-if="props.zone === 'footer' && finished"
    class="ja-regen ja-regen--footer"
    :action="jobAction(props.job.id)"
    label="Regenerate…"
    :confirm="confirm"
    :perform="regenerate"
  />

  <div v-else-if="props.zone === 'more' && hasMore" class="ja-more">
    <h3 class="ja-h">More actions</h3>
    <div v-if="finished" class="ja-recover">
      <label for="job-recover-account" class="ja-label">Recover from an account</label>
      <div class="ja-line">
        <input
          id="job-recover-account"
          v-model="account"
          class="ja-input"
          type="text"
          placeholder="Account id or label, e.g. dola-n11-01"
          autocomplete="off"
          @keyup.enter="recover"
        />
        <button type="button" class="ja-btn" :disabled="busy" @click="recover">Recover</button>
      </div>
      <span class="ja-help">Looks for a finished result of this job on that account.</span>
    </div>
    <div v-if="props.job.callback.status === 'failed' || hasLinks || canFetchTitle" class="ja-icons">
      <IconButton
        v-if="props.job.callback.status === 'failed'"
        icon="resend"
        label="Resend webhook"
        :disabled="busy"
        @click="resend"
      />
      <el-popconfirm
        v-if="hasLinks"
        title="Every share link of this job stops working. Continue?"
        confirm-button-text="Revoke"
        confirm-button-type="danger"
        cancel-button-text="Cancel"
        cancel-button-type="default"
        popper-class="rl-job-pop"
        :hide-icon="true"
        :offset="6"
        width="260"
        @confirm="revoke"
      >
        <template #reference>
          <span class="ja-ref"><IconButton icon="unlink" label="Revoke share links" danger :disabled="busy" /></span>
        </template>
      </el-popconfirm>
      <button v-if="canFetchTitle" type="button" class="ja-btn" :disabled="busy" @click="getTitle">Fetch title</button>
    </div>
  </div>
</template>

<style scoped>
.ja-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.ja-more {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ja-h {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
}
.ja-recover {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ja-label {
  font-size: 13px;
  font-weight: 700;
}
.ja-line {
  display: flex;
  gap: 8px;
}
.ja-input {
  flex: 1;
  min-width: 0;
  height: 36px;
  box-sizing: border-box;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font-family: var(--rl-mono);
  font-size: 13px;
}
.ja-input:focus {
  outline: none;
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.ja-help {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.ja-icons {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.ja-ref {
  display: inline-flex;
}
.ja-btn {
  display: inline-flex;
  align-items: center;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.ja-btn:hover {
  background: var(--rl-head);
}
.ja-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.ja-btn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
/* Regenerate… as the drawer draws it: the secondary button (hairline, surface, 600), 36 high; 44 on the phone's foot */
.ja-regen :deep(.regenerate__go.el-button) {
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-size: 14px;
  font-weight: 600;
}
.ja-regen :deep(.regenerate__go.el-button:hover),
.ja-regen :deep(.regenerate__go.el-button:focus-visible) {
  border-color: var(--rl-line-strong);
  background: var(--rl-head);
  color: var(--rl-fg);
}
.ja-regen--footer {
  flex: 1 1 0;
  min-width: 0;
}
.ja-regen--footer :deep(.regenerate__go.el-button) {
  width: 100%;
  height: 44px;
  font-weight: 700;
}
@media (max-width: 767px) {
  .ja-input,
  .ja-btn {
    height: 44px;
  }
}
</style>

<!-- not scoped: the revoke confirm is a popper teleported to <body>; it wears the popover surface of the menus -->
<style>
html:root .el-popper.rl-job-pop {
  padding: 12px;
  border: 0;
  border-radius: 12px;
  background: var(--rl-pop);
  box-shadow: var(--rl-shadow-pop);
  color: var(--rl-fg);
  font-size: 13px;
  line-height: 19px;
}
html:root .el-popper.rl-job-pop .el-popper__arrow::before {
  border: 0;
  background: var(--rl-pop);
  box-shadow: -1px -1px 0 var(--rl-line-strong);
}
html:root .el-popper.rl-job-pop .el-popconfirm__main {
  margin: 0 0 10px;
}
html:root .el-popper.rl-job-pop .el-popconfirm__action {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin: 0;
}
html:root .el-popper.rl-job-pop .el-popconfirm__action .el-button {
  height: 30px;
  margin: 0;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-size: 13px;
  font-weight: 600;
  line-height: normal;
}
html:root .el-popper.rl-job-pop .el-popconfirm__action .el-button--danger {
  padding: 0 12px;
  border: 0;
  background: var(--rl-bad-solid);
  color: #fff;
  font-weight: 700;
}
</style>
