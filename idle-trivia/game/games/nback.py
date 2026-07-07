"""Letter n-back (default 2-back): a letter stream ticks by; hit the match key
whenever the current letter equals the one N positions earlier.

One round = one stream. The round scores as correct when at least 80% of the
scoreable decisions (respond / don't respond) were right."""

import curses
import random

LETTERS = "BFKMQRTX"
STREAM_LEN = 15
STIMULUS_SECONDS = 1.4
MATCH_RATE = 0.3
PASS_RATIO = 0.8


def make_stream(n):
    """Random letter stream with roughly MATCH_RATE forced n-back matches."""
    stream = [random.choice(LETTERS) for _ in range(n + 1)]
    while len(stream) < STREAM_LEN:
        if random.random() < MATCH_RATE:
            stream.append(stream[-n])
        else:
            stream.append(random.choice([c for c in LETTERS
                                         if c != stream[-n]]))
    return stream


class NBackGame:
    name = "nback"
    title = "N-BACK"
    keys_help = "SPACE when it matches N back"

    def __init__(self, cfg):
        self.n = int(cfg.get("nbackN", 2))

    def playable(self):
        return 1 <= self.n <= 4

    def _draw(self, shell, i, letter, hits, misses, fas, flash=""):
        shell.frame()
        shell.put(2, 2, f"[{self.n}-back · {i + 1}/{STREAM_LEN}]",
                  curses.color_pair(5))
        shell.put(3, 2, f"SPACE when the letter matches the one {self.n} ago.",
                  curses.color_pair(5))
        shell.put(5, 10, f"  {letter}  ",
                  curses.A_BOLD | curses.A_REVERSE)
        shell.put(7, 2, f"hits {hits} · misses {misses} · false alarms {fas}")
        if flash:
            shell.put(5, 20, flash, curses.A_BOLD)
        shell.scr.refresh()

    def play_round(self, shell):
        stream = make_stream(self.n)
        hits = misses = fas = correct_rejects = 0

        for i, letter in enumerate(stream):
            self._draw(shell, i, letter, hits, misses, fas)
            pressed = shell.poll_key(STIMULUS_SECONDS, accept=" m") is not None
            scoreable = i >= self.n
            is_match = scoreable and letter == stream[i - self.n]
            if scoreable:
                if is_match and pressed:
                    hits += 1
                    flash = "✔"
                elif is_match and not pressed:
                    misses += 1
                    flash = "✗ (missed one)"
                elif pressed:
                    fas += 1
                    flash = "✗ (no match)"
                else:
                    correct_rejects += 1
                    flash = ""
            else:
                flash = ""
            if flash:
                self._draw(shell, i, letter, hits, misses, fas, flash=flash)
                shell.wait(0.25)

        decisions = STREAM_LEN - self.n
        right = hits + correct_rejects
        passed = right >= PASS_RATIO * decisions
        shell.frame()
        shell.put(3, 2, f"Stream done: {right}/{decisions} decisions right.",
                  curses.A_BOLD)
        shell.put(5, 2, f"hits {hits} · misses {misses} · false alarms {fas}")
        shell.show_feedback(passed,
                            verdict="✔ Sharp!" if passed else "✗ Below 80%.",
                            detail=f"Pass line: {PASS_RATIO:.0%} of decisions.")
        return passed
