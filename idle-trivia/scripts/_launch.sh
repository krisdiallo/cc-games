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

DEBOUNCE="$(config_get debounceSeconds 8)"
case "$DEBOUNCE" in ''|*[!0-9]*) DEBOUNCE=8;; esac

touch "$PENDING" 2>/dev/null || true

# Debounce loop: bail the instant Claude's done so quick turns never flash a pane.
slept=0
while [ "$slept" -lt "$DEBOUNCE" ]; do
  sleep 1
  slept=$((slept + 1))
  if [ -f "$STOP_FILE" ]; then
    log info "stop arrived during debounce; not rendering ($SESSION_ID)"
    rm -f "$PENDING" "$STOP_FILE" 2>/dev/null || true
    exit 0
  fi
done
rm -f "$PENDING" 2>/dev/null || true

# Race guard: another launcher may have already rendered a game.
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
