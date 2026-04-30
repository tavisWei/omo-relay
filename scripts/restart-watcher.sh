#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
PROJECT_KEY="$(python3 - "$ROOT_DIR" <<'PY'
from pathlib import Path
import hashlib
import sys
root = Path(sys.argv[1]).resolve().as_posix()
print(hashlib.sha256(root.encode()).hexdigest()[:12])
PY
)"
PID_FILE="$ROOT_DIR/.watcher.${PROJECT_KEY}.pid"
LOG_FILE="$LOG_DIR/watcher.${PROJECT_KEY}.log"
POLL_INTERVAL="${WATCHER_POLL_INTERVAL:-5}"
TIMEOUT_SECONDS="${WATCHER_START_TIMEOUT:-20}"

mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" >/dev/null 2>&1; then
    kill "$OLD_PID" >/dev/null 2>&1 || true
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

nohup env PYTHONPATH="$ROOT_DIR/src" python3 -m omo_task_queue.watch --directory "$ROOT_DIR" --poll-interval "$POLL_INTERVAL" --log-level INFO > "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

READY=0
for ((i=0; i<TIMEOUT_SECONDS; i++)); do
  if python3 - "$ROOT_DIR" <<'PY'
import json
import sys
from pathlib import Path

status_path = Path(sys.argv[1]) / '.omo_watcher_status.json'
if not status_path.exists():
    raise SystemExit(1)

data = json.loads(status_path.read_text(encoding='utf-8'))
if data.get('heartbeat_ms'):
    raise SystemExit(0)
raise SystemExit(1)
PY
  then
    READY=1
    break
  fi
  sleep 1
done

if [[ "$READY" -ne 1 ]]; then
  kill "$NEW_PID" >/dev/null 2>&1 || true
  rm -f "$PID_FILE"
  echo "watcher start timeout after ${TIMEOUT_SECONDS}s" >&2
  exit 1
fi

echo "watcher restarted: pid=$NEW_PID poll_interval=${POLL_INTERVAL}s timeout=${TIMEOUT_SECONDS}s"
