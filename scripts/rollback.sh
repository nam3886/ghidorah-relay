#!/usr/bin/env bash
# Roll the box back to the tree kept from the deploy before: see scripts/deploy.sh --help.
#   scripts/rollback.sh [<sha>] [--workers dola,meta] [--allow-schema-mismatch]
exec "$(dirname "$0")/deploy.sh" --rollback "$@"
