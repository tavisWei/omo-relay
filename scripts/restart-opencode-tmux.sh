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
TARGET_FILE="${OMO_TMUX_TARGET_FILE:-$ROOT_DIR/.omo_tmux_target.json}"
export DYLD_LIBRARY_PATH="$HOME/.local/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export PATH="$HOME/.local/bin:$PATH"

SESSION_INFO="$(python3 - "$ROOT_DIR" <<'PY'
import json
import sqlite3
from pathlib import Path
import hashlib
import sys

root = Path(sys.argv[1]).resolve()
confirmed_path = root / '.omo_confirmed_session.json'
selected_path = root / '.omo_selected_session.json'
explicit = __import__('os').environ.get('OPENCODE_SESSION_ID', '').strip()

session_id = explicit
if not session_id and confirmed_path.exists():
    session_id = json.loads(confirmed_path.read_text(encoding='utf-8')).get('session_id', '').strip()
if not session_id and selected_path.exists():
    session_id = json.loads(selected_path.read_text(encoding='utf-8')).get('session_id', '').strip()

db = Path.home()/'.local/share/opencode/opencode.db'
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
if session_id:
    row = cur.execute("SELECT id FROM session WHERE id=? AND directory=? LIMIT 1", (session_id, root.as_posix())).fetchone()
    if row is None:
        session_id = ''
if not session_id:
    row = cur.execute("SELECT id FROM session WHERE directory=? AND parent_id IS NULL ORDER BY time_updated DESC LIMIT 1", (root.as_posix(),)).fetchone()
    session_id = row['id'] if row else ''
conn.close()
project_key = hashlib.sha256(root.as_posix().encode()).hexdigest()[:12]
session_short = session_id.replace('ses_', '')[:8] if session_id else ''
session_name = f"omo-{project_key}-{session_short}" if session_short else f"omo-{project_key}"
print(json.dumps({"session_id": session_id, "session_name": session_name}))
PY
)"

PRIMARY_SESSION_ID="$(python3 - <<'PY' "$SESSION_INFO"
import json, sys
print(json.loads(sys.argv[1]).get('session_id', ''))
PY
)"
SESSION_NAME="${OPENCODE_TMUX_SESSION:-$(python3 - <<'PY' "$SESSION_INFO"
import json, sys
print(json.loads(sys.argv[1]).get('session_name', ''))
PY
)}"

if [[ -z "$PRIMARY_SESSION_ID" || -z "$SESSION_NAME" ]]; then
  echo "no primary opencode session found for project" >&2
  exit 1
fi

exec "$ROOT_DIR/scripts/restart-opencode-tmux-generic.sh" \
  "$TMUX_BIN" \
  "$SESSION_NAME" \
  "$ROOT_DIR" \
  "$PRIMARY_SESSION_ID" \
  "$TARGET_FILE"
