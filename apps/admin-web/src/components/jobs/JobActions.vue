<script setup lang="ts">
import { EditPen, Link, Promotion, Refresh, RefreshLeft } from "@element-plus/icons-vue";
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
import IconAction from "@/components/shared/IconAction.vue";
import { useBusy } from "@/composables/use-busy";
import { useCommandPoll } from "@/composables/use-command-poll";
import { notify } from "@/lib/notify";
import { jobAction } from "@/lib/operation-key";

// The buttons of one job. Each shows only when the job allows it (the same rules as the old admin). `reload` asks the
// dialog to read the job again; `announceJobChange` tells the list.
const props = defineProps<{ job: JobDetail }>();
const emit = defineEmits<{ reload: [] }>();

const { busy, run } = useBusy();
const { poll } = useCommandPoll();
const account = ref("");

const finished = computed(() => props.job.status === "done" || props.job.status === "failed");
const hasLinks = computed(() => props.job.media.some((m) => m.url));
const canFetchTitle = computed(() => Boolean(props.job.external_ref && props.job.account_label));

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
  <div class="job-actions">
    <IconAction v-if="props.job.can_recheck" :icon="Refresh" label="Re-check" :disabled="busy" @click="recheck" />
    <template v-if="finished">
      <el-input v-model="account" class="job-actions__account" placeholder="account id or label" clearable />
      <IconAction :icon="RefreshLeft" label="Recover" :disabled="busy" @click="recover" />
      <RegenerateControl
        :action="jobAction(props.job.id)"
        label="Regenerate"
        :confirm="{
          title: 'Regenerate',
          message: 'Regenerate makes a NEW job for the same key at today’s price, and charges it. Continue?',
          confirmText: 'Regenerate',
          danger: true,
        }"
        :perform="regenerate"
      />
    </template>
    <IconAction
      v-if="props.job.callback.status === 'failed'"
      :icon="Promotion"
      label="Resend webhook"
      :disabled="busy"
      @click="resend"
    />
    <el-popconfirm
      v-if="hasLinks"
      title="Every share link of this job stops working. Continue?"
      confirm-button-text="Revoke"
      confirm-button-type="danger"
      width="260"
      @confirm="revoke"
    >
      <template #reference>
        <span><IconAction :icon="Link" type="danger" label="Revoke share links" :disabled="busy" /></span>
      </template>
    </el-popconfirm>
    <IconAction v-if="canFetchTitle" :icon="EditPen" label="Fetch title" :disabled="busy" @click="getTitle" />
  </div>
</template>

<style scoped>
.job-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.job-actions__account {
  width: 14rem;
}
</style>
