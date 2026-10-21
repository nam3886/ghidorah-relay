## Workflow

Team work uses the superpowers plugin as shipped. This folder is the relay repo on `main` (the owner's); task worktrees live in `.worktrees/<slug>`, and the integration branch `admin-ui` is `.worktrees/admin-ui`. Tests run on the box (`phuongnam@192.168.18.227`) through `scripts/box.sh` and `scripts/web.sh`, always with `env RELAY_GATE_CPUS=0-23` and `--tree <the worktree's folder name>`.

1. A subagent edits and commits only in the task's own worktree (`git worktree add .worktrees/<slug> -b <slug> <base>`); it never touches the root (`main`), `.worktrees/admin-ui` or their git index.
2. A subagent that writes code runs the lint and narrow tests of what it changed before reporting done: `scripts/box.sh -- scripts/check.sh <test files>` (runs ruff and mypy too); `scripts/box.sh -- scripts/check-in-container.sh <test files>` for UI and browser tests; `scripts/web.sh check` when `apps/admin-web` changed.
3. Gate before hand-over: `scripts/box.sh --detach gate-<slug> -- scripts/check.sh --full && scripts/box.sh --wait gate-<slug>`, ending `CHECK: GREEN`; when a test is renamed or removed, first run `scripts/box.sh -- env MUTATE_ONLY="<every marker file naming it>" SOAK_SEEDS=0 scripts/check.sh --full --local`.
