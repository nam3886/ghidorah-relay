import { computed, type ComputedRef } from "vue";
import { useStatsStore } from "@/stores/stats";

// The counts the main navigation carries (top bar and bottom bar alike): Accounts "usable/total" (total = available, dead
// and disabled, as the provider table counts it) in a purple pill, Alerts the open alert groups in an amber one. A tab with nothing to count has none.
export function useNavCounts(): {
  counts: ComputedRef<Record<string, number | string>>;
  countText: (value: number | string) => string | number;
} {
  const stats = useStatsStore();
  const accountsCount = computed(() => {
    const a = stats.data?.accounts;
    const total = a ? a.available + a.dead + a.disabled : 0;
    return a && total > 0 ? `${a.usable}/${total}` : 0;
  });
  const counts = computed<Record<string, number | string>>(() => ({
    accounts: accountsCount.value,
    alerts: stats.openGroups,
  }));
  // A number above 999 reads 999+.
  const countText = (value: number | string): string | number =>
    typeof value === "number" && value > 999 ? "999+" : value;
  return { counts, countText };
}
