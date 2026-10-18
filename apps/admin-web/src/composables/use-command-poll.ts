import { type CommandStatus, getCommand } from "@/api/commands";
import { notify } from "@/lib/notify";

export const POLL_INTERVAL_MS = 1000;
export const POLL_MAX_TRIES = 90;

// Polls a queued command until the worker finishes it: one toast for the outcome. A failed API call ends the poll
// with that error (the caller's busy wrapper toasts it). Returns the final status, or null when still running.
export function useCommandPoll(options: { intervalMs?: number; maxTries?: number } = {}) {
  const intervalMs = options.intervalMs ?? POLL_INTERVAL_MS;
  const maxTries = options.maxTries ?? POLL_MAX_TRIES;

  async function poll(commandId: string, label: string): Promise<CommandStatus | null> {
    for (let i = 0; i < maxTries; i++) {
      const command = await getCommand(commandId);
      if (command.status === "done") {
        // the same form as the failed toast: "Check done"
        notify.success(`${label} done`);
        return command;
      }
      if (command.status === "failed") {
        // as B-States writes it: "Check failed: connect timeout after 8 s"
        notify.error(`${label} failed${command.result?.error ? `: ${command.result.error}` : ""}`);
        return command;
      }
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
    notify.info(`${label}: still running`);
    return null;
  }

  return { poll };
}
