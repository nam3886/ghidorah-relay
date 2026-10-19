import { onBeforeUnmount, watch } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import { confirmDialog } from "./confirm-dialog";

// The one "Discard what you typed?" box. Only one can be up at a time: while it is, every further ask gets the same
// answer (no second box). `ElMessageBox.close()` removes a box but leaves its promise pending for ever, so the latch
// settles a deferred of its own as well.
let pending: Promise<boolean> | null = null;
let keep: (() => void) | null = null;

// true = discard, false = keep editing (Cancel, Escape, backdrop, or the box closed from outside).
export function askDiscard(text = "Discard what you typed?"): Promise<boolean> {
  if (pending) return pending;
  const kept = new Promise<boolean>((resolve) => {
    keep = () => resolve(false);
  });
  const box = confirmDialog({
    title: "Discard changes?",
    message: text,
    confirmText: "Discard",
    cancelText: "Keep editing",
    danger: true,
    // a browser Back changes the hash while the question is up: the question must stay
    closeOnHashChange: false,
  });
  const answer: Promise<boolean> = Promise.race([box, kept]).finally(() => {
    if (pending === answer) {
      pending = null;
      keep = null;
    }
  });
  pending = answer;
  return answer;
}

// Closes a box that is up as "keep" (the parent closed the drawer under it). Nothing to do when none is up.
export function cancelPending(): void {
  if (!pending) return;
  ElMessageBox.close();
  keep?.();
}

// Leaving the page (a route change, a reload, closing the tab) while `isDirty()` is true asks first, with the same box.
export function useRouteLeaveGuard(isDirty: () => boolean): void {
  onBeforeRouteLeave(async () => !isDirty() || (await askDiscard()));
  const warn = (e: BeforeUnloadEvent): void => {
    e.preventDefault();
    e.returnValue = "";
  };
  const stop = watch(
    isDirty,
    (dirty) => {
      if (dirty) window.addEventListener("beforeunload", warn);
      else window.removeEventListener("beforeunload", warn);
    },
    { immediate: true },
  );
  onBeforeUnmount(() => {
    stop();
    window.removeEventListener("beforeunload", warn);
  });
}
