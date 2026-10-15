// The one toast stack (Element Plus messages). Text only: never an HTML string.
// Toasts sit at the bottom right, clear of the header and the tab bar; equal repeats merge into one with a counter,
// and an error stays longer and can be closed. A toast belongs to the tab that raised it: `clear` closes them all
// when the route changes (the shell calls it).
const DURATION_MS = 2500;
const ERROR_DURATION_MS = 6000;
const BASE = { placement: "bottom-right", grouping: true } as const;

export const notify = {
  success: (message: string) => ElMessage({ ...BASE, message, type: "success", duration: DURATION_MS }),
  error: (message: string) =>
    ElMessage({ ...BASE, message, type: "error", duration: ERROR_DURATION_MS, showClose: true }),
  info: (message: string) => ElMessage({ ...BASE, message, type: "info", duration: DURATION_MS }),
  clear: () => ElMessage.closeAll(),
};
