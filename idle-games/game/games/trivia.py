"""Multiple-choice trivia from the bundled question bank (+ Open Trivia DB
refresh, used by the --refresh CLI flag — never inside the game loop)."""

import html
import json
import os
import random
import sys

# Open Trivia DB category ids (https://opentdb.com/api_config.php).
OPENTDB_CATEGORIES = {"general": 9, "science": 17, "tech": 18, "history": 23}


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


class TriviaGame:
    name = "trivia"
    title = "IDLE TRIVIA"
    keys_help = "1-4 answer · s skip"

    def __init__(self, cfg, questions_path):
        self.questions = load_questions(questions_path, cfg.get("categories"))
        if not self.questions:
            # Fall back to the full bank if the category filter emptied it.
            self.questions = load_questions(questions_path, None)
        self._order = list(range(len(self.questions)))
        random.shuffle(self._order)
        self._i = 0

    def playable(self):
        return bool(self.questions)

    def play_round(self, shell):
        q = self.questions[self._order[self._i % len(self._order)]]
        self._i += 1
        return shell.run_mcq(
            prompt=q.get("question", ""),
            options=q["options"],
            answer_idx=int(q["answer"]),
            meta=f"[{q.get('category', '?')}·{q.get('difficulty', '?')}]",
            explanation=q.get("explanation", ""),
        )
