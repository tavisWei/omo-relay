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

TMUX_BIN="${TMUX_BIN:-$HOME/.local/bin/tmux}"
SESSION_NAME="${OPENCODE_TMUX_SESSION:-omo-${PROJECT_KEY}}"
TARGET_FILE="$ROOT_DIR/.omo_tmux_target.json"
export DYLD_LIBRARY_PATH="$HOME/.local/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export PATH="$HOME/.local/bin:$PATH"

PRIMARY_SESSION_ID="$(python3 - <<'PY'
import sqlite3
from pathlib import Path
db = Path.home()/'.local/share/opencode/opencode.db'
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
row = cur.execute("SELECT id FROM session WHERE directory=? AND parent_id IS NULL ORDER BY time_updated DESC LIMIT 1", ('/Users/mrweij/Dev/vibe_coding/opencode/omo-looper',)).fetchone()
print(row['id'] if row else '')
conn.close()
PY
)"

if [[ -z "$PRIMARY_SESSION_ID" ]]; then
  echo "no primary opencode session found for project" >&2
  exit 1
fi

"$TMUX_BIN" kill-session -t "$SESSION_NAME" >/dev/null 2>&1 || true
"$TMUX_BIN" new-session -d -s "$SESSION_NAME" "cd '$ROOT_DIR' && export DYLD_LIBRARY_PATH='$HOME/.local/lib' PATH='$HOME/.local/bin:$PATH' && exec opencode -s '$PRIMARY_SESSION_ID' ."

sleep 3
PANE_ID="$($TMUX_BIN list-panes -t "$SESSION_NAME" -F '#{pane_id}' | head -n 1)"

python3 - <<'PY' "$TARGET_FILE" "$SESSION_NAME" "$PANE_ID" "$ROOT_DIR" "$PRIMARY_SESSION_ID" "$TMUX_BIN attach -t $SESSION_NAME"
import json
import sys
from pathlib import Path

target_file = Path(sys.argv[1])
data = {
    'session_name': sys.argv[2],
    'pane_id': sys.argv[3],
    'attach_command': sys.argv[6],
    'project_dir': sys.argv[4],
    'opencode_session_id': sys.argv[5],
}
target_file.write_text(json.dumps(data, indent=2, sort_keys=True), encoding='utf-8')
PY

echo "opencode tmux session ready: session=$SESSION_NAME pane=$PANE_ID primary_session=$PRIMARY_SESSION_ID"
