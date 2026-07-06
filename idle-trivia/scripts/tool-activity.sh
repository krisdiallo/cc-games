#!/usr/bin/env bash
# PreToolUse hook — Claude just made a tool call, so this turn is a real task,
# not a quick text answer. Ask a launcher still inside its debounce window to
# render immediately instead of waiting out the full debounceSeconds.
#
# Runs on EVERY tool call, so the common case must be a fast no-op:
# two file stats when no launcher is pending. Pure-reasoning turns (no tool
# calls) never reach this script and fall back to the normal debounce.
#
# CONTRACT (spec §4): print NOTHING to stdout, always exit 0, be quick.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

INPUT="$(read_stdin)"
SESSION_ID="$(json_field "$INPUT" session_id)"
[ -z "$SESSION_ID" ] && SESSION_ID="default"

# Fast no-op paths: nothing is waiting to render, or we already signaled.
PENDING="$TRIVIA_HOME/$SESSION_ID.pending"
GO_FILE="$TRIVIA_HOME/$SESSION_ID.go"
[ -f "$PENDING" ] || exit 0
[ -f "$GO_FILE" ] && exit 0

# Config gate (only consulted on the one call that would trigger a render).
if [ "$(config_get openOnToolUse true)" != "true" ]; then
  exit 0
fi

touch "$GO_FILE" 2>/dev/null || true
log info "tool activity during debounce; requesting early render ($SESSION_ID)"
exit 0
