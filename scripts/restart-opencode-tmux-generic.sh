#!/bin/zsh
set -euo pipefail

TMUX_BIN="${1:?tmux binary required}"
SESSION_NAME="${2:?session name required}"
PROJECT_DIR="${3:?project dir required}"
OPENCODE_SESSION_ID="${4:?opencode session id required}"
TARGET_FILE="${5:?target file required}"

export DYLD_LIBRARY_PATH="$HOME/.local/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export PATH="$HOME/.local/bin:$PATH"

"$TMUX_BIN" kill-session -t "$SESSION_NAME" >/dev/null 2>&1 || true
"$TMUX_BIN" new-session -d -s "$SESSION_NAME" "cd '$PROJECT_DIR' && export DYLD_LIBRARY_PATH='$HOME/.local/lib' PATH='$HOME/.local/bin:$PATH' && exec opencode -s '$OPENCODE_SESSION_ID' ."

sleep 3
PANE_ID="$($TMUX_BIN list-panes -t "$SESSION_NAME" -F '#{pane_id}' | head -n 1)"

if [[ -z "$PANE_ID" ]]; then
  echo "tmux pane not ready" >&2
  exit 1
fi

python3 - <<'PY' "$TARGET_FILE" "$SESSION_NAME" "$PANE_ID" "$PROJECT_DIR" "$OPENCODE_SESSION_ID" "$TMUX_BIN attach -t $SESSION_NAME"
import json
import sys
from pathlib import Path

target_file = Path(sys.argv[1])
data = {
    "session_name": sys.argv[2],
    "pane_id": sys.argv[3],
    "attach_command": sys.argv[6],
    "project_dir": sys.argv[4],
    "opencode_session_id": sys.argv[5],
}
target_file.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
PY

echo "opencode tmux session ready: session=$SESSION_NAME pane=$PANE_ID primary_session=$OPENCODE_SESSION_ID"
