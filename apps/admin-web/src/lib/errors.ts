import { ApiError } from "@/api/client";
import { OperationKeyStorageError } from "@/lib/operation-key";

// The kept retry key lives in this tab's sessionStorage: another tab would send a new key.
export const RETRY_SAFE_TEXT = "Trying again is safe. Retry the same selection in this tab; it will not charge twice.";

// What a toast says for a failed call.
export function errorText(err: unknown): string {
  if ((err as { retrySafe?: boolean } | null)?.retrySafe) return RETRY_SAFE_TEXT;
  if (err instanceof OperationKeyStorageError) return err.message;
  if (err instanceof ApiError) return err.param ? `${err.message} (${err.param})` : err.message;
  return "Network error, please try again";
}
