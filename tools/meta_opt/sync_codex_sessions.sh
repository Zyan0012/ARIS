#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${1:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
shift || true
CODEX_HOME_ARG=()
SOURCE_ROOTS=("$@")
SOURCE_ROOT_ARGS=()

if [ "${CODEX_HOME:-}" != "" ]; then
  CODEX_HOME_ARG=(--codex-home "$CODEX_HOME")
fi

if [ "${#SOURCE_ROOTS[@]}" -eq 0 ]; then
  for root in /mnt/d/ML/research_code /mnt/e/paper; do
    [ -d "$root" ] && SOURCE_ROOTS+=("$root")
  done
fi

for root in "${SOURCE_ROOTS[@]}"; do
  SOURCE_ROOT_ARGS+=(--source-root "$root")
done

python3 "$SCRIPT_DIR/codex_aris_log_wrapper.py" --sync-sessions --project "$PROJECT" --include-other-projects --aris-related-only "${SOURCE_ROOT_ARGS[@]}" "${CODEX_HOME_ARG[@]}"
