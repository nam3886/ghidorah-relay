import { defineStore } from "pinia";
import { ref, shallowRef } from "vue";
import type { AlertGroup } from "@/api/alerts";
import { getNewestReply, getOpenAlertGroups, getStats, type Stats } from "@/api/shell";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";

// The stats strip and the attention banner. The strip follows the provider filter; the live alerts and the open
// alert groups cover the whole deployment. Only the newest refresh draws (or toasts a failure): an older, slower
// answer must not bring back an old count.
export const useStatsStore = defineStore("stats", () => {
  const scope = useScopeStore();
  const data = shallowRef<Stats | null>(null);
  const openGroups = ref(0);
  // the muted groups and live conditions (Alert rules): never counted as open, but the banner says how many there are
  const muted = ref(0);
  // the first open group (the most pressing) and, for an unclear reply, its newest reply as stored: the Jobs banner
  // words them. The reply is read again only when the group or its newest row changes; a failed read leaves it out
  // (the banner then quotes the group's title) and toasts nothing.
  const firstGroup = shallowRef<AlertGroup | null>(null);
  const firstReply = ref<string | null>(null);
  const loading = ref(false);
  const latest = useLatest();
  // the group and newest row the reply on hand belongs to ("" when there is none)
  let replyOf = "";

  async function readReply(g: AlertGroup | null, ticket: number): Promise<void> {
    const of = g && g.kind === "unclear_reply" ? `${g.kind}/${g.group}/${g.newest_id}` : "";
    if (of === replyOf) return;
    // never a reply of another group beside this group's counts, not even while the new one is read
    firstReply.value = null;
    replyOf = "";
    if (!g || !of) return;
    const reply = await getNewestReply(g).catch(() => null);
    if (!latest.isCurrent(ticket) || !reply) return;
    firstReply.value = reply;
    replyOf = of;
  }

  async function refresh(): Promise<void> {
    const ticket = latest.next();
    loading.value = true;
    try {
      const [stats, groups] = await Promise.all([getStats(scope.provider), getOpenAlertGroups()]);
      if (!latest.isCurrent(ticket)) return;
      data.value = stats;
      // a group of a muted kind (Alert rules) is listed on the Alerts page but never counted or worded here
      const raised = groups.items.filter((g) => !g.muted_until_ms);
      openGroups.value = raised.length;
      muted.value = groups.items.length - raised.length + (stats.muted_alerts?.length ?? 0);
      firstGroup.value = raised[0] ?? null;
      await readReply(firstGroup.value, ticket);
    } catch (err) {
      if (!latest.isCurrent(ticket)) return;
      notify.error(errorText(err));
    } finally {
      if (latest.isCurrent(ticket)) loading.value = false;
    }
  }

  useAuthStore().onSignOut(() => {
    latest.invalidate();
    data.value = null;
    openGroups.value = 0;
    muted.value = 0;
    firstGroup.value = null;
    firstReply.value = null;
    replyOf = "";
    loading.value = false;
  });

  return { data, openGroups, muted, firstGroup, firstReply, loading, refresh };
});
