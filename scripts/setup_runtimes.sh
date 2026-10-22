#!/usr/bin/env bash
# Create one isolated Python environment per engine (spec: do not require one
# Python environment to contain every engine).
#
#   scripts/setup_runtimes.sh            # all runtimes
#   scripts/setup_runtimes.sh sumo swmm  # selected runtimes
#
# Environments are created under $PARALLEL_RUNTIMES_DIR (default: .runtimes/).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPECS="$ROOT/packages/engines/runtimes"
DEST="${PARALLEL_RUNTIMES_DIR:-$ROOT/.runtimes}"
PY="${PARALLEL_RUNTIME_PYTHON:-$(command -v python3)}"
mkdir -p "$DEST"
engines=("$@")
if [ ${#engines[@]} -eq 0 ]; then
  for d in "$SPECS"/*/; do engines+=("$(basename "$d")"); done
fi
for e in "${engines[@]}"; do
  req="$SPECS/$e/requirements.txt"
  [ -f "$req" ] || { echo "no runtime spec for $e" >&2; exit 1; }
  echo "==> $e"
  uv venv -q --python "$PY" "$DEST/$e"
  flags=()
  if head -1 "$req" | grep -q "no-deps"; then flags+=(--no-deps); fi
  VIRTUAL_ENV="$DEST/$e" uv pip install -q "${flags[@]}" -r "$req"
done
echo "Runtimes ready in $DEST"
