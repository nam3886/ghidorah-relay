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
import { IconRefresh } from "@/components/shared/icons";
import RegenerateControl from "@/components/jobs/RegenerateControl.vue";
import { useBusy } from "@/composables/use-busy";
import { useCommandPoll } from "@/composables/use-command-poll";
import { notify } from "@/lib/notify";
import { jobAction } from "@/lib/operation-key";

// The buttons of one job. Each shows only when the job allows it (the same rules as the old admin). `reload` asks the
// drawer to read the job again; `announceJobChange` tells the list. The drawer places them in three zones: `row` (the
// action row of the overview: Re-check and Regenerate), `card` (the Actions card of a failed job: Recover and
// Regenerate) and `more` (what is left, in the Technical tab). A zone only moves a button; the rules are the same.
const props = defineProps<{ job: JobDetail; zone: "row" | "card" | "more" }>();
const emit = defineEmits<{ reload: [] }>();

const { busy, run } = useBusy();
const { poll } = useCommandPoll();
const account = ref("");

const finished = computed(() => props.job.status === "done" || props.job.status === "failed");
// Recover is in the Actions card of a failed job and under More actions for the others that finished, never in both.
const recoverHere = computed(
  () => finished.value && (props.zone === "card" || (props.zone === "more" && props.job.status !== "failed")),
);
const hasLinks = computed(() => props.job.media.some((m) => m.url));
const canFetchTitle = computed(() => Boolean(props.job.external_ref && props.job.account_label));
// the More actions card is drawn only when it has a button
const hasMore = computed(
  () => recoverHere.value || props.job.callback.status === "failed" || hasLinks.value || canFetchTitle.value,
);

const recheck = () =>
  run(async () => {
    const out = await recheckJob(props.job.id);
    emit("reload");
    announceJobChange();
    notify.success(out.recharged ? "Re-checked: charged again" : "Re-checked");
  });

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
</script>

<template>
  <div v-if="props.zone !== 'more' || hasMore" class="job-actions" :class="`job-actions--${props.zone}`">
    <h3 v-if="props.zone !== 'row'" class="job-actions__title">
      {{ props.zone === "card" ? "Actions" : "More actions" }}
    </h3>
    <div v-if="recoverHere" class="job-actions__recover">
      <label for="job-recover-account" class="job-actions__label">Recover from an account</label>
      <div class="job-actions__line">
        <input
          id="job-recover-account"
          v-model="account"
          class="job-actions__input"
          type="text"
          placeholder="Account id or label, e.g. acct-n11-01"
          autocomplete="off"
        />
        <button type="button" class="job-btn" :disabled="busy" @click="recover">Recover</button>
      </div>
      <span class="job-actions__help">Looks for a finished result of this job on that account.</span>
    </div>
    <div class="job-actions__row" :class="{ 'is-divided': props.zone === 'card' }">
      <slot name="before" />
      <button
        v-if="props.job.can_recheck && props.zone !== 'more'"
        type="button"
        class="job-btn"
        :disabled="busy"
        @click="recheck"
      >
        Re-check
      </button>
      <RegenerateControl
        v-if="finished && props.zone !== 'more'"
        :action="jobAction(props.job.id)"
        label="Regenerate…"
        :confirm="{
          title: 'Regenerate this job?',
          message: 'Regenerate makes a NEW job for the same key at today’s price, and charges it.',
          confirmText: 'Regenerate',
          danger: true,
        }"
        :perform="regenerate"
      >
        <template v-if="props.zone === 'card'" #icon><IconRefresh :size="15" /></template>
      </RegenerateControl>
      <span v-if="finished && props.zone === 'card'" class="job-actions__help">Makes a new job at today’s price.</span>
      <button
        v-if="props.job.callback.status === 'failed' && props.zone === 'more'"
        type="button"
        class="job-btn"
        :disabled="busy"
        @click="resend"
      >
        Resend webhook
      </button>
      <el-popconfirm
        v-if="hasLinks && props.zone === 'more'"
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
          <span
            ><button type="button" class="job-btn job-btn--danger" :disabled="busy">Revoke share links</button></span
          >
        </template>
      </el-popconfirm>
      <button
        v-if="canFetchTitle && props.zone === 'more'"
        type="button"
        class="job-btn"
        :disabled="busy"
        @click="getTitle"
      >
        Fetch title
      </button>
    </div>
  </div>
</template>

<style scoped>
.job-actions {
  --regenerate-height: 36px;
  --regenerate-pad: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.job-actions--row {
  gap: 8px;
}
.job-actions--card,
.job-actions--more {
  padding: 14px 16px;
  border: 1px solid var(--rl-line);
  border-radius: 12px;
}
.job-actions__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}
.job-actions__recover {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.job-actions__label {
  font-size: 13px;
  font-weight: 500;
}
.job-actions__line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.job-actions__input {
  flex: 1 1 220px;
  min-width: 0;
  height: 36px;
  padding: 0 12px;
  box-sizing: border-box;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-family: var(--rl-mono, "JetBrains Mono", ui-monospace, monospace);
  font-size: 13px;
}
.job-actions__help {
  font-size: 12px;
  color: var(--rl-muted);
}
.job-actions__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.job-actions__row.is-divided {
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--rl-line);
}
.job-btn {
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}
.job-btn:hover {
  background: var(--rl-raised);
}
.job-btn:disabled {
  border-color: var(--rl-line-strong);
  background: var(--rl-raised);
  color: var(--rl-faint);
  cursor: default;
}
.job-btn--danger {
  border-color: var(--rl-bad);
  color: var(--rl-bad);
}
@media (max-width: 767.98px) {
  .job-actions--row .job-btn {
    height: 44px;
  }
  .job-actions--row {
    --regenerate-height: 44px;
    width: 100%;
  }
  .job-actions--row .job-actions__row {
    flex-wrap: nowrap;
  }
}
</style>

<!-- not scoped: the revoke confirm is a popper teleported to <body>; it wears the popover surface of the menus -->
<style>
html:root .el-popper.rl-job-pop {
  padding: 12px;
  border: 1px solid var(--rl-line);
  border-radius: 12px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-pop);
  color: var(--rl-fg);
  font-size: 13px;
  line-height: 20px;
}
html:root .el-popper.rl-job-pop .el-popper__arrow {
  display: none;
}
html:root .el-popper.rl-job-pop .el-popconfirm__main {
  margin: 0 0 12px;
}
html:root .el-popper.rl-job-pop .el-popconfirm__action {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin: 0;
}
/* its buttons as the confirm's: Cancel the secondary (surface, strong hairline), Revoke the solid destructive with no
   edge; both 36 high, padded 0 16, in the browser's line height */
html:root .el-popper.rl-job-pop .el-popconfirm__action .el-button {
  height: 36px;
  margin: 0;
  padding: 0 16px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 400;
  line-height: normal;
}
html:root .el-popper.rl-job-pop .el-popconfirm__action .el-button--danger {
  border: 0;
  background: var(--rl-bad-solid);
  color: #fff;
  font-weight: 600;
}
</style>
