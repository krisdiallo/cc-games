# Idle Trivia

Fills Claude Code's wait-state with an interruptible multiple-choice trivia game.
When you submit a prompt, a small trivia game pops up in a **side terminal window
or tmux pane**; the moment Claude finishes, it shows your score and gets out of
the way.

Built entirely on Claude Code's **public hooks API** — no patching of the TUI,
no modifying the binary. The game lives in a separate process because
[hooks cannot draw to the Claude Code terminal](https://code.claude.com/docs/en/hooks).

```
UserPromptSubmit ──► start-trivia.sh ──► (debounce) ──► trivia game in a side pane
Stop             ──► stop-trivia.sh  ──► touch <session>.stop ──► game wraps up & closes
SessionEnd       ──► cleanup.sh      ──► kill pane, delete markers
```

---

## Requirements

- **Python 3** (stdlib `curses` only — no `pip install`).
- A place to render the game:
  - **tmux** (auto-detected; the game opens in a split pane and keeps focus on
    Claude Code), **or**
  - a **GUI terminal** — macOS `Terminal.app`, or a Linux emulator
    (`gnome-terminal`, `konsole`, `alacritty`, `kitty`, `xterm`, … or whatever
    `$TERMINAL` points to).
- On a bare SSH session with neither, the game no-ops and logs a one-time note
  (see [Limitations](#limitations)).

---

## Install

### Option A — personal install (fastest)

Wires the hooks into your `~/.claude/settings.json` **in place** (no file
copying, so `git pull` keeps you current) and seeds a default config:

```bash
scripts/install.sh              # hooks + default config
scripts/install.sh --spinner    # also set trivia-fact spinnerVerbs (M0)
scripts/install.sh --uninstall  # remove the hooks
```

Restart Claude Code (or run `/hooks`) afterward.

### Option B — shareable plugin

The repo is already laid out as a plugin (`.claude-plugin/plugin.json` +
`hooks/hooks.json`). Test it locally:

```bash
claude --plugin-dir ./idle-trivia
# iterate with /reload-plugins
```

To distribute, add it to a plugin marketplace repo and install with `/plugin`.

---

## How it plays

| Key | Action |
|-----|--------|
| `1`–`4` | Answer |
| `s` | Skip |
| `p` | Pause / resume |
| `q` | Quit the game (won't respawn until your next prompt) |

Each answer shows instant correct/incorrect feedback + a one-line explanation,
then advances. A running streak and accuracy sit in the header. Lifetime and
daily-streak stats persist to `~/.claude/trivia/stats.json`.

When Claude finishes, the game shows **"✅ Claude's done — final score"** and
closes according to your `stopBehavior`.

---

## Configuration

Edit `~/.claude/trivia/config.json` (seeded from
[`game/config.example.json`](game/config.example.json)):

```json
{
  "enabled": true,
  "debounceSeconds": 8,
  "stopBehavior": "linger",
  "lingerSeconds": 2,
  "paneHeight": 12,
  "categories": ["tech", "science", "general", "history"],
  "fallbackWithoutTmux": "terminal-window",
  "sound": false
}
```

| Key | Meaning |
|-----|---------|
| `enabled` | Master on/off switch. |
| `debounceSeconds` | Wait this long before rendering, so **quick turns never flash a pane**. If Claude finishes first, the game never appears. |
| `stopBehavior` | `immediate` (close at once), `linger` (show the summary for `lingerSeconds`, default), or `finish-question` (let you complete the current question first). |
| `lingerSeconds` | Summary dwell time for `linger`. |
| `paneHeight` | tmux split height, in rows. |
| `categories` | Which categories to draw from (also which ones `--refresh` pulls). |

---

## Question bank

Ships with ~60 hand-written questions across **tech, science, general, and
history** in [`game/questions.json`](game/questions.json). Option order is
shuffled at load time, so the correct answer isn't always the same key.

Grow the bank from the free [Open Trivia DB](https://opentdb.com) whenever you
like — this is a manual, offline-safe step (**the game never blocks on the
network**):

```bash
python3 game/trivia.py --refresh --amount 100
```

Fetched questions are HTML-unescaped, de-duplicated against what you already
have, and merged in. A network failure leaves the existing bank untouched.

---

## Try it without Claude Code

```bash
# Play directly (Ctrl-C to exit):
python3 game/trivia.py --session test --state-dir /tmp/trivia-test

# In another shell, simulate Claude finishing:
touch /tmp/trivia-test/test.stop
```

---

## M0: trivia in the spinner itself

Independent of the game, you can replace Claude Code's spinner verbs with
"Did you know…" facts (`scripts/install.sh --spinner`, or set `spinnerVerbs`
manually from [`game/spinner-facts.json`](game/spinner-facts.json)). Zero code
risk — even the spinner then carries content.

---

## Limitations

- **GUI-terminal auto-close:** in a **tmux** pane the game closes itself cleanly
  when it exits. In a spawned **GUI terminal window**, the game exits and prints
  "safe to close this window," but whether the *window* auto-closes depends on
  that terminal's "close on clean exit" setting. tmux is the smoothest
  experience; recommended if you have it.
- **Bare SSH, no tmux:** nothing to render into — the hook logs a one-time note
  to `~/.claude/trivia/trivia.log` and stays silent. `screen` support is a
  future candidate.
- **Subagents:** only the main-agent `Stop` closes the game; `SubagentStop` is
  intentionally not wired, so subagents finishing mid-run don't end your game.

---

## State & logs

Everything runtime lives under `~/.claude/trivia/`, keyed by `session_id`:
`<session>.pid`, `<session>.pane`, `<session>.stop`, `<session>.pending`,
plus `stats.json` (flock-guarded for multiple concurrent sessions) and
`trivia.log`. `SessionEnd` cleans a session's markers up.

## Layout

```
idle-trivia/
├── .claude-plugin/plugin.json   # plugin manifest
├── hooks/hooks.json             # hook config (uses ${CLAUDE_PLUGIN_ROOT})
├── scripts/
│   ├── common.sh                # shared helpers (json parse, config, spawn)
│   ├── start-trivia.sh          # UserPromptSubmit: spawn detached launcher
│   ├── _launch.sh               # debounce, then render the game
│   ├── stop-trivia.sh           # Stop: signal wrap-up
│   ├── cleanup.sh               # SessionEnd: tear down
│   └── install.sh               # personal installer / uninstaller
└── game/
    ├── trivia.py                # the curses game (+ --refresh)
    ├── questions.json           # ~60-question seed bank
    ├── config.example.json      # default config
    └── spinner-facts.json       # M0 spinnerVerbs content
```
