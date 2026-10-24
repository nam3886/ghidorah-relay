// The one toast stack (Element Plus messages). Text only: never an HTML string.
// Toasts sit at the bottom right (bottom centre, above the bulk bar, on a phone); equal repeats merge into one with a
// counter, and an error stays 5 s and can be closed. A toast belongs to the tab that raised it: `clear` closes them
// all when the route changes (the shell calls it).
// Element Plus keeps the stack, the timers and the grouping; the body is drawn as B-States draws it: the text, a "×3"
// pill once it repeats, and on an error the X close button. Element Plus groups a repeat by the identical `message`,
// so each open toast keeps one body function, found again by its type and text until the toast closes, and its count
// is ours (Element Plus's own round badge and close glyph are hidden by notify.css).
import { defineComponent, h, type PropType, ref, type Ref } from "vue";
import { IconAlertCircle, IconCheckCircle, IconClose } from "@/components/shared/icons";
// the shared skin (buttons, checkbox, menus) rides with the one module every view already imports
import "@/components/shared/skin.css";
import "./notify.css";

// the toast icons are 18 px, the canvas size (the default icon of a component is 16)
const SuccessIcon = defineComponent({ render: () => h(IconCheckCircle, { size: 18 }) });
const ErrorIcon = defineComponent({ render: () => h(IconAlertCircle, { size: 18 }) });

const DURATION_MS = 2500;
const ERROR_DURATION_MS = 5000;
// the height of the phone's sticky bulk bar plus a gap
const PHONE_OFFSET_PX = 72;

// Read at the moment of the toast (this is not a component, so it cannot use the breakpoint composable); the same
// 768 px edge as C3. Without matchMedia (happy-dom) it is desktop.
export const isPhoneNow = (): boolean =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(max-width: 767.98px)").matches;

// While a drawer's footer is on screen its buttons sit where a toast lands, so every toast that is showing, raised
// before or after the drawer opened, is lifted above it, and comes back down when the footer goes. The lift is one
// custom property on the page (`--rl-toast-lift`, read by notify.css as a `translate` that slides): no toast is
// tracked or closed, and a stack of toasts keeps its order. Only a footer that is really drawn counts: a hidden one,
// one leaving, or one outside the window must not lift a toast.
const LIFT_PROPERTY = "--rl-toast-lift";
const TOAST_GAP_PX = 16;
function drawn(foot: Element): boolean {
  if (foot.getClientRects().length === 0) return false;
  const style = getComputedStyle(foot);
  if (style.display === "none" || style.visibility === "hidden") return false;
  // the drawer that is closing keeps its footer for the leave transition
  for (let e = foot.parentElement; e; e = e.parentElement) if (/-leave-(active|to)/.test(e.className)) return false;
  const r = foot.getBoundingClientRect();
  return (
    r.width > 0 &&
    r.height > 0 &&
    r.bottom > 0 &&
    r.right > 0 &&
    r.top < window.innerHeight &&
    r.left < window.innerWidth
  );
}
// A drawer's footer, else the Settings page's unsaved-changes bar (`.sbar`, which floats above the foot of the window).
const drawnFooter = (): Element | undefined =>
  typeof document === "undefined"
    ? undefined
    : [...document.querySelectorAll(".detail-drawer__foot, .sbar")].find(drawn);

// How far up the toasts go: the footer's height on a desktop (the default gap of Element Plus, 16 px, stays above it);
// on a phone, where toasts already sit 72 px up, only what the footer needs beyond that (a wrapped footer). A bar that
// does not sit on the foot of the window counts from its top down to that foot.
export function toastLift(): number {
  const foot = drawnFooter();
  if (!foot) return 0;
  const r = foot.getBoundingClientRect();
  const height = Math.round(foot.classList.contains("sbar") ? window.innerHeight - r.top : r.height);
  return isPhoneNow() ? Math.max(0, height + TOAST_GAP_PX - PHONE_OFFSET_PX) : height;
}
let measured: Element | undefined;
let sizes: ResizeObserver | undefined;
export function syncToastLift(): void {
  if (typeof document === "undefined") return;
  document.documentElement.style.setProperty(LIFT_PROPERTY, `${toastLift()}px`);
  // a footer that changes height (a phone footer that wraps) lifts again
  const foot = drawnFooter();
  if (foot !== measured) {
    if (measured) sizes?.unobserve(measured);
    measured = foot;
    if (foot) sizes?.observe(foot);
  }
}
// Watches the page for a footer that appears, resizes or leaves; one frame of delay is plenty and joins the bursts.
let watching = false;
export function watchDrawerFooter(): void {
  if (watching || typeof document === "undefined" || typeof MutationObserver === "undefined" || !document.body) return;
  watching = true;
  let queued = false;
  const later = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      syncToastLift();
    });
  };
  if (typeof ResizeObserver !== "undefined") sizes = new ResizeObserver(later);
  new MutationObserver(later).observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["class"],
  });
  window.addEventListener("resize", later);
  later();
}
watchDrawerFooter();

const base = () =>
  isPhoneNow()
    ? ({ placement: "bottom", offset: PHONE_OFFSET_PX, grouping: true } as const)
    : ({ placement: "bottom-right", grouping: true } as const);

// The part of ElMessage this module uses, so a spec can pass a stand-in.
export interface MessageLike {
  (options: object): unknown;
  closeAll(): void;
}

// The drawn body: the text, then the count pill from the second time on, then the close button when there is one.
export const ToastBody = defineComponent({
  name: "ToastBody",
  props: {
    text: { type: String, required: true },
    count: { type: Number, default: 1 },
    onDismiss: { type: Function as PropType<() => void>, default: undefined },
  },
  setup(props) {
    return () => [
      h("span", { class: "rl-toast__text" }, props.text),
      props.count > 1 ? h("span", { class: "rl-toast__count" }, `×${props.count}`) : null,
      props.onDismiss
        ? h(
            "button",
            { type: "button", class: "rl-toast__close", "aria-label": "Close message", onClick: props.onDismiss },
            h(IconClose, { size: 14, stroke: 2 }),
          )
        : null,
    ];
  },
});

interface Shown {
  body: () => ReturnType<typeof h>;
  count: Ref<number>;
  handle: { close?: () => void } | undefined;
}

export function makeNotify(message: MessageLike) {
  const shown = new Map<string, Shown>();

  // One body per open toast of this type and text: a repeat reuses it (Element Plus groups it and restarts its timer)
  // and counts one more; a closed toast forgets it.
  function toast(type: string, text: string, options: object, closable: boolean): unknown {
    const key = `${type}\n${text}`;
    // a toast forgets its own body only: after `clear` or its X, a new toast of the same words may hold the key
    const forget = (own: Shown) => {
      if (shown.get(key) === own) shown.delete(key);
    };
    let entry = shown.get(key);
    if (entry) {
      entry.count.value += 1;
    } else {
      const count = ref(1);
      const made = { count, handle: undefined } as Shown;
      // Forget first: Element Plus groups into a toast until its leave starts, on the next render, so the same error
      // raised right after the click is a new toast, not a repeat of the one going away.
      const dismiss = closable
        ? () => {
            forget(made);
            made.handle?.close?.();
          }
        : undefined;
      made.body = () => h(ToastBody, { text, count: count.value, onDismiss: dismiss });
      shown.set(key, (entry = made));
    }
    const own = entry;
    syncToastLift();
    const handle = message({
      ...base(),
      ...options,
      type,
      message: entry.body,
      onClose: () => forget(own),
    }) as Shown["handle"];
    entry.handle ??= handle;
    return handle;
  }

  return {
    success: (text: string) => toast("success", text, { icon: SuccessIcon, duration: DURATION_MS }, false),
    error: (text: string) =>
      toast("error", text, { icon: ErrorIcon, duration: ERROR_DURATION_MS, showClose: true }, true),
    info: (text: string) => toast("info", text, { duration: DURATION_MS }, false),
    // A closing toast is still in Element Plus's list until it has left: forget the bodies first, so the same words shown
    // right after (on the next page) are a new toast, not a repeat of one that is going away.
    clear: () => {
      shown.clear();
      message.closeAll();
    },
  };
}

export const notify = makeNotify(ElMessage as unknown as MessageLike);
