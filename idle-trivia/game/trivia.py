#!/usr/bin/env python3
"""Idle Trivia — an interruptible multiple-choice trivia game for the wait-state
while Claude Code processes a request.

Runs in its own terminal window or tmux pane (spawned by the hook scripts). It
watches a per-session stop file; when Claude finishes, it wraps up and closes.

Standalone usage (handy for testing without the hooks):
    python3 trivia.py --session test --state-dir /tmp/trivia-test
    python3 trivia.py --refresh --amount 50      # pull fresh Qs from Open Trivia DB

Stdlib only (curses) — no pip install required.
"""

import argparse
import curses
import fcntl
import html
import json
import os
import random
import sys
import time
from datetime import date, timedelta

POLL_MS = 250            # input timeout / stop-file poll cadence
FEEDBACK_SECONDS = 1.5   # how long correct/incorrect feedback shows

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_QUESTIONS = os.path.join(HERE, "questions.json")
DEFAULT_CONFIG = os.path.join(HERE, "config.example.json")

# Open Trivia DB category ids (https://opentdb.com/api_config.php).
OPENTDB_CATEGORIES = {"general": 9, "science": 17, "tech": 18, "history": 23}


# --------------------------------------------------------------------------- #
# Config / questions / stats                                                   #
# --------------------------------------------------------------------------- #

def load_config(state_dir):
    """Runtime config (state_dir/config.json) overrides the bundled default."""
    cfg = {}
    for path in (DEFAULT_CONFIG, os.path.join(state_dir, "config.json")):
        try:
            with open(path) as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
    cfg.setdefault("stopBehavior", "linger")
    cfg.setdefault("lingerSeconds", 2)
    cfg.setdefault("categories", ["tech", "science", "general", "history"])
    cfg.setdefault("sound", False)
    return cfg


def load_questions(path, categories):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    qs = data.get("questions", []) if isinstance(data, dict) else data
    cats = set(categories or [])
    out = []
    for q in qs:
        if not isinstance(q, dict):
            continue
        if cats and q.get("category") not in cats:
            continue
        opts = q.get("options")
        if not (isinstance(opts, list) and len(opts) == 4):
            continue
        try:
            ans = int(q.get("answer"))
        except (TypeError, ValueError):
            continue
        if not 0 <= ans <= 3:
            continue
        # Shuffle option order so the correct answer isn't always in the same
        # slot (the seed bank stores it at index 0). Copy so we never mutate
        # the source list.
        correct_value = opts[ans]
        shuffled = list(opts)
        random.shuffle(shuffled)
        q = dict(q)
        q["options"] = shuffled
        q["answer"] = shuffled.index(correct_value)
        out.append(q)
    return out


def _stats_path(state_dir):
    return os.path.join(state_dir, "stats.json")


def update_stats(state_dir, mutator):
    """Read-modify-write stats.json under an exclusive flock (multi-session safe).
    `mutator(stats_dict)` mutates in place; returns the resulting dict."""
    path = _stats_path(state_dir)
    default = {
        "lifetime": {"answered": 0, "correct": 0, "best_streak": 0},
        "daily": {"date": "", "streak_days": 0, "answered": 0},
    }
    try:
        f = open(path, "a+")
    except OSError:
        stats = json.loads(json.dumps(default))
        mutator(stats)
        return stats
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read()
        try:
            stats = json.loads(raw) if raw.strip() else json.loads(json.dumps(default))
        except ValueError:
            stats = json.loads(json.dumps(default))
        stats.setdefault("lifetime", dict(default["lifetime"]))
        stats.setdefault("daily", dict(default["daily"]))
        mutator(stats)
        f.seek(0)
        f.truncate()
        f.write(json.dumps(stats, indent=2))
        f.flush()
        os.fsync(f.fileno())
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
    return stats


def roll_daily_streak(state_dir):
    """Advance the daily-play streak once per game start."""
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    def m(stats):
        d = stats["daily"]
        if d.get("date") != today:
            if d.get("date") == yesterday:
                d["streak_days"] = d.get("streak_days", 0) + 1
            else:
                d["streak_days"] = 1
            d["date"] = today
            d["answered"] = 0

    return update_stats(state_dir, m)


def record_answer(state_dir, correct):
    def m(stats):
        lt = stats["lifetime"]
        lt["answered"] = lt.get("answered", 0) + 1
        if correct:
            lt["correct"] = lt.get("correct", 0) + 1
        stats["daily"]["answered"] = stats["daily"].get("answered", 0) + 1

    update_stats(state_dir, m)


def record_best_streak(state_dir, streak):
    def m(stats):
        lt = stats["lifetime"]
        if streak > lt.get("best_streak", 0):
            lt["best_streak"] = streak

    update_stats(state_dir, m)


# --------------------------------------------------------------------------- #
# Open Trivia DB refresh (never runs inside the game loop)                      #
# --------------------------------------------------------------------------- #

def refresh_questions(path, amount, categories):
    """Fetch fresh questions from Open Trivia DB and merge into `path`.
    Fully network-guarded: on any failure the existing bank is left untouched."""
    import urllib.request
    import urllib.parse

    cats = categories or list(OPENTDB_CATEGORIES.keys())
    per = max(1, amount // len(cats))

    try:
        with open(path) as f:
            data = json.load(f)
        existing = data.get("questions", []) if isinstance(data, dict) else list(data)
    except (OSError, ValueError):
        existing = []

    seen = {q.get("question", "").strip().lower() for q in existing}
    added = 0

    for cat in cats:
        cid = OPENTDB_CATEGORIES.get(cat)
        if cid is None:
            continue
        params = urllib.parse.urlencode(
            {"amount": per, "category": cid, "type": "multiple"}
        )
        url = "https://opentdb.com/api.php?" + params
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                payload = json.load(resp)
        except Exception as e:  # noqa: BLE001 — never let the fetch escape
            print(f"  ! {cat}: fetch failed ({e}); skipping", file=sys.stderr)
            continue
        if payload.get("response_code") != 0:
            print(f"  ! {cat}: API returned code {payload.get('response_code')}",
                  file=sys.stderr)
            continue
        for i, r in enumerate(payload.get("results", [])):
            question = html.unescape(r.get("question", "")).strip()
            key = question.lower()
            if not question or key in seen:
                continue
            correct = html.unescape(r.get("correct_answer", ""))
            wrong = [html.unescape(w) for w in r.get("incorrect_answers", [])]
            options = wrong + [correct]
            if len(options) != 4:
                continue
            random.shuffle(options)
            existing.append({
                "id": f"{cat}-otdb-{abs(hash(key)) % 100000:05d}",
                "category": cat,
                "difficulty": r.get("difficulty", "medium"),
                "question": question,
                "options": options,
                "answer": options.index(correct),
                "explanation": "",
            })
            seen.add(key)
            added += 1

    try:
        with open(path, "w") as f:
            json.dump({"version": 1, "questions": existing}, f, indent=2)
        print(f"Refreshed: +{added} new question(s); bank now {len(existing)} total.")
    except OSError as e:
        print(f"Could not write {path}: {e}", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Game                                                                         #
# --------------------------------------------------------------------------- #

class TriviaGame:
    def __init__(self, stdscr, questions, cfg, state_dir, stop_file):
        self.scr = stdscr
        self.questions = questions
        self.cfg = cfg
        self.state_dir = state_dir
        self.stop_file = stop_file
        self.session_answered = 0
        self.session_correct = 0
        self.streak = 0
        self.best_streak = 0
        self.paused = False

    # -- helpers ----------------------------------------------------------- #
    def stop_requested(self):
        return os.path.exists(self.stop_file)

    def _finish_question_mode(self):
        return self.cfg.get("stopBehavior") == "finish-question"

    def wait(self, seconds):
        """Sleep while staying responsive; returns True if a stop was requested
        (and we are not in finish-question mode)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.scr.timeout(POLL_MS)
            self.scr.getch()  # drain/ignore keys during feedback
            if self.stop_requested() and not self._finish_question_mode():
                return True
        return False

    # -- rendering --------------------------------------------------------- #
    def _addstr(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and 0 <= x < w:
            try:
                self.scr.addnstr(y, x, text, max(0, w - x - 1), attr)
            except curses.error:
                pass

    def _header(self, stop_pending=False):
        h, w = self.scr.getmaxyx()
        acc = (100 * self.session_correct // self.session_answered
               if self.session_answered else 0)
        left = " IDLE TRIVIA "
        right = f" streak {self.streak} · {self.session_correct}/{self.session_answered} ({acc}%) "
        self._addstr(0, 0, left + " " * max(0, w - len(left) - len(right) - 1) + right,
                     curses.color_pair(1) | curses.A_BOLD)
        if stop_pending:
            self._addstr(1, 0, " ✅ Claude's done — finish this question ",
                         curses.color_pair(4) | curses.A_BOLD)

    def _footer(self):
        h, _ = self.scr.getmaxyx()
        self._addstr(h - 1, 0, " 1-4 answer · s skip · p pause · q quit ",
                     curses.color_pair(1))

    def present_question(self, q):
        """Render one question and handle input.
        Returns: 'answered', 'skip', 'quit', or 'stop'."""
        stop_pending = False
        selected = None
        self.scr.timeout(POLL_MS)

        while True:
            self.scr.erase()
            self._header(stop_pending)
            self._addstr(2, 2, f"[{q.get('category', '?')}·{q.get('difficulty', '?')}]",
                         curses.color_pair(5))
            self._addstr(3, 2, q.get("question", ""), curses.A_BOLD)
            for i, opt in enumerate(q["options"]):
                self._addstr(5 + i, 4, f"{i + 1}. {opt}")
            if self.paused:
                self._addstr(10, 2, " PAUSED — press p to resume ",
                             curses.color_pair(4) | curses.A_BOLD)
            self._footer()
            self.scr.refresh()

            c = self.scr.getch()

            # Stop handling: in finish-question mode, keep going but flag it.
            if self.stop_requested():
                if self._finish_question_mode():
                    stop_pending = True
                else:
                    return "stop"

            if c == -1:
                continue
            ch = chr(c) if 0 <= c < 256 else ""

            if ch == "q":
                return "quit"
            if ch == "p":
                self.paused = not self.paused
                continue
            if self.paused:
                continue
            if ch == "s":
                self.streak = 0
                return "skip"
            if ch in "1234":
                selected = int(ch) - 1
                self._reveal(q, selected, stop_pending)
                return "answered"

    def _reveal(self, q, selected, stop_pending):
        correct_idx = int(q["answer"])
        correct = selected == correct_idx
        self.session_answered += 1
        if correct:
            self.session_correct += 1
            self.streak += 1
            self.best_streak = max(self.best_streak, self.streak)
        else:
            self.streak = 0
        record_answer(self.state_dir, correct)

        self.scr.erase()
        self._header(stop_pending)
        self._addstr(3, 2, q.get("question", ""), curses.A_BOLD)
        for i, opt in enumerate(q["options"]):
            attr = 0
            prefix = "  "
            if i == correct_idx:
                attr = curses.color_pair(2) | curses.A_BOLD
                prefix = "✔ "
            elif i == selected:
                attr = curses.color_pair(3) | curses.A_BOLD
                prefix = "✗ "
            self._addstr(5 + i, 4, f"{prefix}{i + 1}. {opt}", attr)
        verdict = "✔ Correct!" if correct else "✗ Not quite."
        vcolor = curses.color_pair(2) if correct else curses.color_pair(3)
        self._addstr(10, 2, verdict, vcolor | curses.A_BOLD)
        expl = q.get("explanation", "")
        if expl:
            self._addstr(11, 2, expl, curses.color_pair(5))
        self._footer()
        self.scr.refresh()
        self.wait(FEEDBACK_SECONDS)

    # -- main loop --------------------------------------------------------- #
    def run(self):
        order = list(range(len(self.questions)))
        random.shuffle(order)
        i = 0
        quit_by_user = False

        while True:
            if self.stop_requested() and not self._finish_question_mode():
                break
            q = self.questions[order[i % len(order)]]
            i += 1
            status = self.present_question(q)
            if status == "quit":
                quit_by_user = True
                break
            if status == "stop":
                break
            # answered / skip handled — a stop that arrived (finish-question
            # mode, or during feedback) ends the game now.
            if self.stop_requested():
                break

        record_best_streak(self.state_dir, self.best_streak)
        self.wrap_up(quit_by_user)

    def wrap_up(self, quit_by_user):
        behavior = self.cfg.get("stopBehavior", "linger")
        linger = 0 if quit_by_user or behavior == "immediate" else \
            max(0, int(self.cfg.get("lingerSeconds", 2)))
        acc = (100 * self.session_correct // self.session_answered
               if self.session_answered else 0)

        self.scr.erase()
        title = "👋 Quit" if quit_by_user else "✅ Claude's done!"
        self._addstr(1, 2, title, curses.color_pair(2) | curses.A_BOLD)
        self._addstr(3, 2, f"This round: {self.session_correct}/{self.session_answered} correct ({acc}%)")
        self._addstr(4, 2, f"Best streak this round: {self.best_streak}")
        self._addstr(6, 2, "(this window is safe to close)", curses.color_pair(5))
        self.scr.refresh()

        # Honor "immediate" (no linger) but still show the screen a heartbeat so
        # it isn't a jarring flash.
        deadline = time.monotonic() + (linger if linger else 0.4)
        self.scr.timeout(POLL_MS)
        while time.monotonic() < deadline:
            self.scr.getch()


# --------------------------------------------------------------------------- #
# Entrypoint                                                                    #
# --------------------------------------------------------------------------- #

def _curses_main(stdscr, questions, cfg, state_dir, stop_file):
    curses.curs_set(0)
    curses.use_default_colors()
    if curses.has_colors():
        curses.start_color()
        curses.init_pair(1, curses.COLOR_CYAN, -1)     # chrome
        curses.init_pair(2, curses.COLOR_GREEN, -1)    # correct
        curses.init_pair(3, curses.COLOR_RED, -1)      # incorrect
        curses.init_pair(4, curses.COLOR_YELLOW, -1)   # banner
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)  # meta / explanation
    TriviaGame(stdscr, questions, cfg, state_dir, stop_file).run()


def main():
    ap = argparse.ArgumentParser(description="Idle Trivia game")
    ap.add_argument("--session", default="default")
    ap.add_argument("--state-dir", default=os.path.expanduser("~/.claude/trivia"))
    ap.add_argument("--questions", default=DEFAULT_QUESTIONS)
    ap.add_argument("--refresh", action="store_true",
                    help="fetch fresh questions from Open Trivia DB, then exit")
    ap.add_argument("--amount", type=int, default=50,
                    help="questions to fetch with --refresh")
    args = ap.parse_args()

    os.makedirs(args.state_dir, exist_ok=True)
    cfg = load_config(args.state_dir)

    if args.refresh:
        sys.exit(refresh_questions(args.questions, args.amount, cfg.get("categories")))

    questions = load_questions(args.questions, cfg.get("categories"))
    if not questions:
        # Fall back to the full bank if the category filter emptied it.
        questions = load_questions(args.questions, None)
    if not questions:
        print("No questions available. Run: python3 trivia.py --refresh",
              file=sys.stderr)
        sys.exit(1)

    stop_file = os.path.join(args.state_dir, f"{args.session}.stop")
    pid_file = os.path.join(args.state_dir, f"{args.session}.pid")

    # A stop that beat us here (fast turn) means: don't render at all.
    if os.path.exists(stop_file):
        try:
            os.remove(stop_file)
        except OSError:
            pass
        return

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    roll_daily_streak(args.state_dir)
    try:
        curses.wrapper(_curses_main, questions, cfg, args.state_dir, stop_file)
    finally:
        for p in (pid_file, stop_file):
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    main()
