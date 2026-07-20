# cc-games

Little games that keep you at the terminal while Claude Code works — so you
don't reach for your phone during a long agentic run.

## [idle-games](./idle-games)

Interruptible brain games that pop up in a side terminal/tmux pane while
Claude Code is processing, and close themselves the instant Claude finishes.
Built purely on Claude Code's public **hooks API** — no TUI patching.

- **Dungeon roguelite** — one room per wait; your hero, gold, and guild
  upgrades persist forever, and fights freeze mid-swing and resume next prompt
- **5-max poker vs AI personalities** — stateful bankroll, a built-in
  EV/chart trainer that grades every decision, and villains that exploit
  the leaks it finds; optional LLM opponents via `claude -p`
- **Snake** (real-time, freeze/resume), **trivia**, **number sequences**,
  **word games**, **Simon**, and **letter n-back**
- Knows when Claude is actually *waiting on you* (approval prompts, questions)
  and says so — banner + bell + macOS notification
- One game window ever, across all sessions; opens early on Claude's first
  tool call; never flashes a pane on quick turns or Escape-canceled prompts
- Python + `curses`, stdlib only, no dependencies

See [`idle-games/README.md`](./idle-games/README.md) to install and configure.
