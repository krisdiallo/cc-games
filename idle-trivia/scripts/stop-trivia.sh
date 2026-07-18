#!/usr/bin/env bash
# Stop hook — the MAIN agent finished responding. Signal the game to wrap up.
#
# Only wire this to the `Stop` event, never `SubagentStop`: subagents finish
# mid-run, and closing the game then would be wrong (spec §2.2 / §8).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

INPUT="$(read_stdin)"
SESSION_ID="$(json_field "$INPUT" session_id)"
[ -z "$SESSION_ID" ] && SESSION_ID="default"

# The game polls this file (~250ms) and enters its stop behavior.
# It also aborts a not-yet-rendered game still inside the debounce window.
# The turn is over, so any "waiting on the user" marker is stale too.
touch "$TRIVIA_HOME/$SESSION_ID.stop" 2>/dev/null || true
rm -f "$TRIVIA_HOME/$SESSION_ID.attn" 2>/dev/null || true
log info "stop signaled ($SESSION_ID)"

exit 0
