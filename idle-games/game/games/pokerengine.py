"""Pure no-limit hold'em engine — no curses, fully serializable, unit-tested.

Cards are ints 0..51: rank = c // 4 + 2 (2..14), suit = c % 4.
The hand state is a plain dict so a hand can freeze mid-street to JSON and
resume in another process.

Betting is abstracted to the game's four keys: fold / check-call /
raise (~2/3 pot) / all-in. Side pots are computed layer-by-layer, so
multiway all-ins pay out correctly.
"""

import itertools
import random
from collections import Counter

RANKS = "23456789TJQKA"
SUITS = "♠♥♦♣"
SB, BB = 5, 10


def card_str(c):
    return f"{RANKS[c // 4]}{SUITS[c % 4]}"


def rank5(cards):
    """Rank a 5-card hand -> comparable tuple (category, tiebreakers...)."""
    rs = sorted((c // 4 + 2 for c in cards), reverse=True)
    suits = {c % 4 for c in cards}
    counts = Counter(rs)
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    flush = len(suits) == 1
    uniq = sorted(set(rs), reverse=True)
    straight_hi = 0
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_hi = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:      # the wheel
            straight_hi = 5
    if flush and straight_hi:
        return (8, straight_hi)
    if groups[0][1] == 4:
        return (7, groups[0][0], groups[1][0])
    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, groups[0][0], groups[1][0])
    if flush:
        return (5, *rs)
    if straight_hi:
        return (4, straight_hi)
    if groups[0][1] == 3:
        kick = [r for r in rs if r != groups[0][0]]
        return (3, groups[0][0], *kick)
    if groups[0][1] == 2 and groups[1][1] == 2:
        kick = [r for r in rs if r != groups[0][0] and r != groups[1][0]]
        return (2, groups[0][0], groups[1][0], *kick)
    if groups[0][1] == 2:
        kick = [r for r in rs if r != groups[0][0]]
        return (1, groups[0][0], *kick)
    return (0, *rs)


def rank7(cards7):
    return max(rank5(c) for c in itertools.combinations(cards7, 5))


def equity(hole, board, n_opp, iters=200, rng=None):
    """Monte-Carlo equity of `hole` vs n_opp random hands. Ties count as
    split shares, so the result is directly comparable to pot odds."""
    rng = rng or random
    dead = set(hole) | set(board)
    deck = [c for c in range(52) if c not in dead]
    need = 5 - len(board)
    share = 0.0
    for _ in range(iters):
        draw = rng.sample(deck, need + 2 * n_opp)
        full_board = list(board) + draw[:need]
        mine = rank7(tuple(hole) + tuple(full_board))
        best_opp, opp_ties = None, 0
        for i in range(n_opp):
            o = rank7(tuple(draw[need + 2 * i:need + 2 * i + 2]) + tuple(full_board))
            if best_opp is None or o > best_opp:
                best_opp, opp_ties = o, 1
            elif o == best_opp:
                opp_ties += 1
        if best_opp is None or mine > best_opp:
            share += 1.0
        elif mine == best_opp:
            share += 1.0 / (1 + opp_ties)
    return share / iters


# --------------------------------------------------------------------------- #
# Preflop charts (coarse 5-max guide — for bot ranges and hero grading)         #
# --------------------------------------------------------------------------- #

def hand_class(hole):
    """(c1, c2) -> 'AKs' / 'T9o' / 'QQ' notation."""
    r1, r2 = sorted((hole[0] // 4 + 2, hole[1] // 4 + 2), reverse=True)
    if r1 == r2:
        return RANKS[r1 - 2] * 2
    suited = "s" if hole[0] % 4 == hole[1] % 4 else "o"
    return f"{RANKS[r1 - 2]}{RANKS[r2 - 2]}{suited}"


def _expand(token):
    """'ATs+' -> {ATs, AJs, AQs, AKs}; '66+' -> pairs 66..AA; plain passes."""
    out = set()
    plus = token.endswith("+")
    tok = token[:-1] if plus else token
    if len(tok) == 2 and tok[0] == tok[1]:                     # pair
        lo = RANKS.index(tok[0])
        tops = range(lo, 13) if plus else [lo]
        for i in tops:
            out.add(RANKS[i] * 2)
        return out
    hi, lo, suf = tok[0], tok[1], tok[2]
    hi_i, lo_i = RANKS.index(hi), RANKS.index(lo)
    tops = range(lo_i, hi_i) if plus else [lo_i]
    for i in tops:
        out.add(f"{hi}{RANKS[i]}{suf}")
    return out


def parse_range(spec):
    out = set()
    for token in spec.split():
        out |= _expand(token)
    return out


POSITIONS = ["UTG", "CO", "BTN", "SB", "BB"]
OPEN_RANGES = {
    "UTG": parse_range("66+ ATs+ KQs QJs AJo+ KQo"),
    "CO":  parse_range("44+ A8s+ KTs+ QTs+ JTs T9s ATo+ KJo+ QJo"),
    "BTN": parse_range("22+ A2s+ K8s+ Q9s+ J9s+ T8s+ 98s 87s 76s A7o+ K9o+ Q9o+ J9o+ T9o"),
    "SB":  parse_range("22+ A2s+ K9s+ Q9s+ J9s+ T9s 98s 87s A8o+ KTo+ QTo+ JTo"),
    "BB":  set(),   # BB defends rather than opens
}
PREMIUM = parse_range("TT+ AQs+ AKo")
SPECULATIVE = parse_range("22+ A2s+ 54s 65s 76s 87s 98s T9s JTs QJs KQo")


def position_of(seat, button, n):
    order = ["BTN", "SB", "BB", "UTG", "CO"]      # clockwise from button, 5-max
    return order[(seat - button) % n]


# --------------------------------------------------------------------------- #
# Hand state machine                                                            #
# --------------------------------------------------------------------------- #

def new_hand(names, stacks, button, rng=None):
    rng = rng or random
    n = len(names)
    deck = list(range(52))
    rng.shuffle(deck)
    seats = []
    for i in range(n):
        seats.append({"name": names[i], "stack": stacks[i],
                      "hole": [deck.pop(), deck.pop()],
                      "in": True, "allin": False, "contrib": 0, "cr": 0})
    st = {"seats": seats, "button": button, "deck": deck, "board": [],
          "street": 0, "current_bet": 0, "min_raise": BB,
          "pending": [], "history": [], "winners": None, "done": False}
    _post(st, (button + 1) % n, SB)
    _post(st, (button + 2) % n, BB)
    st["current_bet"] = BB
    st["pending"] = _order(st, (button + 3) % n)
    return st


def _post(st, i, amount):
    s = st["seats"][i]
    pay = min(amount, s["stack"])
    s["stack"] -= pay
    s["contrib"] += pay
    s["cr"] += pay
    if s["stack"] == 0:
        s["allin"] = True


def _order(st, start):
    n = len(st["seats"])
    return [i for i in (( start + k) % n for k in range(n))
            if st["seats"][i]["in"] and not st["seats"][i]["allin"]]


def pot_size(st):
    return sum(s["contrib"] for s in st["seats"])


def live(st):
    return [i for i, s in enumerate(st["seats"]) if s["in"]]


def to_act(st):
    return st["pending"][0] if st["pending"] else None


def owed(st, i):
    return st["current_bet"] - st["seats"][i]["cr"]


def legal_actions(st, i):
    acts = []
    o = owed(st, i)
    s = st["seats"][i]
    acts.append("fold" if o > 0 else "check")
    if o > 0:
        acts.append("call")
    if s["stack"] > o:
        acts.append("raise")
    acts.append("allin")
    return acts


def raise_target(st, i):
    """Abstracted sizing: raise to current_bet + max(min_raise, 2/3 pot)."""
    bump = max(st["min_raise"], (pot_size(st) + owed(st, i)) * 2 // 3)
    return st["current_bet"] + bump


def apply_action(st, i, kind):
    assert not st["done"] and to_act(st) == i, "not this seat's turn"
    s = st["seats"][i]
    o = owed(st, i)
    st["pending"].pop(0)
    label = kind
    if kind == "fold":
        s["in"] = False
    elif kind in ("check", "call"):
        pay = min(o, s["stack"])
        _pay(s, pay)
        label = "check" if o == 0 else f"call {pay}"
    elif kind in ("raise", "allin"):
        target = raise_target(st, i) if kind == "raise" else s["cr"] + s["stack"]
        target = min(target, s["cr"] + s["stack"])
        pay = min(target - s["cr"], s["stack"])
        _pay(s, pay)
        if s["cr"] > st["current_bet"]:
            st["min_raise"] = max(st["min_raise"], s["cr"] - st["current_bet"])
            st["current_bet"] = s["cr"]
            st["pending"] = [j for j in _order(st, (i + 1) % len(st["seats"]))
                             if j != i]
        label = f"{'all-in' if s['allin'] else 'raise'} {s['cr']}"
    st["history"].append([st["street"], st["seats"][i]["name"], label])

    if len(live(st)) == 1:
        _award(st)
        return
    if not st["pending"]:
        _next_street(st)


def _pay(s, pay):
    s["stack"] -= pay
    s["contrib"] += pay
    s["cr"] += pay
    if s["stack"] == 0:
        s["allin"] = True


def _next_street(st):
    while True:
        st["street"] += 1
        if st["street"] > 3:
            _award(st)
            return
        for s in st["seats"]:
            s["cr"] = 0
        st["current_bet"] = 0
        st["min_raise"] = BB
        draws = {1: 3, 2: 1, 3: 1}[st["street"]]
        for _ in range(draws):
            st["board"].append(st["deck"].pop())
        st["pending"] = _order(st, (st["button"] + 1) % len(st["seats"]))
        # Everyone all-in (or all but one): run the remaining streets out.
        if len(st["pending"]) > 1:
            return


def _award(st):
    """Layered side pots; folded money is dead and pays into each layer."""
    seats = st["seats"]
    showdown = len(live(st)) > 1
    ranks = {}
    if showdown:
        for i in live(st):
            ranks[i] = rank7(tuple(seats[i]["hole"]) + tuple(st["board"]))
    levels = sorted({s["contrib"] for s in seats if s["contrib"] > 0})
    prev = 0
    won = Counter()
    for level in levels:
        layer = sum(min(s["contrib"], level) - min(prev, s["contrib"])
                    for s in seats)
        eligible = [i for i in live(st) if seats[i]["contrib"] >= level]
        if not eligible:
            prev = level
            continue
        if showdown:
            best = max(ranks[i] for i in eligible)
            winners = [i for i in eligible if ranks[i] == best]
        else:
            winners = eligible
        share, odd = divmod(layer, len(winners))
        for k, w in enumerate(sorted(winners)):
            won[w] += share + (1 if k < odd else 0)
        prev = level
    for i, amount in won.items():
        seats[i]["stack"] += amount
    st["winners"] = {seats[i]["name"]: amount for i, amount in won.items()}
    st["showdown"] = showdown
    st["done"] = True


# --------------------------------------------------------------------------- #
# Bot brains                                                                    #
# --------------------------------------------------------------------------- #

PERSONALITIES = {
    # call_pad: extra equity beyond pot odds needed to call
    # raise_eq: equity to value-raise; bluff: base bluff frequency
    "rock":      {"call_pad": 0.08, "raise_eq": 0.66, "bluff": 0.03},
    "fish":      {"call_pad": -0.10, "raise_eq": 0.72, "bluff": 0.05},
    "shark":     {"call_pad": 0.02, "raise_eq": 0.56, "bluff": 0.12},
    "professor": {"call_pad": 0.01, "raise_eq": 0.62, "bluff": 0.07},
}


def preflop_tier(hole, pos):
    hc = hand_class(hole)
    if hc in PREMIUM:
        return 3
    if hc in OPEN_RANGES.get(pos, set()):
        return 2
    if hc in SPECULATIVE:
        return 1
    return 0


def decide_bot(st, i, pers, hero_stats=None, rng=None, iters=80):
    """Heuristic decision -> one of legal_actions(st, i)."""
    rng = rng or random
    p = PERSONALITIES[pers]
    s = st["seats"][i]
    o = owed(st, i)
    pos = position_of(i, st["button"], len(st["seats"]))
    legal = legal_actions(st, i)

    if st["street"] == 0:
        tier = preflop_tier(s["hole"], pos if pos != "BB" else "BTN")
        facing_raise = st["current_bet"] > BB
        if tier == 3:
            return "raise" if "raise" in legal else "allin"
        if tier == 2:
            if not facing_raise:
                return "raise" if rng.random() < 0.6 and "raise" in legal else \
                    ("call" if o else "check")
            return "call" if o <= s["stack"] // 8 else "fold"
        if tier == 1:
            fishy = pers == "fish" or rng.random() < 0.25
            if o == 0:
                return "check"
            return "call" if (fishy and o <= s["stack"] // 10) else "fold"
        if pers == "fish" and o <= BB and rng.random() < 0.5:
            return "call"
        return "check" if o == 0 else "fold"

    n_opp = len(live(st)) - 1
    eq = equity(s["hole"], st["board"], n_opp, iters=iters, rng=rng)
    price = o / (pot_size(st) + o) if o else 0.0

    bluff = p["bluff"]
    if pers == "shark" and hero_stats:
        f2r = hero_stats.get("fold_to_raise_rate", 0)
        if f2r > 0.55 and n_opp <= 2:
            bluff += 0.15
    if eq >= p["raise_eq"] and "raise" in legal:
        return "raise"
    if o == 0:
        if rng.random() < bluff and "raise" in legal:
            return "raise"
        return "check"
    if eq >= price + p["call_pad"]:
        return "call"
    if rng.random() < bluff / 2 and "raise" in legal and n_opp == 1:
        return "raise"
    return "fold"


# --------------------------------------------------------------------------- #
# Hero decision grading                                                         #
# --------------------------------------------------------------------------- #

def grade_decision(kind, eq, price, pot, o, street, hole=None, pos=None,
                   facing_raise=False):
    """-> (mark, ev_delta, note). mark: '✓' fine, '±' questionable, '✗' error.
    Postflop grading is pot-odds/equity discipline; preflop is chart-based."""
    if street == 0 and hole is not None and pos:
        hc = hand_class(hole)
        in_open = hc in OPEN_RANGES.get(pos, OPEN_RANGES["BTN"]) or hc in PREMIUM
        if not facing_raise:
            if kind == "fold" and in_open and o <= BB:
                return "±", 0, f"{hc} is a standard open from {pos}"
            if kind in ("raise", "call") and not in_open and hc not in SPECULATIVE:
                return "±", 0, f"{hc} is below the {pos} chart"
            return "✓", 0, ""
        if kind in ("call", "raise", "allin") and hc not in PREMIUM \
                and hc not in OPEN_RANGES.get(pos, set()):
            return "±", 0, f"{hc} vs a raise is thin"
        return "✓", 0, ""

    ev_call = eq * (pot + o) - o
    if kind == "fold":
        if eq > price + 0.06:
            return "✗", round(ev_call), f"fold with {eq:.0%} vs {price:.0%} price"
        return "✓", 0, ""
    if kind == "call":
        if eq < price - 0.06:
            return "✗", round(ev_call), f"call needs {price:.0%}, had {eq:.0%}"
        return "✓", 0, ""
    if kind == "check":
        if eq > 0.8:
            return "±", 0, f"{eq:.0%} equity — bet for value"
        return "✓", 0, ""
    # raise / allin: only clear extremes get scored
    if eq < price - 0.12:
        return "✗", round(ev_call), f"raising with {eq:.0%} is spew"
    if eq > 0.75:
        return "✓", 0, "value"
    return "±", 0, "semi-bluff region (unscored)"
