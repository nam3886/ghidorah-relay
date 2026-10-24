import { defineComponent, h, markRaw } from "vue";
import { IconInfo, IconTrashConfirm } from "@/components/shared/icons";
import "./confirm-dialog.css";

// The tone tile's glyph (Dialogs): the bin on a destructive question, the calm "i" on any other; 18 px at stroke 2.
const DangerGlyph = markRaw(defineComponent({ render: () => h(IconTrashConfirm, { size: 18, stroke: 2 }) }));
const CalmGlyph = markRaw(defineComponent({ render: () => h(IconInfo, { size: 18, stroke: 2 }) }));

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  // a destructive action: the confirm button is red
  danger?: boolean;
  // Element Plus closes a box when the address hash changes (as "close"); a question about leaving the page must
  // outlive the hash change of a browser Back, so it passes false
  closeOnHashChange?: boolean;
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
      closeOnHashChange: options.closeOnHashChange ?? true,
      // the canvas draws no close X: Cancel, Esc and the backdrop are the ways out
      showClose: false,
      type: options.danger ? "warning" : "info",
      icon: options.danger ? DangerGlyph : CalmGlyph,
      modalClass: "rl-confirm-scrim",
      confirmButtonClass: options.danger ? "el-button--danger" : undefined,
    });
    return true;
  } catch (err) {
    if (err === "cancel" || err === "close") return false;
    throw err;
  }
}
