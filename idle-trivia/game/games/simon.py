"""Simon-style sequence memory: watch the pads flash, repeat the sequence.

Classic Simon: ONE persistent sequence that gains a pad on every success —
each round you re-watch and repeat everything so far. A miss starts a fresh
sequence at the base length."""

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
        self.seq = []       # the persistent sequence; [] = start fresh

    def playable(self):
        return True

    def _draw_pads(self, shell, active=None, label=""):
        shell.frame()
        shell.put(2, 2, f"[sequence length {len(self.seq)}]",
                  curses.color_pair(5))
        shell.put(3, 2, label, curses.A_BOLD)
        for i in range(4):
            attr = curses.color_pair(PAD_COLORS[i])
            if i == active:
                attr |= curses.A_REVERSE | curses.A_BOLD
            shell.put(5, 4 + i * 8, f"[  {i + 1}  ]", attr)
        shell.scr.refresh()

    def play_round(self, shell):
        if not self.seq:
            self.seq = [random.randrange(4) for _ in range(BASE_LEN)]
        else:
            self.seq.append(random.randrange(4))
        seq = self.seq

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
                    detail=f"New sequence, back to length {BASE_LEN}.")
                self.seq = []
                return False
            progress += ch + " "

        self._draw_pads(shell, label="Your turn:  " + progress)
        shell.show_feedback(True, verdict="✔ Perfect recall!",
                            detail=f"Same sequence + 1: {len(seq) + 1} pads next.")
        return True
