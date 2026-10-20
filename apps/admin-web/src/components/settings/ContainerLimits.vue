<script setup lang="ts">
import type { ContainerRow } from "@/api/settings";
import { ago } from "@/components/accounts/account-model";
import StatusChip from "@/components/shared/StatusChip.vue";
import { fmtTime } from "@/lib/format";
import { bootValue } from "./settings-value";
import SectionState from "./SectionState.vue";
import "./settings-leader.css";
import "./settings-skeleton.css";

// Each relay service's own container limits, read from its cgroup when it started. Read with the settings: their failure
// and their wait are this card's too. A worker of an older version has not reported: its row says so.
defineProps<{ rows: ContainerRow[]; loading: boolean; error: string | null }>();
const emit = defineEmits<{ retry: [] }>();

const LIMITS = [
  ["CPUs", "cpus"],
  ["Memory", "memory"],
  ["PIDs", "pids"],
  ["Shared memory (/dev/shm)", "shm"],
] as const;
const SKELETON = ["96px", "72px", "64px", "128px"];
</script>

<template>
  <div class="containers" :aria-busy="loading">
    <SectionState :error="error" :empty="!rows.length && !loading" empty-text="None" @retry="emit('retry')" />
    <dl v-if="!error && !rows.length && loading" class="set-leader" aria-hidden="true">
      <div v-for="w in SKELETON" :key="w" class="set-leader__row">
        <i class="set-skel" :style="{ width: w }" />
        <span class="set-leader__line" />
        <i class="set-skel" :style="{ width: '56px' }" />
      </div>
    </dl>
    <template v-else-if="!error && rows.length">
      <section
        v-for="row in rows"
        :key="`${row.this_process}:${row.service}`"
        class="containers__block"
        :data-service="row.service"
      >
        <div class="containers__head">
          <h3 class="containers__name">{{ row.service }}</h3>
          <StatusChip v-if="row.stale" status="stale" label="stale" tone="warn" />
          <span v-if="row.this_process" class="containers__when" :title="fmtTime(row.reported_ms)"
            >this process, read at start {{ fmtTime(row.started_ms) }}</span
          >
          <span v-else class="containers__when" :title="fmtTime(row.reported_ms)">seen {{ ago(row.reported_ms) }}</span>
        </div>
        <p v-if="!row.reported" class="containers__none">Not reported by this worker version</p>
        <dl v-else class="set-leader" :aria-label="`Container limits of ${row.service}`">
          <div v-for="[label, key] in LIMITS" :key="key" class="set-leader__row">
            <dt>{{ label }}</dt>
            <span class="set-leader__line" aria-hidden="true" />
            <dd class="num">{{ row[key] }}</dd>
          </div>
          <div v-for="[name, value] in Object.entries(row.boot ?? {})" :key="name" class="set-leader__row">
            <dt>{{ name }}</dt>
            <span class="set-leader__line" aria-hidden="true" />
            <dd class="num">{{ bootValue(value) }}</dd>
          </div>
        </dl>
      </section>
    </template>
  </div>
</template>

<style scoped>
.containers__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  padding: 10px 20px 6px;
  border-top: 1px solid var(--rl-line);
}
.containers__name {
  margin: 0;
  font-family: var(--rl-mono);
  font-size: 13px;
  font-weight: 600;
  line-height: 18px;
}
.containers__when {
  font-size: 12px;
  color: var(--rl-muted);
}
.containers__none {
  margin: 0;
  padding: 8px 20px 18px;
  font-size: 13px;
  color: var(--rl-muted);
}
@media (max-width: 767px) {
  .containers__head,
  .containers__none {
    padding-left: 16px;
    padding-right: 16px;
  }
}
</style>
