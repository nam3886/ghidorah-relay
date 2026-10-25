<script setup lang="ts">
import "./account-dialog.css";
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { type Account, type AccountHistory, accountHistory, accountScreenshot } from "@/api/accounts";
import CommandChip from "@/components/shared/CommandChip.vue";
import IconButton from "@/components/shared/IconButton.vue";
import SideDrawer from "@/components/shared/overlays/SideDrawer.vue";
import { vWheelX } from "@/components/shared/wheel-scroll";
import { providerClass } from "@/lib/providers";
import {
  accountSub,
  type Cell,
  type ChipState,
  chipProps,
  dayMonth,
  healthCells,
  healthOf,
  healthSummary,
  hhmm,
  poolFigure,
  poolLeft,
  poolsInOrder,
  poolTone,
  proxyCheck,
  proxyChipText,
  proxyText,
  type RowAction,
} from "./account-model";

// One account in the right-hand drawer (AccountDetail): the label in mono, its health pill, provider chip and who it is;
// a banner that says why it needs a look; four facts (tier, cycle start, jobs today, added); its health history (the
// last 14 days, then today by hour); its pools; its proxy with Check proxy, the exit address and the latency the last
// check measured through it; its last errors with their times and jobs; its screenshot, inline. The footer: Delete
// (red on hover) apart, Edit, Re-check · R, and Wake now while it sleeps. The drawer only asks; the view runs every
// command. The history and the screenshot are read when the drawer opens and again after each check of the account.
const open = defineModel<boolean>({ required: true });
const props = defineProps<{
  account: Account | null;
  paused: boolean;
  checking: boolean;
  checkChip?: ChipState | null;
  proxyChip?: ChipState | null;
}>();
const emit = defineEmits<{ action: [name: RowAction, account: Account]; proxy: [account: Account]; closed: [] }>();

const a = computed(() => props.account);
const health = computed(() => (a.value ? healthOf(a.value, props.paused) : null));

// the banner: what the health means and what to do about it
const banner = computed(() => {
  const acc = a.value;
  const h = health.value;
  if (!acc || !h) return null;
  const why = h.why ? ` (${h.why})` : "";
  switch (h.kind) {
    case "asleep": {
      const strikes = acc.strikes > 0 ? ` after ${acc.strikes} ${acc.strikes === 1 ? "strike" : "strikes"}` : "";
      const until = h.label.replace("Asleep until ", "");
      // AccountDetail: "dola refused 3 prompts (dola.content_refused). It takes jobs again at 15:10, ..."
      const cause =
        acc.strikes > 0
          ? `${acc.provider} refused ${acc.strikes} ${acc.strikes === 1 ? "prompt" : "prompts"}${why}. `
          : h.why
            ? `${h.why}. `
            : "";
      return {
        tone: "warn",
        title: `Resting${strikes}.`,
        text: `${cause}It takes jobs again at ${until}, or now if you wake it.`,
      };
    }
    case "unknown":
      return {
        tone: "neutral",
        title: "The last check did not answer.",
        text: `What is shown is its last answer${h.why ? ` (${h.why.replace("last answer ", "from ")})` : ""}. Re-check it.`,
      };
    case "dead":
      return {
        tone: "bad",
        title: "The session is dead.",
        text: `It takes no jobs${why}. Re-check it once it is fixed.`,
      };
    case "proxy":
      return { tone: "bad", title: "The proxy is failing.", text: `It takes no jobs${why}. Fix the proxy in Edit.` };
    case "low":
      return { tone: "warn", title: "Low on credits.", text: `${h.why}: it takes jobs again when the pool refills.` };
    case "off":
      return { tone: "neutral", title: "Switched off.", text: "It takes no jobs until it is switched on in Edit." };
    case "paused":
      return {
        tone: "info",
        title: `${acc.provider} is paused in Settings.`,
        text: "Its accounts stay healthy but take no jobs; checks still run.",
      };
    default:
      return null;
  }
});

const stats = computed(() => {
  const acc = a.value;
  if (!acc) return [];
  const days = acc.created_ms ? Math.floor((Date.now() - acc.created_ms) / 86_400_000) : null;
  const tier = acc.tier ? acc.tier.charAt(0).toUpperCase() + acc.tier.slice(1) : "Free";
  return [
    { label: "Tier", value: tier, note: acc.tier_read ? "read from the account" : "not read yet" },
    {
      label: "Cycle start",
      value: acc.cycle_start ? dayMonth(Date.parse(`${acc.cycle_start}T00:00:00Z`)) : "—",
      note: acc.cycle_start ? "the paid cycle" : "no paid cycle",
    },
    {
      label: "Jobs today",
      value: String(acc.jobs_today?.total ?? 0),
      note: acc.jobs_today?.failed ? `${acc.jobs_today.failed} failed` : "none failed",
    },
    {
      label: "Added",
      value: acc.created_ms ? dayMonth(acc.created_ms) : "—",
      note: days === null ? "" : days === 0 ? "today" : `${days} ${days === 1 ? "day" : "days"} ago`,
    },
  ];
});

// When a pool takes jobs again: its rest's end when it rests, else its first window's refill ("07:00", "1 Nov").
function resets(c: Account["capacity"][number]): string {
  const now = Date.now();
  const at = c.rest_until_ms && c.rest_until_ms > now ? c.rest_until_ms : (c.windows?.[0]?.refill_ms ?? null);
  if (!at) return "—";
  return at - now < 86_400_000 ? hhmm(at) : dayMonth(at);
}
const pools = computed(() =>
  poolsInOrder(a.value?.capacity ?? []).map((c) => {
    const left = poolLeft(c);
    const used = c.budget && c.left_today !== null && c.left_today !== undefined ? c.budget - c.left_today : null;
    return {
      name: c.pool,
      used: used === null ? poolFigure(c) : `${used} / ${c.budget}`,
      fill: { width: `${used === null || !c.budget ? 0 : Math.round((used / c.budget) * 100)}%` },
      left: c.left_today === null || c.left_today === undefined ? "?" : String(c.left_today),
      tone: left === null ? "ok" : poolTone(left),
      window: c.windows?.[0]?.name ?? "",
      resets: resets(c),
    };
  }),
);

const proxyFacts = computed(() => {
  const acc = a.value;
  if (!acc?.proxy_host) return [{ label: "Address", value: "direct, no proxy" }];
  const measured = acc.proxy_exit_ms ? ` · checked ${hhmm(acc.proxy_exit_ms)}` : "";
  return [
    { label: "Address", value: proxyText(acc) },
    { label: "User", value: acc.has_proxy_auth ? "password stored" : "none" },
    {
      label: "Exit IP",
      value: acc.proxy_exit_ip ?? (acc.proxy_exit_ms ? "no answer through the proxy" : "not measured yet"),
    },
    {
      label: "Latency",
      value:
        acc.proxy_latency_ms !== null && acc.proxy_latency_ms !== undefined
          ? `${acc.proxy_latency_ms} ms${measured}`
          : `—${measured}`,
    },
  ];
});

// ---- what the drawer reads for itself: the history and the screenshot ----------------------------------------------
const history = shallowRef<AccountHistory | null>(null);
const shot = ref<{ url: string; size: string } | null>(null);
let ticket = 0;
function dropShot(): void {
  if (shot.value) URL.revokeObjectURL(shot.value.url);
  shot.value = null;
}
// Read again when the drawer opens on an account and after each check of it (the times move); a newer read wins.
watch(
  () =>
    open.value && a.value
      ? [a.value.id, a.value.last_checked_ms, a.value.check_failed_ms, a.value.screenshot_ms]
      : null,
  async (key, before) => {
    if (!key || !a.value) return;
    const acc = a.value;
    const mine = ++ticket;
    if (!before || before[0] !== key[0]) {
      history.value = null;
      dropShot();
    }
    // a failed read leaves its part empty: the drawer still shows everything the list gave
    const quiet = async <T,>(read: () => Promise<T>): Promise<T | null> => {
      try {
        return await read();
      } catch {
        return null;
      }
    };
    const [got, blob] = await Promise.all([
      quiet(() => accountHistory(acc.id)),
      acc.has_screenshot ? quiet(() => accountScreenshot(acc.id)) : null,
    ]);
    if (mine !== ticket) return;
    history.value = got ?? null;
    dropShot();
    if (blob instanceof Blob) shot.value = { url: URL.createObjectURL(blob), size: "" };
  },
  { immediate: true },
);
onBeforeUnmount(dropShot);
function shotLoaded(event: Event): void {
  const img = event.target as HTMLImageElement;
  if (shot.value) shot.value = { ...shot.value, size: `${img.naturalWidth}×${img.naturalHeight}` };
}
const shotLine = computed(() => {
  const acc = a.value;
  if (!acc?.has_screenshot) return "none yet";
  return [acc.screenshot_ms ? hhmm(acc.screenshot_ms) : "", shot.value?.size ?? ""].filter(Boolean).join(" · ");
});

const cells = computed(() => healthCells(history.value?.hours ?? []));
const CELL = ["ok", "warn", "bad"] as const;
const cellClass = (c: Cell): string => (c === null ? "is-none" : `is-${CELL[c]}`);
const nowLine = computed(() => hhmm(Date.now()));

// The newest errors, each with its time (the clock today, the day before), its code, the job's own error when it
// adds to the code, and the job it came from.
const errors = computed(() =>
  (history.value?.errors ?? []).map((e) => ({
    key: `${e.at_ms}-${e.code}`,
    at: Date.now() - e.at_ms < 86_400_000 ? hhmm(e.at_ms) : dayMonth(e.at_ms),
    code: e.code,
    tone: e.code.startsWith("proxy") ? "warn" : "bad",
    text: e.job_error && e.job_error !== e.code ? e.job_error : "",
    job: e.job_id,
  })),
);

const act = (name: RowAction): void => {
  if (a.value) emit("action", name, a.value);
};
</script>

<template>
  <SideDrawer
    v-model="open"
    :title="a?.label ?? ''"
    mono
    testid="account-drawer"
    back-label="Back to Accounts"
    @back="open = false"
    @closed="emit('closed')"
  >
    <template v-if="a && health" #sub>
      <span class="acct-pill" :class="`tone-${health.tone}`">{{ health.label }}</span>
      <span class="acct-pchip" :class="providerClass(a.provider)">{{ a.provider }}</span>
      <span v-if="accountSub(a)" class="ad__help">{{ accountSub(a) }}</span>
    </template>
    <template v-if="a">
      <div v-if="banner" role="status" class="ad__banner" :class="`tone-${banner.tone}`">
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
        </svg>
        <span
          ><b>{{ banner.title }}</b> {{ banner.text }}</span
        >
      </div>

      <div class="ad__stats">
        <div v-for="s in stats" :key="s.label" class="ad__stat">
          <span class="ad__help">{{ s.label }}</span>
          <span class="num ad__value">{{ s.value }}</span>
          <span class="num ad__help clip" :title="s.note">{{ s.note }}</span>
        </div>
      </div>

      <section class="ad__sec ad__hist" aria-labelledby="ad-health" data-testid="account-health">
        <div class="ad__hist-head">
          <h3 id="ad-health">Health history</h3>
          <span class="ad__help">last 14 days, then today by hour</span>
          <span class="ad__legend" aria-hidden="true"
            ><i class="is-ok" />usable <i class="is-warn" />asleep <i class="is-bad" />dead or proxy down</span
          >
        </div>
        <div role="img" :aria-label="healthSummary(cells.days)" class="ad__days">
          <span v-for="(c, i) in cells.days" :key="i" class="ad__cell" :class="cellClass(c)" />
        </div>
        <div class="num ad__axis">
          <span>{{ dayMonth(cells.first) }}</span
          ><span>{{ dayMonth(cells.middle) }}</span
          ><span>today</span>
        </div>
        <div role="img" aria-label="Today by hour" class="ad__hours">
          <span v-for="(c, i) in cells.hours" :key="i" class="ad__cell ad__cell--h" :class="cellClass(c)" />
        </div>
        <div class="num ad__axis">
          <span>00:00</span><span>06:00</span><span>12:00</span><span>{{ nowLine }}</span
          ><span>24:00</span>
        </div>
      </section>

      <section v-if="pools.length" class="ad__sec" :class="providerClass(a.provider)" aria-labelledby="ad-pools">
        <h3 id="ad-pools">Pools</h3>
        <div v-wheel-x class="ad__box">
          <table class="ad__table">
            <thead>
              <tr>
                <th>Pool</th>
                <th>Window</th>
                <th>Used</th>
                <th class="r">Left</th>
                <th class="r">Resets</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in pools" :key="p.name">
                <td class="ad__pool">{{ p.name }}</td>
                <td>
                  <span v-if="p.window" class="ad__window">{{ p.window }}</span
                  ><span v-else class="ad__help">—</span>
                </td>
                <td class="ad__used">
                  <div class="ad__used-in">
                    <span class="track"><span class="fill" :class="`is-${p.tone}`" :style="p.fill" /></span>
                    <span class="num ad__help">{{ p.used }}</span>
                  </div>
                </td>
                <td class="r num ad__left" :class="p.tone !== 'ok' && `is-${p.tone}`">{{ p.left }}</td>
                <td class="r num ad__rests">{{ p.resets }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="ad__sec" aria-labelledby="ad-proxy">
        <div class="ad__head">
          <h3 id="ad-proxy">Proxy</h3>
          <span v-if="a.proxy_host" class="acct-pill acct-pill--sm" :class="`tone-${proxyCheck(a).tone}`">{{
            proxyChipText(a)
          }}</span>
          <IconButton v-if="a.proxy_host" icon="proxy" label="Check proxy" @click="emit('proxy', a)" />
          <CommandChip v-if="props.proxyChip" v-bind="chipProps(props.proxyChip)" />
        </div>
        <dl class="ad__ledger">
          <div v-for="f in proxyFacts" :key="f.label" class="ad__ledger-row">
            <dt>{{ f.label }}</dt>
            <dd class="mono num">{{ f.value }}</dd>
          </div>
        </dl>
      </section>

      <section class="ad__sec" aria-labelledby="ad-errors">
        <h3 id="ad-errors">Last errors</h3>
        <ul v-if="errors.length" class="ad__errors">
          <li v-for="e in errors" :key="e.key">
            <span class="num mono ad__help">{{ e.at }}</span>
            <span class="ad__err">
              <span class="mono ad__code" :class="`tone-${e.tone}`">{{ e.code }}</span>
              <span v-if="e.text" class="ad__err-text">{{ e.text }}</span>
            </span>
            <RouterLink
              v-if="e.job"
              class="mono ad__job"
              :to="{ name: 'jobs', query: { job: e.job } }"
              :aria-label="`Open job ${e.job.slice(0, 8)}`"
              >{{ e.job.slice(0, 8) }}</RouterLink
            >
            <span v-else />
          </li>
        </ul>
        <span v-else class="ad__help">{{ history ? "No error recorded." : "" }}</span>
      </section>

      <section class="ad__sec" aria-labelledby="ad-shot">
        <div class="ad__head">
          <h3 id="ad-shot">Last screenshot</h3>
          <span class="num ad__help">{{ shotLine }}</span>
          <IconButton v-if="a.has_screenshot" icon="expand" label="Open full size" @click="act('shot')" />
        </div>
        <button
          v-if="shot"
          type="button"
          class="ad__shot"
          aria-label="Open the screenshot full size"
          @click="act('shot')"
        >
          <img :src="shot.url" alt="" @load="shotLoaded" />
        </button>
      </section>
    </template>

    <template v-if="a" #footer>
      <IconButton icon="delete" label="Delete account" danger @click="act('delete')" />
      <span class="ad__foot">
        <CommandChip v-if="props.checkChip" v-bind="chipProps(props.checkChip)" />
        <IconButton icon="edit" label="Edit account" @click="act('edit')" />
        <IconButton
          icon="recheck"
          label="Re-check account"
          shortcut="R"
          placement="top-end"
          :disabled="props.checking"
          @click="act('check')"
        />
        <button v-if="health?.kind === 'asleep'" type="button" class="ad__primary" @click="act('wake')">
          Wake now
        </button>
      </span>
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
.r {
  text-align: right;
}
.clip {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ad__help {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.ad__banner {
  display: flex;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 19px;
}
.ad__banner svg {
  flex: none;
  margin-top: 2px;
}
.ad__banner.tone-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.ad__banner.tone-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.ad__banner.tone-info {
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.ad__banner.tone-neutral {
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.ad__stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.ad__stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
  background: var(--rl-head);
}
.ad__value {
  font-size: 16px;
  line-height: 22px;
  font-weight: 800;
}
.ad__sec {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ad__sec h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
}
.ad__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.ad__box {
  overflow-x: auto;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.ad__table {
  width: 100%;
  border-collapse: collapse;
}
.ad__table th {
  padding: 8px;
  background: var(--rl-head);
  color: var(--rl-faint);
  font-size: 12px;
  font-weight: 600;
  text-align: left;
}
.ad__table th.r {
  text-align: right;
}
.ad__table td {
  padding: 10px 8px;
  border-top: 1px solid var(--rl-rule);
}
.ad__table th:first-child,
.ad__table td:first-child {
  padding-left: 14px;
}
.ad__table th:last-child,
.ad__table td:last-child {
  padding-right: 14px;
}
.ad__pool {
  font-weight: 700;
}
.ad__used {
  width: 34%;
}
.ad__window {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  background: var(--rl-track);
  color: var(--rl-body);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.ad__used-in {
  display: flex;
  align-items: center;
  gap: 8px;
}
.track {
  flex: 1;
  display: block;
  height: 6px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--rl-track);
}
.fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--pc, var(--rl-brand));
}
.fill.is-warn {
  background: var(--rl-amber);
}
.fill.is-bad {
  background: var(--rl-bad);
}
.ad__left {
  font-weight: 800;
  color: var(--rl-fg);
}
.ad__left.is-warn,
.ad__left.is-bad {
  color: var(--rl-warn);
}
.ad__rests {
  font-size: 12px;
  white-space: nowrap;
  color: var(--rl-muted);
}
.ad__ledger {
  display: flex;
  flex-direction: column;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.ad__ledger-row {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 12px;
  padding: 8px 14px;
  border-top: 1px solid var(--rl-rule);
}
.ad__ledger-row:first-child {
  border-top: 0;
}
.ad__ledger dt {
  font-size: 13px;
  color: var(--rl-faint);
}
.ad__ledger dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 20px;
  color: var(--rl-fg);
}
.ad__errors {
  margin: 0;
  padding: 0;
  overflow: hidden;
  list-style: none;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.ad__errors li {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
  padding: 10px 14px;
  border-top: 1px solid var(--rl-rule);
}
.ad__err {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.ad__err-text {
  font-size: 13px;
  line-height: 19px;
  color: var(--rl-muted);
  overflow-wrap: anywhere;
}
.ad__job {
  font-size: 12px;
  font-weight: 600;
  text-decoration: none;
  color: var(--rl-brand-ink);
}
.ad__job:hover {
  text-decoration: underline;
}
/* the health history (AccountDetail): 14 day cells, then today's 24 hours */
.ad__hist {
  --cell-ok: #32b37a;
  --cell-warn: #f5b544;
  --cell-bad: #e04f43;
  --cell-none: var(--rl-track);
}
:global(html.dark) .ad__hist {
  --cell-ok: #2f9e6e;
  --cell-warn: #d39b20;
  --cell-bad: #e0574a;
}
.ad__hist-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
}
.ad__legend {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  font-size: 12px;
  color: var(--rl-faint);
}
.ad__legend i {
  width: 10px;
  height: 10px;
  border-radius: 3px;
}
.ad__legend i:not(:first-child) {
  margin-left: 6px;
}
.ad__days {
  display: grid;
  grid-template-columns: repeat(14, minmax(0, 1fr));
  gap: 4px;
}
.ad__hours {
  display: grid;
  grid-template-columns: repeat(24, minmax(0, 1fr));
  gap: 2px;
}
.ad__cell {
  display: block;
  height: 18px;
  border-radius: 4px;
}
.ad__cell--h {
  height: 12px;
  border-radius: 3px;
}
.ad__hist .is-ok {
  background: var(--cell-ok);
}
.ad__hist .is-warn {
  background: var(--cell-warn);
}
.ad__hist .is-bad {
  background: var(--cell-bad);
}
.ad__hist .is-none {
  background: var(--cell-none);
}
.ad__axis {
  display: flex;
  justify-content: space-between;
  margin-top: -4px;
  font-size: 11px;
  color: var(--rl-faint);
}
.ad__shot {
  display: block;
  width: 100%;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--rl-line);
  border-radius: 12px;
  background: var(--rl-head);
  cursor: zoom-in;
}
.ad__shot img {
  display: block;
  width: 100%;
  height: auto;
}
.ad__errors li:first-child {
  border-top: 0;
}
.ad__code {
  font-size: 12px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.ad__code.tone-bad {
  color: var(--rl-bad);
}
.ad__code.tone-warn {
  color: var(--rl-warn);
}
.ad__foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.ad__primary {
  height: 36px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}
.ad__primary:hover {
  background: var(--rl-primary-hover);
}
@media (max-width: 767px) {
  .ad__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ad__primary {
    height: 44px;
  }
}
</style>
