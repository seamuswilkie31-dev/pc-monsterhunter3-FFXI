"""Your FFXI equipment: what you hold, what you wear, what it does and how it looks; monsters' drops.

The items are FFXI's own, read from your installation (ffxi_items.py: the local asset folder's items.json).
The engine keeps what you hold and wear (Engine.inventory, Engine.equipment, saved with your levels); this
works out who may wear what (its level, your job, your race), the stats it all adds up to (Engine.set_gear),
how the character looks in it (each worn piece's look in its slot), and what a defeated monster drops.
"""
from __future__ import annotations

import random

import ffxi_items as I

# the slots, as the Equipment menu lists them (FFXI's order), and their names
SLOTS = ["main", "sub", "ranged", "ammo", "head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2",
         "back", "waist", "legs", "feet"]
SLOT_LABEL = {"main": "Main", "sub": "Sub", "ranged": "Range", "ammo": "Ammo", "head": "Head", "neck": "Neck",
              "ear1": "Ear 1", "ear2": "Ear 2", "body": "Body", "hands": "Hands", "ring1": "Ring 1",
              "ring2": "Ring 2", "back": "Back", "waist": "Waist", "legs": "Legs", "feet": "Feet"}
LOOK_SLOTS = ("head", "body", "hands", "legs", "feet", "main", "sub")     # the ones the character shows
# what a new adventurer is handed, by slot: the first of these each job and race may wear
STARTER = {
    "head": ["Bronze Cap", "Cotton Headband", "Leather Bandana"],
    "body": ["Bronze Harness", "Robe", "Cotton Doublet", "Leather Vest"],
    "hands": ["Bronze Mittens", "Cuffs", "Cotton Gloves", "Leather Gloves"],
    "legs": ["Bronze Subligar", "Slops", "Cotton Brais", "Leather Trousers"],
    "feet": ["Bronze Leggings", "Ash Clogs", "Solea", "Leather Highboots"],
    "main": ["Bronze Sword", "Onion Sword", "Bronze Dagger", "Ash Club", "Ash Staff"],
    "sub": ["Lauan Shield", "Ash Shield"],
}
DROP_CHANCE, BIG_DROP_CHANCE = 0.35, 1.0    # an ordinary monster's, a large one's (and it may drop two)


class Gear:
    def __init__(self, engine, race: str, rng: random.Random | None = None):
        self.e, self.race = engine, race
        self.items = I.load_items()
        self.rng = rng or random.Random()
        self.by_name = {}
        for it in sorted(self.items.values(), key=lambda it: it["id"]):
            self.by_name.setdefault(it["name"].lower(), it)

    def ready(self) -> bool:
        return bool(self.items)

    # --- who may wear what -------------------------------------------------------------------------
    def item(self, iid: int) -> dict | None:
        return self.items.get(int(iid))

    def slots_of(self, it: dict) -> list[str]:
        names = I.slot_names(it["slots"])
        # an ear or a ring fits either of its pair
        for a, b in (("ear1", "ear2"), ("ring1", "ring2")):
            if a in names or b in names:
                names = [n for n in names if n not in (a, b)] + [a, b]
        return [s for s in SLOTS if s in names]

    gm = False                                           # a GM wears anything (the launcher's GM)

    def why_not(self, it: dict, slot: str | None = None) -> str:
        """Why you can't wear it now ("" if you can)."""
        if self.gm:
            return ""
        e = self.e
        bit = I.RACE_BITS.get(self.race, 0)
        if bit and it["races"] and not it["races"] & bit:
            return "Your race can't wear it."
        jobs = I.job_list(it["jobs"])
        if jobs and e.job not in jobs:
            return f"{e.job} can't use it."
        if it["level"] > e.level:
            return f"Requires level {it['level']}."
        if slot and slot not in self.slots_of(it):
            return "It doesn't go there."
        if slot == "sub" and "main" in self.slots_of(it) and not it.get("shield"):
            if e.job not in ("NIN", "DNC", "THF", "BLU") or e.level < 5:
                return f"{e.job} can't dual wield."
        return ""

    def usable(self, it: dict, slot: str | None = None) -> bool:
        return not self.why_not(it, slot)

    # --- holding and wearing ---------------------------------------------------------------------
    def give(self, iid: int) -> dict | None:
        it = self.item(iid)
        if it:
            self.e.inventory.append(int(iid))
            self.e.dirty = True
        return it

    def equip(self, slot: str, iid: int | None) -> str:
        """Wear it in that slot (None: take it off). Returns what went wrong ("" if nothing)."""
        e = self.e
        if iid is None:
            e.equipment.pop(slot, None)
        else:
            it = self.item(iid)
            if not it or int(iid) not in e.inventory:
                return "You don't have that."
            why = self.why_not(it, slot)
            if why:
                return why
            worn_elsewhere = [s for s, i in e.equipment.items() if i == int(iid) and s != slot]
            if worn_elsewhere and e.inventory.count(int(iid)) <= len(worn_elsewhere):
                e.equipment.pop(worn_elsewhere[0])                      # the one you have moves
            e.equipment[slot] = int(iid)
        e.dirty = True
        self.apply()
        return ""

    def drop(self, iid: int) -> None:
        e = self.e
        if int(iid) in e.inventory:
            e.inventory.remove(int(iid))
            held = e.inventory.count(int(iid))
            worn = [s for s, i in e.equipment.items() if i == int(iid)]
            for s in worn[held:]:
                e.equipment.pop(s)
            e.dirty = True
            self.apply()

    def check_worn(self) -> list[str]:
        """Take off what you can no longer wear (another job, a lower level): the slots' labels."""
        off = []
        for slot, iid in list(self.e.equipment.items()):
            it = self.item(iid)
            if not it or not self.usable(it, slot) or iid not in self.e.inventory:
                self.e.equipment.pop(slot)
                off.append(SLOT_LABEL.get(slot, slot))
        if off:
            self.e.dirty = True
        return off

    def stats(self) -> dict:
        total = {}
        for slot, iid in self.e.equipment.items():
            it = self.item(iid)
            if it:
                for k, v in it["stats"].items():
                    total[k] = total.get(k, 0) + v
        return total

    def apply(self) -> None:
        self.e.set_gear(self.stats())

    def look(self, base: dict) -> dict:
        """The character's look in this gear: each worn piece's in its slot, the creator's elsewhere."""
        out = dict(base)
        for slot in LOOK_SLOTS:
            it = self.item(self.e.equipment.get(slot, -1)) if slot in self.e.equipment else None
            if it and it.get("model") is not None:
                out[slot] = int(it["model"])
        return out

    def starter_kit(self) -> list[str]:
        """A new adventurer's gear: per slot the first of STARTER this job and race may wear, given and worn."""
        got = []
        for slot, names in STARTER.items():
            for n in names:
                it = self.by_name.get(n.lower())
                if it and self.usable(it, slot):
                    self.give(it["id"])
                    self.e.equipment[slot] = it["id"]
                    got.append(it["name"])
                    break
        self.apply()
        return got

    # --- what monsters drop ----------------------------------------------------------------------
    def roll_drop(self, mob_level: int, big: bool = False) -> list[dict]:
        """What a defeated monster drops: FFXI's ordinary gear (no Rare, Ex, event or GM pieces) from its
        level to three above, mostly what your job and race can wear (sooner or later); a large monster
        always drops, maybe twice."""
        drops = []
        rolls = 2 if big else 1
        for _ in range(rolls):
            if self.rng.random() > (BIG_DROP_CHANCE if big else DROP_CHANCE):
                continue
            lo, hi = max(1, mob_level), min(99, mob_level + 3)
            pool = [it for it in self.items.values()
                    if lo <= it["level"] <= hi and self.slots_of(it) and it.get("model") is not None
                    and it["stats"] and "+1" not in it["name"] and not it.get("flags", 0) & I.SPECIAL_FLAGS]
            bit = I.RACE_BITS.get(self.race, 0)
            pool = [it for it in pool if not bit or not it["races"] or it["races"] & bit]
            mine = [it for it in pool if self.e.job in I.job_list(it["jobs"])]
            choice = mine if mine and self.rng.random() < 0.75 else pool
            if choice:
                drops.append(self.rng.choice(choice))
        return drops

    def describe(self, it: dict) -> str:
        jobs = I.job_list(it["jobs"])
        jobs_text = "All Jobs" if len(jobs) >= 20 else " ".join(jobs)
        slots = ", ".join(SLOT_LABEL[s] for s in self.slots_of(it) if s not in ("ear2", "ring2"))
        head = f"[{slots}]"
        if it.get("damage"):
            head += f" DMG:{it['damage']} Delay:{it['delay']}"
        return f"{it['name']}\n{head}\n{it['desc']}\nLv.{it['level']} {jobs_text}"
