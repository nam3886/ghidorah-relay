import { request } from "@/api/client";

// A command is queued by an admin action; the provider's worker runs it and the page polls its status.
export interface CommandStatus {
  id: string;
  provider: string;
  kind: string;
  account_id: string | null;
  job_id: string | null;
  status: "queued" | "running" | "done" | "failed" | string;
  result: { error?: string; [key: string]: unknown } | null;
  attempts: number;
  created_ms: number | null;
  finished_ms: number | null;
}

export const getCommand = (id: string): Promise<CommandStatus> => request(`/commands/${encodeURIComponent(id)}`);
