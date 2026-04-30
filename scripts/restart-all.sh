#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PROJECT_KEY="$(python3 - "$ROOT_DIR" <<'PY'
from pathlib import Path
import hashlib
import sys
root = Path(sys.argv[1]).resolve().as_posix()
print(hashlib.sha256(root.encode()).hexdigest()[:12])
PY
)"

"$ROOT_DIR/scripts/restart-backend.sh"
"$ROOT_DIR/scripts/restart-frontend.sh"
"$ROOT_DIR/scripts/restart-opencode-tmux.sh"
"$ROOT_DIR/scripts/restart-watcher.sh"

echo "all services restarted for project=$PROJECT_KEY"
