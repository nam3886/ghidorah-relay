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

// While a drawer's footer is on screen its buttons sit where a bottom-right toast would land: the toast goes above it
// (the default gap of Element Plus, 16 px, over the footer as measured now). Only a footer that is really drawn counts:
// a closed drawer's is destroyed, but a hidden one, one leaving, or one outside the window must not lift a toast.
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
// How far a toast must sit from the bottom edge to clear the footer that is drawn, or undefined when there is none.
function footerClearance(): number | undefined {
  if (typeof document === "undefined") return undefined;
  const foot = [...document.querySelectorAll(".detail-drawer__foot")].find(drawn);
  return foot ? Math.round(foot.getBoundingClientRect().height) + TOAST_GAP_PX : undefined;
}

// A phone sheet's footer wraps to two rows when it holds three buttons: the phone offset is the larger of its own and
// the footer's clearance.
const base = () => {
  const clearance = footerClearance();
  return isPhoneNow()
    ? ({ placement: "bottom", offset: Math.max(PHONE_OFFSET_PX, clearance ?? 0), grouping: true } as const)
    : ({
        placement: "bottom-right",
        grouping: true,
        ...(clearance === undefined ? {} : { offset: clearance }),
      } as const);
};

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
