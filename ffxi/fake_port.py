"""A stand-in for the port's bridge (tools/ffxi_bridge.cpp), to test the layer's PortBackend without the game.

It serves the shared block exactly as the port does, from a fake guest RAM holding a fake hunt: a monster
reached through a pointer (as monster data is on the heap), losing HP as if hit, and a hunter hit back now
and then. Run it, then the layer with the addresses it prints:

    uv run fake_port.py --write-config fake.toml
    uv run ffxi_mh3.py --config fake.toml          # in another window

    uv run fake_port.py --test                     # checks the protocol on its own, then exits
"""
from __future__ import annotations

import argparse
import random
import struct
import time

import port_bridge as P

# fake guest RAM: a slice of MEM1
BASE, SPAN = 0x80400000, 0x00100000
MONSTER_PTR = 0x80410000     # a static pointer to the monster's data
MONSTER_DATA = 0x80480000    # where the "heap" put the monster this quest
MONSTER_HP_OFF = 0x2A4
PLAYER_HP = 0x80400010
PLAYER_MAX_HP = 0x80400012


class FakeRam:
    def __init__(self):
        self.b = bytearray(SPAN)

    def ok(self, a: int, n: int) -> bool:
        return BASE <= a and a + n <= BASE + SPAN

    def rd(self, a: int, n: int) -> int:
        return int.from_bytes(self.b[a - BASE:a - BASE + n], "big")

    def wr(self, a: int, n: int, v: int) -> None:
        self.b[a - BASE:a - BASE + n] = (v & ((1 << 8 * n) - 1)).to_bytes(n, "big")


def serve_once(mm, ram: FakeRam) -> None:
    """One tick of tools/ffxi_bridge.cpp's serve()."""
    for i in range(P.WATCHES):
        f = P.WATCH.unpack_from(mm, P.watch_at(i))
        addr, size, n_off, offs, wseq, wval = f[0], f[1], f[2], f[3:3 + P.OFFSETS], f[-2], f[-1]
        r = P.result_at(i)
        ack = P.u32(mm, r + 12)
        a, ok = addr, bool(addr)
        for o in offs[:min(n_off, P.OFFSETS)]:
            if not ok or not ram.ok(a, 4):
                ok = False
                break
            a = (ram.rd(a, 4) + o) & 0xFFFFFFFF
        ok = ok and size in (1, 2, 4) and ram.ok(a, size)
        pending = wseq != ack
        if ok and pending:
            ram.wr(a, size, wval)
        mm[r:r + 12] = struct.pack("<3I", int(ok), a if ok else 0, ram.rd(a, size) if ok else 0)
        if pending:
            P.put_u32(mm, r + 12, wseq)
    _, _, hb, _ = P.HEADER.unpack_from(mm, 0)
    P.put_u32(mm, 8, hb + 1)


def start(mm) -> FakeRam:
    ram = FakeRam()
    ram.wr(MONSTER_PTR, 4, MONSTER_DATA)
    ram.wr(MONSTER_DATA + MONSTER_HP_OFF, 2, 3000)
    ram.wr(PLAYER_HP, 2, 150)
    ram.wr(PLAYER_MAX_HP, 2, 150)
    P.put_u32(mm, 4, P.VERSION)
    P.put_u32(mm, 0, P.MAGIC)
    return ram


CONFIG = f"""# addresses of fake_port.py's fake hunt
backend = "port"
job = "RDM"
weapon = "long_sword"
player_max_hp = 150

[addresses.monster_hp]
address = {MONSTER_PTR:#x}
offsets = [{MONSTER_HP_OFF:#x}]
type = "u16"

[addresses.player_hp]
address = {PLAYER_HP:#x}
offsets = []
type = "u16"

[addresses.player_max_hp]
address = {PLAYER_MAX_HP:#x}
offsets = []
type = "u16"

[hotkeys]
weapon_skills = [0x61, 0x62, 0x63, 0x64]
spells = [0x65, 0x66, 0x67, 0x68, 0x69, 0x60, 0x6F, 0x6A]
"""


def run(seconds: float | None) -> None:
    mm = P.open_block()
    ram = start(mm)
    print("fake port: serving the shared block (Ctrl+C to stop)")
    t0 = next_hit = next_roar = time.monotonic()
    while seconds is None or time.monotonic() - t0 < seconds:
        now = time.monotonic()
        hp_at = MONSTER_DATA + MONSTER_HP_OFF
        if now >= next_hit:
            hp = ram.rd(hp_at, 2)
            ram.wr(hp_at, 2, 3000 if hp <= 1 else max(1, hp - random.randint(22, 48)))
            next_hit = now + random.uniform(0.8, 1.6)
        if now >= next_roar:
            ram.wr(PLAYER_HP, 2, max(1, ram.rd(PLAYER_HP, 2) - random.randint(12, 30)))
            next_roar = now + random.uniform(3.0, 6.0)
        serve_once(mm, ram)
        time.sleep(1 / 60)


def test() -> int:
    """The protocol, ticked by hand: a PortBackend against serve_once."""
    import tomllib
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok &= bool(cond)

    mm = P.open_block()
    mm[:] = bytes(P.SIZE)
    ram = start(mm)
    be = P.PortBackend(tomllib.loads(CONFIG)["addresses"])
    check(not be.connected(), "not connected before the port's first tick")
    serve_once(mm, ram)
    check(be.connected(), "connected once the heartbeat moves")
    check(be.read("monster_hp") == 3000, "monster HP through a pointer chain")
    check(be.read("player_hp") == 150 and be.read("player_max_hp") == 150, "player HP at fixed addresses")

    be.write("monster_hp", 2500)
    check(be.read("monster_hp") == 2500, "a pending write reads back as written")
    check(ram.rd(MONSTER_DATA + MONSTER_HP_OFF, 2) == 3000, "...and is not applied before the port's tick")
    serve_once(mm, ram)
    check(ram.rd(MONSTER_DATA + MONSTER_HP_OFF, 2) == 2500, "the port applies it on its tick")
    ram.wr(MONSTER_DATA + MONSTER_HP_OFF, 2, 2460)  # the game hits the monster
    serve_once(mm, ram)
    check(be.read("monster_hp") == 2460, "the game's own change comes through after the write")

    ram.wr(MONSTER_PTR, 4, 0)  # quest over: the monster's data is gone
    serve_once(mm, ram)
    check(be.read("monster_hp") is None, "a pointer out of RAM reads as no monster")
    be.write("monster_hp", 1)
    serve_once(mm, ram)
    ram.wr(MONSTER_PTR, 4, MONSTER_DATA)
    ram.wr(MONSTER_DATA + MONSTER_HP_OFF, 2, 4000)  # next quest
    serve_once(mm, ram)
    check(be.read("monster_hp") == 4000, "a write refused while unresolved is not replayed later")

    be2 = P.PortBackend(tomllib.loads(CONFIG)["addresses"])  # the layer restarted
    serve_once(mm, ram)
    check(be2.read("monster_hp") == 4000, "a restarted layer starts in step with the port")
    print("\nall good" if ok else "\nsome checks failed")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write-config", metavar="PATH", help="write a config.toml for the fake hunt and exit")
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test:
        return test()
    if a.write_config:
        with open(a.write_config, "w", encoding="utf-8") as f:
            f.write(CONFIG)
        print(f"wrote {a.write_config}")
        return 0
    run(a.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
