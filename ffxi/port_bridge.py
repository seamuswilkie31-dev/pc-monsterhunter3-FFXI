"""The FFXI layer's side of the port's bridge (tools/ffxi_bridge.cpp).

The port, run with MH3_FFXI=1, serves guest memory through a named shared block, "mh3-ffxi"
(Windows only). The layer writes up to 8 watches into it (an address, a pointer chain, a size); the port
resolves them 60 times a second and publishes each value, and applies the layer's writes.

The block's layout must match struct Block in tools/ffxi_bridge.cpp: little-endian u32s,
    header   magic, version, heartbeat, pad
    watch[8] addr, size, n_offsets, offsets[4], write_seq, write_value, base   (written by the layer)
    result[8] valid, resolved, value, write_ack                             (written by the port)
    snap_seq, snap_ack, snap_ok, snap_path[260]: a snapshot of RAM on request (MEM1, then MEM2)
    player_obj, monster_obj, monster_hits, monster_delta: what the port's hooks saw
    menu_open, menu_close_seq/ack, item_seq/ack, ev_head, reserved[2], events[32]: the command menu
    name_x, name_y, name_ok, name_height, camera, reserved: your nameplate (the head through the camera)
    target_x, target_y, target_ok, target_locked, target_model, target_name[16], target_dist: FFXI's target
        (the monster nearest the hunter, or the one locked on with the Classic's L held) and where its
        arrow goes
    bgm_stream, bgm_seq, bgm_mute, reserved: the game's music (what its player was last asked for), and
        the layer's word that it be silent while FFXI's plays
    map[16]: the map's code module linked now
    chat_open, chat_seq, chat_line[128], chat_sent[128]: FFXI's chat line ("/" opens it in the game's
        window; the port captures the typing): open, lines sent so far, what's typed, the last sent
    mute_se[8], companions[6]: the hunter's sounds silenced; the Trusts, where each stands
    auto_carve, mh_hud, carve_seq: FFXI's combat (a fallen target carved: the layer's word, the port's
        count of carvings begun); Monster Hunter's HUD shown in place of FFXI's (F9)
    pad_macros, macro_held, macro_head, macro_events[8]: FFXI's macro bars on the pad (a trigger held, a
        face button or the d-pad: bar << 8 | button)
    got_head, got_items[16], got_names[16][24]: what came into the hunter's pouch in the field
    quest_seq, quest_kind, reserved[4]: a quest's end as its banner loads (2 complete, 3 failed, 4 abandoned)

A watch's base: "address" (addr is an address), or "player" / "monster" (addr is an offset in the
local hunter's object, or in the monster whose HP the game last changed).
"""
from __future__ import annotations

import mmap
import struct
import time

TAG = "mh3-ffxi"
MAGIC = 0x4D483346  # 'MH3F'
VERSION = 12
WATCHES, OFFSETS = 8, 4

HEADER = struct.Struct("<4I")
WATCH = struct.Struct(f"<3I{OFFSETS}I3I")
RESULT = struct.Struct("<4I")
WATCH_AT = HEADER.size
RESULT_AT = WATCH_AT + WATCHES * WATCH.size
SNAP_AT = RESULT_AT + WATCHES * RESULT.size
SNAP_PATH_LEN = 260
OBJ_AT = SNAP_AT + 12 + SNAP_PATH_LEN
OBJ = struct.Struct("<3Ii")
MENU_AT = OBJ_AT + OBJ.size
MENU = struct.Struct("<8I")              # menu_open, close_seq, close_ack, item_seq, item_ack, ev_head, engage, dpad_target
EVENTS_AT = MENU_AT + MENU.size
NAME_AT = EVENTS_AT + 32 * 4
NAME = struct.Struct("<ffIfII")         # name_x, name_y, name_ok, name_height, camera, damage_total
TARGET_AT = NAME_AT + NAME.size
TARGET = struct.Struct("<ffIII16sf")    # x, y, ok, locked, model, name, dist
BGM_AT = TARGET_AT + TARGET.size
BGM = struct.Struct("<4I")              # stream, seq, mute, no_mh_swings
MAP_AT = BGM_AT + BGM.size              # the map's code module linked now: "map_village", "map01"; "": none
CHAT_AT = MAP_AT + 16
CHAT = struct.Struct("<II128s128s")     # open, seq, line, sent
MUTE_AT = CHAT_AT + CHAT.size          # the hunter's sounds silenced: 8 of kind << 16 | code (0xFFFF: all)
COMPANIONS_AT = MUTE_AT + 8 * 4         # the Trusts: where each stands ([0] unused)
COMPANION = struct.Struct("<I4fII2f2fI")  # shown, x y z, yaw, battle, dead, shadow radius and darkness (the
COMPANION_SET = struct.Struct("<I4fII2f")  # layer's); where its name goes, x y, and whether it shows (the port's)
COMBAT_AT = COMPANIONS_AT + 6 * COMPANION.size
COMBAT = struct.Struct("<3I")           # auto_carve, mh_hud, carve_seq
MACROS_AT = COMBAT_AT + COMBAT.size     # pad_macros, macro_held, macro_head, macro_events[8]
GOT_AT = MACROS_AT + 11 * 4             # got_head, got_items[16], got_names[16][24]
QUEST_AT = GOT_AT + 4 + 16 * 4 + 16 * 24   # quest_seq, quest_kind, reserved[4]
SIZE = QUEST_AT + 6 * 4
assert SIZE == 2124, "the layout must match struct Block in tools/ffxi_bridge.cpp"

# the Classic's buttons, as the port's filter reports them (the pad's: by position)
CL_UP, CL_LEFT, CL_X, CL_A, CL_Y, CL_B, CL_DOWN, CL_RIGHT = 0x0001, 0x0002, 0x0008, 0x0010, 0x0020, 0x0040, 0x4000, 0x8000
BASES = {"address": 0, "player": 1, "monster": 2}

MEM1, MEM1_SIZE = 0x80000000, 0x01800000
MEM2, MEM2_SIZE = 0x90000000, 0x04000000

TYPE_SIZE = {"u8": 1, "u16": 2, "u32": 4, "float": 4}


def watch_at(i: int) -> int:
    return WATCH_AT + i * WATCH.size


def result_at(i: int) -> int:
    return RESULT_AT + i * RESULT.size


def u32(mm: mmap.mmap, at: int) -> int:
    return int.from_bytes(mm[at:at + 4], "little")


def put_u32(mm: mmap.mmap, at: int, v: int) -> None:
    mm[at:at + 4] = (v & 0xFFFFFFFF).to_bytes(4, "little")


def open_block() -> mmap.mmap:
    """The shared block, made here if the port hasn't made it yet (both sides may start first)."""
    return mmap.mmap(-1, SIZE, tagname=TAG)


class PortBackend:
    """Reads/writes MH Tri's memory through the native port's bridge. Same interface as DolphinBackend."""

    def __init__(self, addresses: dict):
        self.mm = open_block()
        self.addrs = {k: v for k, v in addresses.items() if v.get("address")}
        if len(self.addrs) > WATCHES:
            raise ValueError(f"the bridge serves {WATCHES} addresses; config.toml sets {len(self.addrs)}")
        self.slot = {}
        for i in range(WATCHES):
            put_u32(self.mm, watch_at(i), 0)  # clear what an earlier run left
        for i, (name, a) in enumerate(self.addrs.items()):
            self.slot[name] = i
            self._set_watch(i, a)
        self._hb, self._hb_time = HEADER.unpack_from(self.mm, 0)[2], 0.0  # connected once it moves
        self._where = {}  # name -> the address last read
        self._ev_tail = u32(self.mm, MENU_AT + 20)  # the command menu's events: from now on
        self._macro_tail = u32(self.mm, MACROS_AT + 8)  # the pad's macros, the pouch's gains: from now on
        self._got_tail = u32(self.mm, GOT_AT)

    def _set_watch(self, i: int, a: dict) -> None:
        t = a.get("type", "u16")
        if t not in TYPE_SIZE:
            raise ValueError(f"unknown type {t!r}")
        base = a.get("base", "address")
        if base not in BASES:
            raise ValueError(f"unknown base {base!r}; pick one of {', '.join(BASES)}")
        offs = list(a.get("offsets", []))
        if len(offs) > OFFSETS:
            raise ValueError(f"the bridge follows {OFFSETS} offsets at most; got {len(offs)}")
        at = watch_at(i)
        ack = u32(self.mm, result_at(i) + 12)  # start in step with the port: no stale write replayed
        # addr last: the port ignores the watch until it is set
        put_u32(self.mm, at, 0)
        self.mm[at:at + WATCH.size] = WATCH.pack(0, TYPE_SIZE[t], len(offs), *(offs + [0] * (OFFSETS - len(offs))),
                                                 ack, 0, BASES[base])
        put_u32(self.mm, at, a["address"])

    def connected(self) -> bool:
        magic, version, hb, _ = HEADER.unpack_from(self.mm, 0)
        if magic != MAGIC or version != VERSION:
            return False
        now = time.monotonic()
        if hb != self._hb:
            self._hb, self._hb_time = hb, now
        return now - self._hb_time < 1.0  # the port bumps it 60 times a second while the game runs

    def has(self, name: str) -> bool:
        return name in self.slot

    def read(self, name: str):
        i = self.slot.get(name)
        if i is None:
            return None
        ack = u32(self.mm, result_at(i) + 12)  # the ack first: the port stores the value before it
        valid, where, value, _ = RESULT.unpack_from(self.mm, result_at(i))
        if not valid:
            self._where.pop(name, None)
            return None  # pointer not valid right now (e.g. not in a quest)
        if self._where.setdefault(name, where) != where:
            # another object (another monster): one tick of nothing, so its HP isn't taken for a hit
            self._where[name] = where
            return None
        at = watch_at(i)
        if u32(self.mm, at + 7 * 4) != ack:
            # a write the port hasn't applied yet: report it, or the engine would take the old value
            # coming back for a hit
            value = u32(self.mm, at + 8 * 4)
        if self.addrs[name].get("type", "u16") == "float":
            return struct.unpack("<f", value.to_bytes(4, "little"))[0]
        return value

    def write(self, name: str, value) -> bool:
        i = self.slot.get(name)
        if i is None:
            return False
        t = self.addrs[name].get("type", "u16")
        if t == "float":
            raw = int.from_bytes(struct.pack("<f", float(value)), "little")
        else:
            raw = int(max(0, min((1 << (8 * TYPE_SIZE[t])) - 1, value)))
        at = watch_at(i)
        seq = u32(self.mm, at + 7 * 4)
        put_u32(self.mm, at + 8 * 4, raw)        # write_value, then
        put_u32(self.mm, at + 7 * 4, seq + 1)    # write_seq: the port applies it on its next tick
        return True

    def snapshot(self, path: str, timeout: float = 30.0) -> bool:
        """Ask the port for a snapshot of RAM at path (MEM1, then MEM2); True once it is written."""
        raw = str(path).encode("utf-8")
        if len(raw) >= SNAP_PATH_LEN:
            raise ValueError(f"snapshot path too long: {path}")
        self.mm[SNAP_AT + 12:SNAP_AT + 12 + SNAP_PATH_LEN] = raw.ljust(SNAP_PATH_LEN, b"\0")
        seq = (u32(self.mm, SNAP_AT + 4) + 1) & 0xFFFFFFFF  # one past the port's last ack
        put_u32(self.mm, SNAP_AT, seq)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if u32(self.mm, SNAP_AT + 4) == seq:
                return bool(u32(self.mm, SNAP_AT + 8))
            time.sleep(0.02)
        return False

    def objects(self) -> dict:
        """What the port's hooks saw: the hunter's and the monster's objects (0: not yet), the game's
        changes to a monster's HP counted, and the last one."""
        player, monster, hits, delta = OBJ.unpack_from(self.mm, OBJ_AT)
        return {"player": player, "monster": monster, "monster_hits": hits, "monster_delta": delta}

    # --- the FFXI command menu: the port's controller filter (tools/ffxi_bridge.cpp, classic_filter) ---
    def menu_open(self) -> bool:
        return bool(u32(self.mm, MENU_AT))

    def menu_events(self) -> list[int]:
        """The buttons pressed in the menu since the last call (CL_* bits), oldest first."""
        head = u32(self.mm, MENU_AT + 20)
        if (head - self._ev_tail) & 0xFFFFFFFF > 32:  # fell behind: keep the last 32
            self._ev_tail = (head - 32) & 0xFFFFFFFF
        out = []
        while self._ev_tail != head:
            out.append(u32(self.mm, EVENTS_AT + 4 * (self._ev_tail % 32)))
            self._ev_tail = (self._ev_tail + 1) & 0xFFFFFFFF
        return out

    def set_engage(self, weapon_out: bool, lock_camera: bool) -> None:
        """FFXI's Attack, for the port: the character's weapon out, the camera locked on the target."""
        put_u32(self.mm, MENU_AT + 24, (1 if weapon_out else 0) | (2 if lock_camera else 0))

    def mute_hunter_sounds(self, sounds: list[tuple[int, int | None]]) -> None:
        """The hunter's own sounds the port drops: (kind, code), code None for all of that kind (kind 1
        its voice, 2 its feet, 3 its weapon). Eight at most."""
        words = [(k & 0xFFFF) << 16 | (0xFFFF if c is None else c & 0xFFFF) for k, c in sounds[:8]]
        self.mm[MUTE_AT:MUTE_AT + 32] = struct.pack("<8I", *(words + [0] * (8 - len(words))))

    def set_companion(self, slot: int, shown: bool, x: float = 0.0, y: float = 0.0, z: float = 0.0,
                      yaw: float = 0.0, battle: bool = False, dead: bool = False, shadow: float = 0.0,
                      shadow_dark: float = 0.0) -> None:
        """A Trust (slot 1-5, its model sent through port_model.PortModel(slot)): drawn or not, its feet in
        the world, its turn from facing +Z (radians, as the hunter's), its weapon out, fallen, its round
        shadow (radius, darkness)."""
        COMPANION_SET.pack_into(self.mm, COMPANIONS_AT + slot * COMPANION.size, 1 if shown else 0, float(x),
                                float(y), float(z), float(yaw), 1 if battle else 0, 1 if dead else 0, float(shadow),
                                float(shadow_dark))

    def companion_name(self, slot: int) -> tuple[float, float] | None:
        """Where a Trust's name goes in the picture (-1..1, y up), as the port projects it; None: unseen."""
        x, y, ok = struct.unpack_from("<2fI", self.mm, COMPANIONS_AT + slot * COMPANION.size + COMPANION_SET.size)
        return (x, y) if ok else None

    # --- FFXI's damage to monsters, dealt through the game (as a hunter's blow: their flinches, falls, carving) ---
    def deals_damage(self) -> bool:
        return True

    def deal_damage(self, amount: int) -> None:
        """FFXI's damage to the monster the HP is read from: the port hands it to the game, which takes it
        off as its own (the layer counts all it has dealt; the port the new part each frame)."""
        if amount > 0:
            total = (u32(self.mm, NAME_AT + 20) + int(amount)) & 0xFFFFFFFF
            put_u32(self.mm, NAME_AT + 20, total)

    def set_mh_swings(self, on: bool) -> None:
        """The game's own attack buttons while FFXI's Attack fights (off: dropped while engaged)."""
        put_u32(self.mm, BGM_AT + 12, 0 if on else 1)

    def set_auto_carve(self, on: bool) -> None:
        """FFXI's combat: the target, once it falls, walked up to and carved (the pad's own stick or a
        button stops it)."""
        put_u32(self.mm, COMBAT_AT, 1 if on else 0)

    def carvings(self) -> int:
        """How many carvings the port has begun (a fallen target walked up to and carved)."""
        return u32(self.mm, COMBAT_AT + 8)

    # --- FFXI's macro bars on the pad; the pouch's gains; a quest's end ---
    def set_pad_macros(self, on: bool) -> None:
        put_u32(self.mm, MACROS_AT, 1 if on else 0)

    def macro_held(self) -> int:
        """The bar a trigger holds open: 0 none, 1 LT, 2 RT."""
        return u32(self.mm, MACROS_AT + 4)

    def macro_events(self) -> list[tuple[int, int]]:
        """The macros pressed since the last call, oldest first: (bar 0 LT / 1 RT, button 0-7: up, right,
        down, left, then the face's top, right, bottom, left)."""
        head = u32(self.mm, MACROS_AT + 8)
        if (head - self._macro_tail) & 0xFFFFFFFF > 8:
            self._macro_tail = (head - 8) & 0xFFFFFFFF
        out = []
        while self._macro_tail != head:
            v = u32(self.mm, MACROS_AT + 12 + 4 * (self._macro_tail % 8))
            out.append((v >> 8 & 0xFF, v & 0xFF))
            self._macro_tail = (self._macro_tail + 1) & 0xFFFFFFFF
        return out

    def got(self) -> list[tuple[int, int, str]]:
        """What came into the hunter's pouch in the field since the last call: (item number, how many, the
        game's name for it)."""
        head = u32(self.mm, GOT_AT)
        if (head - self._got_tail) & 0xFFFFFFFF > 16:
            self._got_tail = (head - 16) & 0xFFFFFFFF
        out = []
        while self._got_tail != head:
            k = self._got_tail % 16
            v = u32(self.mm, GOT_AT + 4 + 4 * k)
            at = GOT_AT + 4 + 16 * 4 + 24 * k
            name = bytes(self.mm[at:at + 24]).split(b"\0")[0].decode("latin-1")
            out.append((v & 0xFFFF, v >> 16, name))
            self._got_tail = (self._got_tail + 1) & 0xFFFFFFFF
        return out

    def quest(self) -> tuple[int, int]:
        """A quest's end as the port saw its banner load: (a count, the last kind: 2 complete, 3 failed, 4
        abandoned)."""
        return u32(self.mm, QUEST_AT), u32(self.mm, QUEST_AT + 4)

    def set_mh_hud(self, on: bool) -> None:
        """Monster Hunter's own HUD drawn (the FFXI interface hidden by the layer), or FFXI's in its place."""
        put_u32(self.mm, COMBAT_AT + 4, 1 if on else 0)

    def set_dpad_target(self, mode: int) -> None:
        """The d-pad changes the target: 0 never, 1 while engaged or locked on, 2 always (the game losing it)."""
        put_u32(self.mm, MENU_AT + 28, mode)

    def close_menu(self) -> None:
        put_u32(self.mm, MENU_AT + 4, u32(self.mm, MENU_AT + 4) + 1)

    def use_item(self) -> None:
        """One press of the button the menu took (the Classic's Y): Monster Hunter's own item use."""
        put_u32(self.mm, MENU_AT + 12, u32(self.mm, MENU_AT + 12) + 1)

    # --- your nameplate: the hunter's head projected through the game's 3D camera, by the port ---
    def set_name_height(self, h: float) -> None:
        self.mm[NAME_AT + 12:NAME_AT + 16] = struct.pack("<f", float(h))

    def target(self) -> dict | None:
        """FFXI's target, as the port picks it: where its arrow goes in the picture (-1..1, y up; None
        if it can't be seen), whether the camera is locked on it, its model's name, its distance."""
        x, y, ok, locked, model, name, dist = TARGET.unpack_from(self.mm, TARGET_AT)
        if not model:
            return None
        return {"arrow": (x, y) if ok else None, "locked": bool(locked), "model": model,
                "name": name.split(b"\0")[0].decode("latin-1"), "dist": dist}

    def game_music(self) -> tuple[int, int]:
        """What Monster Hunter's music player was last asked to play: (its stream, a count of requests)."""
        stream, seq, _, _ = BGM.unpack_from(self.mm, BGM_AT)
        return stream, seq

    def chat(self) -> tuple[bool, str, int, str]:
        """FFXI's chat line: (open, what's typed so far, lines sent so far, the last sent)."""
        is_open, seq, line, sent = CHAT.unpack_from(self.mm, CHAT_AT)
        text = lambda b: b.split(bytes(1))[0].decode("utf-8", "replace")
        return bool(is_open), text(line), seq, text(sent)

    def map_name(self) -> str:
        """The map the game has loaded ("map_village", "map01"...; "" in the menus)."""
        return bytes(self.mm[MAP_AT:MAP_AT + 16]).split(bytes(1))[0].decode("latin-1")

    def mute_game_music(self, on: bool) -> None:
        put_u32(self.mm, BGM_AT + 8, 1 if on else 0)

    def nameplate(self) -> tuple[float, float] | None:
        """Where your head is in the picture (-1..1, y up), or None when it can't be seen."""
        x, y, ok, _, _, _ = NAME.unpack_from(self.mm, NAME_AT)
        return (x, y) if ok else None
