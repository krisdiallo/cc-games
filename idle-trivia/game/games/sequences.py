"""Number sequences: spot the pattern, pick the next term.

All rounds are generated procedurally; difficulty ramps with the current
streak (bigger numbers, subtler patterns)."""

import random


def _arithmetic(lo, hi):
    start, step = random.randint(lo, hi), random.choice(
        [n for n in range(-9, 13) if n not in (0, 1)])
    seq = [start + step * i for i in range(4)]
    return seq, seq[-1] + step, f"arithmetic: +{step}" if step > 0 else f"arithmetic: {step}"


def _geometric(lo, hi):
    start, ratio = random.randint(max(1, lo // 3), max(2, hi // 6)), random.choice([2, 3, -2])
    seq = [start * ratio ** i for i in range(4)]
    return seq, seq[-1] * ratio, f"geometric: ×{ratio}"


def _fibonacci_like(lo, hi):
    a, b = random.randint(1, max(2, hi // 8)), random.randint(1, max(3, hi // 6))
    seq = [a, b]
    while len(seq) < 5:
        seq.append(seq[-1] + seq[-2])
    return seq[:5], seq[3] + seq[4], "each term is the sum of the previous two"


def _squares(lo, hi):
    start = random.randint(1, 6 + hi // 20)
    seq = [(start + i) ** 2 for i in range(4)]
    return seq, (start + 4) ** 2, "consecutive squares"


def _alternating(lo, hi):
    start = random.randint(lo, hi)
    add, sub = random.randint(3, 9 + hi // 10), random.randint(1, 5)
    seq, cur = [start], start
    for i in range(4):
        cur = cur + add if i % 2 == 0 else cur - sub
        seq.append(cur)
    nxt = seq[-1] + add if len(seq) % 2 == 1 else seq[-1] - sub
    return seq, nxt, f"alternating: +{add}, −{sub}"


def _step_growth(lo, hi):
    start, step, grow = random.randint(lo, hi), random.randint(1, 4), random.randint(1, 3)
    seq, cur = [start], start
    for i in range(4):
        cur += step + grow * i
        seq.append(cur)
    return seq, seq[-1] + step + grow * 4, f"the gap grows by {grow} each time"


EASY = [_arithmetic, _squares]
MEDIUM = EASY + [_geometric, _alternating]
HARD = MEDIUM + [_fibonacci_like, _step_growth]


class SequencesGame:
    name = "sequences"
    title = "SEQUENCES"
    keys_help = "1-4 answer · s skip"

    def __init__(self, cfg):
        self.cfg = cfg

    def playable(self):
        return True

    def _generate(self, streak):
        if streak < 3:
            pool, lo, hi = EASY, 1, 20
        elif streak < 6:
            pool, lo, hi = MEDIUM, 2, 40
        else:
            pool, lo, hi = HARD, 5, 90
        return random.choice(pool)(lo, hi)

    def play_round(self, shell):
        seq, answer, why = self._generate(shell.streak)

        # Distractors: plausible near-misses around the true next term.
        gap = abs(seq[-1] - seq[-2]) or 2
        candidates = {answer + gap, answer - gap, answer + 1, answer - 1,
                      answer + 2 * gap, seq[-1] + seq[0], answer + 10}
        candidates.discard(answer)
        wrong = random.sample(sorted(candidates), 3)
        options = wrong + [answer]
        random.shuffle(options)

        return shell.run_mcq(
            prompt="  ".join(str(n) for n in seq) + "   …?",
            options=[str(o) for o in options],
            answer_idx=options.index(answer),
            meta="[what comes next?]",
            explanation=f"Pattern: {why}.",
        )
