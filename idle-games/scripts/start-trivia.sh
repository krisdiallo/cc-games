#!/usr/bin/env bash
# UserPromptSubmit hook — start (or reuse) a trivia game for this session.
#
# CONTRACT (spec §4): print NOTHING to stdout, spawn detached, exit 0 fast.
# Any real work (debounce + terminal spawn) happens in the detached _launch.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

INPUT="$(read_stdin)"
SESSION_ID="$(json_field "$INPUT" session_id)"
[ -z "$SESSION_ID" ] && SESSION_ID="default"
TRANSCRIPT="$(json_field "$INPUT" transcript_path)"

# Opt-outs, cheapest first.
# 1. Per-session env: IDLE_GAMES=off claude   (env propagates to hooks)
case "${IDLE_GAMES:-}" in
  off|OFF|0|false|no)
    log info "disabled via IDLE_GAMES env; skip ($SESSION_ID)"
    exit 0 ;;
esac

# 2. Global config kill switch.
if [ "$(config_get enabled true)" != "true" ]; then
  log info "disabled via config; skip start ($SESSION_ID)"
  exit 0
fi

# 3. Per-project marker: touch .no-idle-games in a repo to keep it game-free.
CWD="$(json_field "$INPUT" cwd)"
if [ -n "$CWD" ] && [ -f "$CWD/.no-idle-games" ]; then
  log info "disabled by $CWD/.no-idle-games; skip ($SESSION_ID)"
  exit 0
fi

# 4. Per-session in-game opt-out: the user pressed Q (quit for session).
if [ -f "$TRIVIA_HOME/$SESSION_ID.quiet" ]; then
  log info "session muted by Q; skip ($SESSION_ID)"
  exit 0
fi

# Multi-turn reuse: a new prompt clears any stop marker so a lingering game
# un-wraps instead of closing. Also drop stale go/attention markers.
rm -f "$TRIVIA_HOME/$SESSION_ID.stop" "$TRIVIA_HOME/$SESSION_ID.go" \
      "$TRIVIA_HOME/$SESSION_ID.attn" 2>/dev/null || true

# If ANY game window is already open — this session's or another's — we're
# done. Exactly one game window ever.
if global_game_alive; then
  log info "a game window is already open; not spawning another ($SESSION_ID)"
  exit 0
fi

# If a game is already alive for this session, we're done (one game per session).
PID_FILE="$TRIVIA_HOME/$SESSION_ID.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
  log info "game already alive; reuse ($SESSION_ID)"
  exit 0
fi

# Spawn the detached launcher (handles debounce, then renders the game).
if command -v setsid >/dev/null 2>&1; then
  setsid bash "$SCRIPT_DIR/_launch.sh" "$SESSION_ID" "$TRANSCRIPT" >>"$LOG_FILE" 2>&1 </dev/null &
else
  nohup bash "$SCRIPT_DIR/_launch.sh" "$SESSION_ID" "$TRANSCRIPT" >>"$LOG_FILE" 2>&1 </dev/null &
fi
disown 2>/dev/null || true

exit 0
