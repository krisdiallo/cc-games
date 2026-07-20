"""Word games: anagram unscrambles and odd-one-out picks, from the bundled
words.json (no network)."""

import json
import os
import random

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "words.json")


def _scramble(word):
    """Shuffle letters until the result differs from the word (always possible
    for the 5+-letter words we bundle)."""
    letters = list(word.upper())
    for _ in range(20):
        random.shuffle(letters)
        if "".join(letters) != word.upper():
            break
    return " ".join(letters)


class WordsGame:
    name = "words"
    title = "WORD GAMES"
    keys_help = "1-4 answer · s skip"

    def __init__(self, cfg):
        try:
            with open(DATA) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.words = [w for w in data.get("anagrams", [])
                      if isinstance(w, str) and len(w) >= 5]
        self.groups = [g for g in data.get("odd_one_out", [])
                       if isinstance(g, dict) and len(g.get("members", [])) == 3
                       and g.get("odd")]

    def playable(self):
        return bool(self.words) or bool(self.groups)

    def _anagram_round(self, shell):
        word = random.choice(self.words)
        # Decoys: other words, preferring same length and shared letters so the
        # scramble isn't solvable by length alone.
        others = [w for w in self.words if w != word]
        others.sort(key=lambda w: (abs(len(w) - len(word)),
                                   -len(set(w) & set(word))))
        wrong = others[:8]
        random.shuffle(wrong)
        options = wrong[:3] + [word]
        random.shuffle(options)
        return shell.run_mcq(
            prompt=f"Unscramble:  {_scramble(word)}",
            options=[o.upper() for o in options],
            answer_idx=options.index(word),
            meta="[anagram]",
        )

    def _odd_one_out_round(self, shell):
        g = random.choice(self.groups)
        options = list(g["members"]) + [g["odd"]]
        random.shuffle(options)
        return shell.run_mcq(
            prompt="Which one doesn't belong?",
            options=options,
            answer_idx=options.index(g["odd"]),
            meta="[odd one out]",
            explanation=g.get("why", ""),
        )

    def play_round(self, shell):
        modes = ([self._anagram_round] if self.words else []) + \
                ([self._odd_one_out_round] if self.groups else [])
        return random.choice(modes)(shell)
