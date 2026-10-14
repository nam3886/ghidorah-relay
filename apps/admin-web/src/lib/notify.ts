// The one toast stack (Element Plus messages). Text only: never an HTML string.
const DURATION_MS = 2500;

export const notify = {
  success: (message: string) => ElMessage({ message, type: "success", duration: DURATION_MS }),
  error: (message: string) => ElMessage({ message, type: "error", duration: DURATION_MS }),
  info: (message: string) => ElMessage({ message, type: "info", duration: DURATION_MS }),
};
