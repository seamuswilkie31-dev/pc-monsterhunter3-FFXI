"""FFXI's rules over Monster Hunter's fight: the layer's engine, with no I/O (easy to test).

Each frame the app hands tick() what it read (the monster's HP and identity, the hunter's HP) and writes
back what tick() returns. Everything FFXI adds works through those two HP values and the layer's own
MP and TP: see ffxi_data's header for what each effect does, and which are stand-ins.

    e = Engine("RDM", "sword_and_shield", sub="WHM", levels={"RDM": 30, "WHM": 15})
    writes = e.tick(now, monster_hp, player_hp, max_hp, monster_id, monster_max)
    writes.update(e.use("Fast Blade", now, monster_hp, player_hp))
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

import ffxi_data as D

TICK = 3.0                       # regen, refresh, damage over time, pets: every 3 s, as FFXI
COUNTED = {"shadows", "stoneskin", "aquaveil"}   # effects whose power is a count or a pool, used up
ROLL_WINDOW = 45.0               # Double-Up works this long after a roll


def interp_ftp(ftp: tuple, tp: float) -> float:
    lo, mid, hi = ftp
    if tp <= 1000:
        return lo
    if tp <= 2000:
        return lo + (mid - lo) * (tp - 1000) / 1000
    return mid + (hi - mid) * (min(tp, 3000) - 2000) / 1000


def resolve_skillchain(opener: list[str], closer: list[str]) -> str | None:
    best = None
    for c in closer:
        for o in opener:
            r = D.SKILLCHAINS.get((o, c))
            if r and (best is None or D.SC_LEVEL[r] > D.SC_LEVEL[best]):
                best = r
    return best


# Vana'diel's days, in order, and their elements; each element beats the next in FFXI's wheel (fire ice wind
# earth thunder water, round; light and dark each other)
DAYS = (("Firesday", "fire"), ("Earthsday", "earth"), ("Watersday", "water"), ("Windsday", "wind"),
        ("Iceday", "ice"), ("Lightningday", "thunder"), ("Lightsday", "light"), ("Darksday", "dark"))
BEATS = {"fire": "ice", "ice": "wind", "wind": "earth", "earth": "thunder", "thunder": "water", "water": "fire",
         "light": "dark", "dark": "light"}


def vana_time(unix: float) -> dict:
    """Vana'diel's time now: 25 times the real, its year 898 at 2002-01-01 00:00 UTC (FFXI's clocks'
    reckoning); the day (and its element), the hour, the minute."""
    ms = (898 * 360 + 30) * 86400000 + (unix - 1009843200) * 1000 * 25
    day = int(ms // 86400000) % 8
    minutes = int(ms // 60000) % (24 * 60)
    return {"day": DAYS[day][0], "element": DAYS[day][1], "hour": minutes // 60, "minute": minutes % 60}


def day_bonus(day_element: str, element: str) -> float:
    """The day's sway on magic of an element: its own a tenth stronger, the one it beats a tenth weaker."""
    if not day_element or not element:
        return 1.0
    return 1.1 if element == day_element else 0.9 if BEATS.get(day_element) == element else 1.0


def spell_family(name: str) -> str:
    """A spell's family, one of which is on at a time: its name without its grade, the -ra (party) forms as
    their own (Protect for Protect III and Protectra II; Dia for Dia II)."""
    words = name.split()
    while len(words) > 1 and words[-1] in ("II", "III", "IV", "V", "VI"):
        words.pop()
    base = " ".join(words)
    return {"Protectra": "Protect", "Shellra": "Shell", "Curaga": "Cure"}.get(base, base)


@dataclass
class Engine:
    job: str
    weapon: str
    player_max_hp: int = 150
    allow_kills: bool = False
    me: str = "You"
    target: str = "target"
    sub: str | None = None
    levels: dict = field(default_factory=dict)      # job -> level (1 if absent)
    exp: dict = field(default_factory=dict)         # job -> EXP into its level
    exp_rate: float = D.EXP_RATE
    rng: random.Random = field(default_factory=random.Random)

    # --- the fight's state ---
    tp: float = 0.0
    mp: float = 0.0
    mh_kind: str = ""                                # Monster Hunter's weapon in hand, its kind (the layer's; "": weapon's)
    mh_rate: dict = field(default_factory=dict)      # a kind -> [damage, seconds] of your blows with it (avg_hit)
    blow_at: float = -99.0                           # your last blow's time
    last_ws_time: float = -99.0
    last_ws_props: list = field(default_factory=list)
    mb_until: float = -99.0
    mb_elements: set = field(default_factory=set)
    sc_name: str = ""
    casting: str | None = None
    cast_done: float = 0.0
    cast_total: float = 0.0
    recasts: dict = field(default_factory=dict)      # action -> when it is ready again
    events: list = field(default_factory=list)       # ("cast" | "spell" | "interrupted" | "level_up" | "level_down",
                                                     # name): for sounds, effects
    buffs: dict = field(default_factory=dict)        # (source, effect) -> {"power", "until"}
    debuffs: dict = field(default_factory=dict)      # on the monster: (source, effect) -> {"power", "until"}
    pet: dict | None = None                          # {"name", "power", "until", "upkeep"}
    next_ws: float = 0.0                             # Sneak Attack and the like: the next WS's bonus
    roll: dict | None = None                         # the last Phantom Roll, for Double-Up
    next_tick: float = 0.0
    monster_id: int | None = None
    monster_max: int = 0
    dot_carry: float = 0.0                           # damage over time not yet whole, to the next tick
    ffxi_owed: int = 0                               # FFXI's damage handed to the game, not yet off the monster's HP
    owed_at: float = 0.0                             # when it was last handed over (not taken in 3 s: let go)
    voice: int = 0                                   # /voice: the character's voice set (se2NN; 0: by race and face)
    day_element: str = ""                            # Vana'diel's day's element (the layer's): its magic stronger,
                                                     # the one it beats weaker
    ffxi_combat: bool = True                         # FFXI's combat (auto-attack, the d-pad's targets, carving), or
                                                     # Monster Hunter's (its buttons, its d-pad)
    checked: bool = False                            # this monster's /check said
    chain: int = -1
    last_kill: float = -999.0
    prev_monster: int | None = None
    prev_player: int | None = None
    log: list = field(default_factory=list)
    kinds: list = field(default_factory=list)        # each log line's kind (ffxi_ui.KIND), for its colour
    times: list = field(default_factory=list)        # when each was said (time.monotonic)
    said: int = 0
    dirty: bool = False                              # progress (levels, EXP, jobs) changed: save it
    inventory: list = field(default_factory=list)    # FFXI items held: their ids (ffxi_items), a repeat a second one
    equipment: dict = field(default_factory=dict)    # slot -> the item id worn there
    gear: dict = field(default_factory=dict)         # the equipment's stats summed (set_gear): DEF, STR, HP...
    gear_effects: dict = field(default_factory=dict) # what they do, as effects (gear_effect)
    def_in_game: bool = False                        # your gear's DEF and VIT the hunter's defense in the game (the
                                                     # layer's, game_defense): not cut from the blows again here
    all_jobs: bool = False                           # every job's spells and abilities at your level (solo)
    trusts: list = field(default_factory=list)       # FFXI's Trusts' spells (ffxi_trust): every job's, all unlocked
    all_magic: bool = False                          # every spell known from the start, whatever the job and level (the layer: on)
    lockstyle: dict = field(default_factory=dict)    # /lockstyle on: the look kept (slot -> model), whatever is worn
    gear_serial: int = 0                             # the character creator's equipment last handed over (its save)
    ws_land: dict = field(default_factory=dict)      # a weapon skill -> seconds until its blow lands (its look's;
                                                     # the layer's: none, at once)
    landing: list = field(default_factory=list)      # weapon skills under way: (when, name, damage)
    engaged: bool = False                            # FFXI's Attack: swinging at the target on the weapon's delay
    next_swing: float = 0.0
    reach_said: float = -99.0
    _now: float = 0.0                                # the last tick's time

    def __post_init__(self):
        self.set_jobs(self.job, self.sub)
        self.mp = self.max_mp

    # ================================================================================================
    # jobs and levels
    # ================================================================================================
    def set_jobs(self, job: str, sub: str | None = None) -> None:
        if job not in D.JOBS or (sub and sub not in D.JOBS) or sub == job:
            raise ValueError(f"no such job combination: {job}/{sub}")
        self.job, self.sub = job, sub
        self.j = D.JOBS[job]
        self.sj = D.JOBS[sub] if sub else None
        self.w = D.WEAPONS[self.weapon]
        self.tp_hit = D.tp_per_hit(self.w["delay"])
        self.mp = min(self.mp, self.max_mp)

    def change_job(self, job: str, sub: str | None = None) -> None:
        """A job change, as at the Mog House: TP, buffs, pet and rolls are lost; MP refilled."""
        self.set_jobs(job, sub)
        self.tp = 0
        self.buffs.clear()
        self.pet = None
        self.roll = None
        self.casting = None
        self.mp = self.max_mp
        self.dirty = True
        self.say(f"{self.me} is now a {self.job_label()}.", "info")

    @property
    def level(self) -> int:
        return max(1, min(D.LEVEL_CAP, self.levels.get(self.job, 1)))

    @property
    def sub_level(self) -> int:
        if not self.sub:
            return 0
        return max(1, min(self.levels.get(self.sub, 1), self.level // D.SUBJOB_DIVISOR))

    @property
    def max_mp(self) -> int:
        mp = D.mp_at(self.job, self.level)
        if self.all_jobs:                                           # the most any job has at your level
            mp = max(D.mp_at(j, self.level) for j in D.JOBS)
        elif self.sub:
            mp += D.mp_at(self.sub, self.sub_level) // 2
        if mp:
            mp += int(self.gear.get("MP", 0))                       # gear's MP: only for a job with MP
        return max(0, mp)

    def job_label(self) -> str:
        if self.all_jobs:
            return f"{self.job}{self.level}+all"
        return f"{self.job}{self.level}" + (f"/{self.sub}{self.sub_level}" if self.sub else "")

    def set_all_jobs(self, on: bool) -> None:
        """Every job's spells and abilities at your level, on one character (solo), or the chosen ones."""
        self.all_jobs = bool(on)
        self.mp = min(self.mp, self.max_mp)
        self.dirty = True
        self.say(f"{self.me} now uses {'every job' if on else 'the chosen jobs'} ({self.job_label()}).", "info")

    # ================================================================================================
    # FFXI's Attack: the weapon out, a swing at the target on the weapon's delay
    # ================================================================================================
    def engage(self, on: bool, now: float) -> None:
        if on == self.engaged:
            return
        self.engaged = bool(on)
        self.next_swing = now + 1.2                                  # the weapon drawn first
        self.say(f"{self.me} {'draws a weapon and engages the ' + self.target if on else 'disengages'}.", "info")

    @property
    def swing_delay(self) -> float:
        """Seconds between swings: the weapon's delay (FFXI's: in 60ths of a second), Haste shortening it."""
        delay = self.gear.get("Delay") or self.w["delay"]
        return max(1.0, delay / 60.0 * (1 - min(0.8, self.effect("haste"))))

    def swing(self, now: float, m, in_reach: bool):
        """A swing when its time has come (none while casting): a hit, a critical one or a miss, TP for a
        hit; the monster's HP after it."""
        if not self.engaged or m is None or m <= 0 or self.casting or now < self.next_swing:
            return m
        self.next_swing = now + self.swing_delay
        if not in_reach:
            if now - self.reach_said > 5:
                self.reach_said = now
                self.say(f"The {self.target} is out of range.", "warn")
            return m
        if self.rng.random() > 0.9:
            self.say(f"{self.me} misses the {self.target}.", "info")
            self._event("swing", "miss")
            return m
        critical = self.rng.random() < 0.05 + self.effect("crit")
        dmg = self.avg_hit * self.rng.uniform(0.85, 1.15) * self.phys_mult
        dmg *= (1 + self.effect("atk") + self.mob("def_down")) * (1.5 if critical else 1.0)
        dmg = max(1, int(dmg))
        if critical:
            self.say(f"{self.me} scores a critical hit!", "skillchain")
        self.say(f"{self.me} hits the {self.target} for {dmg} points of damage.", "damage")
        gain = self.tp_hit * self.j["tp_mult"] * (1 + self.effect("haste") + self.effect("store_tp"))
        self.tp = min(D.TP_MAX, self.tp + gain)
        self._wake()
        self._event("swing", "critical" if critical else "hit")
        return max(self._floor(), m - dmg)

    @property
    def exp_now(self) -> int:
        return self.exp.get(self.job, 0)

    @property
    def tnl(self) -> int:
        return D.TNL[self.level] if self.level < D.LEVEL_CAP else 0

    def _learnt(self, key: str) -> list[str]:
        """What the main job knows at its level, then the subjob at its own (no SP ability from it); with
        all jobs, every job's at the main job's level after them."""
        out = [n for lv, n in sorted(self.j[key], key=lambda t: t[0]) if lv <= self.level]
        if self.all_jobs:
            for j, d in D.JOBS.items():
                if j == self.job:
                    continue
                for lv, n in sorted(d[key], key=lambda t: t[0]):
                    if lv <= self.level and n not in out and not D.ABILITIES.get(n, {}).get("sp"):
                        out.append(n)
            return out
        if self.sj:
            for lv, n in sorted(self.sj[key], key=lambda t: t[0]):
                if lv <= self.sub_level and n not in out and not D.ABILITIES.get(n, {}).get("sp"):
                    out.append(n)
        return out

    def known_spells(self) -> list[str]:
        if self.all_magic:                                   # all unlocked: every job's, at any level
            learnt = self._learnt("spells")
            rest = [n for j in D.JOBS.values() for _, n in sorted(j["spells"], key=lambda t: t[0]) if n in D.SPELLS]
            return list(dict.fromkeys(learnt + rest)) + self.trusts
        return self._learnt("spells") + self.trusts

    # --- others' actions (the Trusts'): their buffs yours, their debuffs your target's, their weapon skills in
    #     your skillchains, as in FFXI ---
    def family_active(self, family: str, on_foe: bool, margin: float = 15.0) -> bool:
        """Whether a spell of that family (spell_family: Protect for Protect II, Protectra) is on you (on_foe:
        on your target) with more than margin seconds left."""
        table = self.debuffs if on_foe else self.buffs
        return any(spell_family(src) == family and b["until"] - self._now > margin for (src, _e), b in table.items())

    def cast_by(self, who: str, name: str, now: float, m, on_foe: bool) -> None:
        """Another's spell gone off (a Trust's): its buffs on you, its debuffs on your target, a weaker one of
        its family replaced (a stronger Protect overwrites, as FFXI's does)."""
        sp = D.SPELLS.get(name, {})
        kind = "debuff" if on_foe else "buff"
        fx = [f for f in sp.get("fx", []) if f[0] == kind]
        if not fx or (on_foe and (m is None or m <= 0)):
            return
        table = self.debuffs if on_foe else self.buffs
        family = spell_family(name)
        for key in [k for k in table if spell_family(k[0]) == family]:
            del table[key]
        for _, eff, power, secs in fx:
            table[(name, eff)] = {"power": power, "until": now + secs}
        if on_foe:
            self.say(f"{who} casts {name}. The {self.target} receives the effect of {name}.", "magic")
        else:
            self.say(f"{who} casts {name}. {self.me} gains the effect of {name}.", "heal")

    def ws_by(self, who: str, name: str, dmg: int, now: float, m):
        """Another's weapon skill (a Trust's): its damage, and the skillchain it opens or closes with yours."""
        if m is None or m <= 0:
            return m
        self.say(f"{who} uses {name}. The {self.target} takes {dmg} points of damage.", "ws")
        ws = D.WEAPON_SKILLS.get(name)
        if ws:
            dmg += self._skillchain(ws["props"], now, dmg)
        self._wake()
        return max(self._floor(), m - dmg)

    def owe(self, dealt: int, now: float) -> None:
        """FFXI's damage handed to the game: when it comes off the monster's HP, it isn't one of your blows."""
        self.ffxi_owed += int(dealt)
        self.owed_at = now

    def set_all_magic(self, on: bool) -> None:
        """Every spell known (all unlocked), or those your jobs have learnt by their levels."""
        self.all_magic = bool(on)
        self.dirty = True
        self.say(f"{self.me} now knows {'every spell' if on else 'the spells of the jobs chosen, by their levels'}.",
                 "info")

    def known_abilities(self) -> list[str]:
        return self._learnt("abilities")

    def known_ws(self) -> list[str]:
        return [n for lv, n in self.w["ws"] if lv <= self.level]

    # ================================================================================================
    # effects
    # ================================================================================================
    def set_gear(self, stats: dict) -> None:
        """The equipment's stats summed (ffxi_items' names: DEF, STR, Attack, Haste...), and what they do:
        DEF and VIT cut the damage you take, STR, Attack and the weapon's DMG add to your hits, AGI and
        Evasion dodge, INT and Magic Atk. Bonus strengthen nukes, MND cures, MP adds MP, Regen and Refresh
        tick, Haste and Store TP build TP. Scaled for Monster Hunter's damage, which isn't FFXI's."""
        g = {k: float(v) for k, v in stats.items()}
        self.gear = g
        get = g.get
        defn = get("DEF", 0)
        self.gear_effects = {
            "defn": 0.45 * defn / (defn + 120) + 0.002 * get("VIT", 0),
            "atk": (get("STR", 0) + 0.7 * get("Attack", 0)) / 220 + get("DMG", 0) / 160,
            "evasion": min(0.2, (get("AGI", 0) + get("Evasion", 0)) / 450),
            "magic": (get("INT", 0) + get("Magic Atk. Bonus", 0)) / 200,
            "heal_bonus": get("MND", 0) / 250,
            "regen": get("Regen", 0),
            "refresh": get("Refresh", 0),
            "haste": get("Haste", 0) / 100,
            "store_tp": get("Store TP", 0) / 100,
        }
        self.mp = min(self.mp, self.max_mp)

    @property
    def game_defense(self) -> int:
        """Your gear as Monster Hunter's defense (an armor's sum and 1, which a monster's blow is taken against as
        damage x 80 / (80 + defense)): DEF one for one, VIT a half. FFXI's level-75 gear, about 300: a blow a
        fifth of its size; a starter's, about 15: most of it."""
        return int(1 + self.gear.get("DEF", 0) + 0.5 * self.gear.get("VIT", 0))

    def effect(self, name: str) -> float:
        """An effect on you now: your buffs, your job's traits at its level, the subjob's at its, your gear's."""
        v = sum(b["power"] for (src, eff), b in self.buffs.items() if eff == name)
        if not (name == "defn" and self.def_in_game):
            v += self.gear_effects.get(name, 0.0)
        if name not in COUNTED:
            v += sum(p for lv, e, p in self.j["traits"] if e == name and lv <= self.level)
            if self.sj:
                v += sum(p for lv, e, p in self.sj["traits"] if e == name and lv <= self.sub_level)
        return v

    def mob(self, name: str) -> float:
        """An effect on the monster now."""
        return sum(b["power"] for (src, eff), b in self.debuffs.items() if eff == name)

    def _use_count(self, name: str, n: float) -> float:
        """Spend n from a counted buff (shadows, stoneskin, aquaveil); returns what was spent."""
        spent = 0.0
        for key in [k for k in self.buffs if k[1] == name]:
            b = self.buffs[key]
            take = min(b["power"], n - spent)
            b["power"] -= take
            spent += take
            if b["power"] <= 0:
                del self.buffs[key]
            if spent >= n:
                break
        return spent

    @property
    def phys_mult(self) -> float:
        return self.j["ws_mult"] * D.level_factor(self.level)

    @property
    def magic_mult(self) -> float:
        return self.j["nuke_mult"] * D.level_factor(self.level) * (1 + self.effect("magic"))

    def _floor(self) -> int:
        return 0 if self.allow_kills else 1

    def _mob_label(self) -> tuple[str, tuple] | None:
        if not self.monster_max:
            return None
        return D.check(D.monster_level(self.monster_max), self.level)

    # ================================================================================================
    # the log
    # ================================================================================================
    def say(self, msg: str, kind: str = "info"):
        self.said += 1                   # every message ever: the recorder counts on it
        self.log.append(msg)
        self.kinds.append(kind)
        self.times.append(time.monotonic())
        del self.log[:-12]
        del self.kinds[:-12]
        del self.times[:-12]

    # ================================================================================================
    # every frame
    # ================================================================================================
    def tick(self, now: float, monster_hp, player_hp, max_hp=None, monster_id=None, monster_max=None) -> dict:
        writes: dict = {}
        self._now = now
        if max_hp:
            self.player_max_hp = int(max_hp)
        if monster_id != self.monster_id:                 # another monster: its own debuffs, its own check
            self.monster_id = monster_id
            self.debuffs.clear()
            self.checked = False
            self.prev_monster = None
        if monster_max:
            self.monster_max = int(monster_max)
        m, p = monster_hp, player_hp

        # your hits on the monster (the game's)
        if self.ffxi_owed and now - self.owed_at > 3.0:      # FFXI's the game never took (it couldn't be hurt)
            self.ffxi_owed = 0
        if m is not None and self.prev_monster is not None:
            delta = self.prev_monster - m
            if delta > 0 and self.ffxi_owed:                 # the game taking off FFXI's own: not your blow
                mine = min(delta, self.ffxi_owed)
                self.ffxi_owed -= mine
                delta -= mine
            if delta > 0:
                m, p = self._on_hit(now, delta, m, p)
            if m == 0 and self.prev_monster > 0:
                self._on_kill(now)
        if m is not None and self.monster_max and not self.checked and self.prev_monster is not None:
            self._check()
        self.prev_monster = m

        # the monster's hits on you
        if p is not None and self.prev_player is not None:
            taken = self.prev_player - p
            if taken > 0:
                if p == 0:
                    self._on_faint()
                else:
                    p = self._on_taken(now, taken, p)
        self.prev_player = p

        # every 3 s
        if now >= self.next_tick:
            self.next_tick = now + TICK
            m, p = self._on_tick(now, m, p)
        self._expire(now)

        if self.casting and now >= self.cast_done:
            m, p = self._finish_cast(now, m, p)
        while self.landing and now >= self.landing[0][0]:        # a weapon skill's blow landing
            _, name, dmg = self.landing.pop(0)
            m, p = self._ws_lands(name, now, dmg, m, p)

        if m is not None and m != monster_hp:
            writes["monster_hp"] = self.prev_monster = m
        if p is not None and p != player_hp:
            writes["player_hp"] = self.prev_player = p
        return writes

    @property
    def avg_hit(self) -> float:
        """Your hit in FFXI's terms: what your Monster Hunter weapon's blows deal a second (learnt, that kind's;
        D.MH_RATE until then), a share of it, over your FFXI weapon's delay (ffxi_data.py's MH_RATE)."""
        dmg, secs = self.mh_rate.get(self.mh_kind or self.weapon, (0.0, 0.0))
        rate = dmg / secs if secs >= D.MH_LEARNT_SECS else D.MH_RATE
        return max(D.MH_RATE_FLOOR, rate) * D.MH_SHARE * self.w["delay"] / 60.0

    def _learn_blow(self, now: float, delta: int) -> None:
        """A blow of yours, learnt: its damage, and the time it took (since the one before, a pause counting a
        second). One far beyond the monster's usual (its HP set at its death, a scripted fall) isn't."""
        if self.monster_max and delta > 0.25 * self.monster_max:
            return
        gap = now - self.blow_at
        self.blow_at = now
        secs = gap if gap < 6.0 else 1.0
        kind = self.mh_kind or self.weapon
        dmg, total = self.mh_rate.get(kind, (0.0, 0.0))
        k = D.MH_DECAY ** secs
        self.mh_rate[kind] = [dmg * k + delta, total * k + secs]

    def _on_hit(self, now: float, delta: int, m: int, p):
        """One of your hits landed (delta: its damage)."""
        if not (self.ffxi_combat and self.engaged):                # (FFXI's Attack fighting for you: not your blows)
            self._learn_blow(now, delta)
        gain = self.tp_hit * self.j["tp_mult"] * (1 + self.effect("haste") + self.effect("store_tp"))
        self.tp = min(D.TP_MAX, self.tp + gain)
        extra = delta * max(0.0, self.effect("atk") + self.mob("def_down")) + self.avg_hit * self.effect("enspell")
        if extra >= 1:
            m = max(self._floor(), m - int(extra))
        self._wake()
        if p is not None and self.effect("drain_hit"):
            p = min(self.player_max_hp, p + max(1, int(delta * self.effect("drain_hit"))))
        if self.effect("aspir_hit"):
            self.mp = min(self.max_mp, self.mp + delta * self.effect("aspir_hit"))
        return m, p

    def _on_taken(self, now: float, taken: int, p: int) -> int:
        """The monster hit you for taken (p: your HP after it). Returns your HP after FFXI's say."""
        prev = p + taken
        if self.effect("shadows") >= 1:
            self._use_count("shadows", 1)
            left = int(self.effect("shadows"))
            self.say(f"1 of {self.me}'s shadows absorbs the damage{f' ({left} left)' if left else ' and disappears'}.", "info")
            return prev
        if self.mob("sleep") > 0:
            self.say(f"The {self.target} is asleep and can't attack.", "info")      # stand-in
            return prev
        if self.rng.random() < self.mob("paralyze"):
            self.say(f"The {self.target} is paralyzed.", "info")                      # stand-in
            return prev
        if self.rng.random() < self.mob("blind"):
            self.say(f"The {self.target} misses {self.me}.", "info")                  # stand-in
            return prev
        if self.rng.random() < self.effect("evasion"):                                # the gear's AGI, Evasion
            self.say(f"{self.me} evades the {self.target}'s attack.", "info")
            return prev
        cut = max(-0.5, min(0.9, self.effect("defn") + self.mob("slow") + self.mob("weight")))
        after = taken * (1 - cut) - self.effect("phalanx")
        after -= self._use_count("stoneskin", max(0.0, after))
        after = max(0, round(after))
        if after > 0:
            self._event("hurt", str(after))                              # the race's voice (the layer's)
            self.tp = min(D.TP_MAX, self.tp + D.TP_WHEN_HIT * self.j["tp_mult"])
            if self.casting:
                if self.effect("aquaveil") >= 1:
                    self._use_count("aquaveil", 1)
                else:
                    self.say(f"{self.me}'s casting of {self.casting} is interrupted.", "warn")
                    self._event("interrupted", self.casting)
                    self.casting = None
        return max(1, min(self.player_max_hp, prev - after))

    def _on_tick(self, now: float, m, p):
        if p is not None and p > 0 and self.effect("regen"):
            p = min(self.player_max_hp, p + int(self.effect("regen")))
        if self.max_mp:
            self.mp = min(self.max_mp, self.mp + max(1, self.max_mp // 100) + self.effect("refresh"))  # MP comes back slowly
        if self.effect("regain"):
            self.tp = min(D.TP_MAX, self.tp + self.effect("regain"))
        if m is not None and m > self._floor():
            # Poison, Dia, Bio, Burn...: each tick a share of your usual blow, and a share of the monster's
            # HP (so a big one feels it too), the fractions kept to the next tick; said, with what did it
            power = self.mob("dot")
            dot = (self.avg_hit * power * D.level_factor(self.level) + self.monster_max * power * 0.04) if power else 0.0
            pet = self.avg_hit * self.pet["power"] * D.level_factor(self.level) if self.pet else 0.0
            self.dot_carry += dot + pet
            whole = int(self.dot_carry)
            if whole >= 1:
                self.dot_carry -= whole
                m = max(self._floor(), m - whole)
                if dot:
                    srcs = sorted({src for (src, eff) in self.debuffs if eff == "dot"})
                    self.say(f"The {self.target} takes {whole} point{'s' if whole != 1 else ''} of damage ({', '.join(srcs)}).", "damage")
        else:
            self.dot_carry = 0.0
        if self.pet and self.pet["upkeep"]:
            if self.mp < self.pet["upkeep"]:
                self.say(f"{self.pet['name']} disappears: {self.me} is out of MP.", "warn")
                self.pet = None
            else:
                self.mp -= self.pet["upkeep"]
        return m, p

    def _expire(self, now: float) -> None:
        for key in [k for k, b in self.buffs.items() if now >= b["until"]]:
            src = key[0]
            del self.buffs[key]
            if not any(k[0] == src for k in self.buffs):
                self.say(f"{self.me}'s {src} effect wears off.", "info")
        for key in [k for k, b in self.debuffs.items() if now >= b["until"]]:
            src = key[0]
            del self.debuffs[key]
            if not any(k[0] == src for k in self.debuffs):
                self.say(f"The {self.target}'s {src} effect wears off.", "info")
        if self.pet and now >= self.pet["until"]:
            self.say(f"{self.pet['name']} disappears.", "info")
            self.pet = None
        if self.roll and now >= self.roll["until"]:
            self.roll = None

    def _wake(self) -> None:
        for key in [k for k in self.debuffs if k[1] == "sleep"]:
            del self.debuffs[key]
            self.say(f"The {self.target} wakes up.", "info")

    def _check(self) -> None:
        self.checked = True
        label = self._mob_label()
        if label:
            self.say(f"The {self.target} {D.CHECK_PHRASE[label[0]]}.", "info")

    def _on_kill(self, now: float) -> None:
        self.debuffs.clear()
        if not self.monster_max:
            return
        mob_lv = D.monster_level(self.monster_max)
        base = D.kill_exp(mob_lv, self.level, self.monster_max)
        self.say(f"{self.me} defeats the {self.target}.", "info")
        self._event("kill", str(mob_lv))                               # its drops (the layer's gear)
        if not base:
            return
        self.chain = self.chain + 1 if now - self.last_kill <= D.CHAIN_WINDOW else 0
        self.last_kill = now
        bonus = D.CHAIN_BONUS[min(self.chain, len(D.CHAIN_BONUS) - 1)]
        gained = int(base * self.exp_rate * bonus * (1 + self.effect("exp_bonus")))
        if self.chain:
            self.say(f"EXP chain #{self.chain}!", "skillchain")
        self.gain_exp(gained)

    def gain_exp(self, n: int) -> None:
        if self.level >= D.LEVEL_CAP:
            return
        self.say(f"{self.me} gains {n} experience points.", "heal")
        self.exp[self.job] = self.exp_now + n
        while self.level < D.LEVEL_CAP and self.exp_now >= self.tnl:
            self.exp[self.job] = self.exp_now - self.tnl
            self.levels[self.job] = self.level + 1
            self.say(f"{self.me} attains level {self.level}!", "burst")
            self._event("level_up", self.job)
        if self.level >= D.LEVEL_CAP:
            self.exp[self.job] = 0
        self.dirty = True

    def _on_faint(self) -> None:
        self._event("faint", "")
        self.buffs.clear()
        self.pet = None
        self.casting = None
        self.tp = 0
        if self.level > 5:
            loss = int(self.tnl * D.DEATH_EXP_LOSS) if self.tnl else int(D.TNL[-1] * D.DEATH_EXP_LOSS)
            self.exp[self.job] = self.exp_now - loss
            self.say(f"{self.me} was defeated and loses {loss} experience points.", "warn")
            if self.exp_now < 0:
                self.levels[self.job] = self.level - 1
                self.exp[self.job] = self.exp_now + self.tnl
                self.say(f"{self.me} falls to level {self.level}.", "warn")
                self._event("level_down", self.job)
            self.dirty = True

    # ================================================================================================
    # actions: weapon skills, job abilities, spells
    # ================================================================================================
    def can_use(self, name: str, now: float) -> tuple[bool, str]:
        if name in D.WEAPON_SKILLS:
            if name not in self.known_ws():
                return False, f"{self.me} can't use {name} yet."
            if self.tp < D.TP_WS_MIN:
                return False, f"Not enough TP for {name} ({int(self.tp)}/1000)."
            return True, ""
        left = self.recasts.get(name, 0) - now
        if name in D.ABILITIES:
            a = D.ABILITIES[name]
            if name not in self.known_abilities():
                return False, f"{self.me} can't use {name}."
            if left > 0:
                return False, f"{name} can be used again in {int(left) + 1} s."
            if a.get("needs_pet") and not self.pet:
                return False, f"{name} needs a pet."
            if a.get("tp") and not self.effect("tp_cost_free") and self.tp < a["tp"]:
                return False, f"Not enough TP for {name} ({int(self.tp)}/{a['tp']})."
            return True, ""
        if name in D.SPELLS:
            if name not in self.known_spells():
                return False, f"{self.me} can't cast {name}."
            if self.casting:
                return False, f"{self.me} is already casting {self.casting}."
            if left > 0:
                return False, f"{name} can be cast again in {int(left) + 1} s."
            if self.mp < self._mp_cost(name):
                return False, f"{self.me} doesn't have enough MP to cast {name}."
            return True, ""
        return False, f"No such action: {name}."

    def _mp_cost(self, name: str) -> int:
        return int(D.SPELLS[name]["mp"] * max(0.0, 1 - self.effect("conserve_mp")))

    def use(self, name: str, now: float, monster_hp, player_hp) -> dict:
        """Use a weapon skill, a job ability, or start casting a spell. Returns HP writes."""
        ok, why = self.can_use(name, now)
        if not ok:
            self.say(why, "warn")
            return {}
        m, p = monster_hp, player_hp
        if name in D.WEAPON_SKILLS:
            m, p = self._weapon_skill(name, now, m, p)
        elif name in D.ABILITIES:
            a = D.ABILITIES[name]
            self.recasts[name] = now + a["recast"]
            if a.get("tp") and not self.effect("tp_cost_free"):
                self.tp -= a["tp"]
            self.say(f"{self.me} uses {name}.", "ws")
            m, p = self._apply(a["fx"], name, now, m, p, physical=True)
        else:
            sp = D.SPELLS[name]
            cast = sp["cast"] * max(0.0, 1 - self.effect("fast_cast"))
            self.casting, self.cast_total, self.cast_done = name, cast, now + cast
            self.say(f"{self.me} starts casting {name}.", "magic")
            self._event("cast", name)
            if cast <= 0:
                m, p = self._finish_cast(now, m, p)
        writes = {}
        if m is not None and m != monster_hp:
            writes["monster_hp"] = self.prev_monster = m
        if p is not None and p != player_hp:
            writes["player_hp"] = self.prev_player = p
        return writes

    # the old interfaces: by slot
    def weapon_skill(self, slot: int, now: float, monster_hp) -> dict:
        ws = self.known_ws()
        return self.use(ws[slot], now, monster_hp, None) if slot < len(ws) else {}

    def cast(self, slot: int, now: float, has_player_hp: bool = True) -> None:
        sp = self.known_spells()
        if slot < len(sp):
            self.use(sp[slot], now, None, None)

    def _weapon_skill(self, name: str, now: float, m, p):
        ws = D.WEAPON_SKILLS[name]
        if m is None:
            self.say(f"{self.me} has no target.", "warn")
            return m, p
        hits = ws.get("hits", 1)
        tp = self.tp
        mult = interp_ftp(ws["ftp"], tp) + 0.8 * (hits - 1)
        dmg = self.avg_hit * mult * self.phys_mult * (1 + self.effect("ws_bonus") + self.next_ws) * (1 + self.mob("def_down"))
        self.next_ws = 0.0
        self.tp = 0
        dmg = int(dmg)
        self.say(f"{self.me} uses {name}.", "ws")
        self._event("ws", name)                                          # its look (the layer's effects)
        delay = float(self.ws_land.get(name) or 0.0)
        if delay > 0:                                                    # the blow lands as its look shows it
            self.landing.append((now + delay, name, dmg))
            self.landing.sort(key=lambda t: t[0])
            self.next_swing = max(self.next_swing, now + delay + 1.0)    # no swing over it
            return m, p
        return self._ws_lands(name, now, dmg, m, p)

    def _ws_lands(self, name: str, now: float, dmg: int, m, p):
        """A weapon skill's blow landing: its damage, the skillchain it closes, its added effects."""
        if m is None or m <= 0:
            return m, p
        ws = D.WEAPON_SKILLS[name]
        if dmg > 0:
            self.say(f"The {self.target} takes {dmg} points of damage.", "damage")
        dmg += self._skillchain(ws["props"], now, dmg)
        m = max(self._floor(), m - dmg)
        if dmg > 0:
            self._wake()
        m, p = self._apply(ws.get("fx", []), name, now, m, p, physical=True)
        return m, p

    def _skillchain(self, props: list, now: float, dmg: int) -> int:
        """Close a skillchain if this follows another weapon skill in its window; returns its damage."""
        bonus = 0
        since = now - self.last_ws_time
        if props and D.SC_WINDOW_OPEN <= since <= D.SC_WINDOW_CLOSE:
            sc = resolve_skillchain(self.last_ws_props, props)
            if sc:
                bonus = int(max(dmg, self.avg_hit) * D.SC_BONUS[D.SC_LEVEL[sc]])
                self.sc_name = sc
                self.mb_until = now + D.MB_WINDOW
                self.mb_elements = D.SC_ELEMENTS[sc]
                self.say(f"Skillchain: {sc}. The {self.target} takes {bonus} points of damage.", "skillchain")
                self._event("skillchain", sc)
                props = [sc]                      # the chain can continue from its result
        if props:
            self.last_ws_time, self.last_ws_props = now, props
        return bonus

    def _event(self, kind: str, name: str) -> None:
        self.events.append((kind, name))
        del self.events[:-32]                                # nobody listening: the last few kept

    def _finish_cast(self, now: float, m, p):
        name, self.casting = self.casting, None
        sp = D.SPELLS[name]
        self.mp -= self._mp_cost(name)                       # FFXI: MP is spent when the spell goes off
        self.recasts[name] = now + sp["recast"]
        self._event("spell", name)
        physical = any(f[0] == "damage" and f[2] == "physical" for f in sp["fx"])
        m, p = self._apply(sp["fx"], name, now, m, p, physical=physical, spell=True)
        if sp.get("props") and m is not None:                # blue magic can skillchain
            extra = self._skillchain(sp["props"], now, int(self.avg_hit))
            m = max(self._floor(), m - extra)
        return m, p

    # ================================================================================================
    # what an action does
    # ================================================================================================
    def _apply(self, fx: list, src: str, now: float, m, p, physical: bool = False, spell: bool = False):
        lf = D.level_factor(self.level)
        cast_word = "casts" if spell else "uses"
        announced = False                                    # "gains the effect of": once per action
        for f in fx:
            kind = f[0]
            if kind in ("damage", "drain", "hit"):
                if m is None:
                    self.say(f"{self.me} {cast_word} {src}, but there is no target.", "warn")
                    continue
                if kind == "hit":
                    dmg = self.avg_hit * f[1] * self.phys_mult * (1 + self.effect("ws_bonus"))
                    burst = False
                elif f[2] == "physical":
                    dmg = self.avg_hit * f[1] * self.phys_mult
                    burst = False
                else:
                    burst = now < self.mb_until and f[2] in self.mb_elements
                    dmg = self.avg_hit * f[1] * max(self.magic_mult, 0.3) * (1 + self.effect("nuke_bonus"))
                    if burst:
                        dmg *= D.MB_BONUS
                    dmg *= day_bonus(self.day_element, f[2])
                dmg = int(dmg * (1 + self.mob("def_down")))
                if spell:
                    head = f"Magic Burst! {self.me} casts {src}. " if burst else f"{self.me} casts {src}. "
                else:
                    head = ""
                self.say(f"{head}The {self.target} takes {dmg} points of damage.",
                         "burst" if burst else ("magic" if spell else "damage"))
                m = max(self._floor(), m - dmg)
                if dmg > 0:
                    self._wake()
                if kind == "drain" and p is not None:
                    back = min(self.player_max_hp - p, int(dmg * f[3]), 40)
                    if back > 0:
                        p += back
                        self.say(f"{self.me} drains {back} HP.", "heal")
            elif kind == "heal":
                if p is None:
                    self.say(f"{src} needs the player_hp address.", "warn")
                    continue
                hp = int(f[1] * self.j["heal_mult"] * (1 + self.effect("heal_bonus")) * (0.6 + 0.4 * lf))
                new = min(self.player_max_hp, p + max(1, hp))
                self.say(f"{self.me} {cast_word} {src}. {self.me} recovers {new - p} HP.", "heal")
                p = new
            elif kind == "full_heal":
                if p is not None:
                    self.say(f"{self.me} recovers {self.player_max_hp - p} HP.", "heal")
                    p = self.player_max_hp
            elif kind == "buff":
                _, eff, power, secs = f
                self.buffs[(src, eff)] = {"power": power, "until": now + secs}
                if not announced:
                    announced = True
                    head = f"{self.me} casts {src}. " if spell else ""
                    self.say(f"{head}{self.me} gains the effect of {src}.", "heal")
            elif kind == "debuff":
                _, eff, power, secs = f
                if m is None:
                    self.say(f"{self.me} {cast_word} {src}, but there is no target.", "warn")
                    continue
                self.debuffs[(src, eff)] = {"power": power, "until": now + secs}
                if eff == "sleep":
                    self.say(f"The {self.target} is asleep.", "magic")
                elif not any(g[0] in ("damage", "hit") for g in fx) and not announced:
                    announced = True
                    head = f"{self.me} casts {src}. " if spell else ""
                    self.say(f"{head}The {self.target} receives the effect of {src}.", "magic")
            elif kind == "pet":
                _, name, power, secs, upkeep = f
                self.pet = {"name": name, "power": power, "until": now + secs, "upkeep": upkeep}
                self.say(f"{self.me} calls {name}." if not spell else f"{self.me} summons {name}.", "magic")
            elif kind == "release":
                if self.pet:
                    self.say(f"{self.pet['name']} is released.", "info")
                    self.pet = None
            elif kind == "mp":
                self.mp = min(self.max_mp, self.mp + f[1])
                self.say(f"{self.me} recovers {f[1]} MP.", "heal")
            elif kind == "mp_full":
                self.say(f"{self.me} recovers {int(self.max_mp - self.mp)} MP.", "heal")
                self.mp = self.max_mp
            elif kind == "aspir":
                got = min(self.max_mp - self.mp, f[1])
                self.mp += got
                self.say(f"{self.me} casts {src}. {self.me} absorbs {int(got)} MP.", "magic")
            elif kind == "tp":
                self.tp = min(D.TP_MAX, self.tp + f[1])
            elif kind == "random_ws":
                self.tp = D.TP_MAX
                self.say(f"{self.me}'s TP is full.", "skillchain")
            elif kind == "next_ws":
                self.next_ws = max(self.next_ws, f[1])
            elif kind == "hp_to":
                if p is not None:
                    p = f[1]
            elif kind == "roll":
                _, eff, lo, hi, secs = f
                die = self.rng.randint(1, 6)
                self.roll = {"src": src, "effect": eff, "lo": lo, "hi": hi, "total": die,
                             "until": now + ROLL_WINDOW, "secs": secs}
                self._set_roll(now)
            elif kind == "double_up":
                if not self.roll:
                    self.say(f"{self.me} has no roll to double up.", "warn")
                    continue
                r = self.roll
                r["total"] += self.rng.randint(1, 6)
                if r["total"] > 11:
                    self.buffs.pop((r["src"], r["effect"]), None)
                    self.buffs[("Bust", "atk")] = {"power": -0.05, "until": now + 60}
                    self.say(f"{r['src']}: {r['total']}. Bust!", "warn")
                    self.roll = None
                else:
                    self._set_roll(now)
        return m, p

    def _set_roll(self, now: float) -> None:
        r = self.roll
        t = r["total"]
        power = r["lo"] + (r["hi"] - r["lo"]) * (min(t, 11) - 1) / 10
        if t == 11:
            power *= 1.25                                    # eleven: the best roll
        self.buffs[(r["src"], r["effect"])] = {"power": power, "until": now + r["secs"]}
        self.say(f"{r['src']}: {t}{'!' if t == 11 else '.'}", "skillchain")

    # ================================================================================================
    # progress: saved between sessions
    # ================================================================================================
    def progress(self) -> dict:
        return {"job": self.job, "sub": self.sub, "levels": dict(self.levels), "exp": dict(self.exp),
                "inventory": list(self.inventory), "equipment": dict(self.equipment), "all_jobs": self.all_jobs,
                "lockstyle": dict(self.lockstyle), "gear_serial": self.gear_serial, "all_magic": self.all_magic,
                "ffxi_combat": self.ffxi_combat, "voice": self.voice,
                "mh_rate": {k: [round(v[0], 2), round(v[1], 3)] for k, v in self.mh_rate.items()}}

    def load_progress(self, d: dict) -> None:
        self.levels = {k: int(v) for k, v in d.get("levels", {}).items() if k in D.JOBS}
        self.exp = {k: int(v) for k, v in d.get("exp", {}).items() if k in D.JOBS}
        self.inventory = [int(i) for i in d.get("inventory", [])]
        self.all_jobs = bool(d.get("all_jobs", False))
        self.equipment = {k: int(v) for k, v in d.get("equipment", {}).items()}
        self.lockstyle = {k: int(v) for k, v in d.get("lockstyle", {}).items()}
        self.gear_serial = int(d.get("gear_serial", 0))
        self.all_magic = bool(d.get("all_magic", self.all_magic))
        self.ffxi_combat = bool(d.get("ffxi_combat", self.ffxi_combat))
        self.voice = int(d.get("voice", 0) or 0)
        self.mh_rate = {str(k): [float(v[0]), float(v[1])] for k, v in (d.get("mh_rate") or {}).items()
                        if isinstance(v, (list, tuple)) and len(v) == 2}
        job, sub = d.get("job", self.job), d.get("sub", self.sub)
        if job in D.JOBS and (sub is None or (sub in D.JOBS and sub != job)):
            self.set_jobs(job, sub)
        self.mp = self.max_mp


# ================================================================================================
# Self-test
# ================================================================================================
def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok &= bool(cond)

    # the data holds together
    bad = []
    for j, d in D.JOBS.items():
        bad += [f"{j}: spell {n}" for _, n in d["spells"] if n not in D.SPELLS]
        bad += [f"{j}: ability {n}" for _, n in d["abilities"] if n not in D.ABILITIES]
    for w, d in D.WEAPONS.items():
        bad += [f"{w}: WS {n}" for _, n in d["ws"] if n not in D.WEAPON_SKILLS]
    for n, ws in D.WEAPON_SKILLS.items():
        bad += [f"{n}: property {p}" for p in ws["props"] if p not in D.SC_LEVEL]
    check(not bad, f"every job's spells and abilities, every weapon's skills exist {bad[:5]}")
    check(len(D.JOBS) == 22, f"22 jobs ({len(D.JOBS)})")
    check(all(sum(1 for d in D.JOBS.values() for _, n in d["abilities"] if D.ABILITIES[n].get("sp") and n in [
        a for _, a in D.JOBS[j]["abilities"]]) for j in D.JOBS), "every job has an SP ability")
    check(D.tp_per_hit(240) == 75 and D.tp_per_hit(480) == 134, "TP per hit from delay")
    check(resolve_skillchain(["Liquefaction"], ["Impaction"]) == "Fusion", "Liquefaction > Impaction = Fusion")
    check(D.check(50, 50)[0] == "Even Match" and D.check(60, 50)[0] == "Incredibly Tough"
          and D.check(30, 50)[0] == "Too Weak", "/check labels")
    check(D.monster_level(80) < 15 < D.monster_level(2000), f"monster levels from HP "
          f"({D.monster_level(80)}, {D.monster_level(2000)})")

    rng = random.Random(1)
    # levels: what a job knows grows with it
    e = Engine("RDM", "sword_and_shield", levels={"RDM": 1}, rng=rng)
    check(e.known_spells() == ["Dia"] and e.known_ws() == ["Fast Blade"], f"level 1 RDM: {e.known_spells()}")
    e30 = Engine("RDM", "sword_and_shield", sub="WHM", levels={"RDM": 30, "WHM": 20}, rng=rng)
    check("Gravity" in e30.known_spells() and e30.sub_level == 15 and "Slow" in e30.known_spells(),
          f"RDM30/WHM15 knows its own and its subjob's spells ({e30.sub_level})")
    check(e30.max_mp > Engine("RDM", "sword_and_shield", levels={"RDM": 30}).max_mp, "the subjob adds MP")
    sp_sub = Engine("RDM", "sword_and_shield", sub="WAR", levels={"RDM": 75, "WAR": 75})
    check("Mighty Strikes" not in sp_sub.known_abilities() and "Berserk" in sp_sub.known_abilities(),
          "no SP ability from the subjob")

    # TP from hits; a weapon skill; a skillchain; a magic burst
    e = Engine("RDM", "sword_and_shield", levels={"RDM": 75}, rng=rng)
    t, m, p = 0.0, 3000, 150
    e.tick(t, m, p, 150, 1, 3000)
    for _ in range(40):
        t += 1
        m -= 30
        e.tick(t, m, p, 150, 1, 3000)
    check(e.tp >= 1000 and abs(e.avg_hit - 30 * D.MH_SHARE * 220 / 60) < 2, f"TP from hits (TP {int(e.tp)})")
    # your hit is your weapon's damage a second: many small blows and few big ones alike, saved with progress
    small, big = Engine("RDM", "sword_and_shield", rng=rng), Engine("RDM", "sword_and_shield", rng=rng)
    small.mh_kind, big.mh_kind = "bowgun", "great_sword"
    for i in range(40):
        small.tick(i * 0.25, 4000 - 5 * i, p, 150, 1, 4000)
        big.tick(i * 2.5, 4000 - 50 * i, p, 150, 1, 4000)
    want = 20 * D.MH_SHARE * 220 / 60
    check(abs(small.avg_hit - big.avg_hit) < 0.15 * want and abs(small.avg_hit - want) < 0.15 * want,
          f"a second's damage, not a blow's ({small.avg_hit:.0f} and {big.avg_hit:.0f})")
    fresh = Engine("RDM", "sword_and_shield", rng=rng)
    check(abs(fresh.avg_hit - D.MH_RATE * D.MH_SHARE * 220 / 60) < 0.1, "an unlearnt weapon: the usual rate")
    fresh.load_progress(small.progress())
    fresh.mh_kind = "bowgun"
    check(abs(fresh.avg_hit - small.avg_hit) < 0.01, "what's learnt is kept with progress")
    weak = Engine("RDM", "sword_and_shield", rng=rng)
    for i in range(40):
        weak.tick(i * 1.0, 4000 - i, p, 150, 1, 4000)
    check(weak.avg_hit >= D.MH_RATE_FLOOR * D.MH_SHARE * 220 / 60 - 0.01, "never under the floor")
    # your FFXI gear as the hunter's defense in the game: then not cut again here
    armored = Engine("PLD", "sword_and_shield", rng=rng)
    armored.set_gear({"DEF": 296, "VIT": 10})
    check(armored.game_defense == 302, f"gear as Monster Hunter's defense ({armored.game_defense})")
    cut = armored.effect("defn")
    armored.def_in_game = True
    check(cut > 0.25 and armored.effect("defn") < cut - 0.25, "the gear's DEF not cut twice")
    w = e.use("Burning Blade", t, m, p)
    check(w.get("monster_hp", m) < m, "a weapon skill damages the monster")
    m = w["monster_hp"]
    t += 3
    e.tp = 1000
    w = e.use("Savage Blade", t, m, p)                      # Liquefaction > Scission = Scission
    check(e.sc_name == "Scission" and t < e.mb_until, f"a skillchain ({e.sc_name})")
    m = w["monster_hp"]
    e.recasts.clear()
    e.use("Stone II", t, m, p)
    w = e.tick(t + 5, m, p, 150, 1, 3000)
    check(any("Magic Burst" in l for l in e.log), "Stone II magic-bursts on Scission")
    m = w.get("monster_hp", m)

    # job abilities: recasts, buffs that add damage to your hits
    e = Engine("WAR", "great_sword", levels={"WAR": 30}, rng=rng)
    e.tick(0, 3000, 150, 150, 1, 3000)
    e.tick(1, 2950, 150, 150, 1, 3000)
    plain = 2950
    w = e.use("Berserk", 2, 2950, 150)
    w = e.tick(3, 2900, 150, 150, 1, 3000)                  # a 50-point hit with Berserk (+25%, +5% trait)
    check(w.get("monster_hp", 2900) < 2900 - 10, f"Berserk adds to each hit ({w})")
    e.use("Berserk", 4, 2900, 150)
    check(any("again in" in l for l in e.log), "an ability on its recast can't be used")
    w = e.tick(5, w.get("monster_hp", 2900), 100, 150, 1, 3000)   # hit for 50 with defense down
    check(w.get("player_hp", 100) < 100, "Berserk's defense down: more damage taken")

    # defenses: shadows, protect, stoneskin
    e = Engine("NIN", "long_sword", levels={"NIN": 40}, rng=rng)
    e.tick(0, 3000, 150, 150, 1, 3000)
    e.use("Utsusemi: Ni", 0, 3000, 150)
    e.tick(2, 3000, 150, 150, 1, 3000)                      # cast done
    w = e.tick(3, 3000, 120, 150, 1, 3000)
    check(w.get("player_hp") == 150, "a shadow absorbs a hit")
    e = Engine("WHM", "hammer", levels={"WHM": 30}, rng=rng)
    e.tick(0, 3000, 150, 150, 1, 3000)
    e.use("Protect II", 0, 3000, 150)
    e.tick(3, 3000, 150, 150, 1, 3000)
    w = e.tick(4, 3000, 100, 150, 1, 3000)
    check(102 <= w.get("player_hp", 100) <= 110, f"Protect refunds part of a hit ({w})")
    e.use("Cure II", 5, 3000, w["player_hp"])
    w2 = e.tick(9, 3000, w["player_hp"], 150, 1, 3000)
    check(w2.get("player_hp", 0) > w["player_hp"], "Cure II heals")
    e.use("Cure", 10, 3000, 140)
    e.tick(10.5, 3000, 120, 150, 1, 3000)
    check(e.casting is None and any("interrupted" in l for l in e.log), "damage interrupts casting")

    # damage over time and the monster's debuffs
    e = Engine("BLM", "hammer", levels={"BLM": 40}, rng=rng)
    e.tick(0, 3000, 150, 150, 1, 3000)
    e.use("Bio II", 0, 3000, 150)
    m = e.tick(2, 3000, 150, 150, 1, 3000).get("monster_hp", 3000)
    hp = [m]
    for i in range(4):
        hp.append(e.tick(3 + 3 * i, hp[-1], 150, 150, 1, 3000).get("monster_hp", hp[-1]))
    check(hp[-1] < hp[0], f"Bio II deals damage over time ({hp})")
    e.recasts.clear()
    e.use("Sleep", 20, hp[-1], 150)
    e.tick(23, hp[-1], 150, 150, 1, 3000)
    w = e.tick(24, hp[-1], 120, 150, 1, 3000)
    check(w.get("player_hp") == 150, "a sleeping monster's hit is cancelled (stand-in)")
    e.tick(25, hp[-1] - 20, 150, 150, 1, 3000)
    check(not any(k[1] == "sleep" for k in e.debuffs), "damage wakes it")

    # pets, TP-costing abilities, dice
    e = Engine("SMN", "hammer", levels={"SMN": 30}, rng=rng)
    e.tick(0, 3000, 150, 150, 1, 3000)
    e.use("Ifrit", 0, 3000, 150)
    e.tick(D.SPELLS["Ifrit"]["cast"] + 0.1, 3000, 150, 150, 1, 3000)
    check(e.pet and e.pet["name"] == "Ifrit", "Ifrit summoned")
    mp0 = e.mp
    m = 3000
    for i in range(3):
        m = e.tick(9 + 3 * i, m, 150, 150, 1, 3000).get("monster_hp", m)
    check(m < 3000 and e.mp < mp0 + 6, f"the avatar fights and costs MP ({m}, MP {mp0}->{e.mp})")
    w = e.use("Burning Strike", 20, m, 150)
    check(w.get("monster_hp", m) < m, "a blood pact hits")
    e = Engine("DNC", "sword_and_shield", levels={"DNC": 20}, rng=rng)
    e.tp = 300
    w = e.use("Curing Waltz", 0, 3000, 100)
    check(e.tp == 100 and w.get("player_hp", 100) > 100, "Curing Waltz spends TP and heals")
    e = Engine("COR", "light_bowgun", levels={"COR": 30}, rng=random.Random(3))
    e.use("Chaos Roll", 0, 3000, 150)
    check(e.effect("atk") > 0.05, f"a Phantom Roll buffs ({e.roll and e.roll['total']})")
    for _ in range(4):
        e.recasts.pop("Double-Up", None)
        e.use("Double-Up", 1, 3000, 150)
    check(any("Bust" in l for l in e.log) or (e.roll and e.roll["total"] <= 11), "Double-Up adds, or busts over 11")

    # kills and levels
    e = Engine("WAR", "great_sword", levels={"WAR": 1}, rng=rng)
    e.tick(0, 80, 150, 150, 7, 80)
    e.tick(1, 40, 150, 150, 7, 80)
    e.tick(2, 0, 150, 150, 7, 80)
    check(any("defeats" in l for l in e.log) and e.exp_now + (e.level - 1) * 500 > 0, "a kill gives EXP")
    e.gain_exp(5000)
    check(e.level >= 4 and e.dirty, f"levels up ({e.level})")
    lv = e.level
    e.levels["WAR"] = 30
    e.tick(10, 3000, 150, 150, 8, 3000)
    e.tick(11, 3000, 0, 150, 8, 3000)
    check(e.exp_now < 0 or e.level < 30 or any("defeated" in l for l in e.log), "fainting costs EXP")
    e2 = Engine("WAR", "great_sword")
    e2.load_progress(e.progress())
    check(e2.level == e.level and e2.job == "WAR", "progress saved and loaded")

    # the layer never lands the killing blow by default
    e = Engine("WAR", "great_sword", levels={"WAR": 75})
    e.tp = 3000
    e.tick(0, 10, 150, 150, 1, 3000)
    w = e.use("Resolution", 0, 10, 150)
    check(w.get("monster_hp") == 1, "the layer never lands the killing blow by default")
    print("\nall good" if ok else "\nsome checks failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
