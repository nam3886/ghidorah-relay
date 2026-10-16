// The one toast stack (Element Plus messages). Text only: never an HTML string.
// Toasts sit at the bottom right (bottom centre, above the bulk bar, on a phone); equal repeats merge into one with a
// counter, and an error stays 5 s and can be closed. A toast belongs to the tab that raised it: `clear` closes them
// all when the route changes (the shell calls it).
import { defineComponent, h } from "vue";
import { IconAlertCircle, IconCheckCircle } from "@/components/shared/icons";
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

export function makeNotify(message: MessageLike) {
  return {
    success: (text: string) =>
      message({ ...base(), message: text, type: "success", icon: SuccessIcon, duration: DURATION_MS }),
    error: (text: string) =>
      message({
        ...base(),
        message: text,
        type: "error",
        icon: ErrorIcon,
        duration: ERROR_DURATION_MS,
        showClose: true,
      }),
    info: (text: string) => message({ ...base(), message: text, type: "info", duration: DURATION_MS }),
    clear: () => message.closeAll(),
  };
}

export const notify = makeNotify(ElMessage as unknown as MessageLike);
