"""5-max no-limit hold'em vs four AI personalities, built to make you better.

Stateful across sessions (state_dir/poker.json):
  - bankroll (+ rebuy count when you bust), biggest pot, hands played
  - career decision stats — the villains exploit the same leaks the trainer
    reports (The Shark bluffs more once you're shown to overfold)
  - the live hand, frozen mid-street when Claude finishes and resumed later

Training stack, always on and offline:
  - every decision is graded vs pot odds/equity (postflop) or the 5-max
    charts (preflop); errors are priced in EV chips
  - a post-hand review screen + recurring leak line

Optional LLM layer (config "pokerLLM": true): villain decisions and a
post-hand coach line via `claude -p` (haiku), with a hard timeout and
silent fallback to the heuristic bots. Subprocesses run with
IDLE_TRIVIA=off so headless hooks can't spawn games recursively.
"""

import curses
import json
import os
import random
import subprocess

from . import pokerengine as pe

STAKE = 1000
VILLAINS = [("The Rock", "rock"), ("The Fish", "fish"),
            ("The Shark", "shark"), ("The Prof", "professor")]
HERO = 0
GRADE_ITERS = 250
BOT_ITERS = 80
STREETS = ["PRE", "FLOP", "TURN", "RIVER"]


def _default_profile():
    return {"bankroll": STAKE, "rebuys": 0, "hands": 0, "net": 0,
            "biggest_pot": 0, "button": 0,
            "career": {"decisions": 0, "errors": 0, "questionable": 0,
                       "vpip": 0, "preflop_seen": 0,
                       "raise_faced": 0, "raise_folded": 0,
                       "overfolds": 0, "overcalls": 0, "spew": 0},
            "run": None}


class PokerGame:
    name = "poker"
    title = "HOLD'EM"
    keys_help = "1 fold · 2 check/call · 3 raise · 4 shove"

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = None
        self.p = _default_profile()

    def playable(self):
        return True

    # -- persistence -------------------------------------------------------- #
    def _bind(self, shell):
        if self.path is not None:
            return
        self.path = os.path.join(shell.state_dir, "poker.json")
        try:
            with open(self.path) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and "bankroll" in loaded:
                base = _default_profile()
                base.update(loaded)
                base["career"] = {**_default_profile()["career"],
                                  **(loaded.get("career") or {})}
                self.p = base
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.p, f)
        except OSError:
            pass

    # -- LLM layer (opt-in, hard-timeout, silent fallback) -------------------- #
    def _llm(self, prompt, timeout):
        if not self.cfg.get("pokerLLM", False):
            return None
        env = dict(os.environ, IDLE_TRIVIA="off")
        try:
            r = subprocess.run(
                ["claude", "-p", prompt, "--model",
                 str(self.cfg.get("pokerLLMModel", "haiku"))],
                capture_output=True, text=True, timeout=timeout, env=env)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    def _llm_decide(self, st, i, pers, legal):
        board = " ".join(pe.card_str(c) for c in st["board"]) or "(preflop)"
        s = st["seats"][i]
        prompt = (
            f"You are '{s['name']}', a {pers} poker player in 5-max NLHE. "
            f"Your hole: {pe.card_str(s['hole'][0])} {pe.card_str(s['hole'][1])}. "
            f"Board: {board}. Pot: {pe.pot_size(st)}. "
            f"To call: {pe.owed(st, i)}. Your stack: {s['stack']}. "
            f"Legal actions: {', '.join(legal)}. "
            'Reply with ONLY JSON: {"action": "<one legal action>", "say": "<short table talk or empty>"}')
        out = self._llm(prompt, timeout=6)
        if not out:
            return None, None
        try:
            start, end = out.index("{"), out.rindex("}") + 1
            d = json.loads(out[start:end])
            action = d.get("action")
            if action in legal:
                return action, str(d.get("say", ""))[:60]
        except (ValueError, TypeError):
            pass
        return None, None

    # -- rendering ------------------------------------------------------------ #
    def _card(self, shell, y, x, c):
        red = c % 4 in (1, 2)     # hearts, diamonds
        shell.put(y, x, f"[{pe.card_str(c)}]",
                  curses.color_pair(3 if red else 0) | curses.A_BOLD)

    def _table(self, shell, st, note="", reveal=False):
        run = self.p["run"]
        shell.frame()
        shell.put(2, 2, f"hand #{self.p['hands'] + 1} · blinds {pe.SB}/{pe.BB}"
                        f" · bankroll {self.p['bankroll'] + st['seats'][HERO]['stack']}◆"
                        f" · best pot {self.p['biggest_pot']}",
                  curses.color_pair(5))
        acted = {}
        for street, name, label in st["history"]:
            acted[name] = label
        for k in range(4):
            i = k + 1
            s = st["seats"][i]
            col = 2 + (k % 2) * 30
            row = 3 + k // 2
            state = "folded" if not s["in"] else acted.get(s["name"], "·")
            hole = ""
            if reveal and s["in"] and st.get("showdown"):
                hole = f" {pe.card_str(s['hole'][0])}{pe.card_str(s['hole'][1])}"
            mark = "▶" if pe.to_act(st) == i else " "
            shell.put(row, col, f"{mark}{s['name']} {s['stack']}{hole} · {state}",
                      0 if s["in"] else curses.color_pair(5))
        board = st["board"]
        shell.put(5, 2, "board:", curses.color_pair(5))
        for j, c in enumerate(board):
            self._card(shell, 5, 9 + j * 5, c)
        if not board:
            shell.put(5, 9, "· · ·", curses.color_pair(5))
        shell.put(5, 34, f"pot {pe.pot_size(st)}", curses.A_BOLD)
        hero = st["seats"][HERO]
        shell.put(6, 2, "you:", curses.A_BOLD)
        self._card(shell, 6, 7, hero["hole"][0])
        self._card(shell, 6, 12, hero["hole"][1])
        o = pe.owed(st, HERO)
        shell.put(6, 18, f"stack {hero['stack']}"
                         + (f" · to call {o}" if o and hero["in"] else ""))
        if pe.to_act(st) == HERO:
            o = pe.owed(st, HERO)
            target = pe.raise_target(st, HERO)
            labels = ["1 fold" if o else "1 fold(=check)",
                      f"2 {'call ' + str(o) if o else 'check'}",
                      f"3 raise→{min(target, hero['cr'] + hero['stack'])}",
                      "4 shove"]
            shell.put(7, 2, "   ".join(labels), curses.A_BOLD)
        if note:
            shell.put(8, 2, note[:70], curses.color_pair(4))
        shell.scr.refresh()

    # -- hero stats the villains exploit -------------------------------------- #
    def _hero_stats(self):
        c = self.p["career"]
        faced = max(1, c["raise_faced"])
        return {"fold_to_raise_rate": c["raise_folded"] / faced}

    # -- one hand ---------------------------------------------------------------- #
    def play_round(self, shell):
        self._bind(shell)
        run = self.p["run"]
        if run is None:
            if self.p["bankroll"] < pe.BB * 4:      # busted: rebuy, tracked
                self.p["rebuys"] += 1
                self.p["bankroll"] = STAKE
            stake = min(STAKE, self.p["bankroll"])
            self.p["bankroll"] -= stake
            self.p["button"] = (self.p["button"] + 1) % 5
            names = ["You"] + [v[0] for v in VILLAINS]
            st = pe.new_hand(names, [stake] + [STAKE] * 4, self.p["button"])
            run = {"st": st, "stake": stake, "grades": [], "talk": ""}
            self.p["run"] = run
            self._save()

        st = run["st"]
        try:
            while not st["done"]:
                if shell.stop_pending:
                    self._save()
                    return None
                i = pe.to_act(st)
                if i == HERO:
                    self._hero_turn(shell, st, run)
                else:
                    self._villain_turn(shell, st, run, i)
                self._save()
        except BaseException:
            self._save()                              # freeze mid-hand
            raise
        return self._settle(shell, st, run)

    def _hero_turn(self, shell, st, run):
        o = pe.owed(st, HERO)
        legal = pe.legal_actions(st, HERO)
        self._table(shell, st, note=run.get("talk", ""))
        ch = shell.get_key(accept="1234")
        kind = {"1": "fold", "2": "call", "3": "raise", "4": "allin"}[ch]
        if kind == "fold" and o == 0:
            kind = "check"                            # never fold for free
        if kind == "call" and o == 0:
            kind = "check"
        if kind == "raise" and "raise" not in legal:
            kind = "allin"

        # Grade BEFORE applying (uses the pre-action pot/price).
        street = st["street"]
        pot = pe.pot_size(st)
        eq = None
        if street > 0:
            eq = pe.equity(st["seats"][HERO]["hole"], st["board"],
                           len(pe.live(st)) - 1, iters=GRADE_ITERS)
        pos = pe.position_of(HERO, st["button"], 5)
        mark, ev, note = pe.grade_decision(
            kind, eq if eq is not None else 0.0,
            (o / (pot + o)) if o else 0.0, pot, o, street,
            hole=st["seats"][HERO]["hole"] if street == 0 else None,
            pos=pos, facing_raise=st["current_bet"] > pe.BB)
        run["grades"].append([street, kind, mark, ev, note])

        c = self.p["career"]
        c["decisions"] += 1
        if street == 0:
            c["preflop_seen"] += 1
            if kind in ("call", "raise", "allin"):
                c["vpip"] += 1
        if o > 0 and st["current_bet"] > pe.BB:
            c["raise_faced"] += 1
            if kind == "fold":
                c["raise_folded"] += 1
        if mark == "✗":
            c["errors"] += 1
            if kind == "fold":
                c["overfolds"] += 1
            elif kind == "call":
                c["overcalls"] += 1
            else:
                c["spew"] += 1
        elif mark == "±":
            c["questionable"] += 1

        pe.apply_action(st, HERO, kind)
        if mark != "✓":
            run["talk"] = f"{mark} {note}" + (f" ({ev:+}◆ EV)" if ev else "")
        else:
            run["talk"] = ""

    def _villain_turn(self, shell, st, run, i):
        pers = dict(VILLAINS)[st["seats"][i]["name"]]
        self._table(shell, st, note=run.get("talk", ""))
        shell.wait(0.4)
        legal = pe.legal_actions(st, i)
        kind, say = self._llm_decide(st, i, pers, legal)
        if kind is None:
            kind = pe.decide_bot(st, i, pers, hero_stats=self._hero_stats(),
                                 iters=BOT_ITERS)
            say = None
        if say:
            run["talk"] = f"{st['seats'][i]['name']}: “{say}”"
        pe.apply_action(st, i, kind)

    # -- settle + review ----------------------------------------------------------- #
    def _settle(self, shell, st, run):
        hero_won = st["winners"].get("You", 0)
        final = st["seats"][HERO]["stack"]
        net = final - run["stake"]
        self.p["bankroll"] += final
        self.p["hands"] += 1
        self.p["net"] += net
        if hero_won > self.p["biggest_pot"]:
            self.p["biggest_pot"] = hero_won
        self.p["run"] = None
        self._save()

        self._table(shell, st, reveal=True)
        h, _ = shell.scr.getmaxyx()
        who = " · ".join(f"{k} +{v}" for k, v in st["winners"].items())
        shell.put(7, 2, f"{'★' if net > 0 else '·'} {who}   (you {net:+}◆)",
                  curses.color_pair(2 if net > 0 else 5) | curses.A_BOLD)
        bad = [g for g in run["grades"] if g[2] != "✓"]
        line = " · ".join(f"{STREETS[g[0]]} {g[2]} {g[4]}" for g in bad[:2]) \
            or "clean hand — every decision ✓"
        shell.put(8, 2, ("review: " + line)[:76], curses.color_pair(4))
        leak = self._leak_line()
        if leak:
            shell.put(9, 2, leak[:76], curses.color_pair(5))
        coach = self._coach(st, run) if bad else None
        if coach:
            shell.put(9, 2, ("coach: " + coach)[:76], curses.color_pair(5))
        shell.draw_footer()
        shell.scr.refresh()
        shell.get_key(timeout=7.0, accept="1234 ")
        return True if net > 0 else (False if net < 0 else None)

    def _coach(self, st, run):
        worst = max((g for g in run["grades"] if g[2] == "✗"),
                    key=lambda g: abs(g[3]), default=None)
        if worst is None:
            return None
        hist = "; ".join(f"{STREETS[s]}: {n} {l}" for s, n, l in st["history"][-8:])
        return self._llm(
            f"One-sentence poker coaching. Hand history: {hist}. "
            f"Hero's flagged mistake on the {STREETS[worst[0]]}: {worst[4]}. "
            "Be specific and kind.", timeout=8)

    def _leak_line(self):
        c = self.p["career"]
        if c["raise_faced"] >= 8 and c["raise_folded"] / c["raise_faced"] > 0.6:
            return (f"leak: you fold to raises "
                    f"{c['raise_folded'] * 100 // c['raise_faced']}% — "
                    "the Shark has noticed")
        if c["decisions"] >= 20 and c["overcalls"] > c["overfolds"] + 2:
            return "leak: calling without the odds is your main EV drain"
        if c["decisions"] >= 20 and c["overfolds"] > c["overcalls"] + 2:
            return "leak: you fold too many hands with the right price"
        if c["preflop_seen"] >= 15 and c["vpip"] / c["preflop_seen"] > 0.45:
            return (f"leak: playing {c['vpip'] * 100 // c['preflop_seen']}% "
                    "of hands preflop — tighten up")
        return ""
