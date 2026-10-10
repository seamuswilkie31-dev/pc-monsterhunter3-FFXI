# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "dolphin-memory-engine>=1.3",
#     "pynput>=1.7",
#     "pillow>=10",
#     "numpy>=1.26",
#     "sounddevice>=0.4",
#     "pycaw>=20240210",
# ]
# ///
"""FFXI's combat for Monster Hunter Tri (Wii): the native PC port, or Dolphin.

FFXI's 22 jobs with a support job, levels and EXP (cap 75), weapon skills, skillchains and magic bursts,
spells and job abilities with recasts, status effects, pets, /check; the FFXI interface drawn in the
game's window, a command menu on the pad, a macro bar on the keyboard. It reads and writes the game's
memory while you play; the rules are in ffxi_engine.py and ffxi_data.py.

    uv run ffxi_mh3.py                    # the port, run with MH3_FFXI=1 MH3_HIDE_HUD=1
    uv run ffxi_mh3.py --sim              # a fake fight, no game needed
    uv run ffxi_mh3.py --job BLM --sub RDM --level 40      # jobs and a level, for testing
    uv run ffxi_mh3.py --backend dolphin  # Dolphin running MH Tri (finding addresses, comparing)
    uv run ffxi_mh3.py --selftest         # check the rules

Levels, EXP and jobs are kept in save.json beside this script.

Personal use only. Single-player only.
"""
from __future__ import annotations

import argparse
import os
import queue
import re
import random
import sys
import time
import tomllib
from pathlib import Path

import ffxi_data as D
from ffxi_engine import Engine, vana_time

HERE = Path(__file__).resolve().parent


# ================================================================================================
# Game memory
# ================================================================================================
class DolphinBackend:
    """Reads/writes MH Tri's memory through Dolphin Memory Engine's library."""

    def __init__(self, addresses: dict):
        import dolphin_memory_engine as dme
        self.dme = dme
        # objects found by the port's hooks (base = "player"/"monster") mean nothing here
        self.addrs = {k: v for k, v in addresses.items()
                      if v.get("address") and v.get("base", "address") == "address"}
        self._next_hook_try = 0.0

    def connected(self) -> bool:
        if self.dme.is_hooked():
            return True
        if time.monotonic() >= self._next_hook_try:
            self._next_hook_try = time.monotonic() + 1.0
            try:
                self.dme.hook()
            except Exception:
                pass
        return self.dme.is_hooked()

    def _resolve(self, name: str) -> int | None:
        a = self.addrs.get(name)
        if not a:
            return None
        try:
            if a.get("offsets"):
                return self.dme.follow_pointers(a["address"], list(a["offsets"]))
            return a["address"]
        except Exception:
            return None  # pointer not valid right now (e.g. not in a quest)

    def has(self, name: str) -> bool:
        return name in self.addrs

    def read(self, name: str):
        addr = self._resolve(name)
        if addr is None:
            return None
        t = self.addrs[name].get("type", "u16")
        try:
            if t == "u8":
                return self.dme.read_byte(addr)
            if t == "u16":
                return int.from_bytes(self.dme.read_bytes(addr, 2), "big")
            if t == "u32":
                return self.dme.read_word(addr)
            if t == "float":
                return self.dme.read_float(addr)
        except Exception:
            return None
        raise ValueError(f"unknown type {t!r} for {name}")

    def write(self, name: str, value) -> bool:
        addr = self._resolve(name)
        if addr is None:
            return False
        t = self.addrs[name].get("type", "u16")
        try:
            if t == "u8":
                self.dme.write_byte(addr, int(max(0, min(0xFF, value))))
            elif t == "u16":
                self.dme.write_bytes(addr, int(max(0, min(0xFFFF, value))).to_bytes(2, "big"))
            elif t == "u32":
                self.dme.write_word(addr, int(max(0, value)))
            elif t == "float":
                self.dme.write_float(addr, float(value))
            return True
        except Exception:
            return False


class SimBackend:
    """A fake hunt so you can try the layer without Dolphin."""

    def __init__(self, max_hp: int):
        self.vals = {"monster_hp": 3000, "player_hp": max_hp, "player_max_hp": max_hp}
        self.next_hit = time.monotonic() + 1.0
        self.next_roar = time.monotonic() + 4.0

    def connected(self) -> bool:
        now = time.monotonic()
        if self.vals["monster_hp"] <= 1:
            self.vals["monster_hp"] = 3000  # new monster
        if now >= self.next_hit:  # you hit the monster
            self.vals["monster_hp"] = max(1, self.vals["monster_hp"] - random.randint(22, 48))
            self.next_hit = now + random.uniform(0.8, 1.6)
        if now >= self.next_roar:  # the monster hits you
            self.vals["player_hp"] = max(1, self.vals["player_hp"] - random.randint(12, 30))
            self.next_roar = now + random.uniform(3.0, 6.0)
        return True

    def has(self, name: str) -> bool:
        return True

    def read(self, name: str):
        return self.vals.get(name)

    def write(self, name: str, value) -> bool:
        self.vals[name] = int(value)
        return True


# ================================================================================================
# Hotkeys: the macro bar
# ================================================================================================
def key_name(vk: int) -> str:
    """A Windows virtual-key code as the key's label."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return f"Num{vk - 0x60}"
    if 0x70 <= vk <= 0x7B:
        return f"F{vk - 0x6F}"
    return {0x6A: "Num*", 0x6B: "Num+", 0x6D: "Num-", 0x6E: "Num.", 0x6F: "Num/", 0xBA: ";", 0xBB: "=",
            0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]",
            0xDE: "'"}.get(vk, hex(vk))


def macro_keys(cfg: dict) -> list[int]:
    """The macro bar's keys: [hotkeys] macros, or its weapon_skills and spells keys, in that order."""
    hk = cfg["hotkeys"]
    return list(hk.get("macros") or (hk.get("weapon_skills", []) + hk.get("spells", [])))


def start_hotkeys(cfg: dict, actions: queue.Queue, typing=lambda: False):
    try:
        from pynput import keyboard
    except Exception as e:
        print(f"[hotkeys off] {e}")
        return None
    keys = {vk: i for i, vk in enumerate(macro_keys(cfg))}
    pause_key = cfg["hotkeys"].get("pause", 0x6E)
    ui_key = cfg["hotkeys"].get("ui_swap", 0x78)                # F9: FFXI's interface or Monster Hunter's

    def on_press(key):
        if typing():                                   # the chat line has the keyboard
            return
        vk = getattr(key, "vk", None)
        if vk is None and hasattr(key, "value"):
            vk = getattr(key.value, "vk", None)
        if vk in keys:
            actions.put(("macro", keys[vk]))
        elif vk == pause_key:
            actions.put(("pause", 0))
        elif vk == ui_key:
            actions.put(("ui", 0))

    lst = keyboard.Listener(on_press=on_press)
    lst.daemon = True
    lst.start()
    return lst


# ================================================================================================
# The app: the engine, the game's memory, the menu, the interface
# ================================================================================================
MENU_ROWS = 10           # a menu list shows this many entries, and scrolls


# FFXI's music for each place (music_tracks in config.toml: the numbers of sound/win/music/data/musicNNN.bgw)
MUSIC_TRACKS = {"village": 112,       # Selbina, a fishing town's
                "town": 110,          # The Grand Duchy of Jeuno
                "field": 109,         # Ronfaure
                "battle": 101,        # Battle Theme
                "tough_battle": 125}  # Tough Battle
# The areas' names, by the map's code module (the game's own short names in data00_eng.bin, in this order)
AREA_NAMES = {"map_village": "Moga Village", "map01": "Deserted Island", "map02": "Sandy Plains",
              "map03": "Flooded Forest", "map04": "Tundra", "map05": "Volcano", "map06": "Great Desert",
              "map07": "Underwater Ruin", "map08": "Land Arena", "map09": "Land Arena", "map10": "Water Arena",
              "map11": "Sacred Land"}
# Monster Hunter's large monsters, by their number (em001...): the tough battle's
LARGE_MONSTERS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 14, 15, 16, 18, 19, 20, 24, 25}


class App:
    def __init__(self, engine: Engine, mem, cfg: dict, save_path: Path | None = None):
        self.e, self.mem, self.cfg = engine, mem, cfg
        self.actions: queue.Queue = queue.Queue()
        self.paused = False
        self.status = "starting"
        self.monster_hp = self.player_hp = None
        self.monster_max = 0           # its max HP: read (monster_max_hp) or the most seen
        self.menu = None               # the command menu: {"stack": [[cursor, scroll], ...]}
        self.save_path = save_path
        self._saved_at = 0.0
        self.rec = None                # --record: the fight, one JSON line per event
        self._rec_hits = self._rec_log = 0
        self._rec_t0 = time.monotonic()
        self.sounds = None             # FFXI's sounds (ffxi_sound.Player), in the game's interface
        self.character = None          # FFXI's character in the game (port_model.PortModel)
        self.port_target = None        # the port's target (port_bridge.target): its arrow, lock, model
        self.gear = self._start_gear()  # FFXI's equipment (ffxi_gear.Gear): None without the item database
        self.chat_seen = None          # the chat line's lines handled so far (port_bridge.chat)
        self.show_hunter = False       # /hunter: Monster Hunter's own hunter shown instead of the character
        self.fx = None                 # FFXI's spell effects in the scene (_start_fx), in the game's window
        self.gm = os.environ.get("FFXI_GM") == "1" or self.cfg.get("gm", False) is True   # the launcher's GM
        if self.gear:
            self.gear.gm = self.gm
        self.party = None              # FFXI's Trusts called (ffxi_trust.Party), in the game's window
        self.trusts = {}               # every Trust, by name, once their list is read (_start_trusts)
        self._trust_list = None        # the list as read on its thread, for the main one to take
        self.chat_typing = None        # what's being typed, while the line is open
        self.mh_weapon = cfg.get("weapon", "sword_and_shield")   # Monster Hunter's weapon: FFXI's like is held
        self._rebuilding = False
        self.music_now = "unset"       # FFXI's piece playing (None: the game's own), and when to leave battle
        self.engaged_until = 0.0
        self._hits_seen = None
        self._hp_seen = None
        self.mh_hud = False            # F9 (or /ui): Monster Hunter's own HUD shown, FFXI's interface hidden
        self.resting = None            # /heal: {"since", "next", "ticks", "at": where the hunter knelt}
        self.pool = []                 # FFXI's treasure pool: {"iid", "name", "until", "passed"}, a kill's drops
        self.banner = None             # an area's name shown as you arrive: (text, until)
        self._map_seen = None          # the map last seen (its name said on arriving)
        self._quest_seen = None        # the port's count of quests ended, as last seen
        self._retarget_at = None       # auto-target: when to engage the next monster after a kill
        self._pet_seen = None          # the engine's pet as last shown beside you
        self._carvings = None          # the port's count of carvings begun, as last seen
        import mh3_text
        self.monster_names = mh3_text.monster_names(cfg.get("mh3_extract"))   # em027: "Aptonoth"

    # --- every frame ---
    def step(self):
        now = time.monotonic()
        if not self.mem.connected():
            self.status = "waiting for the game..."
            return
        self.status = f"PAUSED ({key_name(self.cfg['hotkeys'].get('pause', 0x6E))})" if self.paused else "connected"
        self._menu_step(now)
        m = self.mem.read("monster_hp") if self.mem.has("monster_hp") else None
        p = self.mem.read("player_hp") if self.mem.has("player_hp") else None
        mx = self.mem.read("player_max_hp") if self.mem.has("player_max_hp") else None
        mm = self.mem.read("monster_max_hp") if self.mem.has("monster_max_hp") else None
        if m is None:
            self.monster_max = 0
        elif mm and mm >= m:
            self.monster_max = mm
        else:
            self.monster_max = max(self.monster_max, m)
        self.port_target = self.mem.target() if hasattr(self.mem, "target") else None
        if self.port_target:                                       # the target's own name, as the game has it
            import mh3_text
            name = self.monster_names.get(mh3_text.model_number(self.port_target["name"]) or -1)
            if name:
                self.e.target = name
        mid = None
        if m is not None:
            mid = self.mem.objects()["monster"] if hasattr(self.mem, "objects") else 1
        if self.paused:
            self.e.prev_monster, self.e.prev_player = m, p
            self._drain_actions(only_pause=True)
            return
        self.e.day_element = vana_time(time.time())["element"]    # Vana'diel's day: its element's magic stronger
        writes = self.e.tick(now, m, p, mx, mid, self.monster_max or None)
        actions = self._drain_actions()
        if hasattr(self.mem, "macro_events"):                      # the pad's bars: a trigger held, a button
            bars = self.pad_macros()
            actions += [("use", bars[8 * bar + i]) for bar, i in self.mem.macro_events() if 8 * bar + i < len(bars)]
        for a in actions:
            name = self.macros()[a[1]] if a[0] == "macro" and a[1] < len(self.macros()) else a[1]
            if a[0] in ("macro", "use") and name:
                writes.update(self.e.use(name, now, writes.get("monster_hp", m), writes.get("player_hp", p)))
        self._attack_step(now, m, writes)
        self._trust_fight(now, m, p, writes)
        if "monster_hp" in writes and m is not None and hasattr(self.mem, "deal_damage") \
                and self.cfg.get("damage_through_game", True) is not False:
            # FFXI's damage dealt as the game deals its own (the monster flinching, falling, carved): handed
            # to it, the HP left as read until the game takes it off (not counted again as your blow)
            dealt = m - writes.pop("monster_hp")
            if dealt > 0:
                self.mem.deal_damage(dealt)
                self.e.owe(dealt, now)
            self.e.prev_monster = m
        self._rest_step(now, writes, p)
        for name, val in writes.items():
            self.mem.write(name, val)
        self.monster_hp = writes.get("monster_hp", m)
        self.player_hp = writes.get("player_hp", p)
        self._play_events()
        self._motion_step(now)
        self._carve_said()
        self._got_said()
        self._quest_said()
        self._area_said(now)
        self._pool_step(now)
        self._retarget(now)
        self._music(now, p)
        self._weapon_watch()
        self._chat_step()
        if self.e.dirty and now - self._saved_at > 5:
            self.save()
        if self.rec:
            self._record(now, p)

    # --- FFXI's Attack: engaged, the weapon out, swinging at the target on the weapon's delay ---
    def attack(self, on: bool) -> None:
        """Engage the target (the weapon out, the camera on it, a swing on the weapon's delay while magic and
        abilities go on), or disengage."""
        if on and self.monster_hp is None:
            self.e.say("Unable to attack: no target.", "warn")
            return
        self.e.engage(on, time.monotonic())
        self._engage_port()

    def _creator_gear(self, gear) -> None:
        """The pieces chosen by name in the character creator (character_gear), handed over and worn, once for
        each time it saved (character_gear_serial): changed in the game after, they stay as you leave them."""
        serial = int(self.cfg.get("character_gear_serial", 0) or 0)
        if not serial or serial == self.e.gear_serial:
            return
        e, worn, not_yet = self.e, [], []
        for slot, iid in (self.cfg.get("character_gear") or {}).items():
            it = gear.item(int(iid))
            if not it:
                continue
            if int(iid) not in e.inventory:
                gear.give(int(iid))
            why = gear.equip(slot, int(iid))
            (not_yet.append(f"{it['name']} ({why})") if why else worn.append(it["name"]))
        if worn:
            e.say(f"{e.me} equips {', '.join(worn)}.", "skillchain")
        if not_yet:
            e.say(f"In your Items, not worn yet: {'; '.join(not_yet)}.", "warn")
        e.gear_serial = serial
        e.dirty = True

    def lockstyle(self, on: bool) -> None:
        """FFXI's /lockstyle: on, the look you have now is kept whatever you wear; off, your gear shows again."""
        import ffxi_gear as G
        import ffxi_model as FM
        e = self.e
        if on:
            base = {**FM.DEFAULT_LOOK, **self.cfg.get("character_look", {})}
            look = self.gear.look(base) if self.gear else base
            e.lockstyle = {s: int(look[s]) for s in G.LOOK_SLOTS if look.get(s) is not None}
            e.say("Style lock mode enabled.", "info")
        else:
            e.lockstyle = {}
            e.say("Style lock mode disabled.", "info")
        e.dirty = True
        self.save()
        self._redress()

    def _gm_command(self, text: str) -> None:
        """A GM's command: !additem <name> [count] (any item, by its name), as LandSandBoat's GMs type it;
        !levelup [n] (your job n levels up, the EXP given: FFXI's banner and fanfare as earned)."""
        e = self.e
        word, _, rest = text.partition(" ")
        if word.lower() == "levelup":
            n = int(rest) if rest.strip().isdigit() else 1
            for _ in range(max(1, min(n, 99))):
                if e.level >= D.LEVEL_CAP:
                    break
                e.gain_exp(max(1, e.tnl - e.exp_now))
            e.dirty = True
            self.save()
        elif word.lower() == "additem" and self.gear:
            rest = rest.strip().strip('"')
            count = 1
            if rest.rsplit(" ", 1)[-1].isdigit() and " " in rest:
                rest, n = rest.rsplit(" ", 1)
                count = max(1, min(12, int(n)))
            it = self.gear.by_name.get(rest.lower()) or (self.gear.item(int(rest)) if rest.isdigit() else None)
            if not it:
                e.say(f"No item called {rest}.", "warn")
                return
            for _ in range(count):
                self.gear.give(it["id"])
            e.say(f"{e.me} obtains {it['name']}" + (f" x{count}." if count > 1 else "."), "heal")
            e.dirty = True
            self.save()
        else:
            e.say("GM commands: !additem <name> [count], !levelup [levels]", "info")

    def _start_trusts(self) -> None:
        """FFXI's Trusts (ffxi_trust.py): their list read on a thread of its own (from LandSandBoat's
        database, fetched the first time), each then a spell every job knows (Magic, Trust), calling it to
        follow you; config.toml's trusts = false for none."""
        if not hasattr(self.mem, "set_companion") or self.cfg.get("trusts", True) is False:
            return
        import threading
        import ffxi_model as FM
        import ffxi_trust as T
        scale = self.cfg.get("character_height", 175.0) / FM.HUME_MALE_HEIGHT
        self.party = T.Party(self.mem, scale, lambda text, kind="system": self.e.say(text, kind))
        self.party.clear()

        def read():
            try:
                self._trust_list = T.load_list(FM.ffxi_root(self.cfg.get("ffxi_path")))
            except Exception as ex:                                # noqa: BLE001
                print(f"FFXI Trusts unavailable: {ex}")
        threading.Thread(target=read, daemon=True).start()

    def _trust_step(self, now: float) -> None:
        """The Trusts called, placed each tick where they follow the hunter to; the list, once read, made
        spells (here, on the main thread: the menu reads the spells)."""
        if not self.party:
            return
        self.party.level = self.e.level                         # theirs: yours
        if self._trust_list is not None:
            for t in self._trust_list:
                if t.get("file") and t["name"] not in D.SPELLS:
                    D.SPELLS[t["name"]] = {"school": "trust", "mp": 0, "cast": t["cast"] or 2.0,
                                           "recast": t["recast"], "fx": []}
                    self.trusts[t["name"]] = t
            self.e.trusts = list(self.trusts)
            print(f"FFXI Trusts: {len(self.trusts)} to call (the menu's Magic, Trust; /refa all sends them away)")
            self._trust_list = None
        pet = self.e.pet["name"] if self.e.pet else None              # your pet beside you (an avatar, a wyvern)
        if pet != self._pet_seen:
            if self._pet_seen:
                self.party.release(self._pet_seen, pets=True)
            self._pet_seen = pet
            if pet and self.cfg.get("pets_seen", True) is not False:
                import ffxi_model as FM
                self.party.summon_pet(pet, FM.ffxi_root(self.cfg.get("ffxi_path")))
        dt = min(0.1, now - getattr(self, "_trust_t", now))
        self._trust_t = now
        scene = self.fx.get("scene") if self.fx else None
        self.party.step(dt, scene["hunter"] if scene else None, scene.get("target") if scene else None)
        for m in self.party.members:                             # one just come: the call's light where it stands
            if m.ready and m.placed and not m.arrived:
                actor = self._fx_trust_actor(m.slot)
                if actor is None and self.fx:
                    continue                                     # (its place for effects next step)
                m.arrived = True
                anim = m.trust.get("anim")
                f = self.fx["lib"].spell(anim) if self.fx and anim is not None else None
                if f is not None:
                    self.fx["effects"].play(f, "main", actor, actor)
                if self.sounds and anim is not None:
                    self.sounds.play_spell(anim)

    def _trust_fight(self, now: float, m, p, writes: dict) -> None:
        """The Trusts' turn (ffxi_trust.Party.fight): engaged with you at your target, their blows and magic
        on it, their cures on you; FFXI's sparks and spell effects for them in the scene."""
        if not self.party or not self.party.members:
            return
        e = self.e
        scene = self.fx.get("scene") if self.fx else None
        target = scene.get("target") if scene else None
        mh, ph = writes.get("monster_hp", m), writes.get("player_hp", p)
        m2, p2, shown = self.party.fight(now, {
            "engaged": e.engaged, "target": tuple(target) if target else None, "m": mh, "floor": e._floor(),
            "p": ph, "p_max": e.player_max_hp, "level": e.level, "avg_hit": e.avg_hit, "me": e.me,
            "foe": e.target, "e": e})
        if m2 is not None and m2 != mh:
            writes["monster_hp"] = e.prev_monster = m2
        if p2 is not None and p2 != ph:
            writes["player_hp"] = e.prev_player = p2
        for what in shown:
            if what[0] == "strike" and self.fx:
                self._fx_strike(scene, what[1])
            elif what[0] == "ws" and self.fx:                       # its weapon skill's look, from it
                actor = self._fx_trust_actor(what[2])
                if actor is not None:
                    self._fx_weapon_skill(what[1], actor)
            elif what[0] == "cast" and self.fx:
                self._fx_trust_cast(what[2], what[1])
            elif what[0] == "spell":
                if self.fx:
                    self._fx_trust_cast(what[2], None)
                    actor = self._fx_trust_actor(what[2])
                    if actor is not None:                       # (none yet: no effect, yours left alone)
                        self._fx_spell(what[1], actor)
                anim = D.SPELL_ANIMATION.get(what[1])
                if self.sounds and anim is not None:
                    self.sounds.play_spell(anim)

    def _engage_port(self) -> None:
        ffxi = self.e.ffxi_combat
        if hasattr(self.mem, "set_mh_swings"):                     # the game's own swings: Monster Hunter's combat
            self.mem.set_mh_swings(not ffxi)
        if hasattr(self.mem, "set_dpad_target"):                   # the d-pad's targeting (dpad_target)
            mode = str(self.cfg.get("dpad_target", "auto")).lower()
            self.mem.set_dpad_target({"off": 0, "never": 0, "always": 2, "engaged": 1}.get(mode, 2 if ffxi else 0))
        if hasattr(self.mem, "set_auto_carve"):                    # a fallen target carved: FFXI's combat
            self.mem.set_auto_carve(ffxi and self.cfg.get("auto_carve", True) is not False)
        if hasattr(self.mem, "set_mh_hud"):
            self.mem.set_mh_hud(self.mh_hud)
        if hasattr(self.mem, "set_menu_xy"):                       # the menu on X and Y: FFXI's combat
            self.mem.set_menu_xy(ffxi)
        if hasattr(self.mem, "set_pad_macros"):                    # the triggers' bars: FFXI's combat
            self.mem.set_pad_macros(ffxi and self.cfg.get("pad_macros", True) is not False)
        if hasattr(self.mem, "set_engage"):
            self.mem.set_engage(self.e.engaged, self.e.engaged and self.cfg.get("attack_lock_camera", True) is not False)

    REST_FIRST, REST_EVERY = 20.0, 10.0          # FFXI's resting: the first recovery after 20 s, then each 10 s

    def rest(self, on: bool) -> None:
        """FFXI's /heal: kneeling, your HP and MP come back every 10 s (the first after 20 s), more each time;
        moving, being hurt, attacking or casting stands you up. /heal again stands you up."""
        e, now = self.e, time.monotonic()
        if on and (e.engaged or e.casting):
            e.say(f"{e.me} cannot rest while fighting.", "warn")
            return
        if on:
            scene = self.fx.get("scene") if self.fx else None
            at = tuple(float(v) for v in scene["hunter"][:, 3]) if scene else None
            self.resting = {"since": now, "next": now + self.REST_FIRST, "ticks": 0, "at": at, "hp": None}
            e.say(f"{e.me} starts resting.", "emote")
            if self.character:
                self.character.request("rx0", "rx1")
        elif self.resting:
            self.resting = None
            e.say(f"{e.me} stops resting.", "emote")
            if self.character:
                self.character.request(None)

    def _rest_step(self, now: float, writes: dict, p) -> None:
        """Resting: HP and MP back on FFXI's ticks; stood up by moving, being hurt, fighting or casting."""
        r, e = self.resting, self.e
        if not r:
            return
        p = writes.get("player_hp", p)
        scene = self.fx.get("scene") if self.fx else None
        moved = False
        if scene and r["at"] is not None:
            h = scene["hunter"][:, 3]
            moved = ((float(h[0]) - r["at"][0]) ** 2 + (float(h[2]) - r["at"][2]) ** 2) ** 0.5 > 25.0
        hurt = p is not None and r["hp"] is not None and p < r["hp"]
        if moved or hurt or e.engaged or e.casting:
            self.rest(False)
            return
        if now >= r["next"] and p is not None:
            r["ticks"] += 1
            r["next"] = now + self.REST_EVERY
            n = r["ticks"]
            hp = max(3, round(e.player_max_hp * 0.06)) + 2 * (n - 1)
            mp = (max(2, round(e.max_mp * 0.06)) + (n - 1)) if e.max_mp else 0
            if p < e.player_max_hp:
                p = min(e.player_max_hp, p + hp)
                writes["player_hp"] = e.prev_player = p
            if mp:
                e.mp = min(e.max_mp, e.mp + mp)
        r["hp"] = p

    def set_combat(self, ffxi: bool) -> None:
        """FFXI's combat (Attack fights for you: the pad's Y and B don't swing while it does, the d-pad picks
        the target, a fallen target is carved) or Monster Hunter's (its buttons and d-pad as they were)."""
        e = self.e
        e.ffxi_combat = bool(ffxi)
        e.dirty = True
        self._engage_port()
        e.say("Combat mode: FFXI. Attack fights for you, the d-pad picks your target, the fallen are carved."
              if ffxi else "Combat mode: Monster Hunter. Y and B swing, the d-pad is the game's.", "info")

    def swap_ui(self, mh: bool | None = None) -> None:
        """F9 (or /ui): Monster Hunter's own HUD in place of FFXI's interface, and back."""
        self.mh_hud = (not self.mh_hud) if mh is None else bool(mh)
        self._engage_port()
        self.e.say("Monster Hunter's interface (F9: FFXI's)." if self.mh_hud else "FFXI's interface (F9: Monster Hunter's).",
                   "info")

    def _motion_step(self, now: float) -> None:
        """The character's motions asked for in turn (a weapon skill's: _play_events)."""
        queue_ = getattr(self, "_motions", None)
        while queue_ and self.character and now >= queue_[0][0]:
            self.character.request(queue_.pop(0)[1])

    def _got_said(self) -> None:
        """What came into the pouch in the field, as FFXI says what you obtain."""
        if not hasattr(self.mem, "got"):
            return
        for _iid, n, name in self.mem.got():
            name = name or "an item"
            if n > 1:
                self.e.say(f"{self.e.me} obtains {n} {name}.", "heal")
            else:
                article = "an" if name[:1].lower() in "aeiou" else "a"
                self.e.say(f"{self.e.me} obtains {article} {name}.", "heal")

    def _quest_said(self) -> None:
        """A quest's end (its banner loading), in FFXI's words; complete, FFXI's fanfare."""
        if not hasattr(self.mem, "quest"):
            return
        seq, kind = self.mem.quest()
        if self._quest_seen is None or seq == self._quest_seen:
            self._quest_seen = seq
            return
        self._quest_seen = seq
        e = self.e
        if kind == 2:
            e.say(f"{e.me} has completed the quest!", "skillchain")
            if self.sounds:
                import ffxi_sound as FS
                self.sounds.play_effect(FS.LEVEL_UP)
        elif kind == 3:
            e.say("The quest has failed.", "warn")
        elif kind == 4:
            e.say(f"{e.me} has abandoned the quest.", "info")

    def _area_said(self, now: float) -> None:
        """Arriving in an area: its name across the top a few seconds, as FFXI shows a zone's."""
        if not hasattr(self.mem, "map_name"):
            return
        where = self.mem.map_name()
        if where == self._map_seen:
            return
        first = self._map_seen is None
        self._map_seen = where
        name = AREA_NAMES.get(where)
        if name and not first:
            self.banner = (name, now + 5.0)
            self.e.say(f"=== Area: {name} ===", "info")

    def _retarget(self, now: float) -> None:
        """Auto-target (FFXI's combat, auto_target): a moment after a kill, the next monster near is engaged."""
        if self._retarget_at is None or now < self._retarget_at:
            return
        self._retarget_at = None
        t = self.port_target
        reach = float(self.cfg.get("auto_target_range", 1500.0))
        if t and t["dist"] <= reach and self.monster_hp and not self.e.engaged:
            self.attack(True)

    # --- FFXI's treasure pool: a kill's drops wait in it, each lotted, passed, or yours when its time is up ---
    POOL_TIME, POOL_SIZE = 300.0, 10

    def _pool_add(self, it: dict, now: float) -> None:
        if len(self.pool) >= self.POOL_SIZE:                      # full: the oldest goes out to you
            self._pool_settle(self.pool[0], lotted=False)
        article = "an" if it["name"][:1].lower() in "aeiou" else "a"
        self.e.say(f"You find {article} {it['name']} on the {self.e.target}.", "info")
        self.pool.append({"iid": it["id"], "name": it["name"], "until": now + self.POOL_TIME, "passed": False})

    def _pool_settle(self, entry: dict, lotted: bool) -> None:
        """An item out of the pool: yours (lotted, or its time up unpassed), or lost (passed)."""
        if entry in self.pool:
            self.pool.remove(entry)
        if entry["passed"] and not lotted:
            self.e.say(f"{entry['name']} is lost.", "warn")
            return
        self.gear.give(entry["iid"])
        article = "an" if entry["name"][:1].lower() in "aeiou" else "a"
        self.e.say(f"{self.e.me} obtains {article} {entry['name']}.", "skillchain")
        self.e.dirty = True
        self.save()

    def pool_lot(self, i: int) -> None:
        if 0 <= i < len(self.pool):
            entry = self.pool[i]
            self.e.say(f"{self.e.me}'s lot for {entry['name']}: {random.randint(1, 999)} points.", "info")
            self._pool_settle(entry, lotted=True)

    def pool_pass(self, i: int) -> None:
        if 0 <= i < len(self.pool):
            entry = self.pool[i]
            entry["passed"] = True
            self.e.say(f"{self.e.me} passes {entry['name']}.", "info")

    def _pool_step(self, now: float) -> None:
        for entry in [x for x in self.pool if now >= x["until"]]:
            self._pool_settle(entry, lotted=False)

    def _carve_said(self) -> None:
        """The port began carving a fallen target: said, as FFXI says what you do."""
        if not hasattr(self.mem, "carvings"):
            return
        n = self.mem.carvings()
        if self._carvings is not None and n != self._carvings:
            self.e.say(f"{self.e.me} carves the {self.e.target}.", "info")
        self._carvings = n

    def _mute_port(self) -> None:
        """The hunter's own sounds the port drops, FFXI's race's voice in their place (config.toml's
        mute_hunter_sounds: "kind:code", kind 1 its voice, code in hex or * for all); none while /hunter
        shows Monster Hunter's hunter, or with no FFXI character."""
        if not hasattr(self.mem, "mute_hunter_sounds"):
            return
        if not hasattr(self, "_mute_list"):
            self._mute_list = []
            for s in self.cfg.get("mute_hunter_sounds", ["1:*"]):
                kind, _, code = str(s).partition(":")
                try:
                    self._mute_list.append((int(kind), None if code.strip() in ("", "*") else int(code, 16)))
                except ValueError:
                    print(f"mute_hunter_sounds: {s!r} isn't kind:code")
            self._mute_sent = None
        want = [] if self.show_hunter or not self.cfg.get("character_race") else self._mute_list
        if want != self._mute_sent:
            self.mem.mute_hunter_sounds(want)
            self._mute_sent = want

    def _in_reach(self) -> bool:
        """The target within a swing: its middle a reach away, a big one's reach the longer."""
        t = self.port_target
        if not t:
            return False
        scene = self.fx.get("scene") if self.fx else None
        height = scene["target"][3] if scene and scene.get("target") is not None else 150.0
        return t["dist"] <= float(self.cfg.get("attack_reach", 300.0)) + 0.6 * height

    def _attack_step(self, now: float, m, writes: dict) -> None:
        e = self.e
        if e.engaged and m is None:                    # the target gone (dead, or left): disengage
            self._lost_target = getattr(self, "_lost_target", None) or now
            if now - self._lost_target > 2.0:
                self.attack(False)
        else:
            self._lost_target = None
        if e.engaged:
            before = writes.get("monster_hp", m)
            after = e.swing(now, before, self._in_reach())
            if after is not None and after != before:
                writes["monster_hp"] = e.prev_monster = after
        self._engage_port()
        self._mute_port()

    def _play_events(self):
        """The engine's events as FFXI's: a spell's casting by the character (its school's motions: start,
        chant, release) and its sound as it goes off, a level gained or lost."""
        events, self.e.events[:] = list(self.e.events), []
        for kind, name in events:
            if kind == "spell" and self.party and name in self.trusts:
                self.party.summon(self.trusts[name])
            if kind == "kill" and self.gear:
                self._drops(int(name))
            if kind == "kill" and self.e.engaged:
                self.attack(False)
                if self.e.ffxi_combat and self.cfg.get("auto_target", True) is not False:
                    self._retarget_at = time.monotonic() + 2.5            # (the port moves on from the fallen)
            if kind == "ws" and self.character:        # the skill's own motions, as its file plays them
                plan = getattr(self, "_ws_plans", {}).get(name)
                if plan:
                    now = time.monotonic()
                    self._motions = [(now + t, clip) for t, clip in plan["steps"]]
            if kind == "swing":
                if self.character:                     # FFXI's attack motions in turn
                    self._swing_n = getattr(self, "_swing_n", 0) + 1
                    self.character.request(f"at{self._swing_n % 3}")
                if self.fx and name != "miss":
                    self._fx_strike(self.fx.get("scene"), name == "critical")
        if self.character:
            import ffxi_model as FM
            for kind, name in events:
                cast = FM.CAST_MOTIONS.get(D.SPELLS.get(name, {}).get("school", ""), "")
                if kind == "cast" and cast:
                    self.character.request(cast + "0", cast + "1")
                elif kind == "spell" and cast:
                    self.character.request(cast + "2")
                elif kind == "interrupted":
                    self.character.request(None)
        if self.fx:
            for kind, name in events:
                if kind == "cast":
                    self._fx_cast(name)
                elif kind == "spell":
                    self._fx_spell(name)
                elif kind == "interrupted":
                    self._fx_cast_end()
                elif kind == "ws":
                    self._fx_weapon_skill(name)
                elif kind == "skillchain":
                    self._fx_skillchain(name)
            ups = [k for k, _ in events if k in ("level_up", "level_down")]
            if ups:                                                 # several levels at once: one banner
                self._fx_level(ups[-1] == "level_up")
        if not self.sounds:
            return
        self._voices(events)
        import ffxi_sound as FS
        for kind, name in dict.fromkeys(events):                    # several levels at once: one fanfare
            anim = D.SPELL_ANIMATION.get(name)
            if kind == "spell" and anim is not None:
                self.sounds.play_spell(anim)
            elif kind == "level_up":
                self.sounds.play_effect(FS.LEVEL_UP)
            elif kind == "level_down":
                self.sounds.play_effect(FS.LEVEL_DOWN)

    # --- FFXI's spell effects in the scene (ffxi_fx.py runs them, port_fx.py hands them to the port) ---
    def _start_fx(self):
        """The casting and the spells landing, from FFXI's own effect files: the character's race's casting,
        the shared release, each spell's own."""
        if self.cfg.get("effects", True) is False or not self.cfg.get("character_race"):
            return None
        try:
            import ffxi_fx as FX
            import ffxi_model as FM
            from port_fx import PortFx
            lib = FX.Library(FM.ffxi_root(self.cfg.get("ffxi_path")), self.cfg.get("character_race"))
            me = FX.actor_from_mh([0, 0, 0], FX.yaw_matrix(0.0))
            them = FX.actor_from_mh([0, 0, 0], FX.yaw_matrix(0.0), 200.0)
            print("FFXI effects: casting and spells drawn in the scene")
            return {"lib": lib, "effects": FX.Effects(lib), "port": PortFx(), "me": me, "them": them,
                    "has_target": False, "cast": None, "layers": {}, "images": [], "tex": {}, "last": 0.0,
                    "drawn": False, "hits": None, "pulsed": {}, "hunter": None, "scene": None}
        except Exception as ex:                                       # noqa: BLE001  the layer runs without them
            print(f"[FFXI effects unavailable: {ex}]")
            return None

    def _fx_cast(self, name: str) -> None:
        """A spell begun: its school's casting about the character, until it goes off or is broken off."""
        import ffxi_fx as FX
        fx = self.fx
        self._fx_cast_end()
        routine = FX.CAST_ROUTINE.get(D.SPELLS.get(name, {}).get("school", ""))
        if routine:
            fx["cast"] = fx["effects"].play(fx["lib"].caster, routine, fx["me"], None, 0x3C)

    def _fx_cast_end(self) -> None:
        if self.fx and self.fx["cast"] is not None:
            self.fx["effects"].stop(self.fx["cast"])
            self.fx["cast"] = None

    def _fx_trust_actor(self, slot: int):
        """A Trust's place for its effects (refreshed each step, _fx_step), None if it isn't there."""
        return self.fx.get("trusts", {}).get(slot) if self.fx else None

    def _fx_trust_cast(self, slot: int, name: str | None) -> None:
        """A Trust's casting about it (name None: ended), as yours is about you."""
        import ffxi_fx as FX
        fx = self.fx
        plays = fx.setdefault("trust_casts", {})
        if plays.get(slot) is not None:
            fx["effects"].stop(plays.pop(slot))
        actor = self._fx_trust_actor(slot)
        routine = FX.CAST_ROUTINE.get(D.SPELLS.get(name, {}).get("school", "")) if name else None
        if routine and actor is not None:
            plays[slot] = fx["effects"].play(fx["lib"].caster, routine, actor, None, 0x3C)

    def _fx_spell(self, name: str, source=None) -> None:
        """A spell gone off: its own effect, the release about its caster (you, or a Trust), then on its
        target (the monster for a nuke or an enfeeble, else you)."""
        fx = self.fx
        if source is None:
            self._fx_cast_end()
        anim = D.SPELL_ANIMATION.get(name)
        f = fx["lib"].spell(anim) if anim is not None else None
        if f is None:
            return
        hostile = any(e[0] in ("damage", "drain", "aspir", "debuff") for e in D.SPELLS.get(name, {}).get("fx", []))
        target = fx["them"] if hostile and fx["has_target"] else fx["me"]
        fx["effects"].play(f, "main", source or fx["me"], target)

    def _fx_weapon_skill(self, name: str, source=None) -> None:
        """A weapon skill: the race's own file for it (its flash about the one using it, you or a Trust, its
        strikes on the monster)."""
        fx = self.fx
        anim = D.WS_ANIMATION.get(name)
        f = fx["lib"].weapon_skill(anim) if anim is not None else None
        if f is not None and fx["has_target"]:
            fx["effects"].play(f, "main", source or fx["me"], fx["them"])

    def _fx_level(self, up: bool) -> None:
        """A level gained (or lost): FFXI's own banner about the character, the client's effect (ROM/13/35:
        "Level Up !!" in gold, sparks crossing about it, its echoes; 13/34 the level lost)."""
        import ffxi_sound as FS
        fx = self.fx
        f = fx["lib"].file(fx["lib"].root / (FS.LEVEL_UP if up else FS.LEVEL_DOWN))
        if f is not None:
            fx["effects"].play(f, "main", fx["me"], fx["me"])

    def _fx_skillchain(self, name: str) -> None:
        """A skillchain closed: a burst of its element on the monster, the target's part of a spell of it,
        as it lands."""
        import ffxi_fx as FX
        fx = self.fx
        anim = FX.SKILLCHAIN_LOOK.get(name)
        f = fx["lib"].spell(anim) if anim is not None else None
        if f is not None and fx["has_target"]:
            fx["effects"].pulse(f, "main", fx["me"], fx["them"], alpha=1.0, length=1.0)

    def _fx_step(self, now: float) -> None:
        """30 times a second: where things are (from the port), the effects stepped, their triangles sent."""
        fx = self.fx
        if not fx or now - fx["last"] < 1 / 30:
            return
        import numpy as np
        import ffxi_fx as FX
        dt, fx["last"] = (now - fx["last"] if fx["last"] else 1 / 30), now
        port, effects = fx["port"], fx["effects"]
        scene = port.scene()
        if scene:
            v = scene["camera"]
            effects.camera = FX.Camera(-v[:, :3].T @ v[:, 3], v[:, :3])
            h = scene["hunter"]
            rot = h[:, :3] / np.maximum(np.linalg.norm(h[:, :3], axis=0), 1e-6)
            me = FX.actor_from_mh(h[:, 3], rot, getattr(self, "_char_top", 175.0))   # the character's own height
            fx["me"].pos, fx["me"].facing, fx["me"].height = me.pos, me.facing, me.height
            trusts = fx.setdefault("trusts", {})                  # the Trusts' places, for their effects
            for m in (self.party.members if self.party else []):
                if m.ready and m.placed:
                    a = FX.actor_from_mh(np.array([m.x, m.y, m.z]), FX.yaw_matrix(m.yaw), m.top)
                    if m.slot in trusts:
                        trusts[m.slot].pos, trusts[m.slot].facing, trusts[m.slot].height = a.pos, a.facing, a.height
                    else:
                        trusts[m.slot] = a
            t = scene["target"]
            fx["has_target"] = t is not None
            if t is not None:
                yaw = float(np.arctan2(h[0, 3] - t[0], h[2, 3] - t[2]))
                them = FX.actor_from_mh(t[:3], FX.yaw_matrix(yaw), max(50.0, t[3]))
                fx["them"].pos, fx["them"].facing, fx["them"].height = them.pos, them.facing, them.height
        if scene:
            fx["hunter"], fx["scene"] = scene["hunter"], scene
            self._fx_hits(scene)
            self._fx_status(now)
        effects.step(min(dt, 0.25))
        if effects.busy() and effects.camera is not None:
            verts, counts, keys = effects.geometry()
            if len(verts):
                verts[:, 5] = self._fx_layers(keys, verts[:, 5])
            port.send(verts, counts)
            fx["drawn"] = True
        elif fx["drawn"]:
            port.send(np.zeros((0, 10), np.float32), [0] * 5)
            fx["drawn"] = False
        port.keep_alive()

    def _fx_hits(self, scene) -> None:
        """A blow of the hunter's taking HP off a monster (the port counts the game's): FFXI's spark for the
        weapon in hand, where the blade lands: toward the monster, a reach ahead, at its middle's height."""
        import numpy as np
        import ffxi_fx as FX
        fx = self.fx
        o = self.mem.objects() if hasattr(self.mem, "objects") else None
        if not o:
            return
        hits, before = o.get("monster_hits"), fx["hits"]
        fx["hits"] = hits
        if before is None or hits == before or o.get("monster_delta", 0) >= 0:
            return
        blow = -o["monster_delta"]
        usual = fx.get("usual_blow") or blow
        fx["usual_blow"] = 0.85 * usual + 0.15 * blow                  # what the hunter's blows have been doing
        self._fx_strike(scene, blow >= 1.8 * usual and blow >= 10)

    def _fx_strike(self, scene, critical: bool = False) -> None:
        """A blow landing: FFXI's spark for the weapon in hand (and its critical flash), where the blade lands:
        toward the monster, a reach ahead, at its middle's height."""
        import numpy as np
        import ffxi_fx as FX
        fx = self.fx
        if not scene:
            return
        h = scene["hunter"]
        me = h[:, 3]
        t = scene["target"]
        if t is not None and np.hypot(t[0] - me[0], t[2] - me[2]) > 1:
            way = np.array([t[0] - me[0], 0.0, t[2] - me[2]])
            reach = min(float(np.linalg.norm(way)), 130.0)
            up = float(np.clip(0.5 * t[3], 60.0, 140.0))
        else:
            way = h[:, 2] * np.array([1.0, 0.0, 1.0])
            reach, up = 130.0, 100.0
        way = way / (np.linalg.norm(way) or 1.0)
        spot = me + way * reach
        yaw = float(np.arctan2(-way[0], -way[2]))                   # facing back toward the hunter
        struck = FX.actor_from_mh(spot, FX.yaw_matrix(yaw), 2 * up)   # its "middle" (a joint named) the height struck
        routine = FX.HIT_ROUTINE.get(self.mh_weapon, "hit9")
        fx["effects"].play(fx["lib"].shared, routine, fx["me"], struck)
        if critical:                                                 # far over the usual: FFXI's critical flash
            fx["effects"].play(fx["lib"].shared, FX.CRITICAL_ROUTINE, fx["me"], struck)

    def _fx_status(self, now: float) -> None:
        """A debuff lasting on the monster: every 3 s its spell's look on it again, faint and short."""
        fx = self.fx
        if not fx["has_target"]:
            return
        active = {src for (src, eff), b in self.e.debuffs.items() if b["until"] > now + 1}
        pulsed = fx["pulsed"]
        for src in list(pulsed):
            if src not in active:
                del pulsed[src]
        for src in active:
            anim = D.SPELL_ANIMATION.get(src)
            if anim is None:
                continue
            if src not in pulsed:
                pulsed[src] = now + 4.0                             # its own effect plays first
            if now < pulsed[src]:
                continue
            pulsed[src] = now + 3.0
            f = fx["lib"].spell(anim)
            if f is not None:
                fx["effects"].pulse(f, "main", fx["me"], fx["them"], alpha=0.35, length=0.45)

    def _fx_layers(self, keys, column):
        """The frame's textures as the port's layers (new ones loaded and sent), each corner's index in
        `keys` turned into its layer (+1000 kept: its alpha read as half)."""
        import numpy as np
        from PIL import Image
        import ffxi_fx as FX
        from port_fx import MAX_LAYERS, TEX
        fx = self.fx
        layers, images = fx["layers"], fx["images"]
        new = [k for k in keys if k not in layers]
        if new:
            if len(images) + len(new) > MAX_LAYERS:              # full: only what's drawn now
                layers.clear()
                images.clear()
                new = list(keys)
            for k in new:
                img = FX.load_texture(fx["lib"], k, fx["tex"])
                if img is None:
                    img = np.full((TEX, TEX, 4), 255, np.uint8)
                layers[k] = len(images)
                images.append(np.array(Image.fromarray(img, "RGBA").resize((TEX, TEX), Image.LANCZOS)))
            fx["port"].set_textures(images)
        lut = np.array([layers[k] for k in keys] + [0], np.float32)
        half = column > 999.5
        idx = np.where(half, column - 1000, column).astype(np.int64)
        out = np.where(idx >= 0, lut[np.clip(idx, 0, len(keys))], -1.0)
        return np.where(half & (idx >= 0), out + 1000, out)

    # --- the race's voice in place of the hunter's: attacking, hurt, falling ---
    def _voices(self, events) -> None:
        """FFXI's voice for the character's race (its file's vatk, vdam, vded: four grunts or none, at
        random): on each of the hunter's attacks (the port counts them) and each swing, when hurt, when
        falling. Monster Hunter's own grunts are muted by the port (MH3_MUTE_SE)."""
        if self.cfg.get("race_voice", True) is False or not self.cfg.get("character_race"):
            return
        if not hasattr(self, "_voice_sets"):
            self._voice_sets, self._voice_at = {}, 0.0
            try:
                import ffxi_fx as FX
                import ffxi_model as FM
                lib = self.fx["lib"] if self.fx else FX.Library(FM.ffxi_root(self.cfg.get("ffxi_path")),
                                                                self.cfg["character_race"])
                import ffxi_sound as FS
                face = int(self.cfg.get("character_look", {}).get("face", 1))
                vset = self.e.voice or self.cfg.get("voice_set") or FS.voice_set(lib.root, self.cfg["character_race"], face)
                self._voice_now = vset
                for kind, routine in (("attack", "vatk"), ("hurt", "vdam"), ("faint", "vded")):
                    opts = FX.voice_options(lib.caster, routine)            # se236's, as the file names them
                    self._voice_sets[kind] = [None if n is None else (int(vset) * 1000 + n % 1000 if vset else n)
                                              for n in opts]
                print(f"FFXI voice: set se{vset} ({self.cfg['character_race']}, face {face})")
            except Exception as ex:                                  # noqa: BLE001  silent then
                print(f"[FFXI voices unavailable: {ex}]")
        want = None
        swings = self.character.swings() if self.character and hasattr(self.character, "swings") else None
        if swings is not None:
            if getattr(self, "_swings_seen", None) is not None and swings != self._swings_seen:
                want = "attack"
            self._swings_seen = swings
        for kind, _name in events:
            if kind == "swing":
                want = want or "attack"
            elif kind == "hurt":
                want = "hurt"
            elif kind == "faint":
                want = "faint"
        options = self._voice_sets.get(want) if want else None
        now = time.monotonic()
        if options and (want != "attack" or now - self._voice_at > 0.4):
            self._voice_at = now
            pick = random.choice(options)
            if pick is not None:
                self.sounds.play(pick)

    def voice_command(self, arg: str) -> None:
        """/voice: the voice heard (its attack grunt played); /voice next or prev: the readable sets in turn;
        /voice 214: that set; /voice auto: the race's again; /voice off or on. Kept with your progress."""
        import ffxi_model as FM
        import ffxi_sound as FS
        e = self.e
        if arg in ("off", "on"):
            self.cfg["race_voice"] = arg == "on"
            e.say(f"Voice {'on' if arg == 'on' else 'off'}.", "info")
            return
        root = FM.ffxi_root(self.cfg.get("ffxi_path"))
        if not hasattr(self, "_all_voices"):
            self._all_voices = FS.voice_sets(root)
        now = getattr(self, "_voice_now", None) or e.voice
        if arg in ("next", "prev", "n", "p") and self._all_voices:
            sets = self._all_voices
            i = sets.index(now) if now in sets else -1
            e.voice = sets[(i + (1 if arg[0] == "n" else -1)) % len(sets)]
        elif arg.isdigit() and FS.readable_voice(root, int(arg)):
            e.voice = int(arg)
        elif arg == "auto":
            e.voice = 0
        elif arg:
            e.say("/voice: next, prev, a set's number (201-328), auto, off, on.", "warn")
            return
        if hasattr(self, "_voice_sets"):
            del self._voice_sets                                   # chosen again from the new set
        e.dirty = True
        self._voices([])                                           # (builds the sets)
        options = [n for n in (self._voice_sets or {}).get("attack", []) if n is not None]
        if self.sounds and options:
            self.sounds.play(options[0])
        e.say(f"Voice: se{self._voice_now}" + (" (the race's)" if not e.voice else "") +
              ". /voice next or prev to hear others.", "info")

    def _music_tracks(self) -> dict:
        """The pieces for each place: MUSIC_TRACKS, config.toml's [music_tracks] over them (numbers only: a key
        that strayed under the table, as the creator's gear once did, isn't a piece)."""
        own = {k: v for k, v in self.cfg.get("music_tracks", {}).items() if isinstance(v, int)}
        return {**MUSIC_TRACKS, **own}

    def _music(self, now: float, player_hp) -> None:
        """FFXI's music in place of Monster Hunter's: the village's, the field's, or, engaged (the weapon
        out with a monster near, or blows traded in the last 6 seconds), battle music, tougher for a large
        monster; in the menus the game's own."""
        if not self.sounds or self.cfg.get("music", True) is False or not hasattr(self.mem, "map_name"):
            return
        tracks = self._music_tracks()
        where = self.mem.map_name()
        hits = self.mem.objects().get("monster_hits") if hasattr(self.mem, "objects") else None
        if hits != self._hits_seen and self._hits_seen is not None:
            self.engaged_until = now + 6
        self._hits_seen = hits
        if player_hp is not None and self._hp_seen is not None and player_hp < self._hp_seen:
            self.engaged_until = now + 6
        self._hp_seen = player_hp
        t = self.port_target
        if t and t["dist"] < 1500 and self.character and self.character.battle():
            self.engaged_until = now + 6
        town = any(w in where for w in ("village", "town", "lobby"))
        if not where:
            want = None
        elif now < self.engaged_until and not town:
            import mh3_text
            big = t and (mh3_text.model_number(t["name"]) or 0) in LARGE_MONSTERS
            want = tracks["tough_battle" if big else "battle"]
        elif town:
            want = tracks["town" if "town" in where or "lobby" in where else "village"]
        else:
            want = tracks["field"]
        if want != self.music_now:
            self.music_now = want
            self.sounds.play_music(want)
            name = next((k for k, v in tracks.items() if v == want), "")
            piece = f"{want} ({name})" if want is not None else "the game's own"
            print(f"FFXI music: {piece} in {where or 'the menus'}")
        self.mem.mute_game_music(want is not None)

    def _start_sounds(self):
        """FFXI's sounds from your own installation (sounds in config.toml), the known spells' made
        ready ahead."""
        if self.cfg.get("sounds", True) is False:
            return None
        try:
            import ffxi_model as FM
            import ffxi_sound as FS
            player = FS.Player(FM.ffxi_root(self.cfg.get("ffxi_path")), float(self.cfg.get("sound_volume", 0.7)),
                               float(self.cfg.get("music_volume", 1.0)))
            player.prepare(D.SPELL_ANIMATION[n] for n in self.e.known_spells() if n in D.SPELL_ANIMATION)
            if self.cfg.get("music", True) is not False:            # decoded ahead, so a change doesn't wait
                player.prepare_music(self._music_tracks().values())
            return player
        except (Exception, SystemExit) as ex:                      # the layer runs without them
            print(f"[FFXI sounds unavailable: {ex}]")
            return None

    def save(self):
        """Levels, EXP and jobs, kept between sessions."""
        self._saved_at = time.monotonic()
        self.e.dirty = False
        if self.save_path:
            import json
            tmp = self.save_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.e.progress(), indent=1), encoding="utf-8")
            tmp.replace(self.save_path)

    def pad_macros(self) -> list[str]:
        """The pad's two bars, eight each (LT then RT; each the d-pad's up, right, down, left, then the face's
        top, right, bottom, left): config.toml's pad_macros_lt / pad_macros_rt where set, else your weapon
        skills and newest spells and abilities, as the keys' macros run on."""
        e = self.e
        ws = e.known_ws()[-4:]
        ws += [""] * (4 - len(ws))
        own = sorted([(lv, x) for lv, x in e.j["spells"] + e.j["abilities"]
                      if lv <= e.level and not D.ABILITIES.get(x, {}).get("sp")], key=lambda t: -t[0])
        auto = ws + [x for _, x in own]
        out = []
        for bar, key in ((0, "pad_macros_lt"), (1, "pad_macros_rt")):
            chosen = self.cfg.get(key, [])
            for i in range(8):
                k = 8 * bar + i
                out.append(chosen[i] if i < len(chosen) and chosen[i] else (auto[k] if k < len(auto) else ""))
        return out

    def macros(self) -> list[str]:
        """What each macro key does: [macros] in config.toml where set, else the newest of each."""
        e, n = self.e, len(macro_keys(self.cfg))
        ws = e.known_ws()[-4:]
        ws += [""] * (4 - len(ws))
        own = sorted([(lv, x) for lv, x in e.j["spells"] + e.j["abilities"]
                      if lv <= e.level and not D.ABILITIES.get(x, {}).get("sp")], key=lambda t: -t[0])
        auto = ws + [x for _, x in own]
        chosen = self.cfg.get("macros", [])
        return [(chosen[i] if i < len(chosen) and chosen[i] else (auto[i] if i < len(auto) else "")) for i in range(n)]

    def start_recording(self, path) -> None:
        import json
        self._json = json
        self.rec = open(path, "a", encoding="utf-8")
        self._rec_log = self.e.said
        if hasattr(self.mem, "objects"):
            self._rec_hits = self.mem.objects()["monster_hits"]
        self._rec_player = None
        print(f"recording the fight to {path}")

    def _record(self, now: float, player_hp) -> None:
        """The game's hits on monsters (the port's hook), the layer's messages, the hunter's HP."""
        e, t = self.e, round(now - self._rec_t0, 2)
        out = []
        if hasattr(self.mem, "objects"):
            o = self.mem.objects()
            if o["monster_hits"] != self._rec_hits:
                n = (o["monster_hits"] - self._rec_hits) & 0xFFFFFFFF
                self._rec_hits = o["monster_hits"]
                out.append({"t": t, "ev": "game_hit", "n": n, "last_delta": o["monster_delta"],
                            "monster": hex(o["monster"]), "hp": self.monster_hp, "max": self.monster_max,
                            "tp": int(e.tp)})
        k = min(e.said - self._rec_log, len(e.log))      # the messages said since the last time
        if k > 0:
            for line, kind in zip(e.log[-k:], e.kinds[-k:]):
                out.append({"t": t, "ev": kind, "text": line, "hp": self.monster_hp, "tp": int(e.tp),
                            "mp": int(e.mp), "job": e.job_label()})
        self._rec_log = e.said
        if player_hp is not None and player_hp != self._rec_player:
            if self._rec_player is not None:
                out.append({"t": t, "ev": "player_hp", "from": self._rec_player, "to": player_hp})
            self._rec_player = player_hp
        for r in out:
            self.rec.write(self._json.dumps(r) + "\n")
        if out:
            self.rec.flush()

    # --- the FFXI command menu: the pad's left face button held (the Classic's Y) opens it ---
    def menu_root(self, now: float) -> list[dict]:
        """The menu, built afresh: what is usable changes with TP, MP, recasts and levels.
        A node: label, ok (usable), detail (right side), and children (a list) or act (an action)."""
        e = self.e

        def action(n):
            ok, _ = e.can_use(n, now)
            left = e.recasts.get(n, 0) - now
            if left > 0:
                detail = f"{int(left) + 1}s"
            elif n in D.SPELLS:
                detail = f"{e._mp_cost(n)} MP" if D.SPELLS[n]["mp"] else ""
            elif n in D.ABILITIES and D.ABILITIES[n].get("tp"):
                detail = f"{D.ABILITIES[n]['tp']} TP"
            else:
                detail = ""
            return {"label": n, "ok": ok, "detail": detail, "act": ("use", n)}

        root = []
        # FFXI's command menu, in its order: Attack (Disengage while attacking), Magic, Abilities, Items...
        has_target = self.monster_hp is not None
        root.append({"label": "Disengage" if e.engaged else "Attack", "ok": e.engaged or has_target, "detail": "",
                     "act": ("attack",), "help": "Draw your weapon and attack your target on your weapon's delay "
                                                 "(magic and abilities go on meanwhile)." if not e.engaged else
                                                 "Put your weapon away."})
        spells = e.known_spells()
        if spells:
            schools = {}
            for n in spells:
                schools.setdefault(D.SPELLS[n]["school"], []).append(n)
            names = {"white": "White Magic", "black": "Black Magic", "song": "Songs", "ninjutsu": "Ninjutsu",
                     "summoning": "Summoning Magic", "blue": "Blue Magic", "geomancy": "Geomancy", "trust": "Trust"}
            if len(schools) == 1:
                root.append({"label": "Magic", "ok": True, "detail": "", "children": [action(n) for n in spells]})
            else:
                root.append({"label": "Magic", "ok": True, "detail": "", "children": [
                    {"label": names.get(s, s.title()), "ok": True, "detail": "",
                     "children": [action(n) for n in ns]} for s, ns in schools.items()]})
        if self.pool:                                             # FFXI's treasure pool: Lot or Pass each
            root.append({"label": "Treasure Pool", "ok": True, "detail": f"{len(self.pool)}", "children": [
                {"label": x["name"], "ok": not x["passed"], "icon": x["iid"],
                 "detail": "Passed" if x["passed"] else f"{int(max(0, x['until'] - now)) // 60}:{int(max(0, x['until'] - now)) % 60:02d}",
                 "children": [{"label": "Lot", "ok": True, "detail": "", "act": ("lot", i)},
                              {"label": "Pass", "ok": not x["passed"], "detail": "", "act": ("pass", i)}]}
                for i, x in enumerate(self.pool)]})
        if self.party and self.party.members:                     # the Trusts called: each to send away
            called = [m.trust["name"] for m in self.party.members]
            root.append({"label": "Party", "ok": True, "detail": f"{len(called)}", "children": [
                {"label": "Release All", "ok": True, "detail": "", "act": ("release", None)}] + [
                {"label": f"Release {n}", "ok": True, "detail": "", "act": ("release", n)} for n in called]})
        abilities = []
        if e.known_abilities():
            abilities.append({"label": "Job Abilities", "ok": True, "detail": "",
                              "children": [action(n) for n in e.known_abilities()]})
        if e.known_ws():
            abilities.append({"label": "Weapon Skills", "ok": e.tp >= D.TP_WS_MIN, "detail": "",
                              "children": [action(n) for n in e.known_ws()]})
        if abilities:
            root.append({"label": "Abilities", "ok": True, "detail": "", "children": abilities})
        if self.gear:
            root += self._gear_menu()
        # Monster Hunter's own item use (as a tap of the pad's X): named apart from FFXI's Items, the inventory
        root.append({"label": "Pouch Item", "ok": True, "detail": "", "act": ("item",),
                     "help": "Monster Hunter's item pouch: uses the item chosen in the game's item bar "
                             "(a potion, a whetstone...), as a tap of X does.\nFFXI's gear is in Items."})
        jobs = [{"label": d["name"], "ok": j != e.job, "detail": f"Lv{e.levels.get(j, 1)}", "act": ("job", j)}
                for j, d in D.JOBS.items()]
        subs = [{"label": "None", "ok": e.sub is not None, "detail": "", "act": ("sub", None)}]
        subs += [{"label": d["name"], "ok": j not in (e.job, e.sub), "detail": f"Lv{e.levels.get(j, 1)}",
                  "act": ("sub", j)} for j, d in D.JOBS.items()]
        root.append({"label": "Job", "ok": True, "detail": e.job_label(), "children": [
            {"label": "Main Job", "ok": True, "detail": e.job, "children": jobs},
            {"label": "Support Job", "ok": not e.all_jobs, "detail": e.sub or "", "children": subs},
            {"label": "All Jobs", "ok": True, "detail": "On" if e.all_jobs else "Off", "act": ("all_jobs",),
             "help": "On: every job's magic and abilities at your level on one character (solo play). "
                     "Off: your main job's, and your support job's at half its level."},
            {"label": "All Magic", "ok": True, "detail": "On" if e.all_magic else "Off", "act": ("all_magic",),
             "help": "On: every spell unlocked from the start, whatever your job and level. "
                     "Off: the spells your jobs have learnt by their levels."}]})
        root.append({"label": "Config", "ok": True, "detail": "", "children": [
            {"label": "Combat Mode", "ok": True, "detail": "FFXI" if e.ffxi_combat else "Monster Hunter",
             "act": ("combat",),
             "help": "FFXI: Attack fights for you (Y and B don't swing while it does), the d-pad picks your "
                     "target, a fallen target is carved.\nMonster Hunter: Y and B swing, the d-pad is the game's "
                     "(/mhcombat)."},
            {"label": "Interface", "ok": True, "detail": "Monster Hunter" if self.mh_hud else "FFXI", "act": ("ui",),
             "help": "FFXI's windows, or Monster Hunter's own HUD in their place (F9, /ui)."}]})
        return root

    def _gear_menu(self) -> list[dict]:
        """Equipment (each slot: what's worn, and what you hold that goes there) and Items, FFXI's inventory
        (each piece: equip it, drop it). A piece's description shows beside the menu (help)."""
        import ffxi_gear as G
        g, e = self.gear, self.e
        held = sorted(set(e.inventory), key=lambda i: (-(g.item(i) or {}).get("level", 0), (g.item(i) or {}).get("name", "")))
        slots = []
        for slot in G.SLOTS:
            worn = g.item(e.equipment[slot]) if slot in e.equipment else None
            choices = []
            if worn:
                choices.append({"label": "(Remove)", "ok": True, "detail": "", "act": ("equip", slot, None)})
            for iid in held:
                it = g.item(iid)
                if not it or slot not in g.slots_of(it):
                    continue
                why = g.why_not(it, slot)
                choices.append({"label": it["name"], "ok": not why, "detail": f"Lv{it['level']}", "icon": iid,
                                "help": g.describe(it) + (f"\n({why})" if why else ""), "act": ("equip", slot, iid)})
            slots.append({"label": G.SLOT_LABEL[slot], "ok": bool(choices), "detail": worn["name"] if worn else "",
                          "icon": e.equipment.get(slot, 0),
                          "help": g.describe(worn) if worn else "", "children": choices or
                          [{"label": "(Nothing to wear here)", "ok": False, "detail": ""}]})
        slots.append({"label": "Lockstyle", "ok": True, "detail": "On" if e.lockstyle else "Off", "act": ("lockstyle",),
                      "help": "On: the look you have now stays, whatever you wear (FFXI's /lockstyle).\n"
                              "Off: your equipment shows."})
        stats = g.stats()
        shown = " ".join(f"{k}{'' if k == 'DEF' else '+'}{v}" if k == 'DEF' else f"{k}+{v}"
                         for k, v in stats.items() if k not in ("Delay",))
        items = []
        for iid in held:
            it = g.item(iid)
            if not it:
                continue
            n = e.inventory.count(iid)
            worn = [s for s, i in e.equipment.items() if i == iid]
            first = next((s for s in g.slots_of(it) if s not in e.equipment), (g.slots_of(it) or [None])[0])
            why = g.why_not(it, first) if first else "It can't be worn."
            items.append({"label": it["name"] + (f" x{n}" if n > 1 else ""), "ok": True, "icon": iid,
                          "detail": "worn" if worn else f"Lv{it['level']}", "help": g.describe(it), "children": [
                              {"label": "Equip", "ok": bool(first) and not why and not worn, "detail": "",
                               "help": why, "act": ("equip", first, iid)},
                              {"label": "Drop", "ok": True, "detail": "", "act": ("drop", iid)}]})
        return [{"label": "Equipment", "ok": True, "detail": shown[:24], "help": "Your gear's stats: " + (shown or "none"),
                 "children": slots},
                {"label": "Items", "ok": bool(items), "detail": f"{len(e.inventory)}",
                 "help": "Your FFXI gear: equip a piece, or drop it.",
                 "children": items or [{"label": "(Empty)", "ok": False, "detail": ""}]}]

    def _menu_levels(self, now: float) -> list[tuple[str, list[dict], int, int]]:
        """The open levels: (title, nodes, cursor, scroll) from the first to the deepest."""
        out, nodes, title = [], self.menu_root(now), "Command"
        for cur, scroll in self.menu["stack"]:
            cur = min(cur, len(nodes) - 1) if nodes else 0
            out.append((title, nodes, cur, scroll))
            if not nodes or "children" not in nodes[cur]:
                break
            title, nodes = nodes[cur]["label"], nodes[cur]["children"]
        return out[:len(self.menu["stack"])]

    def _menu_step(self, now: float):
        mem = self.mem
        if not hasattr(mem, "menu_open"):
            return
        if not mem.menu_open():
            mem.menu_events()                      # what was pressed before it closed: not ours
            self.menu = None
            return
        if self.menu is None:
            self.menu = {"stack": [[0, 0]]}
        import port_bridge as P
        # its confirm and back: FFXI's combat, the pad's Y and X (A and B left to the game); else its A and B
        confirm, back = (P.CL_X, P.CL_Y) if self.e.ffxi_combat else (P.CL_B, P.CL_A)
        for ev in mem.menu_events():
            levels = self._menu_levels(now)
            title, nodes, cur, scroll = levels[-1]
            top = self.menu["stack"][-1]
            n = max(1, len(nodes))
            if ev & P.CL_UP:
                top[0] = (cur - 1) % n
            elif ev & P.CL_DOWN:
                top[0] = (cur + 1) % n
            elif ev & P.CL_LEFT:                   # a page up
                top[0] = max(0, cur - MENU_ROWS)
            elif ev & P.CL_RIGHT:                  # a page down
                top[0] = min(n - 1, cur + MENU_ROWS)
            elif ev & confirm:                     # the pad's Y (FFXI's combat) or A: confirm
                node = nodes[cur] if nodes else None
                if node and "children" in node:
                    self.menu["stack"].append([0, 0])
                elif node and node["ok"]:
                    self._menu_act(node["act"])
                    break
            elif ev & back:                        # the pad's X (FFXI's combat) or B: back
                self.menu["stack"].pop()
                if not self.menu["stack"]:
                    self._menu_close()
                    break
            top = self.menu["stack"][-1]           # keep the cursor in view
            if top[0] < top[1]:
                top[1] = top[0]
            elif top[0] >= top[1] + MENU_ROWS:
                top[1] = top[0] - MENU_ROWS + 1

    def _menu_act(self, act: tuple):
        e = self.e
        if act[0] == "use":
            self.actions.put(("use", act[1]))
        elif act[0] == "attack":
            self.attack(not e.engaged)
        elif act[0] == "all_magic":
            e.set_all_magic(not e.all_magic)
            self.save()
            return                                     # the menu stays open: its detail shows the change
        elif act[0] == "all_jobs":
            e.set_all_jobs(not e.all_jobs)
            self._gear_check()
            self.save()
            return                                     # the menu stays open: its detail shows the change
        elif act[0] == "item":
            self.mem.use_item()                    # Monster Hunter's own item use
        elif act[0] == "release":
            if self.party:
                self.party.release(act[1])
        elif act[0] == "lot":
            self.pool_lot(act[1])
            self._menu_close()
            return
        elif act[0] == "pass":
            self.pool_pass(act[1])
            return
        elif act[0] == "combat":
            self.set_combat(not e.ffxi_combat)
            self.save()
            return                                     # the menu stays open: its detail shows the change
        elif act[0] == "ui":
            self.swap_ui()
            return
        elif act[0] == "lockstyle":
            self.lockstyle(not e.lockstyle)
            return                                     # the menu stays open: its detail shows the change
        elif act[0] == "job":
            e.change_job(act[1], e.sub if e.sub != act[1] else None)
            self._gear_check()
            self.save()
        elif act[0] == "sub":
            e.change_job(e.job, act[1])
            self._gear_check()
            self.save()
        elif act[0] == "equip":
            self.equip(act[1], act[2])
            return                                     # the menu stays open: more to change
        elif act[0] == "drop":
            it = self.gear.item(act[1]) if self.gear else None
            if it:
                self.gear.drop(act[1])
                e.say(f"{e.me} drops {it['name']}.", "info")
                self.save()
                self._redress()
            if self.menu and len(self.menu["stack"]) > 1:
                self.menu["stack"].pop()
            return
        self._menu_close()

    def _gear_check(self) -> None:
        """After a job change: off with what the new job can't wear."""
        if self.gear:
            off = self.gear.check_worn()
            self.gear.apply()
            if off:
                self.e.say(f"{self.e.me} can't wear what was in: {', '.join(off)}.", "warn")
                self._redress()

    def _menu_close(self):
        self.mem.close_menu()
        self.menu = None

    def menu_view(self, now: float) -> tuple:
        """The menu as ffxi_ui draws it: per level, its title, the rows in view, the cursor among them,
        and whether there is more above and below."""
        if self.menu is None:
            return ()
        out = []
        for title, nodes, cur, scroll in self._menu_levels(now):
            rows = tuple((nd["label"], nd["ok"], nd["detail"], nd.get("icon", 0)) for nd in nodes[scroll:scroll + MENU_ROWS])
            out.append((title, rows, cur - scroll, scroll > 0, scroll + MENU_ROWS < len(nodes)))
        return tuple(out)

    def menu_help(self, now: float) -> str:
        """The highlighted entry's description (an item's stats), shown beside the menu."""
        if self.menu is None:
            return ""
        levels = self._menu_levels(now)
        if not levels:
            return ""
        _title, nodes, cur, _scroll = levels[-1]
        return nodes[cur].get("help", "") if nodes else ""

    # --- the FFXI interface in the game's window (the port) ---
    def ui_state(self, now: float):
        import ffxi_ui as U
        e = self.e
        buffs = {}
        for (src, eff), b in e.buffs.items():
            if b["until"] - now < 3600:
                buffs[src] = min(buffs.get(src, 1e9), b["until"] - now)
            else:
                buffs.setdefault(src, None)                       # a stance: no timer
        effects = [(src, None if left is None else int(left)) for src, left in buffs.items()]
        if e.pet:
            effects.insert(0, (e.pet["name"], int(e.pet["until"] - now)))
        if now < e.mb_until:
            effects.append((f"MB {e.sc_name}", int(e.mb_until - now)))
        elif D.SC_WINDOW_OPEN <= now - e.last_ws_time <= D.SC_WINDOW_CLOSE:
            effects.append(("Chain", int(D.SC_WINDOW_CLOSE - (now - e.last_ws_time))))
        keys = macro_keys(self.cfg)
        pal = []
        held = self.mem.macro_held() if hasattr(self.mem, "macro_held") and e.ffxi_combat else 0
        if held:                                                   # a trigger held: its bar, as FFXI's on a pad
            labels = ("\u2191", "\u2192", "\u2193", "\u2190", "Y", "B", "A", "X")
            bar = self.pad_macros()[8 * (held - 1):8 * held]
            entries = zip(labels, bar)
        else:
            entries = ((key_name(k), n) for k, n in zip(keys, self.macros()))
        for label, n in entries:
            if n:
                ok, _ = e.can_use(n, now)
                left = e.recasts.get(n, 0) - now
                pal.append((label, n, ok, f"{int(left) + 1}s" if left > 0 else ""))
        cast_frac = 1 - max(0.0, e.cast_done - now) / e.cast_total if e.casting and e.cast_total else 0.0
        target = check = None
        if self.monster_hp is not None and self.monster_max:
            target = max(0.0, min(1.0, self.monster_hp / self.monster_max))
            check = e._mob_label()
        elif self.port_target:                                     # targeted, not yet hit: as FFXI, unhurt
            target = 1.0
        if self.mh_hud:                    # Monster Hunter's HUD in its place: only what's asked for
            typing = self.chat_typing is not None
            return U.UiState(name=e.me, log=self._log_lines(now) if typing else (), chat_input=self.chat_typing,
                             menu=self.menu_view(now), menu_help=self.menu_help(now))
        return U.UiState(
            name=e.me, job=e.job_label(), hp=self.player_hp, max_hp=e.player_max_hp,
            mp=int(e.mp), max_mp=e.max_mp, tp=int(e.tp), exp=e.exp_now, tnl=e.tnl,
            effects=tuple(effects[:8]),
            target_pct=None if target is None else round(target, 3), target_name=e.target.title(),
            target_check=check, target_effects=tuple(sorted({s for s, _ in e.debuffs})),
            casting=e.casting, cast_frac=round(cast_frac, 2),
            log=self._log_lines(now), palette=tuple(pal), menu=self.menu_view(now), menu_help=self.menu_help(now),
            nameplate=self._nameplate(), linkpearl=self._linkpearl(), **self._target_arrow(now),
            chat_input=self.chat_typing, party=self.party.party_rows() if self.party else (), gm=self.gm,
            **self._equip_grid(now), palette_title=("LT", "RT")[held - 1] if held else "",
            clock=self._clock() if self.cfg.get("vanadiel_clock", True) is not False else None,
            pool=tuple((x["iid"], x["name"], int(max(0, x["until"] - now)), x["passed"]) for x in self.pool),
            banner=self.banner[0] if self.banner and now < self.banner[1] else None,
            party_names=self.party.nameplates() if self.party and self.cfg.get("nameplate", True) is not False else (),
            status="" if self.status == "connected" else self.status)

    def _clock(self) -> tuple:
        """Vana'diel's day and hour: (day, element, hour, minute), the minute in tens (redrawn at most then)."""
        v = vana_time(time.time())
        return (v["day"], v["element"], v["hour"], v["minute"] // 10 * 10)

    def _equip_grid(self, now: float) -> dict:
        """FFXI's Equipment window while the menu's Equipment is open: what's worn in each slot, and the slot
        the menu points at."""
        if self.menu is None or not self.gear:
            return {}
        import ffxi_gear as G
        levels = self._menu_levels(now)
        for i, (title, nodes, cur, _scroll) in enumerate(levels):
            if title == "Equipment":
                grid = tuple(int(self.e.equipment.get(s) or 0) for s in G.SLOTS)
                return {"equip_grid": grid, "equip_cursor": cur if cur < len(G.SLOTS) else -1}
        return {}

    def _target_arrow(self, now: float) -> dict:
        """FFXI's arrow above the port's target (the nearest monster, or the one locked on), bobbing."""
        t = self.port_target if self.cfg.get("target_arrow", True) else None
        if not t or not t["arrow"]:
            return {"target_locked": bool(t and t["locked"])}
        bob = round((now * 1.2) % 1.0, 2)                       # a little over a second up and down
        return {"target_arrow": (round(t["arrow"][0], 3), round(t["arrow"][1], 3), t["locked"], bob),
                "target_locked": t["locked"]}

    def _log_lines(self, now: float) -> tuple:
        """The combat log's last lines, each gone log_fade seconds after it was said (0: they stay)."""
        e, fade = self.e, float(self.cfg.get("log_fade", 10))
        lines = list(zip(e.log, e.kinds, e.times))[-7:]
        if fade > 0:
            lines = [(t, k, at) for t, k, at in lines if now - at < fade]
        return tuple((t, k) for t, k, _ in lines)

    def _start_gear(self):
        """Your FFXI equipment: the item database read from your installation (once, into the asset
        folder), what you wear applied; a new adventurer is handed a starter kit."""
        if self.cfg.get("equipment", True) is False:
            return None
        try:
            import ffxi_gear as G
            import ffxi_items as I
            import ffxi_model as FM
            known = I.load_items()
            if not known or "flags" not in next(iter(known.values())):    # none yet, or from before the flags
                print("FFXI items: reading them from your installation (once)...")
                I.build(FM.ffxi_root(self.cfg.get("ffxi_path")))
            gear = G.Gear(self.e, self.cfg.get("character_race") or "hume_male")
            if not gear.ready():
                return None
            gear.gm = os.environ.get("FFXI_GM") == "1" or self.cfg.get("gm", False) is True   # a GM wears anything
            if not self.e.inventory:
                kit = gear.starter_kit()
                if kit:
                    self.e.say(f"{self.e.me} receives a starter kit: {', '.join(kit)}.", "skillchain")
                    self.e.dirty = True
            self._creator_gear(gear)
            off = gear.check_worn()
            if off:
                self.e.say(f"{self.e.me} can't wear what was in: {', '.join(off)}.", "warn")
            gear.apply()
            print(f"FFXI gear: {len(gear.items)} pieces known; holding {len(self.e.inventory)}, wearing "
                  f"{len(self.e.equipment)}: {gear.stats()}")
            return gear
        except Exception as ex:                                     # noqa: BLE001  the layer runs without it
            print(f"[FFXI equipment unavailable: {ex}]")
            return None

    def _drops(self, mob_level: int) -> None:
        """A defeated monster's drops: FFXI's gear about its level, yours to keep."""
        import mh3_text
        import ffxi_model as FM
        t = self.port_target
        big = bool(t) and (mh3_text.model_number(t["name"]) or 0) in LARGE_MONSTERS
        now = time.monotonic()
        for it in self.gear.roll_drop(mob_level, big):
            if self.cfg.get("treasure_pool", True) is False:
                self.gear.give(it["id"])
                article = "an" if it["name"][:1].lower() in "aeiou" else "a"
                self.e.say(f"{self.e.me} obtains {article} {it['name']}.", "skillchain")
            else:
                self._pool_add(it, now)
        self.save()

    def equip(self, slot: str, iid) -> None:
        """Wear it (None: take it off); the character dressed again if its look changed."""
        if not self.gear:
            return
        before = self.gear.look({})
        why = self.gear.equip(slot, iid)
        if why:
            self.e.say(why, "warn")
            return
        it = self.gear.item(iid) if iid is not None else None
        self.e.say(f"{self.e.me} equips {it['name']}." if it else f"{self.e.me} unequips the {slot}.", "info")
        self.save()
        if self.gear.look({}) != before:
            self._redress()

    def _redress(self) -> None:
        """The character built again (its look or weapon changed), on a thread of its own."""
        import threading
        if self._rebuilding or not self.cfg.get("character_race"):
            return
        self._rebuilding = True

        def rebuild():
            try:
                pm = self._send_character()
                if pm:
                    self.character = pm
            finally:
                self._rebuilding = False
        threading.Thread(target=rebuild, daemon=True).start()

    def _chat_step(self) -> None:
        """FFXI's chat line, typed in the game's window ("/" opens it): each line sent handled."""
        if not hasattr(self.mem, "chat"):
            return
        is_open, line, seq, sent = self.mem.chat()
        self.chat_typing = line if is_open else None
        if self.chat_seen is None:
            self.chat_seen = seq
        if seq != self.chat_seen:
            self.chat_seen = seq
            self.chat_command(sent)

    def typing(self) -> bool:
        return self.chat_typing is not None

    def chat_command(self, text: str) -> None:
        """A line from the chat line: an emote (/wave), an action (/ma "Cure", /ja, /ws), /echo, /hunter
        (Monster Hunter's hunter in the character's place, and back), /light 2 (the character's brightness
        under the game's light), /shade 0.5 (how much dark ground dims it), or words said."""
        import ffxi_model as FM
        e, text = self.e, text.strip()
        if not text:
            return
        if text.startswith("!") and self.gm:                         # a GM's command (LandSandBoat's words)
            self._gm_command(text[1:])
            return
        if not text.startswith("/"):
            e.say(f"{e.me} : {text}", "say")
            return
        word, _, rest = text[1:].partition(" ")
        word, rest = word.lower(), rest.strip().strip('"')
        rest = re.sub(r"\s*<\w+>$", "", rest).strip().strip('"')   # a target in brackets (<t>, <me>): the one there is
        if word == "heal":                                         # FFXI's resting, HP and MP coming back
            self.rest(not self.resting if rest.lower() not in ("on", "off") else rest.lower() == "on")
        elif word in FM.EMOTES:
            code, msg, hold = FM.EMOTES[word]
            e.say(msg.format(me=e.me, their="their"), "emote")
            if self.character:
                self.character.request(code, hold if isinstance(hold, str) else (code if hold else None))
        elif word in ("attack", "a"):
            self.attack(rest.lower() != "off" and not (self.e.engaged and not rest))
        elif word in ("mhcombat", "buttons"):                      # Monster Hunter's combat on (or off: FFXI's)
            self.set_combat(rest.lower() in ("off", "0") if rest else not e.ffxi_combat)
            self.save()
        elif word in ("lot", "pass"):                              # FFXI's: /lot, /pass (all, or the first)
            if not self.pool:
                e.say("There is nothing in the treasure pool.", "warn")
            elif word == "lot":
                self.pool_lot(0)
            else:
                for i in range(len(self.pool)) if rest.lower() == "all" else [0]:
                    self.pool_pass(i)
        elif word == "combat":                                     # /combat ffxi | mh
            self.set_combat(rest.lower() not in ("mh", "monster", "monsterhunter") if rest else not e.ffxi_combat)
            self.save()
        elif word in ("ui", "hud"):                                # FFXI's interface or Monster Hunter's
            self.swap_ui({"mh": True, "monster": True, "ffxi": False}.get(rest.lower()) if rest else None)
        elif word == "voice":                                      # the character's voice: tried, kept
            self.voice_command(rest.lower())
        elif word == "lockstyle":                                  # FFXI's: the look kept, whatever is worn
            self.lockstyle(rest.lower() != "off" if rest else not e.lockstyle)
        elif word in ("refa", "release"):                          # FFXI's: /refa all sends the Trusts away
            if not self.party:
                e.say("No Trusts here.", "warn")
            else:
                name = next((m.trust["name"] for m in self.party.members if m.trust["name"].lower() == rest.lower()), None)
                self.party.release(None if rest.lower() in ("", "all") else name or rest)
        elif word in ("s", "say"):
            e.say(f"{e.me} : {rest}", "say")
        elif word == "echo":
            e.say(rest, "info")
        elif word == "light":                                      # the character's brightness, tuned by eye
            try:
                self.cfg["character_light"] = max(0.0, min(8.0, float(rest)))
            except ValueError:
                pass
            if self.character:
                self._show_flags(self.character)
            e.say(f"Character light: {self.cfg.get('character_light', 2.0):g} (0: unlit; 2: lit by "
                  "the game's lights; character_light in config.toml keeps it).", "info")
        elif word == "shade":                                      # how much dark ground dims it, by eye
            try:
                self.cfg["character_ground_shade"] = max(0.0, min(1.0, float(rest)))
            except ValueError:
                pass
            if self.character:
                self._show_flags(self.character)
            e.say(f"Ground shade: {self.cfg.get('character_ground_shade', 0.0):g} (0: as the hunter, not dimmed; "
                  "1: as dark as the ground; character_ground_shade in config.toml keeps it).", "info")
        elif word in ("hunter", "compare"):                        # the two in the same light, one then the other
            self.show_hunter = not self.show_hunter
            if self.character:
                self._show_flags(self.character)
            e.say("Monster Hunter's hunter shown (/hunter again: the FFXI character)." if self.show_hunter
                  else "The FFXI character shown.", "info")
        elif word in ("ma", "magic", "ja", "jobability", "ws", "weaponskill", "pet", "use"):
            name = next((n for n in list(D.SPELLS) + list(D.ABILITIES) + list(D.WEAPON_SKILLS)
                         if n.lower() == rest.lower()), None)
            if name:
                self.actions.put(("use", name))
            else:
                e.say(f"No such action: {rest}.", "warn")
        else:
            e.say(f"Unknown command: /{word}.", "warn")

    def _show_flags(self, pm) -> None:
        """The character drawn and the hunter hidden, or (/hunter) the hunter alone; and how bright it is
        under the game's light (character_light: 0, unlit, its textures as they are; lit, FFXI's textures
        are made for twice the light: 2) and how much dark ground dims it (character_ground_shade: 0, as
        the game's own hunter)."""
        pm.set_flags(not self.show_hunter, not self.show_hunter and self.cfg.get("hide_hunter", True),
                     self.gm and self.cfg.get("gm_aura", True) is not False)          # a GM's aura about it
        pm.set_light(self.cfg.get("character_light", 2.0), self.cfg.get("character_ground_shade", 0.0))
        # its shadow (character_shadow): "ffxi", FFXI's round one in place of Monster Hunter's foot shadows;
        # "mh", Monster Hunter's kept; "none"
        mode = str(self.cfg.get("character_shadow", "ffxi")).lower()
        radius = 0.24 * getattr(self, "_char_top", 175.0) * float(self.cfg.get("character_shadow_size", 1.0))
        pm.set_shadow(not self.show_hunter and mode != "mh", not self.show_hunter and mode == "ffxi", radius,
                      float(self.cfg.get("character_shadow_darkness", 0.55)))

    def _weapon_watch(self) -> None:
        """Monster Hunter's weapon, from its hunter's motion's name (w03_*: the lance): when it changes,
        the character is built again holding FFXI's like of it, on a thread of its own."""
        import threading
        import ffxi_model as FM
        if not self.character or not self.cfg.get("character_race"):
            return
        m = self.character.motion()
        if not (len(m) > 3 and m[0] == "w" and m[1:3].isdigit()):
            return
        kind = FM.MH_WEAPONS.get(int(m[1:3]))
        if not kind or kind == self.mh_weapon or self._rebuilding:
            return
        self.mh_weapon = kind
        self._redress()

    def _send_character(self):
        """FFXI's character in your hunter's place, read from your own FFXI installation and handed to the
        port, which draws it where the hunter stands: a player character (character_race and
        character_look in config.toml: character.py chooses them) or a model file (character_model)."""
        race, dat = self.cfg.get("character_race"), self.cfg.get("character_model")
        if not race and not dat:
            return None
        try:
            import ffxi_model as FM
            from port_model import PortModel
            root = FM.ffxi_root(self.cfg.get("ffxi_path"))
            height, yaw = self.cfg.get("character_height", 175.0), self.cfg.get("character_yaw", 0.0)
            mirror = self.cfg.get("character_mirror", True)
            if race:
                look = {**FM.DEFAULT_LOOK, **self.cfg.get("character_look", {})}
                if self.e.lockstyle:                                # /lockstyle on: the look kept
                    look = {**look, **self.e.lockstyle}
                elif self.gear:                                     # what you wear shows; the creator's elsewhere
                    look = self.gear.look(look)
                look, style = FM.look_for_weapon(root, race, look, self.mh_weapon)
                model = FM.load_character(root, race, look, style)
                # the weapon's skills, each the race's own motions from its file, played as its look plays them
                skills = {n: D.WS_ANIMATION[n] for _, n in D.WEAPONS.get(self.mh_weapon, {}).get("ws", [])
                          if n in D.WS_ANIMATION}
                ws_clips, self._ws_plans = FM.ws_clips(root, race, skills)
                model["clips"] = model.get("clips", []) + ws_clips
                self.e.ws_land = {n: p["lands"] for n, p in self._ws_plans.items() if p["lands"]}
                packed = FM.pack(model, yaw=yaw, mirror=mirror, scale=height / FM.HUME_MALE_HEIGHT)
                if race.startswith("tarutaru"):
                    # a Tarutaru's run covers 195 units a second to a Hume's 450: kept to the hunter's
                    # pace it plays at twice its speed, a scurry; counted as covering more, it plays at
                    # about 1.3 times (its feet slide a little)
                    slower = float(self.cfg.get("tarutaru_run_slower", 1.8))
                    for c in packed["clips"]:
                        if c["name"] in ("run", "brun") and c["speed"]:
                            c["speed"] *= slower
                what = (f"{FM.RACES[race]['label']} " + " ".join(f"{k} {v}" for k, v in look.items())
                        + f", holding a {FM.STYLES.get(style, '?')} for Monster Hunter's {self.mh_weapon.replace('_', ' ')}")
            else:
                model = FM.load(root / dat)
                packed = FM.pack(model, height, yaw, mirror)
                what = dat
            if hasattr(self.mem, "set_name_height"):             # your name 25 units above its head
                place = packed["place"]
                rest = FM.skin_corners(packed["verts"], FM.joint_world(model["skeletons"][0]))
                top = float((rest @ place[:, :3].T + place[:, 3])[:, 1].max())
                self.mem.set_name_height(top + 25)
                self._char_top = top
            pm = PortModel()
            pm.send(packed)
            self._show_flags(pm)
            moves = ", ".join(f"{c['name']}" + (f" {c['speed']:.0f}/s" if c["speed"] else "") for c in packed["clips"])
            print(f"FFXI character: {what}, {len(packed['verts']) // 3} triangles, {len(packed['layers'])} textures, "
                  f"{len(packed['parents'])} joints; motions {moves}")
            return pm
        except (Exception, SystemExit) as ex:                      # the layer runs without it
            print(f"[FFXI character unavailable: {ex}]")
            return None

    def _linkpearl(self):
        """The linkpearl's colour by your name (the launcher's choice, or linkpearl = "#rrggbb" in the config)."""
        import os
        v = os.environ.get("FFXI_LINKPEARL") or self.cfg.get("linkpearl") or ""
        v = v.strip().lstrip("#")
        try:
            return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)) if len(v) == 6 else None
        except ValueError:
            return None

    def _nameplate(self):
        if not hasattr(self.mem, "nameplate") or self.cfg.get("nameplate", True) is False:
            return None
        p = self.mem.nameplate()
        return None if p is None else (round(p[0], 3), round(p[1], 3))

    def run_game_ui(self, seconds: float | None = None):
        """The FFXI interface drawn in the game's window, through the port (port_overlay.py)."""
        import ffxi_ui as U
        from port_overlay import PortOverlay
        out, ui = PortOverlay(), U.FfxiUi(self.cfg.get("window_theme", 1), self.cfg.get("ffxi_assets", True))
        model = self.character = self._send_character()
        self.sounds = self._start_sounds()
        self.fx = self._start_fx()
        self._start_trusts()
        start, last_draw = time.monotonic(), 0.0
        print("FFXI interface: in the game's window. Ctrl+C to stop.")
        close_after = float(self.cfg.get("close_with_game", 8))  # the game gone this long: the layer closes too
        alive_at = None
        try:
            while seconds is None or time.monotonic() - start < seconds:
                self.step()
                now = time.monotonic()
                if self.mem.connected():
                    alive_at = now
                elif alive_at is not None and close_after > 0 and now - alive_at > close_after:
                    print("The game has closed: the FFXI layer closes with it.")
                    break
                if now - last_draw >= 1 / 20:            # 20 images a second at most, only when it changed
                    last_draw = now
                    img = ui.render(self.ui_state(now))
                    if img is not None:
                        out.show(img)
                out.keep_alive()
                self._fx_step(now)
                self._trust_step(now)
                if model:
                    model.keep_alive()
                time.sleep(1 / 60)
        except KeyboardInterrupt:
            pass
        finally:
            out.show(None)
            if self.party:
                self.party.clear()
            self.save()

    def _drain_actions(self, only_pause=False):
        out = []
        while True:
            try:
                a = self.actions.get_nowait()
            except queue.Empty:
                return out
            if a[0] == "pause":
                self.paused = not self.paused
                self.e.say("Layer paused." if self.paused else "Layer resumed.")
            elif a[0] == "ui":
                self.swap_ui()
            elif not only_pause:
                out.append(a)

    # --- console UI ---
    def run_console(self, seconds: float | None = None):
        start, last_print, seen = time.monotonic(), 0.0, self.e.said
        try:
            while seconds is None or time.monotonic() - start < seconds:
                self.step()
                if time.monotonic() - last_print > 0.5:
                    last_print = time.monotonic()
                    print(self.status_line())
                new = min(self.e.said - seen, len(self.e.log))
                for line in self.e.log[len(self.e.log) - new:] if new > 0 else []:
                    print("   >", line)
                seen = self.e.said
                time.sleep(1 / 60)
        finally:
            self.save()

    def status_line(self) -> str:
        e = self.e
        return (f"[{self.status}] {e.job_label()} | TP {int(e.tp):4d} | MP {int(e.mp)}/{e.max_mp} | "
                f"HP {self.player_hp} | Monster {self.monster_hp} | EXP {e.exp_now}/{e.tnl} | avg hit {e.avg_hit:.0f}"
                + (f" | casting {e.casting}" if e.casting else ""))

    # --- a separate overlay window (Dolphin, or the simulation) ---
    def run_overlay(self):
        import tkinter as tk
        root = tk.Tk()
        root.title("FFXI layer")
        root.overrideredirect(True)   # no title bar, doesn't steal focus from Dolphin
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.88)
        except tk.TclError:
            pass
        BG, FG, DIM = "#0b1020", "#e8ecf6", "#8a93ad"
        root.configure(bg=BG)
        root.geometry("+30+30")
        c = tk.Canvas(root, width=380, height=260, bg=BG, highlightthickness=1, highlightbackground="#2a3350")
        c.pack()
        drag = {}
        c.bind("<ButtonPress-1>", lambda ev: drag.update(x=ev.x, y=ev.y))
        c.bind("<B1-Motion>", lambda ev: root.geometry(f"+{ev.x_root - drag['x']}+{ev.y_root - drag['y']}"))
        c.bind("<Double-Button-1>", lambda ev: root.destroy())

        def draw():
            try:
                self.step()
            except Exception as ex:  # keep the overlay alive; show the error
                self.e.say(f"error: {ex}", "warn")
            now = time.monotonic()
            s = self.ui_state(now)
            c.delete("all")
            y = 14
            c.create_text(12, y, text=f"{s.name}  {s.job}   TP {s.tp}", fill=FG, anchor="w", font=("Segoe UI", 10, "bold"))
            y += 18
            c.create_text(12, y, text=f"HP {s.hp}/{s.max_hp}   MP {s.mp}/{s.max_mp}   EXP {s.exp}/{s.tnl}",
                          fill=FG, anchor="w", font=("Segoe UI", 9))
            y += 16
            if s.target_pct is not None:
                c.create_text(12, y, text=f"{s.target_name} {int(s.target_pct * 100)}%"
                              + (f"  ({s.target_check[0]})" if s.target_check else ""), fill="#ff9a8a",
                              anchor="w", font=("Segoe UI", 9))
                y += 16
            for k, n, ok, d in s.palette:
                c.create_text(12, y, text=f"[{k}] {n} {d}", fill=FG if ok else DIM, anchor="w", font=("Segoe UI", 8))
                y += 13
            for line, _ in s.log[-5:]:
                c.create_text(12, y, text=line, fill=FG, anchor="w", font=("Segoe UI", 8))
                y += 13
            c.config(height=y + 8)
            root.after(33, draw)

        draw()
        try:
            root.mainloop()
        finally:
            self.save()


# ================================================================================================
def load_progress(path: Path) -> dict:
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "config.toml"))
    ap.add_argument("--job", help="main job (else the save's, else config's)")
    ap.add_argument("--sub", help="support job, or none")
    ap.add_argument("--level", type=int, help="set the main job's level (testing)")
    ap.add_argument("--weapon", help="override weapon from config")
    ap.add_argument("--sim", action="store_true", help="fake fight, no game")
    ap.add_argument("--backend", choices=["port", "dolphin"], help="override backend from config")
    ap.add_argument("--no-overlay", action="store_true", help="print to the console instead of an overlay")
    ap.add_argument("--ui", choices=["game", "window", "console"],
                    help="game: in the game's window (the port's default); window: a separate overlay; console")
    ap.add_argument("--seconds", type=float, help="stop after N seconds")
    ap.add_argument("--record", metavar="FILE", help="write the fight's events to FILE (JSON lines)")
    ap.add_argument("--allow-kills", action="store_true", help="let weapon skills/spells take the monster to 0 HP")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        import ffxi_engine
        return ffxi_engine.selftest()

    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    save_path = Path(cfg.get("save", HERE / "save.json"))
    if not save_path.is_absolute():
        save_path = HERE / save_path
    saved = load_progress(save_path)
    job = (a.job or saved.get("job") or cfg.get("job", "RDM")).upper()
    sub = a.sub if a.sub is not None else saved.get("sub", cfg.get("sub"))
    sub = None if not sub or str(sub).lower() == "none" else str(sub).upper()
    weapon = a.weapon or cfg.get("weapon", "long_sword")
    if job not in D.JOBS:
        sys.exit(f"unknown job {job}; pick one of {', '.join(D.JOBS)}")
    if sub and (sub not in D.JOBS or sub == job):
        sys.exit(f"unknown or same support job {sub}")
    if weapon not in D.WEAPONS:
        sys.exit(f"unknown weapon {weapon}; pick one of {', '.join(D.WEAPONS)}")

    engine = Engine(job, weapon, cfg.get("player_max_hp", 150), allow_kills=a.allow_kills or cfg.get("allow_kills", True) is not False,
                    me=(os.environ.get("FFXI_NAME") or "").strip()[:15] or cfg.get("name", "Adventurer"),
                    exp_rate=cfg.get("exp_rate", D.EXP_RATE))
    engine.all_magic = cfg.get("all_magic", True) is not False      # a saved choice, below, wins
    engine.ffxi_combat = str(cfg.get("combat_mode", "ffxi")).lower() != "mh"
    engine.load_progress(saved)
    engine.set_jobs(job, sub)
    if a.level:
        engine.levels[job] = max(1, min(D.LEVEL_CAP, a.level))
    engine.mp = engine.max_mp
    backend = a.backend or cfg.get("backend", "port")
    if a.sim:
        mem = SimBackend(engine.player_max_hp)
    else:
        if backend == "port":
            from port_bridge import PortBackend
            mem = PortBackend(cfg.get("addresses", {}))
        elif backend == "dolphin":
            mem = DolphinBackend(cfg.get("addresses", {}))
        else:
            sys.exit(f"unknown backend {backend}; pick port or dolphin")

    app = App(engine, mem, cfg, save_path)
    if hasattr(mem, "set_name_height"):
        mem.set_name_height(cfg.get("nameplate_height", 200))
    if a.record:
        app.start_recording(a.record)
    start_hotkeys(cfg, app.actions, app.typing)
    keys = macro_keys(cfg)
    print(f"FFXI layer: {engine.me}, {D.JOBS[engine.job]['name']} ({engine.job_label()}) with "
          f"{weapon.replace('_', ' ')}. Macros: "
          + ", ".join(f"{key_name(k)} {n}" for k, n in zip(keys, app.macros()) if n)
          + f". {key_name(cfg['hotkeys'].get('pause', 0x6E))} = pause, "
          f"{key_name(cfg['hotkeys'].get('ui_swap', 0x78))} = FFXI's interface or Monster Hunter's.")
    ui = "console" if a.no_overlay else a.ui or cfg.get("ui") or ("game" if backend == "port" and not a.sim else "window")
    if ui == "console":
        app.run_console(a.seconds)
    elif ui == "game":
        app.run_game_ui(a.seconds)
    else:
        try:
            app.run_overlay()
        except Exception as ex:
            print(f"[overlay unavailable: {ex}] falling back to console")
            app.run_console(a.seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
