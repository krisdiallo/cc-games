"""Simon-style sequence memory: watch the pads flash, repeat the sequence.

One round = one sequence attempt. The sequence grows by one on every success
and resets to the base length after a miss (classic Simon)."""

import curses
import random

BASE_LEN = 3
FLASH_ON = 0.55
FLASH_GAP = 0.22

PAD_COLORS = [2, 3, 4, 1]   # GOOD, BAD, WARN, CHROME — four distinct pads


class SimonGame:
    name = "simon"
    title = "SIMON"
    keys_help = "watch, then 1-4 repeat"

    def __init__(self, cfg):
        self.level = BASE_LEN

    def playable(self):
        return True

    def _draw_pads(self, shell, active=None, label=""):
        shell.frame()
        shell.put(2, 2, f"[sequence length {self.level}]",
                  curses.color_pair(5))
        shell.put(3, 2, label, curses.A_BOLD)
        for i in range(4):
            attr = curses.color_pair(PAD_COLORS[i])
            if i == active:
                attr |= curses.A_REVERSE | curses.A_BOLD
            shell.put(5, 4 + i * 8, f"[  {i + 1}  ]", attr)
        shell.scr.refresh()

    def play_round(self, shell):
        seq = [random.randrange(4) for _ in range(self.level)]

        # Watch phase.
        self._draw_pads(shell, label="Watch…")
        shell.wait(0.8)
        for pad in seq:
            self._draw_pads(shell, active=pad, label="Watch…")
            shell.wait(FLASH_ON)
            self._draw_pads(shell, label="Watch…")
            shell.wait(FLASH_GAP)

        # Repeat phase.
        progress = ""
        for step, expected in enumerate(seq):
            self._draw_pads(shell, label=f"Your turn:  {progress}")
            ch = shell.get_key(accept="1234")
            pressed = int(ch) - 1
            self._draw_pads(shell, active=pressed,
                            label=f"Your turn:  {progress}{ch} ")
            shell.wait(0.15)
            if pressed != expected:
                self._draw_pads(shell, active=expected,
                                label="Sequence was: "
                                      + " ".join(str(p + 1) for p in seq))
                shell.show_feedback(
                    False, verdict=f"✗ Missed at step {step + 1}.",
                    detail=f"Back to length {BASE_LEN}.")
                self.level = BASE_LEN
                return False
            progress += ch + " "

        self.level += 1
        self._draw_pads(shell, label="Your turn:  " + progress)
        shell.show_feedback(True, verdict="✔ Perfect recall!",
                            detail=f"Next sequence: {self.level} pads.")
        return True
