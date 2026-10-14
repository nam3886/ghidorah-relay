import { computed, ref } from "vue";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";

// Runs an action with a busy flag (bind it to a button's :loading or :disabled); a thrown error becomes a toast.
// Overlapping runs are counted, so the flag stays on until the last one ends.
export function useBusy() {
  const pending = ref(0);
  const busy = computed(() => pending.value > 0);

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    pending.value++;
    try {
      return await action();
    } catch (err) {
      notify.error(errorText(err));
      return undefined;
    } finally {
      pending.value--;
    }
  }

  return { busy, run };
}
