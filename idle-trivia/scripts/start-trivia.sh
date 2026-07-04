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

# Global kill switch.
if [ "$(config_get enabled true)" != "true" ]; then
  log info "disabled via config; skip start ($SESSION_ID)"
  exit 0
fi

# Multi-turn reuse: a new prompt clears any stop marker so a lingering game
# un-wraps instead of closing.
rm -f "$TRIVIA_HOME/$SESSION_ID.stop" 2>/dev/null || true

# If a game is already alive for this session, we're done (one game per session).
PID_FILE="$TRIVIA_HOME/$SESSION_ID.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
  log info "game already alive; reuse ($SESSION_ID)"
  exit 0
fi

# Spawn the detached launcher (handles debounce, then renders the game).
if command -v setsid >/dev/null 2>&1; then
  setsid bash "$SCRIPT_DIR/_launch.sh" "$SESSION_ID" >>"$LOG_FILE" 2>&1 </dev/null &
else
  nohup bash "$SCRIPT_DIR/_launch.sh" "$SESSION_ID" >>"$LOG_FILE" 2>&1 </dev/null &
fi
disown 2>/dev/null || true

exit 0
