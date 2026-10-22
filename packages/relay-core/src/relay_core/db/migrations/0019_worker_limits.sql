-- Expand only: the container limits a worker read from its own cgroup at start; NULL from a worker that predates it.
ALTER TABLE worker_heartbeats ADD COLUMN limits jsonb;
