# cc-games

Little games that keep you at the terminal while Claude Code works — so you
don't reach for your phone during a long agentic run.

## [idle-trivia](./idle-trivia)

An interruptible multiple-choice trivia game that pops up in a side
terminal/tmux pane while Claude Code is processing, and closes itself the
instant Claude finishes. Built purely on Claude Code's public **hooks API**
(`UserPromptSubmit` / `Stop` / `SessionEnd`) — no TUI patching.

- Python + `curses` (stdlib only, no dependencies)
- ~60-question seed bank + optional Open Trivia DB refresh
- Debounced so quick turns never flash a pane
- Per-session state, streaks, lifetime stats
- Ships as a Claude Code plugin, or install into your user settings directly

See [`idle-trivia/README.md`](./idle-trivia/README.md) to install and configure.
