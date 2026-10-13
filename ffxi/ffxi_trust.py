"""FFXI's Trusts: called by magic, they follow you, up to five at once, every one of them unlocked.

What each Trust is: FFXI's trust spells (896 on) and the NPC each calls, as LandSandBoat's database
records them (github.com/LandSandBoat/server, sql/spell_list.sql and mob_pools.sql: a reference, fetched
once and kept with the extracted assets, as ffxi_items.py does the equipment's looks); and what each
casts and uses, its own (mob_spell_lists.sql: each spell from a level; mob_skill_lists.sql and
mob_skills.sql: its weapon skills). Each one's look
is an NPC model of its own (the pool's model number); the client's file for model m is file m + 96907
(FTABLE's ROM/309/20 is model 3000, Shantotto's, and the numbers run on together from there): the
skeleton, the meshes, the textures and the motions in one file, the motions named as a player's
(idl, wlk, run, btl, at0.., the schools' casting, dfm, ded, cor), so a Trust is packed as the player's
character is, at the same scale, and the port draws and animates it the same way (port_model's slots 1-5,
port_bridge.set_companion).

    uv run ffxi_trust.py list           # every Trust, its jobs, its model's file
"""
from __future__ import annotations

import itertools
import json
import math
import re
import threading
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import ffxi_assets as A

LSB = "https://raw.githubusercontent.com/LandSandBoat/server/base/sql/"
FIRST_SPELL, LAST_SPELL = 896, 1199
POOL_BASE = 5000                         # a trust spell's NPC: pool 5000 + the spell's number
MODEL_FILE = 96907                       # the file number of NPC model m: m + this (models 3000 on)
MAX_CALLED = 5                           # a party of six: you and five
JOBS = ["", "WAR", "MNK", "WHM", "BLM", "RDM", "THF", "PLD", "DRK", "BST", "BRD", "RNG", "SAM", "NIN", "DRG",
        "SMN", "BLU", "COR", "PUP", "DNC", "SCH", "GEO", "RUN"]

# The names as FFXI writes them, where the database's keys don't say
NAMES = {"d_shantotto": "D. Shantotto", "selh_teus": "Selh'teus", "i_shield_uc": "I. Shield (UC)",
         "king_of_hearts": "King of Hearts", "matsui-p": "Matsui-P", "aahm": "AAHM", "aaev": "AAEV",
         "aamr": "AAMR", "aatt": "AATT", "aagk": "AAGK", "excenmille_s": "Excenmille (S)"}


def display_name(key: str) -> str:
    if key in NAMES:
        return NAMES[key]
    words = key.split("_")
    tail = ""
    if words[-1] == "uc":
        words, tail = words[:-1], " (UC)"
    out = []
    for w in words:
        if w == "ii":
            out.append("II")
        else:
            out.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(out) + tail


def _fetch(dest: Path, name: str) -> str:
    p = dest / f"lsb_{name}.sql"
    if not p.exists():
        with urllib.request.urlopen(LSB + f"{name}.sql", timeout=120) as r:
            p.write_bytes(r.read())
    return p.read_text("utf-8")


def build_list(root: Path | None = None) -> list[dict]:
    """Every Trust: its spell (number, name, cast and recast in seconds), its NPC's model number and jobs,
    and whether this installation has its model's file. Kept as trusts.json."""
    import ffxi_sound as S
    dest = A.asset_dir()
    spells = {}
    for m in re.finditer(r"INSERT INTO `spell_list` VALUES \((\d+),'([^']+)',0x[0-9A-Fa-f]+,(\d+),(\d+),[^,]+,(\d+),"
                         r"(\d+),[^,]+,(\d+),(\d+),(\d+),(-?\d+),(-?\d+),(\d+),", _fetch(dest, "spell_list")):
        sid = int(m.group(1))
        if FIRST_SPELL <= sid <= LAST_SPELL:
            spells[sid] = {"key": m.group(2), "cast": int(m.group(8)) / 1000.0, "recast": int(m.group(9)) / 1000.0,
                           "anim": int(m.group(12))}             # its effect: file 2800 + this (all 939: the call)
    spell_lists, skill_lists = _own_lists(dest)
    out = []
    for m in re.finditer(r"INSERT INTO `mob_pools` VALUES \((\d+),'[^']*','([^']*)',\d+,0x([0-9A-Fa-f]{40}),([-\d,]+)\)",
                         _fetch(dest, "mob_pools")):
        sid = int(m.group(1)) - POOL_BASE
        if sid not in spells:
            continue
        look = bytes.fromhex(m.group(3))
        model = int.from_bytes(look[2:4], "little")
        v = [int(x) for x in m.group(4).split(",")]           # mJob, sJob, cmbSkill, cmbDelay, cmbDmgMult, ...,
        t = {**spells[sid], "spell": sid, "name": display_name(spells[sid]["key"]), "model": model,   # [16] spellList,
             "job": JOBS[v[0]] if v[0] < len(JOBS) else "",                                          # [19] skill_list_id
             "sub": JOBS[v[1]] if 0 < v[1] < len(JOBS) else "",
             "delay": v[3] / 60.0, "damage": v[4] / 100.0,
             "spells": spell_lists.get(v[16], []) if len(v) > 16 else [],
             "skills": skill_lists.get(v[19], []) if len(v) > 19 else []}
        p = S.file_path(root, model + MODEL_FILE) if root and model >= 3000 else None
        t["file"] = str(p) if p and p.exists() else None
        out.append(t)
    out.sort(key=lambda t: t["spell"])
    (dest / "trusts.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _own_lists(dest: Path) -> tuple[dict, dict]:
    """Each Trust's own spells (its list's number -> [[the layer's name for it, the level it casts it from],
    ...]: those the layer knows, the -ra forms as their single ones') and weapon skills (number -> names)."""
    import ffxi_data as D
    keys = {int(m.group(1)): m.group(2) for m in
            re.finditer(r"INSERT INTO `spell_list` VALUES \((\d+),'([^']+)'", _fetch(dest, "spell_list"))}
    known = {_norm(n): n for n in D.SPELLS}
    spells: dict = {}
    for m in re.finditer(r"INSERT INTO `mob_spell_lists` VALUES \('[^']*',(\d+),(\d+),(\d+),(\d+)\)",
                         _fetch(dest, "mob_spell_lists")):
        key = keys.get(int(m.group(2)), "")
        name = known.get(_norm(key)) or known.get(_norm(key.replace("protectra", "protect").replace("shellra", "shell")))
        if name:
            spells.setdefault(int(m.group(1)), []).append([name, int(m.group(3))])
    names = {int(m.group(1)): m.group(2) for m in
             re.finditer(r"INSERT INTO `mob_skills` VALUES \((\d+),\d+,'([^']+)'", _fetch(dest, "mob_skills"))}
    skills: dict = {}
    for m in re.finditer(r"INSERT INTO `mob_skill_lists` VALUES \('[^']*',(\d+),(\d+)\);[ \t]*(?:--[ \t]*([^\r\n]*))?",
                         _fetch(dest, "mob_skill_lists")):
        label = (m.group(3) or "").strip() or display_name(names.get(int(m.group(2)), ""))
        if label:
            skills.setdefault(int(m.group(1)), []).append(label)
    return spells, skills


def load_list(root: Path | None = None) -> list[dict]:
    """The Trusts (build_list's), read from trusts.json, made first if it isn't there."""
    p = A.asset_dir() / "trusts.json"
    try:
        out = json.loads(p.read_text(encoding="utf-8"))
        if out and "delay" in out[0] and "anim" in out[0] and "skills" in out[0] and \
                (root is None or any(t.get("file") for t in out)):
            return out
    except (OSError, ValueError):
        pass
    return build_list(root)


RUN_PACE, WALK_PACE = 380.0, 130.0      # a Trust's usual run and walk, in the hunter's units a second (their median)


def build_model(trust: dict, scale: float) -> dict:
    """A Trust packed for the port (ffxi_model.pack): its own motions as a player's are chosen, at the
    player's scale (the hunter's units per FFXI's), so a Tarutaru is as small beside you as in FFXI."""
    import ffxi_model as FM
    model = FM.load(Path(trust["file"]))
    model["clips"] = FM.character_clips([model["motions"]], [])
    packed = FM.pack(model, scale=scale, origin_ground=True)       # stood as FFXI stands it: by its origin
    for c in packed["clips"]:                                 # a Tarutaru's run kept to a Hume's pace: counted as
        if c["name"] in ("run", "brun") and 0 < c["speed"] < 250:   # covering more (as the player's own)
            c["speed"] *= 1.8
    # a pace measured beyond any FFXI's (a Moogle hovering, a plant, a run or walk its feet don't show): the usual
    # pace, so the legs go at a likely speed, not whirling or crawling (2026-10-12: 11 of 109 Trusts so)
    run = next((c["speed"] for c in packed["clips"] if c["name"] == "run"), 0.0)
    if not 250.0 <= run <= 700.0:
        run = RUN_PACE
    for c in packed["clips"]:
        if c["name"] in ("run", "brun"):
            c["speed"] = run
        elif c["name"] in ("wlk", "bwlk") and not (60.0 <= c["speed"] <= min(250.0, 0.6 * run)):
            c["speed"] = WALK_PACE
    rest = FM.skin_corners(packed["verts"], FM.joint_world(model["skeletons"][0]))
    place = packed["place"]
    packed["top"] = float((rest @ place[:, :3].T + place[:, 3])[:, 1].max())
    return packed


# ------------------------------------------------------------------------------------------------
# The party: who is called, where each stands
# ------------------------------------------------------------------------------------------------
# How each job fights beside you: who cures you, and below what share of your HP (a subjob's healing a
# little later); who casts at the foe; who keeps back from it; each job's weapon skill (FFXI's, a typical
# one of its weapon)
# A Trust's own last-stand ability, as FFXI's (River's notes, from bg-wiki and ffxiclopedia): used when its HP falls
# under `below` (here your HP's: the monsters strike only you), at most every SIGNATURE_RECAST seconds; its look (its
# file's routine `look`, played on it) lasts `lasts` seconds (0: until it ends so), or ends as it uses `ends_with`
# (`forced`: its next weapon skill is that one, at `tp`); `aura`: an effect on you meanwhile (August's Daybreak: physical
# damage taken -50%). Shantotto II's orbs and Arciela's crystals are no such thing: they last from its coming
# (App._fx_lasting).
SIGNATURE = {
    "Teodor": {"ability": "Start from Scratch", "below": 0.50, "lasts": 0.0, "ends_with": "Hemocladis",
               "forced": True, "tp": 2000.0, "look": "efon"},
    "August": {"ability": "Daybreak", "below": 0.66, "lasts": 90.0, "ends_with": "No Quarter", "look": "eff1",
               "aura": ("defn", 0.5)},
    "Mumor II": {"ability": "Firesday Night Fever", "below": 0.50, "lasts": 240.0, "ends_with": "Final Eternal Heart",
                 "look": "efon"},
    "Morimar": {"ability": "Vehement Resolution", "below": 0.50, "lasts": 0.0, "ends_with": "12 Blades of Remorse",
                "forced": True, "look": "efon"},
}
SIGNATURE_RECAST = 180.0                 # (August's Daybreak's, about 3 minutes; the others' alike)
HEALERS = {"WHM": 0.75, "RDM": 0.55, "SCH": 0.65, "PLD": 0.45, "RUN": 0.4, "BLU": 0.45, "GEO": 0.5}
NUKERS = {"BLM", "RDM", "SCH", "GEO", "DRK"}
BACK_ROW = {"WHM", "BLM", "SCH", "GEO", "SMN", "BRD", "RNG", "COR"}
WEAPON_SKILL = {"WAR": "Raging Rush", "MNK": "Asuran Fists", "THF": "Evisceration", "PLD": "Savage Blade",
                "DRK": "Guillotine", "BST": "Decimation", "SAM": "Tachi: Gekko", "NIN": "Blade: Jin",
                "DRG": "Drakesbane", "RNG": "Sidewinder", "DNC": "Pyrrhic Kleos", "BLU": "Chant du Cygne",
                "COR": "Savage Blade", "PUP": "Victory Smite", "RUN": "Resolution", "BRD": "Mordant Rime",
                "WHM": "Hexa Strike", "BLM": "Spiral Hell", "RDM": "Savage Blade", "SMN": "Garland of Bliss",
                "SCH": "Omniscience", "GEO": "Exudation"}


# Pets seen: FFXI's monster model of each (LandSandBoat's pet_list and mob_pools: the pool's model number),
# the model's file its number + 1300
MONSTER_FILE = 1300
PET_MODELS = {"Carbuncle": 16, "Fenrir": 17, "Ifrit": 18, "Titan": 19, "Leviathan": 20, "Garuda": 21, "Shiva": 22,
              "Ramuh": 23, "Wyvern": 24, "Diabolos": 25}


def _keeps_back(trust: dict) -> bool:
    return trust["job"] in BACK_ROW


def _max_mp(trust: dict, level: int) -> float:
    """A Trust's MP: its jobs' (the main's full, the subjob's a third), as a player's grows with level."""
    import ffxi_data as D
    full = D.JOBS.get(trust["job"], {}).get("mp", 0) + D.JOBS.get(trust["sub"], {}).get("mp", 0) / 3
    return full * max(1, min(level, D.LEVEL_CAP)) / D.LEVEL_CAP


def _cast_motion(school: str) -> str:
    import ffxi_model as FM
    return FM.CAST_MOTIONS.get(school, "")


def _known(trust: dict, level: int) -> list[str]:
    """The spells it casts at that level: its own list's (LandSandBoat's), else its jobs' (the subjob's at
    half the level)."""
    import ffxi_data as D
    if trust.get("spells"):
        return [n for n, lv in trust["spells"] if lv <= level]
    out = []
    for job, lv_scale in ((trust["job"], 1), (trust["sub"], 2)):
        out += [n for lv, n in D.JOBS.get(job, {}).get("spells", []) if lv * lv_scale <= level]
    return out


def _best(trust: dict, level: int, kind: str, mp: float) -> str | None:
    """The strongest of the spells of that kind (heal, damage) it knows at that level and has MP for."""
    import ffxi_data as D
    best, power = None, 0.0
    for n in _known(trust, level):
        sp = D.SPELLS.get(n)
        if not sp or sp["mp"] > mp:
            continue
        for fx in sp["fx"]:
            if fx[0] == kind and fx[1] > power:
                best, power = n, fx[1]
    return best


# what a Trust casts on you (a party's: the self-only ones, Blink, Stoneskin, an Enspell, kept for itself), and
# on the foe (not those that stop the fight: sleep, bind, stun)
ON_YOU = ("Protect", "Shell", "Haste", "Refresh", "Regen", "Auspice", "Valor Minuet", "Sword Madrigal",
          "Blade Madrigal", "Advancing March", "Victory March", "Mage's Ballad", "Army's Paeon", "Knight's Minne",
          "Indi-")
ON_FOE = ("Dia", "Bio", "Slow", "Paralyze", "Blind", "Poison", "Gravity", "Battlefield Elegy", "Carnage Elegy",
          "Foe Requiem", "Burn", "Frost", "Shock", "Drown", "Pyrohelix", "Cryohelix", "Ionohelix", "Noctohelix",
          "Luminohelix", "Kurayami", "Hojo", "Dokumori", "Indi-Frailty", "Indi-Poison", "Indi-Slow")


def _enhance(trust: dict, level: int, mp: float, on_foe: bool, engine) -> str | None:
    """A buff for you (on_foe: a debuff for the foe) it knows, has MP for, and whose kind isn't on already:
    the strongest of each kind, in its list's order."""
    import ffxi_data as D
    from ffxi_engine import spell_family
    kind, allowed = ("debuff", ON_FOE) if on_foe else ("buff", ON_YOU)
    best: dict = {}
    for n in _known(trust, level):
        sp = D.SPELLS.get(n)
        if not sp or sp["mp"] > mp or not n.startswith(allowed):
            continue
        power = max((abs(f[2]) for f in sp["fx"] if f[0] == kind), default=None)
        if power is None:
            continue
        fam = spell_family(n)
        if fam not in best or power > best[fam][1]:
            best[fam] = (n, power)
    for fam, (n, _p) in best.items():
        if not engine.family_active(fam, on_foe):
            return n
    return None


@dataclass
class Member:
    trust: dict
    slot: int                            # the port's model slot, 1-5
    pm: object = None                    # port_model.PortModel(slot), once its model is sent
    ready: bool = False
    failed: str = ""
    top: float = 150.0                   # its height, in the game's units
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    placed: bool = False
    engaged: bool = False
    next_swing: float = 0.0
    next_spell: float = 0.0
    tp: float = 0.0
    mp: float = 0.0
    max_mp: float = 0.0
    casting: tuple | None = None         # (spell, when it goes off, on the target or on you)
    arrived: bool = False                # its coming shown (the call's effect where it appears)
    pet: bool = False                    # a summoner's avatar, a beast, a wyvern: the engine's pet, seen
    swing_n: int = 0
    sig_on: float = 0.0                  # its last-stand ability (SIGNATURE) in effect since then (0: not)
    sig_ready: float = 0.0               # and when it may use it again


@dataclass
class Party:
    """The Trusts called, following the hunter along the way it went: each a little further behind (the
    first nearest), to one side and the other in turn, at a run when far behind, at a walk near, standing
    turned as you are once there. Its feet at the height the hunter's were along the way (the port then
    sets it on the ground it finds under it); called with nothing walked yet, behind you."""
    mem: object                          # port_bridge's (set_companion)
    scale: float                         # the hunter's units per FFXI's
    say: object = print                  # a line for the log: say(text, kind)
    members: list = field(default_factory=list)
    trail: deque = field(default_factory=lambda: deque(maxlen=400))
    last_hunter: tuple | None = None

    def called(self, name: str) -> Member | None:
        return next((m for m in self.members if m.trust["name"] == name), None)

    level: int = 1                       # the Trusts' level: yours (Party.fight sets it)
    _fought: float = 0.0                 # the last fight's tick

    def summon_pet(self, name: str, root) -> bool:
        """Your pet seen beside you (the engine's: an avatar, a wyvern), its model FFXI's (PET_MODELS: monster
        model m in file m + 1300), following and fighting as a Trust does (its blows the engine's pet's)."""
        import ffxi_sound as S
        model = PET_MODELS.get(name)
        p = S.file_path(root, model + MONSTER_FILE) if model is not None else None
        if not p or not p.exists() or self.called(name):
            return False
        if len(self.members) >= MAX_CALLED:
            self.say(f"There is no room for {name} to appear.", "warn")
            return False
        used = {m.slot for m in self.members}
        m = Member({"name": name, "file": str(p), "job": "", "sub": "", "delay": 3.5, "damage": 1.0},
                   min(s for s in range(1, MAX_CALLED + 1) if s not in used), pet=True)
        self.members.append(m)
        threading.Thread(target=self._build, args=(m,), daemon=True).start()
        return True

    def summon(self, trust: dict) -> bool:
        if self.called(trust["name"]):
            self.say(f"{trust['name']} is already in the party.", "warn")
            return False
        if len(self.members) >= MAX_CALLED:
            self.say("You cannot call any more alter egos.", "warn")
            return False
        if not trust.get("file"):
            self.say(f"{trust['name']}'s model isn't in this FFXI installation.", "warn")
            return False
        used = {m.slot for m in self.members}
        m = Member(trust, min(s for s in range(1, MAX_CALLED + 1) if s not in used))
        m.max_mp = m.mp = _max_mp(trust, self.level)
        self.members.append(m)
        threading.Thread(target=self._build, args=(m,), daemon=True).start()
        return True

    def _build(self, m: Member) -> None:
        try:
            from port_model import PortModel
            packed = build_model(m.trust, self.scale)
            m.top = packed["top"]
            if m not in self.members:                         # released while it was being made
                return
            m.pm = m.pm or PortModel(m.slot)
            m.pm.send(packed)
            m.ready = True
            if not m.pet:
                self.say(f"{m.trust['name']} joins the party.", "system")
        except Exception as e:                                # noqa: BLE001
            m.failed = str(e)
            self.say(f"{m.trust['name']} couldn't be called: {e}", "warn")
            if m in self.members:
                self.members.remove(m)

    def release(self, name: str | None = None, pets: bool = False) -> None:
        """One Trust sent away, or (None) all of them (pets: the pet too, silently: the engine says it)."""
        gone = [m for m in self.members if (name is None or m.trust["name"] == name) and (pets or not m.pet)]
        for m in gone:
            if m.sig_on:                                      # its last stand's aura on you goes with it
                self._sig_end(m, SIGNATURE[m.trust["name"]], getattr(self, "_engine", None), [], 0.0)
            self.members.remove(m)
            self.mem.set_companion(m.slot, False)
            if not m.pet:
                self.say(f"{m.trust['name']} has left the party.", "system")
        if name and not gone:
            self.say(f"{name} isn't in the party.", "warn")

    def clear(self) -> None:
        for s in range(1, MAX_CALLED + 1):
            self.mem.set_companion(s, False)

    # --- each tick ---
    def step(self, dt: float, hunter, target: tuple | None = None) -> None:
        """hunter: its world matrix (3x4 rows), or None (no scene: none of them shown); target: the
        monster fought (its feet x y z, its height), the engaged standing about it."""
        if hunter is None:
            for m in self.members:
                self.mem.set_companion(m.slot, False)
            return
        hx, hy, hz = float(hunter[0][3]), float(hunter[1][3]), float(hunter[2][3])
        fyaw = math.atan2(float(hunter[0][2]), float(hunter[2][2]))   # the way it faces (+Z turned)
        if self.last_hunter and math.dist(self.last_hunter, (hx, hy, hz)) > 1500.0:   # elsewhere: a new area
            self.trail.clear()
            for m in self.members:
                m.placed = False
        self.last_hunter = (hx, hy, hz)
        if not self.trail or math.dist(self.trail[-1], (hx, hy, hz)) > 20.0:
            self.trail.append((hx, hy, hz))
        for i, m in enumerate(self.members):
            if not m.ready:
                continue
            fighting = m.engaged and target is not None
            if fighting:
                tx, ty, tz = self._battle_spot(i, m, (hx, hy, hz), target)
            else:
                tx, ty, tz = self._spot(i, (hx, hy, hz), fyaw)
            if not m.placed:
                m.x, m.y, m.z, m.yaw, m.placed = tx, ty, tz, fyaw, True
            dx, dz = tx - m.x, tz - m.z
            dist = math.hypot(dx, dz)
            if dist > 1500.0:
                m.x, m.z = tx, tz
            elif dist > 12.0:
                speed = 470.0 if dist > 250.0 else 360.0 if dist > 70.0 else 150.0
                step = min(dist, speed * dt)
                m.x += dx / dist * step
                m.z += dz / dist * step
                m.yaw = math.atan2(dx, dz)
            else:                                             # there: turned as you are, or to the foe
                face = math.atan2(target[0] - m.x, target[2] - m.z) if fighting else fyaw
                m.yaw += math.remainder(face - m.yaw, math.tau) * min(1.0, dt * (6.0 if fighting else 3.0))
            m.y = ty                                          # (the port sets it on the ground it finds)
            self.mem.set_companion(m.slot, True, m.x, m.y, m.z, m.yaw, battle=m.engaged,
                                   shadow=0.24 * m.top, shadow_dark=0.55)

    def _battle_spot(self, i: int, m: Member, here: tuple, target: tuple) -> tuple:
        """Where the i-th stands fighting: about the target on your side of it, the ones who fight hand to
        hand at arm's length (the bigger the foe, the further), the casters further back."""
        tx, ty, tz, th = target
        base = math.atan2(here[0] - tx, here[2] - tz)        # from the target toward you
        k = (i // 2 + 1) * (1 if i % 2 == 0 else -1)
        angle = base + 0.55 * k
        r = (0.5 * th + 140.0) if not _keeps_back(m.trust) else (0.5 * th + 520.0)
        x, z = tx + math.sin(angle) * r, tz + math.cos(angle) * r
        return x, self._ground_y(x, z, here[1]), z

    def _ground_y(self, x: float, z: float, fallback: float) -> float:
        """The ground's height at x, z, as the hunter's own steps tell it: its trail's nearest point within reach,
        else its own height. Not the monster's: it leaps and flies, and the Trusts rose into the air with it
        (2026-10-12)."""
        best, y = 400.0 * 400.0, fallback
        for p in itertools.islice(reversed(self.trail), 150):
            d = (p[0] - x) ** 2 + (p[2] - z) ** 2
            if d < best:
                best, y = d, p[1]
        return y

    def in_reach(self, m: Member, target: tuple) -> bool:
        tx, _, tz, th = target
        return math.hypot(m.x - tx, m.z - tz) <= 0.5 * th + 200.0

    # --- fighting, each tick ---
    def fight(self, now: float, f: dict) -> tuple:
        """The Trusts' turn: engaged with you, swinging at your target on their delay, their weapon skills at
        1000 TP, their job's spells (the healers curing you when you're hurt, the others' magic at the
        foe), FFXI's words for each. f: engaged (yours), target (feet x y z, height; None: none), m (its HP;
        None: none), floor (the least it's left at: the game deals the last blow), p and p_max (your HP),
        level, avg_hit (your blows' usual damage: the scale of theirs), me, foe (its name). Returns the
        monster's HP, yours, and what to show: ("strike", critical), ("cast", name, slot) as a Trust begins
        a spell and ("spell", name, slot) as it goes off."""
        import random
        import ffxi_data as D
        self.level = f["level"]
        dt = min(0.25, now - self._fought) if self._fought else 0.0
        self._fought = now
        m_hp, p_hp, shown = f["m"], f["p"], []
        target, foe, me = f["target"], f["foe"], f["me"]
        engine = f.get("e")                                   # the layer's: buffs, debuffs, skillchains
        self._engine = engine
        lf = D.level_factor(self.level)
        for mb in self.members:
            if not mb.ready:
                continue
            t = mb.trust
            name = t["name"]
            full = _max_mp(t, self.level)                     # a level changed: the same share of more
            if full != mb.max_mp:
                mb.mp = mb.mp / mb.max_mp * full if mb.max_mp else full
                mb.max_mp = full
            mb.mp = min(mb.max_mp, mb.mp + mb.max_mp * dt * (0.004 if mb.engaged else 0.015))   # back between fights
            want = f["engaged"] and target is not None and m_hp is not None and m_hp > 0   # (not at the fallen)
            if want != mb.engaged:
                mb.engaged = want
                mb.next_swing = now + 1.5 + 0.3 * mb.slot
                mb.next_spell = now + 2.0 + 1.5 * mb.slot
                if not want:
                    mb.casting = None
                    mb.tp = 0.0
            sig = SIGNATURE.get(name)
            if sig and mb.sig_on and sig["lasts"] and now >= mb.sig_on + sig["lasts"]:
                self._sig_end(mb, sig, engine, shown, now)            # worn off
            # a spell going off
            if mb.casting and now >= mb.casting[1]:
                spell, _, on_foe = mb.casting
                mb.casting = None
                sp = D.SPELLS.get(spell, {})
                mb.mp = max(0.0, mb.mp - sp.get("mp", 0))
                school = sp.get("school", "")
                cast = _cast_motion(school)
                if cast and mb.pm:
                    mb.pm.request(cast + "2")
                for fx in sp.get("fx", []):
                    if fx[0] == "heal" and p_hp is not None and f["p_max"]:
                        hp = int(fx[1] * D.JOBS.get(t["job"], {}).get("heal_mult", 1.0) * (0.6 + 0.4 * lf))
                        new = min(f["p_max"], p_hp + max(1, hp))
                        self.say(f"{name} casts {spell}. {me} recovers {new - p_hp} HP.", "heal")
                        p_hp = new
                    elif fx[0] in ("damage", "drain") and on_foe and m_hp is not None and m_hp > 0:
                        dmg = int(f["avg_hit"] * fx[1] * D.JOBS.get(t["job"], {}).get("nuke_mult", 1.0) * 0.6
                                  * random.uniform(0.9, 1.1))
                        self.say(f"{name} casts {spell}. The {foe} takes {dmg} points of damage.", "magic")
                        m_hp = max(f["floor"], m_hp - dmg)
                if engine is not None and any(fx[0] in ("buff", "debuff") for fx in sp.get("fx", [])) and \
                        not any(fx[0] in ("damage", "drain", "heal") for fx in sp.get("fx", [])):
                    engine.cast_by(name, spell, now, m_hp, on_foe)      # yours, or the foe's
                shown.append(("spell", spell, mb.slot))
                continue
            if mb.casting:
                continue
            if mb.pet:                                        # a pet: its blows seen, the engine's pet dealing them
                if mb.engaged and now >= mb.next_swing and self.in_reach(mb, target):
                    mb.next_swing = now + max(2.0, t.get("delay", 3.5)) * random.uniform(0.9, 1.1)
                    mb.swing_n += 1
                    if mb.pm:
                        mb.pm.request(f"at{mb.swing_n % 3}")
                    shown.append(("strike", False))
                continue
            # its last stand, you being hard pressed (SIGNATURE)
            if sig and mb.engaged and not mb.sig_on and now >= mb.sig_ready and p_hp is not None and f["p_max"] \
                    and p_hp < f["p_max"] * sig["below"]:
                self._sig_begin(mb, sig, engine, shown, now, me)
                continue
            # a cure for you, when you're hurt (whether fighting or not)
            if p_hp is not None and f["p_max"]:
                need = HEALERS.get(t["job"]) or HEALERS.get(t["sub"], 0.0) * 0.8
                if need and p_hp < f["p_max"] * need and now >= mb.next_spell:
                    cure = _best(t, self.level, "heal", mb.mp)
                    if cure:
                        shown.append(self._begin(mb, cure, now, False, me))
                        continue
            # a buff for you (Protect, Shell, Haste, Refresh, a song...), fighting or not, again once it wears off
            if engine is not None and now >= mb.next_spell:
                buff = _enhance(t, self.level, mb.mp, False, engine)
                if buff:
                    shown.append(self._begin(mb, buff, now, False, me))
                    continue
            if not mb.engaged:
                continue
            # a debuff on the foe (Dia, Slow, Paralyze, Poison...), again once it wears off
            if engine is not None and now >= mb.next_spell:
                debuff = _enhance(t, self.level, mb.mp, True, engine)
                if debuff:
                    shown.append(self._begin(mb, debuff, now, True, foe))
                    continue
            # magic at the foe
            if now >= mb.next_spell and t["job"] in NUKERS:
                nuke = _best(t, self.level, "damage", mb.mp)
                if nuke:
                    shown.append(self._begin(mb, nuke, now, True, foe))
                    continue
                mb.next_spell = now + 6.0
            # a blow, in reach and in its time
            if now >= mb.next_swing and self.in_reach(mb, target):
                mb.next_swing = now + max(2.0, t.get("delay", 4.0)) * random.uniform(0.95, 1.1)
                last_stand = bool(sig and mb.sig_on)
                if mb.tp >= (sig.get("tp", 1000.0) if last_stand else 1000.0):
                    # its own weapon skills (those that chain first), else its job's usual one; in its last stand,
                    # the one that ends it (forced: that one; else it among the rest)
                    own = [s for s in t.get("skills", []) if s in D.WEAPON_SKILLS] or t.get("skills", [])
                    ws = random.choice(own) if own else WEAPON_SKILL.get(t["job"], "Savage Blade")
                    if last_stand and (sig.get("forced") or (sig["ends_with"] not in own and random.random() < 0.5)):
                        ws = sig["ends_with"]
                    dmg = int(f["avg_hit"] * 2.6 * max(0.4, t.get("damage", 1.0)) * random.uniform(0.85, 1.15)
                              * (1.0 + 0.5 * (mb.tp - 1000.0) / 1000.0 if mb.tp > 1000.0 else 1.0))
                    mb.tp = 0.0
                    if engine is not None:
                        m_hp = engine.ws_by(name, ws, dmg, now, m_hp)        # its skillchains with yours
                    else:
                        self.say(f"{name} uses {ws}. The {foe} takes {dmg} points of damage.", "ws")
                        m_hp = max(f["floor"], m_hp - dmg)
                    if mb.pm:
                        mb.pm.request(f"at{mb.swing_n % 3}")
                    shown.append(("ws", ws, mb.slot))
                    shown.append(("strike", True))
                    if last_stand and ws == sig["ends_with"]:
                        self._sig_end(mb, sig, engine, shown, now)
                    continue
                mb.swing_n += 1
                if mb.pm:
                    mb.pm.request(f"at{mb.swing_n % 3}")
                if random.random() > 0.9:
                    self.say(f"{name} misses the {foe}.", "info")
                    continue
                critical = random.random() < 0.06
                dmg = f["avg_hit"] * 0.7 * max(0.3, t.get("damage", 1.0)) * random.uniform(0.85, 1.15)
                dmg = max(1, int(dmg * (1.5 if critical else 1.0)))
                if critical:
                    self.say(f"{name} scores a critical hit!", "skillchain")
                self.say(f"{name} hits the {foe} for {dmg} points of damage.", "damage")
                mb.tp = min(3000.0, mb.tp + 60.0 + 40.0 * (t.get("delay", 4.0) / 4.0))
                m_hp = max(f["floor"], m_hp - dmg)
                shown.append(("strike", critical))
        return m_hp, p_hp, shown

    def _sig_begin(self, mb: Member, sig: dict, engine, shown: list, now: float, me: str) -> None:
        """Its last stand: said, its look on it (shown: the layer plays it), its aura on you."""
        name = mb.trust["name"]
        mb.sig_on, mb.sig_ready = now, now + SIGNATURE_RECAST
        self.say(f"{name} uses {sig['ability']}.", "ws")
        aura = sig.get("aura")
        if aura and engine is not None:
            engine.buffs[(sig["ability"], aura[0])] = {"power": aura[1], "until": now + (sig["lasts"] or 600.0)}
            self.say(f"{me} gains the effect of {sig['ability']}.", "heal")
        shown.append(("signature", True, mb.slot))

    def _sig_end(self, mb: Member, sig: dict, engine, shown: list, now: float) -> None:
        """Its last stand over (worn off, its weapon skill used, it gone): its look and its aura with it."""
        mb.sig_on = 0.0
        aura = sig.get("aura")
        if aura and engine is not None and engine.buffs.pop((sig["ability"], aura[0]), None) is not None:
            self.say(f"{engine.me}'s {sig['ability']} effect wears off.", "info")
        shown.append(("signature", False, mb.slot))

    def _begin(self, mb: Member, spell: str, now: float, on_foe: bool, at: str) -> tuple:
        import ffxi_data as D
        sp = D.SPELLS[spell]
        mb.casting = (spell, now + max(1.0, sp["cast"]), on_foe)
        mb.next_spell = now + max(sp["cast"] + sp["recast"], 9.0 if on_foe else 4.0) + (3.0 if on_foe else 0.0)
        cast = _cast_motion(sp["school"])
        if cast and mb.pm:
            mb.pm.request(cast + "0", cast + "1")
        self.say(f"{mb.trust['name']} starts casting {spell} on {('the ' + at) if on_foe else at}.", "magic")
        return ("cast", spell, mb.slot)

    def nameplates(self) -> tuple:
        """Each Trust's name where the port places it above its head: ((name, x, y), ...)."""
        out = []
        for m in self.members:
            at = self.mem.companion_name(m.slot) if m.ready and hasattr(self.mem, "companion_name") else None
            if at:
                out.append((m.trust["name"], round(at[0], 3), round(at[1], 3)))
        return tuple(out)

    def party_rows(self) -> tuple:
        """The party list's rows: each Trust's name, HP (they're never hurt: the monsters see only you),
        MP (None: a job without magic), TP."""
        rows = []
        for m in self.members:
            if m.ready and not m.pet:
                rows.append((m.trust["name"], 1.0, (m.mp / m.max_mp) if m.max_mp else None, int(m.tp)))
        return tuple(rows)

    def _spot(self, i: int, here: tuple, fyaw: float) -> tuple:
        """Where the i-th called stands: that far back along the way the hunter came, to one side."""
        back = 140.0 + 80.0 * i
        side = 40.0 if i % 2 == 0 else -40.0
        x, y, z = here
        left = back
        prev = here
        for p in reversed(self.trail):
            seg = math.dist((prev[0], prev[2]), (p[0], p[2]))
            if seg >= left and seg > 0:
                f = left / seg
                x, y, z = prev[0] + (p[0] - prev[0]) * f, prev[1] + (p[1] - prev[1]) * f, prev[2] + (p[2] - prev[2]) * f
                left = 0
                break
            left -= seg
            prev = p
            x, y, z = p
        if left > 0:                                          # not walked that far: behind, as it faces
            x -= math.sin(fyaw) * left
            z -= math.cos(fyaw) * left
        ax, az = x - here[0], z - here[2]                     # to the side of the way back
        n = math.hypot(ax, az) or 1.0
        return x + (-az / n) * side, y, z + (ax / n) * side


def main() -> int:
    import argparse
    import ffxi_model as FM
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["list"])
    ap.add_argument("--ffxi", help="FFXI's folder (else the launcher's)")
    a = ap.parse_args()
    root = FM.ffxi_root(a.ffxi)
    trusts = build_list(root)
    for t in trusts:
        print(f"{t['spell']:5} {t['name']:22} {t['job']:>3}/{t['sub'] or '-':3} model {t['model']:5} "
              f"cast {t['cast']:g}s  {t['file'] or '(no model file)'}")
    print(f"{len(trusts)} Trusts, {sum(1 for t in trusts if t['file'])} with their models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
