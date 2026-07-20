"""Shared shell for idle brain games.

Owns everything that is not game content: curses setup, the per-session
stop-file protocol, pause/quit/switch keys, header/footer chrome, feedback,
stats persistence, wrap-up, and the GUI-terminal auto-close.

Games implement:

    class Game:
        name      = "trivia"          # config/stats key
        title     = "IDLE TRIVIA"     # header text
        keys_help = "1-4 answer"      # game-specific part of the footer

        def play_round(self, shell) -> bool | None:
            ...one round; True=correct, False=wrong, None=skipped/aborted

and use the shell primitives: put/get_key/wait/poll_key/run_mcq/show_feedback.
get_key and wait raise ShellStop/ShellQuit/ShellSwitch to unwind a round from
any depth; the run loop catches them.

Screen contract (pane is ~12 rows): shell owns row 0 (header), row 1 (stop
banner) and the last row (footer); games draw on rows 2..h-2.
"""

import curses
import fcntl
import json
import os
import sys
import time
from datetime import date, timedelta

import turnstate

POLL_MS = 100            # input timeout / stop-file poll cadence
FEEDBACK_SECONDS = 1.5   # how long correct/incorrect feedback shows
TURNCHECK_SECONDS = 2.0  # how often to consult the transcript
INTERRUPT_IDLE_SECONDS = 45  # Escape + this much silence = abandoned window

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.example.json")

CHROME, GOOD, BAD, WARN, META = 1, 2, 3, 4, 5

# Arrow keys act as wasd everywhere (curses.wrapper enables keypad mode).
ARROW_KEYS = {
    curses.KEY_UP: "w", curses.KEY_DOWN: "s",
    curses.KEY_LEFT: "a", curses.KEY_RIGHT: "d",
}


def _key_to_char(c):
    """Translate a getch() code to a 1-char string, or None if unmapped."""
    if c in ARROW_KEYS:
        return ARROW_KEYS[c]
    if 0 <= c < 256:
        return chr(c)
    return None


class ShellStop(Exception):
    """Claude finished (stop file) — unwind to wrap-up."""


class ShellQuit(Exception):
    """User pressed q — unwind to wrap-up."""


class ShellSwitch(Exception):
    """User pressed g — switch to the next game."""


# --------------------------------------------------------------------------- #
# Config / stats (state_dir/stats.json, flock-guarded)                         #
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
    cfg.setdefault("games", ["dungeon", "poker", "snake", "trivia",
                             "sequences", "words", "simon", "nback"])
    cfg.setdefault("game", "random")
    cfg.setdefault("sound", False)
    cfg.setdefault("autoCloseTerminal", True)
    return cfg


def _stats_path(state_dir):
    return os.path.join(state_dir, "stats.json")


def update_stats(state_dir, mutator):
    """Read-modify-write stats.json under an exclusive flock (multi-session safe).
    `mutator(stats_dict)` mutates in place; returns the resulting dict."""
    path = _stats_path(state_dir)
    default = {
        "lifetime": {"answered": 0, "correct": 0, "best_streak": 0},
        "daily": {"date": "", "streak_days": 0, "answered": 0},
        "games": {},
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
        stats.setdefault("games", {})
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


def _game_bucket(stats, game_name):
    return stats["games"].setdefault(
        game_name, {"answered": 0, "correct": 0, "best_streak": 0})


def record_answer(state_dir, game_name, correct):
    def m(stats):
        lt = stats["lifetime"]
        lt["answered"] = lt.get("answered", 0) + 1
        if correct:
            lt["correct"] = lt.get("correct", 0) + 1
        stats["daily"]["answered"] = stats["daily"].get("answered", 0) + 1
        g = _game_bucket(stats, game_name)
        g["answered"] += 1
        if correct:
            g["correct"] += 1

    update_stats(state_dir, m)


def record_best_streak(state_dir, game_name, streak):
    def m(stats):
        lt = stats["lifetime"]
        if streak > lt.get("best_streak", 0):
            lt["best_streak"] = streak
        g = _game_bucket(stats, game_name)
        if streak > g["best_streak"]:
            g["best_streak"] = streak

    update_stats(state_dir, m)


# --------------------------------------------------------------------------- #
# GUI-terminal auto-close                                                       #
# --------------------------------------------------------------------------- #

def close_own_terminal_window():
    """Best-effort: on macOS Terminal.app / iTerm2, close the GUI window this
    game is running in once it exits. (In a tmux pane the pane self-closes, so
    this is a no-op there.)

    A detached `delay 0.4` closer is used so it fires *after* this Python
    process has exited — by then only the login shell remains in the window, so
    Terminal closes it without the "terminate running processes?" prompt. The
    window is matched by tty so we only ever close our own window.
    """
    import subprocess

    if sys.platform != "darwin" or os.environ.get("TMUX"):
        return
    term = os.environ.get("TERM_PROGRAM", "")
    try:
        tty = os.ttyname(0)
    except OSError:
        try:
            tty = os.ttyname(sys.stdout.fileno())
        except (OSError, ValueError):
            return

    if term == "Apple_Terminal":
        script = (
            'delay 0.4\n'
            'tell application "Terminal"\n'
            f'  close (every window whose tty is "{tty}") saving no\n'
            'end tell'
        )
    elif term == "iTerm.app":
        script = (
            'delay 0.4\n'
            'tell application "iTerm2"\n'
            '  repeat with w in windows\n'
            '    repeat with t in tabs of w\n'
            '      repeat with s in sessions of t\n'
            f'        if (tty of s) is "{tty}" then close t\n'
            '      end repeat\n'
            '    end repeat\n'
            '  end repeat\n'
            'end tell'
        )
    else:
        return

    try:
        # start_new_session detaches the closer from this window's foreground
        # process group so it survives our exit and can act on the window.
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Shell                                                                         #
# --------------------------------------------------------------------------- #

class Shell:
    def __init__(self, stdscr, cfg, state_dir, stop_file, games,
                 transcript_path=None):
        self.scr = stdscr
        self.cfg = cfg
        self.state_dir = state_dir
        self.stop_file = stop_file
        self.transcript_path = transcript_path or None
        self._next_turncheck = 0.0
        # <session>.attn — touched by the Notification hook when Claude is
        # blocked on the user (permission approval, question, idle prompt).
        self.attn_file = stop_file[:-len(".stop")] + ".attn"
        self.games = games                  # ordered list of Game instances
        self.game = None                    # current Game
        self.session_answered = 0
        self.session_correct = 0
        self.streak = 0
        self.best_streak = 0
        self.paused = False
        self.stop_pending = False           # stop seen in finish-question mode
        self.attention = False              # Claude is waiting on the user

    # -- stop protocol ------------------------------------------------------ #
    def stop_requested(self):
        return os.path.exists(self.stop_file)

    def _finish_question_mode(self):
        return self.cfg.get("stopBehavior") == "finish-question"

    def _check_stop(self):
        """Raise ShellStop on stop — or, in finish-question mode, just set the
        banner flag and let the current round complete. Also tracks the
        attention marker (Claude waiting on the user) and rings once when it
        appears."""
        attn = os.path.exists(self.attn_file)
        if attn != self.attention:
            self.attention = attn
            if attn:
                try:
                    curses.beep()
                except curses.error:
                    pass
            self.draw_header()
            self.scr.refresh()
        if not self.stop_requested():
            self._check_abandoned()
            return
        if self._finish_question_mode():
            if not self.stop_pending:
                self.stop_pending = True
                self.draw_header()
                self.scr.refresh()
        else:
            raise ShellStop()

    def _request_quit(self, session_wide):
        """q = quit until the next prompt; Q = quit for the whole session
        (drops a .quiet marker that start-trivia.sh honors)."""
        if session_wide:
            try:
                open(self.stop_file[:-len(".stop")] + ".quiet", "w").close()
            except OSError:
                pass
        raise ShellQuit()

    def _check_abandoned(self):
        """Escape interrupts fire no hook, so no stop file ever arrives. If
        the transcript's last entry is the interrupt marker and it has sat
        there for a while, the user walked away — close up. (A quick
        Escape-edit-resubmit keeps the window: the marker is younger than the
        threshold, and the resubmit reuses this game.)"""
        if not self.transcript_path:
            return
        now = time.monotonic()
        if now < self._next_turncheck:
            return
        self._next_turncheck = now + TURNCHECK_SECONDS
        interrupted, age = turnstate.last_turn_state(self.transcript_path)
        if interrupted and age is not None and age > INTERRUPT_IDLE_SECONDS:
            raise ShellStop()

    # -- input primitives ---------------------------------------------------- #
    def get_key(self, timeout=None, accept=None):
        """Wait up to `timeout` seconds (forever if None) for a key.
        Returns the key as a 1-char string, or None on timeout.

        Handles the global keys itself: q quits, g switches game, p pauses
        (blocks here until resumed). If `accept` is given, only those keys are
        returned; others are ignored.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        self.scr.timeout(POLL_MS)
        while True:
            c = self.scr.getch()
            self._check_stop()
            ch = _key_to_char(c) if c != -1 else None
            if ch is not None:
                if ch in "qQ":
                    self._request_quit(ch == "Q")
                if ch == "g" and len(self.games) > 1:
                    raise ShellSwitch()
                if ch == "p":
                    self._pause_blocking()
                    continue
                if accept is None or ch in accept:
                    return ch
            if deadline is not None and time.monotonic() >= deadline:
                return None

    def wait(self, seconds, interruptible=True):
        """Sleep while staying responsive to stop/quit/pause. Keys other than
        the global ones are drained and ignored."""
        deadline = time.monotonic() + seconds
        self.scr.timeout(POLL_MS)
        while time.monotonic() < deadline:
            c = self.scr.getch()
            if interruptible:
                self._check_stop()
            ch = _key_to_char(c) if c != -1 else None
            if ch is not None:
                if ch in "qQ":
                    self._request_quit(ch == "Q")
                if ch == "g" and len(self.games) > 1:
                    raise ShellSwitch()
                if ch == "p":
                    self._pause_blocking()
                    deadline = time.monotonic() + seconds  # restart the beat

    def poll_key(self, seconds, accept):
        """Collect the first accepted key within a fixed time window (for paced
        games like n-back). Returns the key or None; the window always runs to
        its end so the stimulus cadence stays steady."""
        deadline = time.monotonic() + seconds
        hit = None
        self.scr.timeout(POLL_MS)
        while time.monotonic() < deadline:
            c = self.scr.getch()
            self._check_stop()
            ch = _key_to_char(c) if c != -1 else None
            if ch is not None:
                if ch in "qQ":
                    self._request_quit(ch == "Q")
                if ch == "g" and len(self.games) > 1:
                    raise ShellSwitch()
                if ch == "p":
                    self._pause_blocking()
                    continue
                if hit is None and ch in accept:
                    hit = ch
        return hit

    def _pause_blocking(self):
        self.paused = True
        h, w = self.scr.getmaxyx()
        self.put(0, max(0, (w - 30) // 2), " PAUSED — press p to resume ",
                 curses.color_pair(WARN) | curses.A_BOLD)
        self.scr.refresh()
        self.scr.timeout(POLL_MS)
        while True:
            c = self.scr.getch()
            self._check_stop()   # stop still ends a paused game
            if c != -1 and 0 <= c < 256:
                ch = chr(c)
                if ch == "p":
                    break
                if ch in "qQ":
                    self._request_quit(ch == "Q")
        self.paused = False
        self.draw_header()
        self.scr.refresh()

    # -- rendering ------------------------------------------------------------ #
    def put(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and 0 <= x < w:
            try:
                self.scr.addnstr(y, x, text, max(0, w - x - 1), attr)
            except curses.error:
                pass

    def draw_header(self):
        h, w = self.scr.getmaxyx()
        acc = (100 * self.session_correct // self.session_answered
               if self.session_answered else 0)
        left = f" {self.game.title} " if self.game else " IDLE GAMES "
        right = (f" streak {self.streak} · "
                 f"{self.session_correct}/{self.session_answered} ({acc}%) ")
        self.put(0, 0, left + " " * max(0, w - len(left) - len(right) - 1) + right,
                 curses.color_pair(CHROME) | curses.A_BOLD)
        if self.stop_pending:
            self.put(1, 0, " ✅ Claude's done — finish this round ",
                     curses.color_pair(WARN) | curses.A_BOLD)
        elif self.attention:
            self.put(1, 0, " ⚠ CLAUDE IS WAITING FOR YOU — check the Claude "
                           "window (approval/question) ",
                     curses.color_pair(BAD) | curses.A_BOLD | curses.A_REVERSE)
        else:
            self.put(1, 0, " " * (w - 1))   # clear a lifted banner

    def draw_footer(self):
        h, _ = self.scr.getmaxyx()
        help_txt = self.game.keys_help if self.game else ""
        extra = " · g next game" if len(self.games) > 1 else ""
        self.put(h - 1, 0, f" {help_txt}{extra} · p pause · q/Q quit ",
                 curses.color_pair(CHROME))

    def frame(self):
        """Start a fresh screen with chrome; games draw rows 2..h-2 after this."""
        self.scr.erase()
        self.draw_header()
        self.draw_footer()

    def show_feedback(self, correct, verdict=None, detail=""):
        """Standard verdict line (+ optional detail) near the bottom, then the
        feedback dwell. Call after the game has drawn its reveal state."""
        h, _ = self.scr.getmaxyx()
        if verdict is None:
            verdict = "✔ Correct!" if correct else "✗ Not quite."
        color = curses.color_pair(GOOD if correct else BAD)
        self.put(h - 3, 2, verdict, color | curses.A_BOLD)
        if detail:
            self.put(h - 2, 2, detail, curses.color_pair(META))
        self.draw_footer()
        self.scr.refresh()
        self.wait(FEEDBACK_SECONDS)

    # -- shared MCQ round ------------------------------------------------------ #
    def run_mcq(self, prompt, options, answer_idx, meta="", explanation="",
                skippable=True):
        """Render a 4-option multiple-choice round and score it.
        Returns True/False for answered, None for skipped."""
        self.frame()
        self.put(2, 2, meta, curses.color_pair(META))
        self.put(3, 2, prompt, curses.A_BOLD)
        for i, opt in enumerate(options):
            self.put(5 + i, 4, f"{i + 1}. {opt}")
        self.scr.refresh()

        accept = "1234s" if skippable else "1234"
        ch = self.get_key(accept=accept)
        if ch == "s":
            return None
        selected = int(ch) - 1
        correct = selected == answer_idx

        # Reveal.
        self.frame()
        self.put(2, 2, meta, curses.color_pair(META))
        self.put(3, 2, prompt, curses.A_BOLD)
        for i, opt in enumerate(options):
            attr, prefix = 0, "  "
            if i == answer_idx:
                attr = curses.color_pair(GOOD) | curses.A_BOLD
                prefix = "✔ "
            elif i == selected:
                attr = curses.color_pair(BAD) | curses.A_BOLD
                prefix = "✗ "
            self.put(5 + i, 4, f"{prefix}{i + 1}. {opt}", attr)
        self.show_feedback(correct, detail=explanation)
        return correct

    # -- main loop -------------------------------------------------------------- #
    def _score(self, result):
        if result is None:
            self.streak = 0    # skips break the streak, as before
            return
        self.session_answered += 1
        if result:
            self.session_correct += 1
            self.streak += 1
            self.best_streak = max(self.best_streak, self.streak)
        else:
            self.streak = 0
        record_answer(self.state_dir, self.game.name, result)

    def _next_game(self):
        i = self.games.index(self.game)
        return self.games[(i + 1) % len(self.games)]

    def run(self, first_game):
        self.game = first_game
        quit_by_user = False
        try:
            while True:
                if self.stop_requested() and not self._finish_question_mode():
                    break
                try:
                    result = self.game.play_round(self)
                    self._score(result)
                except ShellSwitch:
                    record_best_streak(self.state_dir, self.game.name,
                                       self.best_streak)
                    self.game = self._next_game()
                    self.streak = 0
                    continue
                # A stop that arrived mid-round (finish-question mode, or during
                # feedback) ends the game now that the round is complete.
                if self.stop_pending or self.stop_requested():
                    break
        except ShellStop:
            pass
        except ShellQuit:
            quit_by_user = True

        record_best_streak(self.state_dir, self.game.name, self.best_streak)
        self.wrap_up(quit_by_user)

    def wrap_up(self, quit_by_user):
        behavior = self.cfg.get("stopBehavior", "linger")
        linger = 0 if quit_by_user or behavior == "immediate" else \
            max(0, int(self.cfg.get("lingerSeconds", 2)))
        acc = (100 * self.session_correct // self.session_answered
               if self.session_answered else 0)

        self.scr.erase()
        title = "👋 Quit" if quit_by_user else "✅ Claude's done!"
        self.put(1, 2, title, curses.color_pair(GOOD) | curses.A_BOLD)
        self.put(3, 2, f"This round: {self.session_correct}/{self.session_answered}"
                       f" correct ({acc}%)")
        self.put(4, 2, f"Best streak this round: {self.best_streak}")
        self.put(6, 2, "(this window is safe to close)", curses.color_pair(META))
        self.scr.refresh()

        # Honor "immediate" (no linger) but still show the screen a heartbeat so
        # it isn't a jarring flash.
        deadline = time.monotonic() + (linger if linger else 0.4)
        self.scr.timeout(POLL_MS)
        while time.monotonic() < deadline:
            self.scr.getch()

        # Fire the (detached) GUI-terminal auto-close; harmless in tmux/SSH.
        if self.cfg.get("autoCloseTerminal", True):
            close_own_terminal_window()


def curses_main(stdscr, cfg, state_dir, stop_file, games, first_game,
                transcript_path=None):
    curses.curs_set(0)
    curses.use_default_colors()
    if curses.has_colors():
        curses.start_color()
        curses.init_pair(CHROME, curses.COLOR_CYAN, -1)
        curses.init_pair(GOOD, curses.COLOR_GREEN, -1)
        curses.init_pair(BAD, curses.COLOR_RED, -1)
        curses.init_pair(WARN, curses.COLOR_YELLOW, -1)
        curses.init_pair(META, curses.COLOR_MAGENTA, -1)
    Shell(stdscr, cfg, state_dir, stop_file, games,
          transcript_path=transcript_path).run(first_game)
