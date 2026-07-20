"""Was the current turn interrupted (user pressed Escape)?

No hook fires on an Escape interrupt, but Claude Code appends a user entry
whose text starts with "[Request interrupted by user" to the session
transcript (verified empirically). The hooks hand us transcript_path, so the
launcher checks this just before rendering, and the running game polls it to
notice an abandoned window.

CLI (used by _launch.sh):  python3 turnstate.py <transcript.jsonl>
  exit 0 -> turn looks interrupted (do not render)
  exit 1 -> turn looks live
"""

import json
import os
import sys
from datetime import datetime, timezone

INTERRUPT_PREFIX = "[Request interrupted by user"
TAIL_BYTES = 65536


def _entry_text(msg):
    content = (msg or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
    return ""


def last_turn_state(transcript_path):
    """Return (interrupted: bool, age_seconds: float | None) for the newest
    conversation entry. Defensive: any parse problem reads as 'live'."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return False, None

    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        interrupted = (d.get("type") == "user"
                       and _entry_text(d.get("message")).startswith(INTERRUPT_PREFIX))
        age = None
        ts = d.get("timestamp")
        if isinstance(ts, str):
            try:
                then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - then).total_seconds()
            except ValueError:
                pass
        return interrupted, age
    return False, None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(1)
    interrupted, _ = last_turn_state(sys.argv[1])
    sys.exit(0 if interrupted else 1)
