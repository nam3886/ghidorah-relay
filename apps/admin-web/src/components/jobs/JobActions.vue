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
import { confirmDialog } from "@/components/shared/confirm-dialog";
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
    const ok = await confirmDialog({
      title: "Revoke share links",
      message: "Every share link of this job stops working. Continue?",
      confirmText: "Revoke",
      danger: true,
    });
    if (!ok) return;
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
    <el-button v-if="props.job.can_recheck" :disabled="busy" @click="recheck">Re-check</el-button>
    <template v-if="finished">
      <el-input v-model="account" class="job-actions__account" placeholder="account id or label" clearable />
      <el-button :disabled="busy" @click="recover">Recover</el-button>
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
    <el-button v-if="props.job.callback.status === 'failed'" :disabled="busy" @click="resend">Resend webhook</el-button>
    <el-button v-if="hasLinks" :disabled="busy" @click="revoke">Revoke share links</el-button>
    <el-button v-if="canFetchTitle" :disabled="busy" @click="getTitle">Fetch title</el-button>
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
