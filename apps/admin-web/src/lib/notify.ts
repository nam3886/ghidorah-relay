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
    let entry = shown.get(key);
    if (entry) {
      entry.count.value += 1;
    } else {
      const count = ref(1);
      const made = { count, handle: undefined } as Shown;
      const dismiss = closable ? () => made.handle?.close?.() : undefined;
      made.body = () => h(ToastBody, { text, count: count.value, onDismiss: dismiss });
      shown.set(key, (entry = made));
    }
    const own = entry;
    const handle = message({
      ...base(),
      ...options,
      type,
      message: entry.body,
      // a toast that closes forgets its own body only: after `clear` a new toast of the same words may hold the key
      onClose: () => {
        if (shown.get(key) === own) shown.delete(key);
      },
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
