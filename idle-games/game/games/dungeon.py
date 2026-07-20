"""Dungeon roguelite: one room per wait-state, a hero that persists forever.

Progress lives in state_dir/dungeon.json (per player, not per session):
  run  — the live descent: floor, hp, damage, gold, xp, potions, and the
         mid-room state (a fight freezes when Claude finishes and resumes
         exactly there on your next prompt).
  meta — permanent: shards (banked on death: gold/10 + floor), guild
         upgrades bought with them, and lifetime records.

Round semantics for the shell: room survived = correct, died = wrong,
left/browsed = skipped (no score).
"""

import curses
import fcntl
import json
import os
import random

BASE_HP = 16
BASE_DMG = (3, 5)
XP_PER_LEVEL = 12

MONSTERS = [
    # (name, hp, dmg, min floor)
    ("off-by-one imp", 7, 2, 1),
    ("race-condition rat", 9, 3, 1),
    ("cache goblin", 12, 3, 2),
    ("zombie process", 14, 4, 3),
    ("memory leech", 16, 4, 5),
    ("null wraith", 20, 5, 7),
    ("spaghetti golem", 26, 6, 10),
    ("deadlock basilisk", 32, 7, 14),
    ("segfault serpent", 40, 8, 18),
]
BOSSES = [
    ("merge dragon", 30, 6),
    ("the legacy monolith", 46, 8),
    ("kernel panic, incarnate", 64, 10),
]

GUILD_UPGRADES = {
    # key: (label, base cost, max rank, apply description)
    "vitality": ("Vitality (+3 start HP)", 12, 5),
    "whetstone": ("Whetstone (+1 damage)", 20, 3),
    "satchel": ("Satchel (start with a potion)", 8, 2),
    "maprooms": ("Old maps (start 2 floors deeper)", 25, 3),
}


# --------------------------------------------------------------------------- #
# Profile persistence (flock-guarded, same pattern as stats.json)              #
# --------------------------------------------------------------------------- #

def _default_profile():
    return {
        "version": 1,
        "meta": {"shards": 0, "upgrades": {}, "deepest_floor": 0,
                 "kills": 0, "runs": 0},
        "run": None,
    }


class Profile:
    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "dungeon.json")
        self.data = _default_profile()
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and "meta" in loaded:
                base = _default_profile()
                base["meta"].update(loaded.get("meta") or {})
                base["run"] = loaded.get("run")
                self.data = base
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            with open(self.path, "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.seek(0)
                f.truncate()
                f.write(json.dumps(self.data, indent=2))
                f.flush()
                os.fsync(f.fileno())
                fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            pass


def new_run(meta):
    up = meta.get("upgrades", {})
    hp = BASE_HP + 3 * up.get("vitality", 0)
    dmg_bonus = up.get("whetstone", 0)
    return {
        "floor": 1 + 2 * up.get("maprooms", 0),
        "hp": hp, "max_hp": hp,
        "dmg": [BASE_DMG[0] + dmg_bonus, BASE_DMG[1] + dmg_bonus],
        "gold": 0, "xp": 0, "level": 1,
        "potions": up.get("satchel", 0),
        "blessed": 0,
        "room": None,
    }


# --------------------------------------------------------------------------- #
# The game                                                                     #
# --------------------------------------------------------------------------- #

class DungeonGame:
    name = "dungeon"
    title = "DUNGEON"
    keys_help = "1-4 act"

    def __init__(self, cfg):
        self.cfg = cfg
        self.profile = None   # bound lazily; needs shell.state_dir

    def playable(self):
        return True

    # -- plumbing ----------------------------------------------------------- #
    def _bind(self, shell):
        if self.profile is None:
            self.profile = Profile(shell.state_dir)
        if self.profile.data["run"] is None:
            self.profile.data["run"] = new_run(self.profile.data["meta"])
            self.profile.data["meta"]["runs"] += 1
            self.profile.save()

    def _status(self, shell):
        run = self.profile.data["run"]
        bar_w = 10
        filled = max(0, min(bar_w, round(bar_w * run["hp"] / run["max_hp"])))
        hp_bar = "█" * filled + "░" * (bar_w - filled)
        shell.put(2, 2,
                  f"floor {run['floor']} · HP {hp_bar} {run['hp']}/{run['max_hp']}"
                  f" · dmg {run['dmg'][0]}-{run['dmg'][1]}"
                  f" · gold {run['gold']} · potion x{run['potions']}",
                  curses.color_pair(5))

    def _frame(self, shell, *lines):
        shell.frame()
        self._status(shell)
        for i, (text, attr) in enumerate(lines):
            shell.put(4 + i, 2, text, attr)
        shell.scr.refresh()

    def _roll_dmg(self, run):
        return random.randint(run["dmg"][0], run["dmg"][1]) + run.get("blessed", 0)

    # -- rooms -------------------------------------------------------------- #
    def _new_room(self, floor):
        if floor % 5 == 0:
            b = BOSSES[min(len(BOSSES) - 1, floor // 10)]
            hp = b[1] + 2 * floor
            return {"kind": "fight", "boss": True, "name": b[0], "hp": hp,
                    "max_hp": hp, "dmg": b[2] + floor // 4,
                    "telegraph": "heavy", "beat": 0}
        kind = random.choices(
            ["fight", "chest", "trap", "shrine", "merchant"],
            weights=[58, 15, 10, 8, 9])[0]
        if kind == "fight":
            pool = [m for m in MONSTERS if m[3] <= floor] or MONSTERS[:2]
            name, hp, dmg, _ = random.choice(pool[-4:])
            hp += floor // 2
            dmg += floor // 5
            return {"kind": "fight", "boss": False, "name": name, "hp": hp,
                    "max_hp": hp, "dmg": dmg,
                    "telegraph": random.choice(["light", "heavy"]), "beat": 0}
        return {"kind": kind}

    def play_round(self, shell):
        self._bind(shell)
        run = self.profile.data["run"]
        if run["room"] is None:
            run["room"] = self._new_room(run["floor"])
            self.profile.save()

        kind = run["room"]["kind"]
        handler = {"fight": self._fight, "chest": self._chest,
                   "trap": self._trap, "shrine": self._shrine,
                   "merchant": self._merchant}[kind]
        result = handler(shell, run)

        if result is not False:          # survived (or browsed): next floor
            if run["room"] is None:      # room actually resolved
                run["floor"] += 1
                meta = self.profile.data["meta"]
                meta["deepest_floor"] = max(meta["deepest_floor"], run["floor"])
        self.profile.save()
        return result

    # -- fight -------------------------------------------------------------- #
    def _fight(self, shell, run):
        room = run["room"]
        banner = ("⚔ BOSS: " if room["boss"] else "") + room["name"]
        flavor = f"A wild {room['name']} blocks the path!" if not room["boss"] \
            else f"{room['name']} awakens…"

        while True:
            tele = ("winds up a BIG hit!" if room["telegraph"] == "heavy"
                    else "circles for a quick jab.")
            e_w = 10
            e_fill = max(0, min(e_w, round(e_w * room["hp"] / room["max_hp"])))
            self._frame(
                shell,
                (flavor, curses.A_BOLD),
                (f"{banner}  HP {'█' * e_fill}{'░' * (e_w - e_fill)}  — it {tele}",
                 curses.color_pair(3 if room["telegraph"] == "heavy" else 5)),
                ("1 strike   2 block   3 potion   4 flee (lose half gold)", 0),
            )
            self.profile.save()          # freeze point: resume exactly here
            ch = shell.get_key(accept="1234")

            e_dmg = room["dmg"] * (2 if room["telegraph"] == "heavy" else 1)
            log = []
            if ch == "1":
                hit = self._roll_dmg(run)
                room["hp"] -= hit
                log.append(f"You strike for {hit}.")
                if room["hp"] <= 0:
                    return self._fight_won(shell, run, room)
                run["hp"] -= e_dmg
                log.append(f"It hits for {e_dmg}." if room["telegraph"] == "light"
                           else f"The BIG hit lands for {e_dmg}!")
            elif ch == "2":
                # A clean block negates a jab; a braced BIG hit still leaks ~30%.
                taken = 0 if room["telegraph"] == "light" \
                    else max(1, round(e_dmg * 0.3))
                run["hp"] -= taken
                log.append("You block the jab clean." if taken == 0
                           else f"You brace — the BIG hit still does {taken}.")
            elif ch == "3":
                if run["potions"] > 0:
                    run["potions"] -= 1
                    heal = 8 + run["level"]
                    run["hp"] = min(run["max_hp"], run["hp"] + heal)
                    log.append(f"Coffee potion: +{heal} HP. The enemy waits, politely.")
                else:
                    log.append("No potions left! You fumble; it attacks.")
                    run["hp"] -= e_dmg
            elif ch == "4":
                if room["boss"]:
                    log.append("No fleeing a boss!")
                else:
                    run["gold"] //= 2
                    run["room"] = None
                    self.profile.save()
                    self._frame(shell, ("You slip away down the stairs…", 0))
                    shell.show_feedback(True, verdict="↓ Fled.",
                                        detail="Half your gold paid the toll.")
                    return None

            if run["hp"] <= 0:
                return self._death(shell, run, room["name"])

            room["telegraph"] = random.choices(
                ["light", "heavy"], weights=[65, 35])[0]
            room["beat"] += 1
            self.profile.save()
            if log:
                self._frame(shell, (flavor, curses.A_BOLD),
                            ("  ".join(log), 0))
                shell.wait(0.9)

    def _fight_won(self, shell, run, room):
        gold = random.randint(3, 8) + run["floor"] + (15 if room["boss"] else 0)
        xp = 4 + run["floor"] // 2 + (8 if room["boss"] else 0)
        run["gold"] += gold
        run["xp"] += xp
        self.profile.data["meta"]["kills"] += 1
        run["room"] = None
        leveled = self._maybe_level_up(shell, run)
        self.profile.save()
        self._frame(shell, (f"The {room['name']} is defeated!", curses.A_BOLD))
        shell.show_feedback(True, verdict=f"✔ +{gold} gold, +{xp} xp."
                            + (f"  LEVEL {run['level']}!" if leveled else ""),
                            detail=f"Floor {run['floor'] + 1} awaits.")
        return True

    def _maybe_level_up(self, shell, run):
        need = XP_PER_LEVEL + 4 * run["level"]
        if run["xp"] < need:
            return False
        # Prompt BEFORE spending the xp: a stop mid-choice then costs nothing
        # and the level-up re-offers on the next victory.
        self._frame(shell, (f"LEVEL UP → {run['level'] + 1}!  Choose:",
                            curses.A_BOLD),
                    ("1 +4 max HP   2 +1 damage   3 full heal", 0))
        ch = shell.get_key(accept="123")
        run["xp"] -= need
        run["level"] += 1
        if ch == "1":
            run["max_hp"] += 4
            run["hp"] += 4
        elif ch == "2":
            run["dmg"] = [run["dmg"][0] + 1, run["dmg"][1] + 1]
        else:
            run["hp"] = run["max_hp"]
        return True

    def _death(self, shell, run, killer):
        meta = self.profile.data["meta"]
        shards = run["gold"] // 10 + run["floor"]
        meta["shards"] += shards
        meta["deepest_floor"] = max(meta["deepest_floor"], run["floor"])
        # The next run starts at the guild so the banked shards are spendable
        # right away (as the death screen promises).
        reborn = new_run(meta)
        reborn["room"] = {"kind": "merchant"}
        self.profile.data["run"] = reborn
        meta["runs"] += 1
        self.profile.save()
        self._frame(shell,
                    (f"☠ Slain by the {killer} on floor {run['floor']}.",
                     curses.A_BOLD),
                    (f"+{shards} shards banked (guild total: {meta['shards']}).", 0),
                    (f"Deepest ever: floor {meta['deepest_floor']}.", 0))
        shell.show_feedback(False, verdict="☠ The run ends…",
                            detail="Next room: the guild — spend your shards.")
        return False

    # -- non-fight rooms ------------------------------------------------------ #
    def _chest(self, shell, run):
        self._frame(shell, ("A dusty chest sits in the corner.", curses.A_BOLD),
                    ("1 open it   2 leave it", 0))
        self.profile.save()
        ch = shell.get_key(accept="12")
        run["room"] = None
        if ch == "2":
            shell.show_feedback(True, verdict="You walk on.", detail="")
            return None
        roll = random.random()
        if roll < 0.15:
            hit = 3 + run["floor"] // 3
            run["hp"] -= hit
            if run["hp"] <= 0:
                return self._death(shell, run, "mimic")
            self._frame(shell, ("It's a MIMIC! It bites and flees.", 0))
            shell.show_feedback(False, verdict=f"✗ -{hit} HP.", detail="")
            return False
        if roll < 0.45:
            run["potions"] += 1
            shell.show_feedback(True, verdict="✔ A coffee potion!", detail="")
        else:
            gold = random.randint(6, 14) + run["floor"]
            run["gold"] += gold
            shell.show_feedback(True, verdict=f"✔ +{gold} gold.", detail="")
        return True

    def _trap(self, shell, run):
        key = random.choice("1234")
        self._frame(shell, ("Click. A tripwire! DODGE —", curses.A_BOLD),
                    (f"press {key} NOW!", curses.color_pair(4) | curses.A_BOLD))
        self.profile.save()
        hit_key = shell.poll_key(1.3, accept="1234")
        run["room"] = None
        if hit_key == key:
            shell.show_feedback(True, verdict="✔ You dive clear!", detail="")
            return True
        dmg = 3 + run["floor"] // 2
        run["hp"] -= dmg
        if run["hp"] <= 0:
            return self._death(shell, run, "dart trap")
        shell.show_feedback(False, verdict=f"✗ Darts! -{dmg} HP.",
                            detail="(wrong key or too slow)")
        return False

    def _shrine(self, shell, run):
        self._frame(shell, ("A flickering shrine hums with static.", curses.A_BOLD),
                    ("1 pray (risky)   2 leave", 0))
        self.profile.save()
        ch = shell.get_key(accept="12")
        run["room"] = None
        if ch == "2":
            shell.show_feedback(True, verdict="You keep walking.", detail="")
            return None
        if random.random() < 0.6:
            run["blessed"] = run.get("blessed", 0) + 1
            shell.show_feedback(True, verdict="✔ Blessed: +1 damage this run.",
                                detail="")
            return True
        hit = 4
        run["hp"] = max(1, run["hp"] - hit)
        shell.show_feedback(False, verdict=f"✗ The static bites: -{hit} HP.",
                            detail="")
        return False

    def _merchant(self, shell, run):
        meta = self.profile.data["meta"]
        bought_any = False
        while True:
            self._frame(
                shell,
                (f"The guild merchant. gold {run['gold']} · shards {meta['shards']}◆",
                 curses.A_BOLD),
                ("1 potion 20g   2 +2 max HP 35g   3 guild upgrades   4 leave", 0),
            )
            self.profile.save()
            ch = shell.get_key(accept="1234")
            if ch == "1" and run["gold"] >= 20:
                run["gold"] -= 20
                run["potions"] += 1
                bought_any = True
            elif ch == "2" and run["gold"] >= 35:
                run["gold"] -= 35
                run["max_hp"] += 2
                run["hp"] += 2
                bought_any = True
            elif ch == "3":
                bought_any = self._guild_shop(shell, run, meta) or bought_any
            elif ch == "4":
                break
        run["room"] = None
        self.profile.save()
        shell.show_feedback(True, verdict="The merchant nods.",
                            detail="Deeper we go.")
        return True if bought_any else None

    def _guild_shop(self, shell, run, meta):
        keys = list(GUILD_UPGRADES)
        bought = False
        while True:
            up = meta["upgrades"]
            lines = []
            for i, key in enumerate(keys):
                label, cost, cap = GUILD_UPGRADES[key]
                rank = up.get(key, 0)
                price = cost * (rank + 1)
                tag = "MAX" if rank >= cap else f"{price}◆"
                lines.append((f"{i + 1} {label}  [rank {rank}]  {tag}", 0))
            self._frame(shell, (f"Guild upgrades — shards {meta['shards']}◆ "
                                "(permanent, apply next run; 5 = back)",
                                curses.A_BOLD), *lines)
            ch = shell.get_key(accept="12345")
            if ch == "5":
                return bought
            key = keys[int(ch) - 1]
            label, cost, cap = GUILD_UPGRADES[key]
            rank = up.get(key, 0)
            price = cost * (rank + 1)
            if rank < cap and meta["shards"] >= price:
                meta["shards"] -= price
                up[key] = rank + 1
                bought = True
                self.profile.save()
