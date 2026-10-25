<script setup lang="ts">
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import { computed } from "vue";
import type { ContainerRow } from "@/api/settings";
import { ago } from "@/components/accounts/account-model";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { fmtTime } from "@/lib/format";
import { bootValue } from "./settings-value";
import SectionState from "./SectionState.vue";
import "./settings-grid.css";
import "./settings-skeleton.css";

// Each relay service's own container limits, read from its cgroup when it started, as a comparison table: one row per
// measure, one column per service, the service's stamp and `stale` chip under its name. Read with the settings: their
// failure and their wait are this card's too. A worker of an older version has not reported: its column says so and
// its cells are dashes. A phone gets one block per service.
const props = defineProps<{ rows: ContainerRow[]; loading: boolean; error: string | null }>();
const emit = defineEmits<{ retry: [] }>();
const { isPhone } = useBreakpoint();

const LIMITS = [
  ["CPUs", "cpus"],
  ["Memory", "memory"],
  ["PIDs", "pids"],
  ["Shared memory (/dev/shm)", "shm"],
] as const;
// the four limits under one band (no worker reports an environment value: every one became code or a setting)
const lines = LIMITS.map(([label, key]) => ({ label, key }));
// a value the service does not report is a dash, dimmed (an older worker reports nothing)
const NONE = "-";
const cell = (row: ContainerRow, line: { key: string }): string =>
  bootValue(row[line.key as (typeof LIMITS)[number][1]]);
const groups = [{ name: "Container limits", lines }];
const stamp = (row: ContainerRow): string =>
  row.this_process ? `this process, read at start ${fmtTime(row.started_ms)}` : `seen ${ago(row.reported_ms)}`;
const rowKey = (row: ContainerRow): string => `${row.this_process}:${row.service}`;
const skeleton = computed(() => props.loading && !props.rows.length && !props.error);
const SKELETON = ["96px", "72px", "64px", "128px", "88px"];
</script>

<template>
  <div class="containers" :aria-busy="loading">
    <SectionState :error="error" :empty="!rows.length && !loading" empty-text="None" @retry="emit('retry')" />

    <ul v-if="skeleton && isPhone" class="set-list" aria-hidden="true">
      <li v-for="w in SKELETON" :key="w">
        <i class="set-skel" :style="{ width: w }" />
        <i class="set-skel" :style="{ width: '56px' }" />
      </li>
    </ul>
    <template v-else-if="!error && rows.length && isPhone">
      <section v-for="row in rows" :key="rowKey(row)" class="set-block" :data-service="row.service">
        <div class="set-block__head">
          <div class="set-svc">
            <h3 class="set-svc__name">{{ row.service }}</h3>
            <StatusChip v-if="row.stale" status="stale" label="stale" tone="warn" />
          </div>
          <span class="set-svc__when" :title="fmtTime(row.reported_ms)">{{ stamp(row) }}</span>
        </div>
        <p v-if="!row.reported" class="set-block__none">Not reported by this worker version</p>
        <template v-else>
          <template v-for="g in groups" :key="g.name">
            <template v-if="g.lines.length">
              <h4 class="set-list__group">{{ g.name }}</h4>
              <ul class="set-list" :aria-label="`${g.name} of ${row.service}`">
                <li v-for="line in g.lines" :key="line.label">
                  <span class="set-list__name">{{ line.label }}</span>
                  <span class="set-list__value">{{ cell(row, line) }}</span>
                </li>
              </ul>
            </template>
          </template>
        </template>
      </section>
    </template>

    <ArrowScroller v-else-if="!error && (rows.length || skeleton)" class="set-scroll" label="Container limits" table>
      <table class="set-table set-table--matrix" aria-label="Container limits">
        <thead>
          <tr v-if="skeleton">
            <th scope="col">Name</th>
            <th scope="col"><i class="set-skel" :style="{ width: '72px' }" /></th>
            <th scope="col"><i class="set-skel" :style="{ width: '96px' }" /></th>
          </tr>
          <tr v-else>
            <th scope="col">Name</th>
            <th v-for="row in rows" :key="rowKey(row)" scope="col" :data-service="row.service">
              <div class="set-svc">
                <span class="set-svc__name">{{ row.service }}</span>
                <StatusChip v-if="row.stale" status="stale" label="stale" tone="warn" />
              </div>
              <span class="set-svc__when" :title="fmtTime(row.reported_ms)">{{ stamp(row) }}</span>
              <span v-if="!row.reported" class="set-svc__none">Not reported by this worker version</span>
            </th>
          </tr>
        </thead>
        <tbody v-if="skeleton" aria-hidden="true">
          <tr v-for="w in SKELETON" :key="w" class="set-skel-row">
            <td><i class="set-skel" :style="{ width: w }" /></td>
            <td><i class="set-skel" :style="{ width: '56px' }" /></td>
            <td><i class="set-skel" :style="{ width: '56px' }" /></td>
          </tr>
        </tbody>
        <tbody v-else>
          <template v-for="g in groups" :key="g.name">
            <tr class="set-table__group">
              <th scope="rowgroup" :colspan="rows.length + 1">{{ g.name }}</th>
            </tr>
            <tr v-for="line in g.lines" :key="line.label">
              <th scope="row">{{ line.label }}</th>
              <td
                v-for="row in rows"
                :key="rowKey(row)"
                :data-service="row.service"
                :class="{ 'set-table__none': cell(row, line) === NONE }"
              >
                {{ cell(row, line) }}
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </ArrowScroller>
  </div>
</template>
