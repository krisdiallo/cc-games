#!/usr/bin/env bash
# Detached launcher (spawned by start-trivia.sh; never called by a hook directly).
# Waits out the debounce window, then renders the game — unless Claude finished
# first (fast turn), in which case it exits silently with no pane-flash.

SESSION_ID="${1:-default}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

GAME="$SCRIPT_DIR/../game/trivia.py"
STOP_FILE="$TRIVIA_HOME/$SESSION_ID.stop"
PENDING="$TRIVIA_HOME/$SESSION_ID.pending"
GO_FILE="$TRIVIA_HOME/$SESSION_ID.go"

DEBOUNCE="$(config_get debounceSeconds 8)"
case "$DEBOUNCE" in ''|*[!0-9]*) DEBOUNCE=8;; esac

rm -f "$GO_FILE" 2>/dev/null || true   # only honor tool activity from THIS turn
touch "$PENDING" 2>/dev/null || true

# Debounce loop: bail the instant Claude's done so quick turns never flash a
# pane; render early the moment a tool call marks this turn as a real task
# (tool-activity.sh touches the go file). Stop always wins over go.
slept=0
while [ "$slept" -lt "$DEBOUNCE" ]; do
  sleep 1
  slept=$((slept + 1))
  if [ -f "$STOP_FILE" ]; then
    log info "stop arrived during debounce; not rendering ($SESSION_ID)"
    rm -f "$PENDING" "$STOP_FILE" "$GO_FILE" 2>/dev/null || true
    exit 0
  fi
  if [ -f "$GO_FILE" ]; then
    log info "tool activity after ${slept}s; rendering early ($SESSION_ID)"
    break
  fi
done
rm -f "$PENDING" "$GO_FILE" 2>/dev/null || true

# Race guard: another launcher (any session) may have already rendered a game
# during our debounce. One window ever.
if global_game_alive; then
  log info "a game window opened during debounce; not spawning ($SESSION_ID)"
  exit 0
fi
if [ -f "$TRIVIA_HOME/$SESSION_ID.pid" ] && \
   kill -0 "$(cat "$TRIVIA_HOME/$SESSION_ID.pid" 2>/dev/null)" 2>/dev/null; then
  log info "game already alive after debounce ($SESSION_ID)"
  exit 0
fi

PYTHON="$(command -v python3 || command -v python 2>/dev/null)"
if [ -z "$PYTHON" ]; then
  log error "no python interpreter found; cannot start game ($SESSION_ID)"
  exit 0
fi

# The game watches STATE_DIR/<session>.stop and writes its own pid file.
GAME_CMD="$PYTHON \"$GAME\" --session \"$SESSION_ID\" --state-dir \"$TRIVIA_HOME\""
spawn_in_terminal "$GAME_CMD" "$SESSION_ID"

exit 0
