"""The FFXI layer's side of the port's bridge (tools/ffxi_bridge.cpp).

The port, run with MH3_FFXI=1, serves guest memory through a named shared block, "mh3-ffxi"
(Windows only). The layer writes up to 8 watches into it (an address, a pointer chain, a size); the port
resolves them 60 times a second and publishes each value, and applies the layer's writes.

The block's layout must match struct Block in tools/ffxi_bridge.cpp: little-endian u32s,
    header   magic, version, heartbeat, pad
    watch[8] addr, size, n_offsets, offsets[4], write_seq, write_value     (written by the layer)
    result[8] valid, resolved, value, write_ack                             (written by the port)
    snap_seq, snap_ack, snap_ok, snap_path[260]: a snapshot of RAM on request (MEM1, then MEM2)
"""
from __future__ import annotations

import mmap
import struct
import time

TAG = "mh3-ffxi"
MAGIC = 0x4D483346  # 'MH3F'
VERSION = 2
WATCHES, OFFSETS = 8, 4

HEADER = struct.Struct("<4I")
WATCH = struct.Struct(f"<3I{OFFSETS}I2I")
RESULT = struct.Struct("<4I")
WATCH_AT = HEADER.size
RESULT_AT = WATCH_AT + WATCHES * WATCH.size
SNAP_AT = RESULT_AT + WATCHES * RESULT.size
SNAP_PATH_LEN = 260
SIZE = SNAP_AT + 12 + SNAP_PATH_LEN  # 704

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

    def _set_watch(self, i: int, a: dict) -> None:
        t = a.get("type", "u16")
        if t not in TYPE_SIZE:
            raise ValueError(f"unknown type {t!r}")
        offs = list(a.get("offsets", []))
        if len(offs) > OFFSETS:
            raise ValueError(f"the bridge follows {OFFSETS} offsets at most; got {len(offs)}")
        at = watch_at(i)
        ack = u32(self.mm, result_at(i) + 12)  # start in step with the port: no stale write replayed
        # addr last: the port ignores the watch until it is set
        put_u32(self.mm, at, 0)
        self.mm[at:at + WATCH.size] = WATCH.pack(0, TYPE_SIZE[t], len(offs), *(offs + [0] * (OFFSETS - len(offs))),
                                                 ack, 0)
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
        valid, _, value, _ = RESULT.unpack_from(self.mm, result_at(i))
        if not valid:
            return None  # pointer not valid right now (e.g. not in a quest)
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
