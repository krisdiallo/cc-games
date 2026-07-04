#!/usr/bin/env bash
# SessionEnd hook — tear down any game and delete this session's state markers.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

INPUT="$(read_stdin)"
SESSION_ID="$(json_field "$INPUT" session_id)"
[ -z "$SESSION_ID" ] && SESSION_ID="default"

# Close the tmux pane, if we opened one.
PANE_FILE="$TRIVIA_HOME/$SESSION_ID.pane"
if [ -f "$PANE_FILE" ] && command -v tmux >/dev/null 2>&1; then
  tmux kill-pane -t "$(cat "$PANE_FILE" 2>/dev/null)" 2>/dev/null || true
fi

# Kill the game process, if still running.
PID_FILE="$TRIVIA_HOME/$SESSION_ID.pid"
if [ -f "$PID_FILE" ]; then
  kill "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null || true
fi

rm -f "$TRIVIA_HOME/$SESSION_ID.pid" \
      "$TRIVIA_HOME/$SESSION_ID.pane" \
      "$TRIVIA_HOME/$SESSION_ID.stop" \
      "$TRIVIA_HOME/$SESSION_ID.pending" 2>/dev/null || true

log info "cleaned up ($SESSION_ID)"
exit 0
