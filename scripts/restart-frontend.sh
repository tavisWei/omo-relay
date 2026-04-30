#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/logs"
PROJECT_KEY="$(python3 - "$ROOT_DIR" <<'PY'
from pathlib import Path
import hashlib
import sys
root = Path(sys.argv[1]).resolve().as_posix()
print(hashlib.sha256(root.encode()).hexdigest()[:12])
PY
)"
PID_FILE="$ROOT_DIR/.frontend.${PROJECT_KEY}.pid"
HOST="${FRONTEND_HOST:-127.0.0.1}"
PORT="${FRONTEND_PORT:-$((30000 + 0x${PROJECT_KEY:4:4} % 10000))}"
BACKEND_PORT="${OMO_UI_PORT:-$((20000 + 0x${PROJECT_KEY:0:4} % 10000))}"
TIMEOUT_SECONDS="${FRONTEND_START_TIMEOUT:-20}"

mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" >/dev/null 2>&1; then
    kill "$OLD_PID" >/dev/null 2>&1 || true
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

PORT_PID="$(lsof -ti tcp:"$PORT" || true)"
if [[ -n "$PORT_PID" ]]; then
  kill $PORT_PID >/dev/null 2>&1 || true
  sleep 1
fi

nohup env OMO_UI_PORT="$BACKEND_PORT" npm --prefix "$FRONTEND_DIR" run dev -- --host "$HOST" --port "$PORT" > "$LOG_DIR/frontend.${PROJECT_KEY}.log" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

READY=0
for ((i=0; i<TIMEOUT_SECONDS; i++)); do
  if python3 - "$HOST" "$PORT" <<'PY'
import sys
from urllib.request import urlopen

host, port = sys.argv[1], sys.argv[2]
try:
    with urlopen(f"http://{host}:{port}", timeout=2) as resp:
        raise SystemExit(0 if resp.status == 200 else 1)
except Exception:
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
  echo "frontend start timeout after ${TIMEOUT_SECONDS}s" >&2
  exit 1
fi

echo "frontend restarted: pid=$NEW_PID host=$HOST port=$PORT timeout=${TIMEOUT_SECONDS}s"
