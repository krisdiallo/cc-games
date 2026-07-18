#!/usr/bin/env bash
# Phase-1 personal installer: wires the idle-trivia hooks into your user
# settings.json (in place — no file copying, so `git pull` keeps you current)
# and seeds a default config. Optional M0 spinner-facts install.
#
#   scripts/install.sh              # install hooks + default config
#   scripts/install.sh --spinner    # ...also set spinnerVerbs to trivia facts (M0)
#   scripts/install.sh --uninstall  # remove the hooks again
#
# The shareable plugin (see README §Plugin) is the alternative to this script.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS="$REPO_DIR/scripts"
GAME="$REPO_DIR/game"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
TRIVIA_HOME="$CLAUDE_DIR/trivia"

MODE="install"
WITH_SPINNER="false"
for arg in "$@"; do
  case "$arg" in
    --uninstall) MODE="uninstall" ;;
    --spinner)   WITH_SPINNER="true" ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
mkdir -p "$CLAUDE_DIR" "$TRIVIA_HOME"
chmod +x "$SCRIPTS"/*.sh "$GAME"/trivia.py 2>/dev/null || true

# Seed a runtime config the user can edit, if absent.
if [ "$MODE" = "install" ] && [ ! -f "$TRIVIA_HOME/config.json" ]; then
  cp "$GAME/config.example.json" "$TRIVIA_HOME/config.json"
  echo "wrote default config -> $TRIVIA_HOME/config.json"
fi

SPINNER_JSON="$GAME/spinner-facts.json"

python3 - "$SETTINGS" "$SCRIPTS" "$MODE" "$WITH_SPINNER" "$SPINNER_JSON" <<'PY'
import json, os, sys

settings_path, scripts, mode, with_spinner, spinner_json = sys.argv[1:6]

try:
    with open(settings_path) as f:
        settings = json.load(f)
except (OSError, ValueError):
    settings = {}

hooks = settings.setdefault("hooks", {})
wanted = {
    "UserPromptSubmit": os.path.join(scripts, "start-trivia.sh"),
    "PreToolUse":       os.path.join(scripts, "tool-activity.sh"),
    "PostToolUse":      os.path.join(scripts, "tool-activity.sh"),
    "Notification":     os.path.join(scripts, "notify-attention.sh"),
    "Stop":             os.path.join(scripts, "stop-trivia.sh"),
    "SessionEnd":       os.path.join(scripts, "cleanup.sh"),
}

def strip(event, cmd_substr):
    groups = hooks.get(event, [])
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", [])
                      if cmd_substr not in h.get("command", "")]
    hooks[event] = [g for g in groups if g.get("hooks")]
    if not hooks[event]:
        hooks.pop(event, None)

for event, cmd in wanted.items():
    strip(event, os.path.basename(cmd))          # idempotent: remove old entry first
    if mode == "install":
        hooks.setdefault(event, []).append(
            {"hooks": [{"type": "command", "command": cmd}]})

if mode == "install" and with_spinner == "true":
    try:
        verbs = json.load(open(spinner_json)).get("verbs", [])
        if verbs:
            settings["spinnerVerbs"] = {"mode": "replace", "verbs": verbs}
    except (OSError, ValueError):
        pass

if not hooks:
    settings.pop("hooks", None)

os.makedirs(os.path.dirname(settings_path), exist_ok=True)
with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print(f"{'installed' if mode=='install' else 'removed'} idle-trivia hooks in {settings_path}")
PY

echo "done. Restart Claude Code (or run /hooks) to pick up the change."
