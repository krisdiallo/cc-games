"""Snake: real-time, wasd/arrows, and idle-friendly — a run frozen when
Claude finishes (or you switch games) resumes exactly where it was on the
next prompt. Lifetime best length persists in state_dir/snake.json.

Round semantics: one run per round; dying with a new personal best counts
as correct, an ordinary death as wrong, a frozen run as no-score.
"""

import curses
import json
import os
import random

BASE_TICK = 0.16
MIN_TICK = 0.08
AUTOSAVE_TICKS = 25

DIRS = {"w": (0, -1), "s": (0, 1), "a": (-1, 0), "d": (1, 0)}
OPPOSITE = {"w": "s", "s": "w", "a": "d", "d": "a"}


class SnakeGame:
    name = "snake"
    title = "SNAKE"
    keys_help = "wasd/arrows steer"

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = None          # bound on first round (needs state_dir)
        self.data = {"best": 0, "run": None}

    def playable(self):
        return True

    # -- persistence --------------------------------------------------------- #
    def _bind(self, shell):
        if self.path is not None:
            return
        self.path = os.path.join(shell.state_dir, "snake.json")
        try:
            with open(self.path) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data["best"] = int(loaded.get("best", 0))
                self.data["run"] = loaded.get("run")
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.data, f)
        except OSError:
            pass

    # -- board --------------------------------------------------------------- #
    def _geometry(self, shell):
        h, w = shell.scr.getmaxyx()
        gw = max(16, min(w - 6, 32))
        gh = max(4, h - 7)        # box interior; pane of 12 rows -> 5
        return gw, gh

    def _new_run(self, gw, gh):
        cx, cy = gw // 2, gh // 2
        snake = [[cx - i, cy] for i in range(3)]
        return {"gw": gw, "gh": gh, "snake": snake, "dir": "d",
                "food": self._drop_food(snake, gw, gh), "score": 0}

    def _drop_food(self, snake, gw, gh):
        occupied = {tuple(p) for p in snake}
        while True:
            p = [random.randrange(gw), random.randrange(gh)]
            if tuple(p) not in occupied:
                return p

    def _draw(self, shell, run):
        shell.frame()
        shell.put(2, 2, f"length {len(run['snake'])} · score {run['score']}"
                        f" · best {self.data['best']}", curses.color_pair(5))
        top, left = 3, 2
        gw, gh = run["gw"], run["gh"]
        shell.put(top, left, "┌" + "─" * gw + "┐")
        for y in range(gh):
            shell.put(top + 1 + y, left, "│")
            shell.put(top + 1 + y, left + 1 + gw, "│")
        shell.put(top + 1 + gh, left, "└" + "─" * gw + "┘")
        fx, fy = run["food"]
        shell.put(top + 1 + fy, left + 1 + fx, "●",
                  curses.color_pair(3) | curses.A_BOLD)
        for i, (x, y) in enumerate(run["snake"]):
            ch = "█" if i == 0 else "▓"
            shell.put(top + 1 + y, left + 1 + x, ch,
                      curses.color_pair(2) | (curses.A_BOLD if i == 0 else 0))
        shell.scr.refresh()

    # -- the run -------------------------------------------------------------- #
    def play_round(self, shell):
        self._bind(shell)
        gw, gh = self._geometry(shell)
        run = self.data["run"]
        if not (run and run.get("gw") == gw and run.get("gh") == gh):
            run = self._new_run(gw, gh)
            self.data["run"] = run
            self._save()

        ticks = 0
        try:
            while True:
                self._draw(shell, run)
                if shell.stop_pending:            # finish-question mode: freeze
                    self._save()
                    return None
                tick = max(MIN_TICK, BASE_TICK - run["score"] * 0.004)
                key = shell.poll_key(tick, accept="wasd")
                if key and key != OPPOSITE[run["dir"]]:
                    run["dir"] = key

                dx, dy = DIRS[run["dir"]]
                head = [run["snake"][0][0] + dx, run["snake"][0][1] + dy]
                hit_wall = not (0 <= head[0] < gw and 0 <= head[1] < gh)
                hit_self = head in run["snake"][:-1]
                if hit_wall or hit_self:
                    return self._game_over(shell, run, hit_wall)

                run["snake"].insert(0, head)
                if head == run["food"]:
                    run["score"] += 1
                    run["food"] = self._drop_food(run["snake"], gw, gh)
                else:
                    run["snake"].pop()

                ticks += 1
                if ticks % AUTOSAVE_TICKS == 0:
                    self._save()
        except BaseException:
            # Stop/quit/switch (or anything else) freezes the run for later.
            self._save()
            raise

    def _game_over(self, shell, run, hit_wall):
        length = len(run["snake"])
        new_best = length > self.data["best"]
        if new_best:
            self.data["best"] = length
        self.data["run"] = None
        self._save()
        self._draw(shell, run)
        cause = "wall" if hit_wall else "yourself"
        shell.show_feedback(
            new_best,
            verdict=(f"★ NEW BEST: length {length}!" if new_best
                     else f"✗ Ran into the {cause} at length {length}."),
            detail=f"Best ever: {self.data['best']}.")
        return new_best
