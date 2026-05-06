#!/bin/zsh
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PROJECT_KEY="$(python3 - "$ROOT_DIR" <<'PY'
from pathlib import Path
import hashlib
import sys
root = Path(sys.argv[1]).resolve().as_posix()
print(hashlib.sha256(root.encode()).hexdigest()[:12])
PY
)"

echo "=== restarting backend ==="
"$ROOT_DIR/scripts/restart-backend.sh" || echo "WARNING: backend restart failed (will retry next restart-all.sh)"

echo "=== restarting frontend ==="
"$ROOT_DIR/scripts/restart-frontend.sh" || echo "WARNING: frontend restart failed"

echo "=== restarting opencode tmux ==="
"$ROOT_DIR/scripts/restart-opencode-tmux.sh" || echo "WARNING: tmux restart failed"

echo "=== restarting watcher ==="
"$ROOT_DIR/scripts/restart-watcher.sh" || echo "WARNING: watcher restart failed"

echo "all services restart attempted for project=$PROJECT_KEY"
