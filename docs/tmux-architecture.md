# Tmux Integration Architecture — OMO Relay

> **Status**: Living document. Update when tmux integration behavior changes.
> **Last updated**: 2026-05-06 (v2 — send-keys epoch)

## 1. System Overview

OMO Relay uses tmux to create persistent terminal sessions that run OpenCode (a terminal-based AI coding assistant). The Watcher loop monitors OpenCode session state via its SQLite database and dispatches queued tasks into the tmux session using **character-by-character `send-keys`** (not `paste-buffer`).

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│  Watcher    │────▶│  Session     │────▶│  Tmux         │
│  (poll 5s)  │     │  Continuer   │     │  Session      │
└─────────────┘     └──────────────┘     └───────────────┘
       │                    │                     │
       │                    │                     ▼
       │                    │            ┌───────────────┐
       │                    └───────────▶│  OpenCode     │
       │                                 │  (node TUI)   │
       ▼                                 └───────────────┘
┌─────────────┐
│  OpenCode   │
│  SQLite DB  │
└─────────────┘
```

## 2. Core Modules

| Module | File | Role |
|--------|------|------|
| **TmuxTargetStore** | `tmux_target.py` | Session lifecycle: create, validate, kill, metadata persistence |
| **OpencodeSessionContinuer** | `session_continuer.py` | Task injection: build prompt, ensure target, send to tmux |
| **WatchLoop** | `watch.py` | Main loop: observe session, decide to dispatch, handle errors |
| **OpenCodeObserver** | `opencode_observer.py` | Read OpenCode DB to determine session readiness |

## 3. Tmux Session Lifecycle

### 3.1 Session Naming

```
omo-{project_hash[:12]}-{session_short_id[:8]}

Example: omo-d748b52b2c1a-22283f70
         │   │              │
         │   │              └── First 8 chars of OpenCode session ID (ses_ stripped)
         │   └── SHA256(project_dir)[:12]
         └── Fixed prefix
```

### 3.2 Session Creation

1. `TmuxTargetStore.ensure_target()` is called
2. Validates existing target metadata (`.omo_tmux_target.json`)
3. If stale/missing, kills existing session and creates new one
4. Shell script: `tmux new-session -d -s $NAME "cd $PROJECT && exec opencode -s $SESSION_ID ."`
5. Waits for pane to stabilize (3s in script, additional validation in Python)
6. Writes target metadata: `{session_name, pane_id, attach_command, project_dir, opencode_session_id}`

### 3.3 Session Validation

`validate_target()` checks in order:
1. Metadata file exists - `"tmux target metadata missing"`
2. Project directory matches - `"tmux target project mismatch"`
3. OpenCode session ID matches - `"tmux target session mismatch"`
4. Tmux session running (`has-session`) - `"tmux session not running"`
5. Pane exists (`list-panes` by pane_id) - `"tmux pane missing"`

### 3.4 Session Destruction

- `TmuxTargetStore.clear()` removes the JSON metadata file
- Shell script: `tmux kill-session -t $NAME` (done before creating new)

## 4. Task Dispatch Flow

### 4.1 Watcher Decision Logic

Each poll cycle (default 5s):

```
1. Locate session (confirmed → selected → primary from DB)
2. Take snapshot of OpenCode session state
3. Recover any running task (check if session advanced)
4. Check readiness: ready_for_continuation / soft_stalled / stalled
5. If not ready → skip this cycle
6. If running task exists → skip
7. claim_next() from queue → if none → skip
8. _launch_task() → calls SessionContinuer
```

### 4.2 Session Continuer Flow

```python
continue_task(session_id, task):
    1. ensure_task_target()  ─── ensures tmux session exists and is valid
    2. _resolve_live_pane()  ─── queries live tmux for current pane ID
    3. _is_pane_ready()      ─── checks pane shows "OpenCode" TUI (30s poll + 15s wait)
    4. _send_command()       ─── character-by-character send-keys + Enter
    5. _pane_exists()        ─── verify pane still alive
```

### 4.3 Task Injection Mechanism (CRITICAL: send-keys, NOT paste-buffer)

**Confirmed working approach** (2026-05-06):

Tasks are injected **character-by-character** using `send-keys`, NOT `paste-buffer`.

| Method | Works on interrupted opencode? | Performance |
|--------|-------------------------------|-------------|
| `paste-buffer` (load-buffer + paste-buffer) | ❌ No | Fast |
| `send-keys -l` (literal, all at once) | ❌ No | Fast |
| `send-keys` (character-by-character, 10ms delay) | ✅ Yes | ~1.5s per 150-char prompt |

**Why paste-buffer fails**: OpenCode in "interrupted" TUI state does not process `paste-buffer` input. The text arrives at the TTY but the event loop ignores it. Character-by-character `send-keys` simulates real typing, which the TUI event loop DOES process even when interrupted.

**Implementation** (`session_continuer.py:_send_command`):

```python
def _send_command(self, pane_target: str, text: str) -> subprocess.CompletedProcess[str]:
    for ch in text:
        result = subprocess.run(
            [self._tmux_executable, "send-keys", "-t", pane_target, ch],
            capture_output=True, check=False, env=tmux_environment(),
        )
        if result.returncode != 0:
            return result
        time.sleep(0.01)  # 10ms per character
    return subprocess.run(
        [self._tmux_executable, "send-keys", "-t", pane_target, "Enter"],
        capture_output=True, text=True, check=False, env=tmux_environment(),
    )
```

### 4.4 Pane Readiness Check

```
_is_pane_ready(pane_target):
    - capture-pane -t pane_target -p
    - Check: returncode == 0 AND "OpenCode" in captured text
    - If NOT ready: send Escape to dismiss interrupted TUI, then poll every 1s up to 30 attempts, then wait 15s extra
```

### 4.5 Post-Send Validation

Uses `_pane_exists()` (not `_is_pane_ready()`) to avoid false-positive failures:
- `_pane_exists(pane_target)`: checks only that the pane is alive (`list-panes -t pane_target`)
- Does NOT check for "OpenCode" text (which may not be visible in interrupted TUI)

## 5. Error Handling & Recovery

### 5.1 OpenCode "interrupted" State

When a previous OpenCode run was interrupted (Ctrl+C or process killed), reconnecting via `opencode -s session_id` shows the TUI in "interrupted" state. In this state:
- ❌ `paste-buffer` input is ignored
- ❌ `send-keys -l` input is ignored  
- ✅ Character-by-character `send-keys` is processed
- ✅ The TUI shows "Sisyphus - Ultraworker · kimi-for-coding · interrupted" in the status bar

**Detection**: `_is_pane_ready` returns False because the interrupted TUI does not contain "OpenCode" text. When this happens, an Escape key is sent to attempt dismissal, then the poll loop waits.

### 5.2 Tmux-Specific Errors

The following errors are classified as "tmux recovery errors" and receive special treatment:

| Error Token | Meaning |
|-------------|---------|
| `failed to create tmux target` | Shell script or tmux session creation failed |
| `tmux pane not ready` | Pane didn't appear within timeout |
| `can't find pane` | Pane disappeared |
| `can't find session` | Session was killed externally |
| `tmux pane missing` | Pane not found during validation |
| `tmux session not running` | Session stopped |
| `tmux target session mismatch` | OpenCode session ID doesn't match |

### 5.3 Tmux Error Recovery Policy

- Task transitions to `RETRY_WAIT`
- `retry_count` is incremented
- Checked against `max_retries` before retry
- After `max_retries`, task stays in `RETRY_WAIT` requiring manual intervention

### 5.4 Non-Tmux Recovery (no_message_advance)

When a task was sent (launch_success) but the OpenCode session didn't advance:
- Recovery checks if `latest_message_id` changed (session advanced) → `recover_done`
- If unchanged AND session is `stalled` (no activity for `stalled_threshold`) → `recover_retry`
- If unchanged AND session is NOT stalled (assistant is working) → `recover_wait` (keep waiting)

**Key design decision**: Use `stalled` (not `ready_for_continuation`) for retry gating. This ensures we only retry when the session is truly idle, not when the assistant is actively generating a response that hasn't completed yet.

## 6. Known Bug Patterns & History

### Bug #1 (FIXED 2026-05-06): Paste-buffer does not work on interrupted OpenCode TUI

**Root cause**: `_send_buffer` used `load-buffer` + `paste-buffer`, which OpenCode's "interrupted" TUI state ignores. The text arrives at the TTY but the event loop does not process it.

**Fix**: Replaced `_send_buffer` with `_send_command` that uses character-by-character `send-keys` (10ms delay per character), which simulates real typing and IS processed by the interrupted TUI.

**Files changed**: `session_continuer.py:_send_buffer()` → `_send_command()`

**Evidence**: 2 tasks (`5257c6ef` and `4b37f162`) successfully dispatched and processed after the fix, confirmed by new messages in the OpenCode DB.

### Bug #2 (FIXED 2026-05-06): Post-send validation false-positive on interrupted TUI

**Root cause**: Post-send validation used `_is_pane_ready()` which checks for "OpenCode" in `capture-pane`. Interrupted TUI does not show "OpenCode", causing valid dispatches to be marked as failures.

**Fix**: Changed post-send validation to use `_pane_exists()` which only checks if the pane is alive, not text content.

### Bug #3 (FIXED 2026-05-06): Stale pane_id targeting

**Root cause**: Pane ID changes every time tmux session is recreated. Target file may have stale pane_id.

**Fix**: Added `_resolve_live_pane()` that queries live tmux for current pane before each dispatch.

### Bug #4 (FIXED 2026-05-06): Premature recovery retry

**Root cause**: `recover_retry` used `ready_for_continuation` (idle for 3s) which fires too fast. Assistant takes 20s+ to respond, but retries exhausted in 15s.

**Fix**: Changed retry gating from `ready_for_continuation` to `stalled` (idle for `stalled_threshold` seconds, default 120s).

### Bug #5 (FIXED 2026-05-06): recover_retry never incrementing retry_count

**Root cause**: `recover_retry` in `_recover_running_task()` did not increment `retry_count`, causing infinite loops.

**Fix**: Added `task.retry_count += 1` before the RETRY_WAIT transition.

### Bug #6 (FIXED 2026-05-06): recover_retry timestamp comparison reversed

**Root cause**: `latest_message_completed_ms > launched_at_ms` should have been `<` to check "completed before launch."

**Fix**: Changed `>` to `<`.

### Bug #7 (FIXED 2026-05-06): restart-all.sh set -e kills entire restart

**Root cause**: `set -euo pipefail` caused `restart-backend.sh` failure to abort before `restart-watcher.sh` ran.

**Fix**: Removed `set -e`, added `|| echo WARNING` fallback for each sub-script.

### Bug #8 (FIXED 2026-05-06): .pyc bytecode cache masking source changes

**Root cause**: Python loads cached `.pyc` files even after `.py` source was modified. Code fixes never took effect after restart.

**Fix**: 
- `restart-watcher.sh`: clears `__pycache__` before each start
- `restart-watcher.sh`: adds `PYTHONDONTWRITEBYTECODE=1` environment variable

## 7. Code Invariants (DO NOT VIOLATE)

1. **Task injection**: Always use character-by-character `send-keys` with 10ms inter-character delay. NEVER use `paste-buffer` or `send-keys -l`. These do not work on interrupted sessions.

2. **Pane targeting**: Before each dispatch, call `_resolve_live_pane()` to get the current pane ID from live tmux. Never use stored `pane_id` directly.

3. **Post-send validation**: Use `_pane_exists()` (checks pane is alive). Do NOT use `_is_pane_ready()` (checks for "OpenCode" text — unreliable on interrupted TUI).

4. **Recovery retry gating**: Use `stalled()` (not `ready_for_continuation()`) to decide when to retry. This prevents premature retries while assistant is working.

5. **Retry budget**: Both tmux errors and no-advance errors must respect `max_retries`. `retry_count` must be incremented for both.

6. **No `set -e` in restart-all.sh**: Each service restart must use `|| echo WARNING` to allow remaining services to start if one fails.

7. **No `.pyc` cache**: Every restart must clear `__pycache__` and use `PYTHONDONTWRITEBYTECODE=1`.

## 8. Deployment Checklist

Before deploying code changes that affect tmux integration:

1. Clear bytecode cache: `find . -type d -name "__pycache__" -exec rm -rf {} +`
2. Kill existing watchers: `pkill -f "omo_task_queue.watch"`
3. Start watcher with: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:$PYTHONPATH" python -m omo_task_queue.watch --directory . --poll-interval 5 --log-level INFO`
4. Verify with test task: insert a pending task into the queue and watch for `launch_success`
5. Check OpenCode DB for new messages after task dispatch

## 9. Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `TMUX_BIN` | `~/.local/bin/tmux` | Tmux executable path |
| `WATCHER_POLL_INTERVAL` | `5` | Seconds between watcher polls |
| `PYTHONDONTWRITEBYTECODE` | `1` | MUST be set to prevent stale bytecode |
| `DYLD_LIBRARY_PATH` | `~/.local/lib:$DYLD_LIBRARY_PATH` | Library path for tmux/openlink |
| `PATH` | `~/.local/bin:$PATH` | Path for finding opencode/tmux |

## 10. Debugging Checklist

When tmux tasks aren't dispatching:

1. Check watcher status: `cat .omo_watcher_status.json | python -m json.tool`
2. Check task queue: `sqlite3 omo_task_queue.db "SELECT id, status, retry_count, error_message FROM tasks"`
3. Check tmux session: `tmux has-session -t omo-* && tmux list-panes -t omo-* -F '#{pane_id}|#{pane_current_command}|#{pane_active}'`
4. Check pane content: `tmux capture-pane -t omo-* -p | head -20`
5. Check target metadata: `cat .omo_tmux_target*.json`
6. Check watcher log: `tail -50 logs/watcher.*.log | grep -E "launch|error|skip|recover"`
7. Verify opencode is running: The pane command should be `node`
8. Check OpenCode DB for new messages: `sqlite3 ~/.local/share/opencode/opencode.db "SELECT id, time_updated FROM message WHERE session_id='ses_...' ORDER BY time_created DESC LIMIT 5"`
9. Manually test paste: `echo "/ulw-loop test" | tmux load-buffer - && tmux paste-buffer -t %N`
10. Manually test send-keys: `tmux send-keys -l -t %N "/ulw-loop test" && tmux send-keys -t %N Enter`
