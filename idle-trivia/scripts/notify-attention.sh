#!/usr/bin/env bash
# Notification hook — Claude is waiting on the USER (permission approval,
# question, idle prompt). Without this, the game happily distracts you while
# Claude sits blocked.
#
# Touches <session>.attn (the game polls it and shows a banner + bell) and,
# on macOS, fires a system notification so you're covered even when the game
# window is buried or closed. Cleared by tool-activity.sh (work resumed),
# stop-trivia.sh, cleanup.sh, or your next prompt.
#
# CONTRACT (spec §4): print NOTHING to stdout, always exit 0.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

INPUT="$(read_stdin)"
SESSION_ID="$(json_field "$INPUT" session_id)"
[ -z "$SESSION_ID" ] && SESSION_ID="default"

# Ignore notification types that don't need the user's attention.
# (Payload fields here are only partially documented; parse defensively and
# treat unknown types as attention-worthy.)
KIND="$(json_field "$INPUT" notification_type)"
[ -z "$KIND" ] && KIND="$(json_field "$INPUT" matcher)"
case "$KIND" in
  auth_success|*success*) exit 0 ;;
esac

MSG="$(json_field "$INPUT" message)"
[ -z "$MSG" ] && MSG="Claude is waiting for your input"

printf '%s\n' "$MSG" > "$TRIVIA_HOME/$SESSION_ID.attn" 2>/dev/null || true
log info "attention: ${KIND:-unknown} — $MSG ($SESSION_ID)"

# macOS system notification (best-effort, detached, config-gated).
if [ "$(config_get systemNotifications true)" = "true" ] \
   && [ "$(uname 2>/dev/null)" = "Darwin" ] \
   && command -v osascript >/dev/null 2>&1; then
  ESC_MSG="${MSG//\"/\\\"}"
  osascript -e "display notification \"$ESC_MSG\" with title \"Claude Code\" sound name \"Glass\"" \
    >/dev/null 2>&1 &
  disown 2>/dev/null || true
fi

exit 0
