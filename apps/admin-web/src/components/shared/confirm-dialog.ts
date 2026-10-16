import "./confirm-dialog.css";

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  // a destructive action: the confirm button is red
  danger?: boolean;
}

// The part of ElMessageBox this helper uses, so a spec can pass a stand-in.
export interface MessageBoxLike {
  confirm(message: string, title: string, options: object): Promise<unknown>;
}

// Asks and resolves true on confirm, false on Cancel, Esc, backdrop or close. The message is plain text only: HTML
// strings are never rendered (and an HTML string with an inline style would be blocked by the CSP anyway).
export async function confirmDialog(options: ConfirmOptions, box: MessageBoxLike = ElMessageBox): Promise<boolean> {
  try {
    await box.confirm(options.message, options.title, {
      confirmButtonText: options.confirmText ?? "Confirm",
      cancelButtonText: options.cancelText ?? "Cancel",
      customClass: "rl-confirm",
      type: options.danger ? "warning" : "info",
      confirmButtonClass: options.danger ? "el-button--danger" : undefined,
    });
    return true;
  } catch (err) {
    if (err === "cancel" || err === "close") return false;
    throw err;
  }
}
