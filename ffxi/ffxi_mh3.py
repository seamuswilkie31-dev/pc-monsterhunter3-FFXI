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
import queue
import re
import random
import sys
import time
import tomllib
from pathlib import Path

import ffxi_data as D
from ffxi_engine import Engine

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
        self.chat_typing = None        # what's being typed, while the line is open
        self.mh_weapon = cfg.get("weapon", "sword_and_shield")   # Monster Hunter's weapon: FFXI's like is held
        self._rebuilding = False
        self.music_now = "unset"       # FFXI's piece playing (None: the game's own), and when to leave battle
        self.engaged_until = 0.0
        self._hits_seen = None
        self._hp_seen = None
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
        writes = self.e.tick(now, m, p, mx, mid, self.monster_max or None)
        for a in self._drain_actions():
            name = self.macros()[a[1]] if a[0] == "macro" and a[1] < len(self.macros()) else a[1]
            if a[0] in ("macro", "use") and name:
                writes.update(self.e.use(name, now, writes.get("monster_hp", m), writes.get("player_hp", p)))
        for name, val in writes.items():
            self.mem.write(name, val)
        self.monster_hp = writes.get("monster_hp", m)
        self.player_hp = writes.get("player_hp", p)
        self._play_events()
        self._music(now, p)
        self._weapon_watch()
        self._chat_step()
        if self.e.dirty and now - self._saved_at > 5:
            self.save()
        if self.rec:
            self._record(now, p)

    def _play_events(self):
        """The engine's events as FFXI's: a spell's casting by the character (its school's motions: start,
        chant, release) and its sound as it goes off, a level gained or lost."""
        events, self.e.events[:] = list(self.e.events), []
        for kind, name in events:
            if kind == "kill" and self.gear:
                self._drops(int(name))
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
        if not self.sounds:
            return
        import ffxi_sound as FS
        for kind, name in dict.fromkeys(events):                    # several levels at once: one fanfare
            anim = D.SPELL_ANIMATION.get(name)
            if kind == "spell" and anim is not None:
                self.sounds.play_spell(anim)
            elif kind == "level_up":
                self.sounds.play_effect(FS.LEVEL_UP)
            elif kind == "level_down":
                self.sounds.play_effect(FS.LEVEL_DOWN)

    def _music(self, now: float, player_hp) -> None:
        """FFXI's music in place of Monster Hunter's: the village's, the field's, or, engaged (the weapon
        out with a monster near, or blows traded in the last 6 seconds), battle music, tougher for a large
        monster; in the menus the game's own."""
        if not self.sounds or self.cfg.get("music", True) is False or not hasattr(self.mem, "map_name"):
            return
        tracks = {**MUSIC_TRACKS, **self.cfg.get("music_tracks", {})}
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
                player.prepare_music({**MUSIC_TRACKS, **self.cfg.get("music_tracks", {})}.values())
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
        if e.known_ws():
            root.append({"label": "Weapon Skills", "ok": e.tp >= D.TP_WS_MIN, "detail": "",
                         "children": [action(n) for n in e.known_ws()]})
        if e.known_abilities():
            root.append({"label": "Job Abilities", "ok": True, "detail": "",
                         "children": [action(n) for n in e.known_abilities()]})
        spells = e.known_spells()
        if spells:
            schools = {}
            for n in spells:
                schools.setdefault(D.SPELLS[n]["school"], []).append(n)
            names = {"white": "White Magic", "black": "Black Magic", "song": "Songs", "ninjutsu": "Ninjutsu",
                     "summoning": "Summoning Magic", "blue": "Blue Magic", "geomancy": "Geomancy"}
            if len(schools) == 1:
                root.append({"label": "Magic", "ok": True, "detail": "", "children": [action(n) for n in spells]})
            else:
                root.append({"label": "Magic", "ok": True, "detail": "", "children": [
                    {"label": names.get(s, s.title()), "ok": True, "detail": "",
                     "children": [action(n) for n in ns]} for s, ns in schools.items()]})
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
            {"label": "Support Job", "ok": True, "detail": e.sub or "", "children": subs}]})
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
                choices.append({"label": it["name"], "ok": not why, "detail": f"Lv{it['level']}",
                                "help": g.describe(it) + (f"\n({why})" if why else ""), "act": ("equip", slot, iid)})
            slots.append({"label": G.SLOT_LABEL[slot], "ok": bool(choices), "detail": worn["name"] if worn else "",
                          "help": g.describe(worn) if worn else "", "children": choices or
                          [{"label": "(Nothing to wear here)", "ok": False, "detail": ""}]})
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
            items.append({"label": it["name"] + (f" x{n}" if n > 1 else ""), "ok": True,
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
            elif ev & P.CL_B:                      # the pad's bottom button (Xbox A): confirm
                node = nodes[cur] if nodes else None
                if node and "children" in node:
                    self.menu["stack"].append([0, 0])
                elif node and node["ok"]:
                    self._menu_act(node["act"])
                    break
            elif ev & P.CL_A:                      # the pad's right button (Xbox B): back
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
        elif act[0] == "item":
            self.mem.use_item()                    # Monster Hunter's own item use
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
            rows = tuple((nd["label"], nd["ok"], nd["detail"]) for nd in nodes[scroll:scroll + MENU_ROWS])
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
        for k, n in zip(keys, self.macros()):
            if n:
                ok, _ = e.can_use(n, now)
                left = e.recasts.get(n, 0) - now
                pal.append((key_name(k), n, ok, f"{int(left) + 1}s" if left > 0 else ""))
        cast_frac = 1 - max(0.0, e.cast_done - now) / e.cast_total if e.casting and e.cast_total else 0.0
        target = check = None
        if self.monster_hp is not None and self.monster_max:
            target = max(0.0, min(1.0, self.monster_hp / self.monster_max))
            check = e._mob_label()
        elif self.port_target:                                     # targeted, not yet hit: as FFXI, unhurt
            target = 1.0
        return U.UiState(
            name=e.me, job=e.job_label(), hp=self.player_hp, max_hp=e.player_max_hp,
            mp=int(e.mp), max_mp=e.max_mp, tp=int(e.tp), exp=e.exp_now, tnl=e.tnl,
            effects=tuple(effects[:8]),
            target_pct=None if target is None else round(target, 3), target_name=e.target.title(),
            target_check=check, target_effects=tuple(sorted({s for s, _ in e.debuffs})),
            casting=e.casting, cast_frac=round(cast_frac, 2),
            log=self._log_lines(now), palette=tuple(pal), menu=self.menu_view(now), menu_help=self.menu_help(now),
            nameplate=self._nameplate(), **self._target_arrow(now), chat_input=self.chat_typing,
            status="" if self.status == "connected" else self.status)

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
            if not self.e.inventory:
                kit = gear.starter_kit()
                if kit:
                    self.e.say(f"{self.e.me} receives a starter kit: {', '.join(kit)}.", "skillchain")
                    self.e.dirty = True
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
        for it in self.gear.roll_drop(mob_level, big):
            self.gear.give(it["id"])
            article = "an" if it["name"][:1].lower() in "aeiou" else "a"
            self.e.say(f"{self.e.me} obtains {article} {it['name']}.", "skillchain")
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
        (Monster Hunter's hunter in the character's place, and back), or words said."""
        import ffxi_model as FM
        e, text = self.e, text.strip()
        if not text:
            return
        if not text.startswith("/"):
            e.say(f"{e.me} : {text}", "say")
            return
        word, _, rest = text[1:].partition(" ")
        word, rest = word.lower(), rest.strip().strip('"')
        rest = re.sub(r"\s*<\w+>$", "", rest).strip().strip('"')   # a target in brackets (<t>, <me>): the one there is
        if word in FM.EMOTES:
            code, msg, hold = FM.EMOTES[word]
            e.say(msg.format(me=e.me, their="their"), "emote")
            if self.character:
                if word == "heal":
                    self.character.request("rx0", "rx1")
                else:
                    self.character.request(code, code if hold else None)
        elif word in ("s", "say"):
            e.say(f"{e.me} : {rest}", "say")
        elif word == "echo":
            e.say(rest, "info")
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
        """The character drawn and the hunter hidden, or (/hunter) the hunter alone."""
        pm.set_flags(not self.show_hunter, not self.show_hunter and self.cfg.get("hide_hunter", True))

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
                if self.gear:                                       # what you wear shows; the creator's elsewhere
                    look = self.gear.look(look)
                look, style = FM.look_for_weapon(root, race, look, self.mh_weapon)
                model = FM.load_character(root, race, look, style)
                packed = FM.pack(model, yaw=yaw, mirror=mirror, scale=height / FM.HUME_MALE_HEIGHT)
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
        start, last_draw = time.monotonic(), 0.0
        print("FFXI interface: in the game's window. Ctrl+C to stop.")
        try:
            while seconds is None or time.monotonic() - start < seconds:
                self.step()
                now = time.monotonic()
                if now - last_draw >= 1 / 20:            # 20 images a second at most, only when it changed
                    last_draw = now
                    img = ui.render(self.ui_state(now))
                    if img is not None:
                        out.show(img)
                out.keep_alive()
                if model:
                    model.keep_alive()
                time.sleep(1 / 60)
        except KeyboardInterrupt:
            pass
        finally:
            out.show(None)
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

    engine = Engine(job, weapon, cfg.get("player_max_hp", 150), allow_kills=a.allow_kills,
                    me=cfg.get("name", "Adventurer"), exp_rate=cfg.get("exp_rate", D.EXP_RATE))
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
          + f". {key_name(cfg['hotkeys'].get('pause', 0x6E))} = pause.")
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
