#!/usr/bin/env bash
# Shared helpers for idle-trivia hook scripts.
# Sourced by start-trivia.sh, stop-trivia.sh, cleanup.sh and _launch.sh.
#
# Design rules (see spec §4 "Handler contract"):
#   - Never print to stdout (UserPromptSubmit stdout becomes model context).
#   - Never exit non-zero from a hook (exit 2 would block the user's prompt).
#   - All diagnostics go to the log file only.

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Runtime state lives here, keyed by session_id. Override with CLAUDE_TRIVIA_HOME.
TRIVIA_HOME="${CLAUDE_TRIVIA_HOME:-$HOME/.claude/trivia}"
mkdir -p "$TRIVIA_HOME" 2>/dev/null || true
LOG_FILE="$TRIVIA_HOME/trivia.log"

# Bundled defaults, resolved relative to this file so scripts work whether
# they're run from the plugin dir or copied into ~/.claude/trivia/.
DEFAULT_CONFIG="$COMMON_DIR/../game/config.example.json"
RUNTIME_CONFIG="$TRIVIA_HOME/config.json"

log() {
  # log LEVEL MESSAGE
  printf '%s [%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "${1:-info}" "${2:-}" \
    >>"$LOG_FILE" 2>/dev/null || true
}

# Drain and echo all of stdin (so the hook pipe closes cleanly).
read_stdin() { cat 2>/dev/null || true; }

# json_field JSON FIELD  -> value of top-level FIELD ("" if absent).
json_field() {
  local json="$1" field="$2"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$json" | jq -r --arg f "$field" '.[$f] // empty' 2>/dev/null || true
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$json" | python3 -c \
      'import sys,json
try:
    d=json.load(sys.stdin); v=d.get(sys.argv[1],""); print(v if v is not None else "")
except Exception:
    pass' "$field" 2>/dev/null || true
  fi
}

# config_get KEY DEFAULT  -> config value (runtime overrides bundled default).
config_get() {
  local key="$1" default="$2" file="$RUNTIME_CONFIG"
  [ -f "$file" ] || file="$DEFAULT_CONFIG"
  [ -f "$file" ] || { printf '%s' "$default"; return; }
  command -v python3 >/dev/null 2>&1 || { printf '%s' "$default"; return; }
  python3 -c \
    'import sys,json
try:
    d=json.load(open(sys.argv[1]))
    v=d.get(sys.argv[2], sys.argv[3])
    print(str(v).lower() if isinstance(v,bool) else v)
except Exception:
    print(sys.argv[3])' "$file" "$key" "$default" 2>/dev/null || printf '%s' "$default"
}

# global_game_alive -> 0 if ANY session's game window is currently open.
# Reads the pid recorded in game.lock by the running game. This is the cheap
# pre-check; the game itself holds an exclusive flock on the same file, so a
# rare race here just means a spawned duplicate exits (and closes its own
# window) immediately.
global_game_alive() {
  local pid
  pid="$(cat "$TRIVIA_HOME/game.lock" 2>/dev/null)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# spawn_in_terminal COMMAND SESSION_ID
# Tries, in order: tmux split (if inside tmux) -> macOS Terminal -> Linux GUI
# terminal -> one-time "no UI" warning. Records tmux pane id for cleanup.
spawn_in_terminal() {
  local cmd="$1" session="$2" height
  height="$(config_get paneHeight 12)"
  case "$height" in ''|*[!0-9]*) height=12;; esac

  # 1. tmux — auto-used when a session is present; -d keeps focus on Claude Code.
  if [ -n "${TMUX:-}" ] && command -v tmux >/dev/null 2>&1; then
    local pane
    pane="$(tmux split-window -d -l "$height" -P -F '#{pane_id}' "$cmd" 2>/dev/null)" || pane=""
    if [ -n "$pane" ]; then
      printf '%s' "$pane" > "$TRIVIA_HOME/$session.pane"
      log info "spawned in tmux pane $pane ($session)"
      return 0
    fi
  fi

  # 2. macOS GUI terminal.
  if [ "$(uname 2>/dev/null)" = "Darwin" ] && command -v osascript >/dev/null 2>&1; then
    # Escape embedded double-quotes for AppleScript.
    local esc="${cmd//\"/\\\"}"
    osascript -e "tell application \"Terminal\" to do script \"$esc\"" >/dev/null 2>&1 \
      && { log info "spawned in macOS Terminal ($session)"; return 0; }
  fi

  # 3. Linux GUI terminal ($TERMINAL wins, else probe common emulators).
  local term="${TERMINAL:-}"
  if [ -z "$term" ]; then
    local t
    for t in x-terminal-emulator gnome-terminal konsole xfce4-terminal alacritty kitty foot xterm; do
      command -v "$t" >/dev/null 2>&1 && { term="$t"; break; }
    done
  fi
  if [ -n "$term" ] && command -v "$term" >/dev/null 2>&1; then
    "$term" -e bash -lc "$cmd" >/dev/null 2>&1 &
    disown 2>/dev/null || true
    log info "spawned in $term ($session)"
    return 0
  fi

  # 4. No UI available (e.g. bare SSH). Warn once, then stay silent.
  local warn="$TRIVIA_HOME/.no-terminal-warned"
  if [ ! -f "$warn" ]; then
    log warn "No tmux and no GUI terminal found; idle-trivia cannot render a game. (Shown once.)"
    touch "$warn" 2>/dev/null || true
  fi
  return 1
}
