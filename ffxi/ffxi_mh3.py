# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "dolphin-memory-engine>=1.3",
#     "pynput>=1.7",
#     "pillow>=10",
# ]
# ///
"""FFXI combat layer for Monster Hunter Tri (Wii): the native PC port, or Dolphin.

Adds TP, weapon skills, skillchains, magic bursts, MP, spells and jobs on top of MH Tri by reading and
writing the game's memory while you play.

    uv run ffxi_mh3.py --sim              # try it with a fake fight, no game needed
    uv run ffxi_mh3.py                    # the port, run with MH3_FFXI=1; addresses in config.toml
    uv run ffxi_mh3.py --backend dolphin  # Dolphin running MH Tri (finding addresses, comparing)
    uv run ffxi_mh3.py --job BLM --weapon hammer
    uv run ffxi_mh3.py --selftest         # check the rules engine

Personal use only. Single-player only.
"""
from __future__ import annotations

import argparse
import queue
import random
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import ffxi_data as D

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
# FFXI rules engine (no I/O - easy to test)
# ================================================================================================
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


@dataclass
class Engine:
    job: str
    weapon: str
    player_max_hp: int = 150
    allow_kills: bool = False
    me: str = "You"                 # the hunter's name, for the combat log
    target: str = "target"          # what the log calls the monster

    tp: float = 0.0
    mp: float = 0.0
    avg_hit: float = 30.0
    last_ws_time: float = -99.0
    last_ws_props: list = field(default_factory=list)
    mb_until: float = -99.0
    mb_elements: set = field(default_factory=set)
    sc_name: str = ""
    casting: str | None = None
    cast_done: float = 0.0
    regen_until: float = -99.0
    regen_next: float = 0.0
    regen_power: int = 0
    protect_until: float = -99.0
    protect_power: float = 0.0
    mp_next: float = 0.0
    prev_monster: int | None = None
    prev_player: int | None = None
    log: list = field(default_factory=list)
    kinds: list = field(default_factory=list)   # each log line's kind (ffxi_ui.KIND), for its colour

    def __post_init__(self):
        self.j = D.JOBS[self.job]
        self.w = D.WEAPONS[self.weapon]
        self.mp = self.j["mp"]
        self.tp_hit = D.tp_per_hit(self.w["delay"])

    # --- helpers ---
    def say(self, msg: str, kind: str = "info"):
        self.log.append(msg)
        self.kinds.append(kind)
        del self.log[:-12]
        del self.kinds[:-12]

    def _monster_floor(self) -> int:
        return 0 if self.allow_kills else 1

    # --- per-frame update: returns {name: new_value} memory writes ---
    def tick(self, now: float, monster_hp, player_hp, max_hp=None) -> dict:
        writes: dict = {}
        if max_hp:
            self.player_max_hp = int(max_hp)

        # monster HP: detect your hits
        if monster_hp is None:
            self.prev_monster = None
        else:
            if self.prev_monster is not None:
                delta = self.prev_monster - monster_hp
                if delta > 0:
                    self.avg_hit += (delta - self.avg_hit) * 0.2
                    self.tp = min(D.TP_MAX, self.tp + self.tp_hit * self.j["tp_mult"])
                elif delta < -300:
                    pass  # another monster
            self.prev_monster = monster_hp

        # player HP: detect damage taken
        if player_hp is not None and self.prev_player is not None:
            taken = self.prev_player - player_hp
            if taken > 0:
                self.tp = min(D.TP_MAX, self.tp + D.TP_WHEN_HIT * self.j["tp_mult"])
                if self.casting:
                    self.say(f"{self.me}'s casting of {self.casting} is interrupted.", "warn")
                    self.casting = None
                if now < self.protect_until:
                    refund = max(1, round(taken * self.protect_power))
                    player_hp = min(self.player_max_hp, player_hp + refund)
                    writes["player_hp"] = player_hp
        self.prev_player = player_hp

        # MP regen
        if now >= self.mp_next:
            self.mp = min(self.j["mp"], self.mp + self.j["mp_regen"])
            self.mp_next = now + 3.0

        # Regen ticks
        if now < self.regen_until and now >= self.regen_next and player_hp is not None:
            player_hp = min(self.player_max_hp, player_hp + self.regen_power)
            writes["player_hp"] = self.prev_player = player_hp
            self.regen_next = now + D.SPELLS["Regen"]["tick"]

        # Finish casting
        if self.casting and now >= self.cast_done:
            writes.update(self._finish_spell(now, monster_hp, player_hp))
        return writes

    # --- weapon skills ---
    def weapon_skill(self, slot: int, now: float, monster_hp) -> dict:
        if slot >= len(self.w["ws"]):
            return {}
        name = self.w["ws"][slot]
        ws = D.WEAPON_SKILLS[name]
        if self.tp < D.TP_WS_MIN:
            self.say(f"Not enough TP for {name} ({int(self.tp)}/1000).", "warn")
            return {}
        if monster_hp is None:
            self.say("You have no target.", "warn")
            return {}
        hits = ws.get("hits", 1)
        dmg = self.avg_hit * (interp_ftp(ws["ftp"], self.tp) + 0.8 * (hits - 1)) * self.j["ws_mult"]
        dmg = int(dmg)
        self.say(f"{self.me} uses {name}.", "ws")
        self.say(f"The {self.target} takes {dmg} points of damage.", "damage")
        self.tp = 0

        # skillchain?
        props = ws["props"]
        since = now - self.last_ws_time
        if D.SC_WINDOW_OPEN <= since <= D.SC_WINDOW_CLOSE:
            sc = resolve_skillchain(self.last_ws_props, props)
            if sc:
                bonus = int(dmg * D.SC_BONUS[D.SC_LEVEL[sc]])
                dmg += bonus
                self.sc_name = sc
                self.mb_until = now + D.MB_WINDOW
                self.mb_elements = D.SC_ELEMENTS[sc]
                self.say(f"Skillchain: {sc}. The {self.target} takes {bonus} points of damage.", "skillchain")
                props = [sc]  # the chain can continue from its result
        self.last_ws_time, self.last_ws_props = now, props

        new = max(self._monster_floor(), monster_hp - dmg)
        self.prev_monster = new
        return {"monster_hp": new}

    # --- spells ---
    def cast(self, slot: int, now: float, has_player_hp: bool) -> None:
        spells = self.j["spells"]
        if slot >= len(spells):
            return
        name = spells[slot]
        sp = D.SPELLS[name]
        if self.casting:
            self.say(f"You are already casting {self.casting}.", "warn")
            return
        if self.mp < sp["mp"]:
            self.say(f"You do not have enough MP to cast {name}.", "warn")
            return
        if sp["kind"] in ("heal", "regen", "protect", "drain") and not has_player_hp:
            self.say(f"{name} needs the player_hp address.", "warn")
            return
        self.casting, self.cast_done = name, now + sp["cast"]
        self.say(f"{self.me} starts casting {name}.", "magic")

    def _finish_spell(self, now: float, monster_hp, player_hp) -> dict:
        name, self.casting = self.casting, None
        sp = D.SPELLS[name]
        self.mp -= sp["mp"]  # FFXI: MP is only spent when the spell goes off
        kind, writes = sp["kind"], {}
        if kind == "heal" and player_hp is not None:
            new = min(self.player_max_hp, player_hp + sp["power"])
            self.say(f"{self.me} casts {name}. {self.me} recovers {new - player_hp} HP.", "heal")
            writes["player_hp"] = self.prev_player = new
        elif kind == "regen":
            self.regen_until, self.regen_next, self.regen_power = now + sp["duration"], now + sp["tick"], sp["power"]
            self.say(f"{self.me} casts Regen. {self.me} gains the effect of Regen.", "heal")
        elif kind == "protect":
            self.protect_until, self.protect_power = now + sp["duration"], sp["power"]
            self.say(f"{self.me} casts Protect. {self.me} gains the effect of Protect.", "heal")
        elif kind in ("nuke", "drain"):
            if monster_hp is None:
                self.say(f"{self.me} casts {name}, but there is no target.", "warn")
                return {}
            dmg = self.avg_hit * sp["power"] * self.j["nuke_mult"]
            burst = now < self.mb_until and sp["element"] in self.mb_elements
            if burst:
                dmg *= D.MB_BONUS
            dmg = int(dmg)
            new = max(self._monster_floor(), monster_hp - dmg)
            writes["monster_hp"] = self.prev_monster = new
            if burst:
                self.say(f"Magic Burst! {self.me} casts {name}. The {self.target} takes {dmg} points of damage.", "burst")
            else:
                self.say(f"{self.me} casts {name}. The {self.target} takes {dmg} points of damage.", "magic")
            if kind == "drain" and player_hp is not None:
                heal = min(40, dmg // 4)
                writes["player_hp"] = self.prev_player = min(self.player_max_hp, player_hp + heal)
        return writes


# ================================================================================================
# Hotkeys
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


def start_hotkeys(cfg: dict, actions: queue.Queue):
    try:
        from pynput import keyboard
    except Exception as e:
        print(f"[hotkeys off] {e}")
        return None
    ws_keys = {vk: i for i, vk in enumerate(cfg["hotkeys"]["weapon_skills"])}
    sp_keys = {vk: i for i, vk in enumerate(cfg["hotkeys"]["spells"])}
    pause_key = cfg["hotkeys"].get("pause", 0x6E)

    def on_press(key):
        vk = getattr(key, "vk", None)
        if vk is None and hasattr(key, "value"):
            vk = getattr(key.value, "vk", None)
        if vk in ws_keys:
            actions.put(("ws", ws_keys[vk]))
        elif vk in sp_keys:
            actions.put(("spell", sp_keys[vk]))
        elif vk == pause_key:
            actions.put(("pause", 0))

    lst = keyboard.Listener(on_press=on_press)
    lst.daemon = True
    lst.start()
    return lst


# ================================================================================================
# Main loop + overlay
# ================================================================================================
class App:
    def __init__(self, engine: Engine, mem, cfg: dict):
        self.e, self.mem, self.cfg = engine, mem, cfg
        self.actions: queue.Queue = queue.Queue()
        self.paused = False
        self.status = "starting"
        self.monster_hp = self.player_hp = None
        self.monster_max = 0           # its max HP: read (monster_max_hp) or the most seen

    def step(self):
        now = time.monotonic()
        if not self.mem.connected():
            self.status = "waiting for the game..."
            return
        self.status = f"PAUSED ({key_name(self.cfg['hotkeys'].get('pause', 0x6E))})" if self.paused else "connected"
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
        if self.paused:
            self.e.prev_monster, self.e.prev_player = m, p
            self._drain_actions(only_pause=True)
            return
        writes = self.e.tick(now, m, p, mx)
        for kind, slot in self._drain_actions():
            if kind == "ws":
                m = writes.get("monster_hp", m)
                writes.update(self.e.weapon_skill(slot, now, m))
            elif kind == "spell":
                self.e.cast(slot, now, p is not None)
        for name, val in writes.items():
            self.mem.write(name, val)
        self.monster_hp = writes.get("monster_hp", m)
        self.player_hp = writes.get("player_hp", p)

    # --- the FFXI interface in the game's window (the port) ---
    def ui_state(self, now: float):
        import ffxi_ui as U
        e, hk = self.e, self.cfg["hotkeys"]
        effects = tuple(n for n, until in (("Protect", e.protect_until), ("Regen", e.regen_until)) if now < until)
        tags = []
        if now < e.mb_until:
            tags.append(f"MB {e.sc_name}")
        elif D.SC_WINDOW_OPEN <= now - e.last_ws_time <= D.SC_WINDOW_CLOSE:
            tags.append("Chain")
        pal = [(key_name(hk["weapon_skills"][i]), n, e.tp >= D.TP_WS_MIN) for i, n in enumerate(e.w["ws"])]
        pal += [(key_name(hk["spells"][i]), n, e.mp >= D.SPELLS[n]["mp"])
                for i, n in enumerate(e.j["spells"][:len(hk["spells"])])]
        cast_frac = 0.0
        if e.casting:
            total = D.SPELLS[e.casting]["cast"]
            cast_frac = 1 - max(0.0, e.cast_done - now) / total if total else 1.0
        target = None
        if self.monster_hp is not None and self.monster_max:
            target = max(0.0, min(1.0, self.monster_hp / self.monster_max))
        return U.UiState(
            name=e.me, job=e.job, hp=self.player_hp, max_hp=e.player_max_hp,
            mp=int(e.mp), max_mp=e.j["mp"], tp=int(e.tp), effects=effects + tuple(tags),
            target_pct=None if target is None else round(target, 3), target_name=e.target.title(),
            casting=e.casting, cast_frac=round(cast_frac, 2),
            log=tuple(zip(e.log[-7:], e.kinds[-7:])), palette=tuple(pal),
            status="" if self.status == "connected" else self.status)

    def run_game_ui(self, seconds: float | None = None):
        """The FFXI interface drawn in the game's window, through the port (port_overlay.py)."""
        import ffxi_ui as U
        from port_overlay import PortOverlay
        out, ui = PortOverlay(), U.FfxiUi()
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
                time.sleep(1 / 60)
        except KeyboardInterrupt:
            pass
        finally:
            out.show(None)

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
        start, last_print, seen = time.monotonic(), 0.0, 0
        while seconds is None or time.monotonic() - start < seconds:
            self.step()
            if time.monotonic() - last_print > 0.5:
                last_print = time.monotonic()
                print(self.status_line())
            for line in self.e.log[seen:] if len(self.e.log) > seen else []:
                print("   >", line)
            seen = len(self.e.log)
            time.sleep(1 / 60)

    def status_line(self) -> str:
        e = self.e
        return (f"[{self.status}] {e.job} | TP {int(e.tp):4d} | MP {int(e.mp)}/{e.j['mp']} | "
                f"HP {self.player_hp} | Monster {self.monster_hp} | avg hit {e.avg_hit:.0f}"
                + (f" | casting {e.casting}" if e.casting else ""))

    # --- overlay UI ---
    def run_overlay(self):
        import tkinter as tk
        e = self.e
        root = tk.Tk()
        root.title("FFXI layer")
        root.overrideredirect(True)   # no title bar, doesn't steal focus from Dolphin
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.88)
        except tk.TclError:
            pass
        BG, FG, DIM, TP, MP, HP, GOLD = "#0b1020", "#e8ecf6", "#8a93ad", "#7fd36b", "#5aa9ff", "#ff6b6b", "#f2c94c"
        root.configure(bg=BG)
        root.geometry("+30+30")
        W = 300
        c = tk.Canvas(root, width=W, height=330, bg=BG, highlightthickness=1, highlightbackground="#2a3350")
        c.pack()

        drag = {}
        c.bind("<ButtonPress-1>", lambda ev: drag.update(x=ev.x, y=ev.y))
        c.bind("<B1-Motion>", lambda ev: root.geometry(f"+{ev.x_root - drag['x']}+{ev.y_root - drag['y']}"))
        c.bind("<Double-Button-1>", lambda ev: root.destroy())

        ws_names = e.w["ws"]
        sp_names = e.j["spells"]
        num = key_name

        def bar(y, label, val, mx, color):
            c.create_text(12, y, text=label, fill=DIM, anchor="w", font=("Segoe UI", 9, "bold"))
            x0, x1 = 44, W - 70
            c.create_rectangle(x0, y - 5, x1, y + 5, fill="#1a2238", outline="")
            if mx:
                c.create_rectangle(x0, y - 5, x0 + (x1 - x0) * max(0, min(1, val / mx)), y + 5, fill=color, outline="")
            c.create_text(W - 12, y, text=f"{int(val)}" + (f"/{int(mx)}" if label != "TP" else ""),
                          fill=FG, anchor="e", font=("Consolas", 9))

        def draw():
            try:
                self.step()
            except Exception as ex:  # keep the overlay alive; show the error
                e.say(f"error: {ex}")
            c.delete("all")
            now = time.monotonic()
            c.create_text(12, 14, text=f"{e.j['name']} ({e.job})  ·  {e.weapon.replace('_', ' ').title()}",
                          fill=FG, anchor="w", font=("Segoe UI", 10, "bold"))
            c.create_text(12, 30, text=self.status, fill=DIM, anchor="w", font=("Segoe UI", 8))
            bar(50, "HP", self.player_hp or 0, e.player_max_hp, HP)
            bar(68, "MP", e.mp, e.j["mp"], MP)
            bar(86, "TP", e.tp, D.TP_MAX, TP if e.tp >= 1000 else "#4a6b44")
            y = 108
            for i, n in enumerate(ws_names):
                ok = e.tp >= 1000
                c.create_text(12, y, text=f"[{num(self.cfg['hotkeys']['weapon_skills'][i])}] {n}",
                              fill=FG if ok else DIM, anchor="w", font=("Segoe UI", 9))
                y += 15
            y += 4
            for i, n in enumerate(sp_names[:len(self.cfg["hotkeys"]["spells"])]):
                col, row = i % 2, i // 2
                ok = e.mp >= D.SPELLS[n]["mp"]
                c.create_text(12 + col * 145, y + row * 15, text=f"[{num(self.cfg['hotkeys']['spells'][i])}] {n}",
                              fill=MP if ok else DIM, anchor="w", font=("Segoe UI", 9))
            y += ((len(sp_names) + 1) // 2) * 15 + 6
            tags = []
            if e.casting:
                tags.append(f"Casting {e.casting} {max(0, e.cast_done - now):.1f}s")
            if now < e.mb_until:
                tags.append(f"MB window: {e.sc_name} {e.mb_until - now:.0f}s")
            elif D.SC_WINDOW_OPEN <= now - e.last_ws_time <= D.SC_WINDOW_CLOSE:
                tags.append(f"Chain window {D.SC_WINDOW_CLOSE - (now - e.last_ws_time):.0f}s")
            if now < e.protect_until:
                tags.append("Protect")
            if now < e.regen_until:
                tags.append("Regen")
            for tag in tags:
                c.create_text(12, y, text=tag, fill=GOLD, anchor="w", font=("Segoe UI", 9, "bold"))
                y += 15
            y += 4
            for line in e.log[-5:]:
                c.create_text(12, y, text=line, fill=FG, anchor="w", font=("Segoe UI", 8))
                y += 14
            c.config(height=y + 6)
            root.after(16, draw)

        draw()
        root.mainloop()


# ================================================================================================
# Self-test
# ================================================================================================
def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok &= bool(cond)

    check(D.tp_per_hit(240) == 75 and D.tp_per_hit(480) == 134, "TP per hit from delay")
    check(resolve_skillchain(["Liquefaction"], ["Impaction"]) == "Fusion", "Liquefaction > Impaction = Fusion")
    check(resolve_skillchain(["Fusion"], ["Fragmentation", "Scission"]) == "Light", "Fusion > Fragmentation = Light")
    check(resolve_skillchain(["Scission"], ["Scission"]) is None, "no chain from same property")
    for job in D.JOBS:
        for s in D.JOBS[job]["spells"]:
            check(s in D.SPELLS, f"{job} spell {s} exists") if s not in D.SPELLS else None
    for w, d in D.WEAPONS.items():
        for n in d["ws"]:
            if n not in D.WEAPON_SKILLS:
                check(False, f"{w} WS {n} exists")
            for p in D.WEAPON_SKILLS[n]["props"]:
                if p not in D.SC_LEVEL:
                    check(False, f"{n} property {p} known")
    for (a, b), r in D.SKILLCHAINS.items():
        if r not in D.SC_ELEMENTS:
            check(False, f"skillchain {r} has elements")

    e = Engine("RDM", "sword_and_shield")
    t, m, p = 0.0, 3000, 150
    e.tick(t, m, p)
    for _ in range(40):  # 40 hits of 30
        t += 1; m -= 30
        e.tick(t, m, p)
    check(e.tp >= 1000 and abs(e.avg_hit - 30) < 1, f"TP builds from hits (TP={int(e.tp)})")
    w = e.weapon_skill(1, t, m)  # Burning Blade: Liquefaction
    check(w and w["monster_hp"] < m, "weapon skill damages monster")
    m = w["monster_hp"]
    for _ in range(40):
        t += 0.1; m -= 30
        e.tick(t, m, p)
    t = e.last_ws_time + 3
    e.tp = 1000
    w = e.weapon_skill(3, t, m)  # Savage Blade: Fragmentation/Scission -> Liquefaction>Scission = Scission
    check(e.sc_name == "Scission" and t < e.mb_until, f"skillchain fires ({e.sc_name})")
    m = w["monster_hp"]
    e.mp = 999
    e.j = dict(e.j, spells=["Stone II"])
    e.cast(0, t, True)
    w = e.tick(t + 5, m, p)
    check("monster_hp" in w and any("Magic Burst" in l for l in e.log), "Stone II magic-bursts on Scission")
    e2 = Engine("WHM", "hammer")
    e2.tick(0, 3000, 100)
    e2.cast(0, 0, True)
    e2.tick(0.5, 3000, 90)  # took damage mid-cast
    check(e2.casting is None and any("interrupted" in l for l in e2.log), "damage interrupts casting")
    e2.cast(0, 1, True)
    w = e2.tick(5, 3000, 90)
    check(w.get("player_hp") == 115, "Cure heals 25")
    e3 = Engine("WAR", "great_sword")
    e3.tp = 3000
    check(e3.weapon_skill(0, 0, 10)["monster_hp"] == 1, "layer never lands the killing blow by default")
    print("\nall good" if ok else "\nsome checks failed")
    return 0 if ok else 1


# ================================================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "config.toml"))
    ap.add_argument("--job", help="override job from config")
    ap.add_argument("--weapon", help="override weapon from config")
    ap.add_argument("--sim", action="store_true", help="fake fight, no game")
    ap.add_argument("--backend", choices=["port", "dolphin"], help="override backend from config")
    ap.add_argument("--no-overlay", action="store_true", help="print to the console instead of an overlay")
    ap.add_argument("--ui", choices=["game", "window", "console"],
                    help="game: in the game's window (the port's default); window: a separate overlay; console")
    ap.add_argument("--seconds", type=float, help="console mode: stop after N seconds")
    ap.add_argument("--allow-kills", action="store_true", help="let weapon skills/spells take the monster to 0 HP")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    job = (a.job or cfg.get("job", "RDM")).upper()
    weapon = a.weapon or cfg.get("weapon", "long_sword")
    if job not in D.JOBS:
        sys.exit(f"unknown job {job}; pick one of {', '.join(D.JOBS)}")
    if weapon not in D.WEAPONS:
        sys.exit(f"unknown weapon {weapon}; pick one of {', '.join(D.WEAPONS)}")

    engine = Engine(job, weapon, cfg.get("player_max_hp", 150), allow_kills=a.allow_kills,
                    me=cfg.get("name", "Adventurer"))
    backend = a.backend or cfg.get("backend", "port")
    if a.sim:
        mem = SimBackend(engine.player_max_hp)
    else:
        if not cfg.get("addresses", {}).get("monster_hp", {}).get("address"):
            print("monster_hp address isn't set in config.toml yet - the layer will connect but can't do much.\n"
                  "Find it with Dolphin Memory Engine (see README), or try --sim first.")
        if backend == "port":
            from port_bridge import PortBackend
            mem = PortBackend(cfg.get("addresses", {}))
        elif backend == "dolphin":
            mem = DolphinBackend(cfg.get("addresses", {}))
        else:
            sys.exit(f"unknown backend {backend}; pick port or dolphin")

    app = App(engine, mem, cfg)
    start_hotkeys(cfg, app.actions)
    hk = cfg["hotkeys"]
    print(f"FFXI layer: {D.JOBS[job]['name']} with {weapon.replace('_', ' ')}. "
          f"{' '.join(map(key_name, hk['weapon_skills']))} = weapon skills, "
          f"{' '.join(map(key_name, hk['spells']))} = spells, {key_name(hk.get('pause', 0x6E))} = pause.")
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
