#!/usr/bin/env python3
"""Idle brain games — interruptible mini-games for the wait-state while Claude
Code processes a request. (Named trivia.py for compatibility: the hook scripts
and existing installs launch this path.)

Runs in its own terminal window or tmux pane (spawned by the hook scripts). It
watches a per-session stop file; when Claude finishes, it wraps up and closes.

Games live in games/ (trivia, sequences, words, simon, nback) on top of the
shared shell (shell.py). Config picks which are enabled and which one starts.

Standalone usage (handy for testing without the hooks):
    python3 trivia.py --session test --state-dir /tmp/trivia-test
    python3 trivia.py --game simon
    python3 trivia.py --refresh --amount 50      # pull fresh Qs from Open Trivia DB

Stdlib only (curses) — no pip install required.
"""

import argparse
import curses
import fcntl
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import shell as game_shell            # noqa: E402
from games import REGISTRY, build_games   # noqa: E402
from games.trivia import refresh_questions  # noqa: E402

DEFAULT_QUESTIONS = os.path.join(HERE, "questions.json")


def pick_first_game(games, choice):
    """`choice` is a game name, or "random"."""
    by_name = {g.name: g for g in games}
    if choice in by_name:
        return by_name[choice]
    return random.choice(games)


def acquire_global_lock(state_dir):
    """One game window EVER, across all Claude sessions: take an exclusive
    flock on state_dir/game.lock for this process's lifetime. The OS releases
    the lock when the process dies, so it can never go stale. Returns the
    held file object, or None if another game already owns the lock."""
    path = os.path.join(state_dir, "game.lock")
    try:
        f = open(path, "a+")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    # Record our pid so the hook scripts can skip spawning cheaply.
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def main():
    ap = argparse.ArgumentParser(description="Idle brain games")
    ap.add_argument("--session", default="default")
    ap.add_argument("--state-dir", default=os.path.expanduser("~/.claude/trivia"))
    ap.add_argument("--questions", default=DEFAULT_QUESTIONS)
    ap.add_argument("--game", default=None,
                    help=f"start with this game ({', '.join(REGISTRY)}) "
                         "or 'random' (default: config)")
    ap.add_argument("--transcript", default="",
                    help="session transcript path (for Escape-interrupt detection)")
    ap.add_argument("--refresh", action="store_true",
                    help="fetch fresh questions from Open Trivia DB, then exit")
    ap.add_argument("--amount", type=int, default=50,
                    help="questions to fetch with --refresh")
    args = ap.parse_args()

    os.makedirs(args.state_dir, exist_ok=True)
    cfg = game_shell.load_config(args.state_dir)

    if args.refresh:
        sys.exit(refresh_questions(args.questions, args.amount,
                                   cfg.get("categories")))

    games = build_games(cfg, args.questions)
    if not games:
        print("No playable games (check the 'games' list in config.json, and "
              "run --refresh if the trivia bank is empty).", file=sys.stderr)
        sys.exit(1)
    first = pick_first_game(games, args.game or cfg.get("game", "random"))

    stop_file = os.path.join(args.state_dir, f"{args.session}.stop")
    pid_file = os.path.join(args.state_dir, f"{args.session}.pid")

    # A stop that beat us here (fast turn) means: don't render at all.
    if os.path.exists(stop_file):
        try:
            os.remove(stop_file)
        except OSError:
            pass
        return

    # Another game window is already open (this or any other session):
    # bow out silently and close the window we were spawned into.
    lock = acquire_global_lock(args.state_dir)
    if lock is None:
        if cfg.get("autoCloseTerminal", True):
            game_shell.close_own_terminal_window()
        return

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    game_shell.roll_daily_streak(args.state_dir)
    try:
        curses.wrapper(game_shell.curses_main, cfg, args.state_dir,
                       stop_file, games, first, args.transcript)
    finally:
        for p in (pid_file, stop_file):
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    main()
