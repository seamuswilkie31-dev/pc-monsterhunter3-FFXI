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
# as the real game holds them (NEXT.md), found by the port's hooks
EM_HP, PL_HP, PL_MAX_HP = 0x7A0, 0x370, 0x372   # s32 in the monster; s16 in the hunter
PLAYER_OBJ = 0x80420000
MONSTER_OBJ, MONSTER2_OBJ = 0x80440000, 0x80460000


class FakeRam:
    def __init__(self):
        self.b = bytearray(SPAN)
        self.player_obj = self.monster_obj = 0  # what the hooks have seen
        self.monster_hits, self.monster_delta = 0, 0
        self.items_used = 0          # Y presses the layer passed to the game

    def em_hp_add(self, obj: int, delta: int) -> None:
        """The game's mh3_EmHpAdd, and the port's hook on it."""
        self.wr(obj + EM_HP, 4, max(0, self.rd(obj + EM_HP, 4) + delta))
        self.monster_obj, self.monster_delta = obj, delta
        self.monster_hits += 1

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
        addr, size, n_off, offs, wseq, wval, base = f[0], f[1], f[2], f[3:3 + P.OFFSETS], f[-3], f[-2], f[-1]
        r = P.result_at(i)
        ack = P.u32(mm, r + 12)
        a, ok = addr, bool(addr)
        if ok and base:
            obj = {1: ram.player_obj, 2: ram.monster_obj}.get(base, 0)
            a, ok = obj + addr, bool(obj)
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
    seq = P.u32(mm, P.SNAP_AT)
    if seq != P.u32(mm, P.SNAP_AT + 4):
        path = bytes(mm[P.SNAP_AT + 12:P.SNAP_AT + 12 + P.SNAP_PATH_LEN]).split(b"\0")[0].decode("utf-8")
        mem1 = bytearray(P.MEM1_SIZE)
        mem1[BASE - P.MEM1:BASE - P.MEM1 + SPAN] = ram.b
        with open(path, "wb") as f:
            f.write(mem1)
            f.write(bytes(P.MEM2_SIZE))
        P.put_u32(mm, P.SNAP_AT + 8, 1)
        P.put_u32(mm, P.SNAP_AT + 4, seq)
    P.OBJ.pack_into(mm, P.OBJ_AT, ram.player_obj, ram.monster_obj, ram.monster_hits, ram.monster_delta)
    # the command menu, as classic_filter: the layer's close and item requests
    m = list(P.MENU.unpack_from(mm, P.MENU_AT))
    if m[1] != m[2]:
        m[0], m[2] = 0, m[1]
    if m[3] != m[4]:
        m[4] = m[3]
        ram.items_used += 1
    P.MENU.pack_into(mm, P.MENU_AT, *m)
    _, _, hb, _ = P.HEADER.unpack_from(mm, 0)
    P.put_u32(mm, 8, hb + 1)


def press(mm, bits: int) -> None:
    """A button pressed on the pad, as classic_filter sees it: Y (held, in the port) opens the menu and
    (tapped) closes it; while it is open, every press is an event for the layer."""
    m = list(P.MENU.unpack_from(mm, P.MENU_AT))
    if bits & P.CL_Y:
        m[0] ^= 1
        bits &= ~P.CL_Y
    if m[0] and bits:
        P.put_u32(mm, P.EVENTS_AT + 4 * (m[5] % 32), bits)
        m[5] += 1
    P.MENU.pack_into(mm, P.MENU_AT, *m)


def start(mm) -> FakeRam:
    ram = FakeRam()
    ram.wr(MONSTER_PTR, 4, MONSTER_DATA)
    ram.wr(MONSTER_DATA + MONSTER_HP_OFF, 2, 3000)
    ram.wr(PLAYER_HP, 2, 150)
    ram.wr(PLAYER_MAX_HP, 2, 150)
    ram.wr(PLAYER_OBJ + PL_HP, 2, 150)
    ram.wr(PLAYER_OBJ + PL_MAX_HP, 2, 150)
    ram.wr(MONSTER_OBJ + EM_HP, 4, 3000)
    ram.wr(MONSTER2_OBJ + EM_HP, 4, 900)
    ram.player_obj = PLAYER_OBJ  # Pl_master_ck runs every frame
    P.put_u32(mm, 4, P.VERSION)
    P.put_u32(mm, 0, P.MAGIC)
    return ram


CONFIG = f"""# addresses of fake_port.py's fake hunt
backend = "port"
job = "RDM"
weapon = "long_sword"
player_max_hp = 150

[addresses.monster_hp]
base = "monster"
address = {EM_HP:#x}
type = "u32"

[addresses.player_hp]
base = "player"
address = {PL_HP:#x}
type = "u16"

[addresses.player_max_hp]
base = "player"
address = {PL_MAX_HP:#x}
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
        if now >= next_hit:
            if ram.rd(MONSTER_OBJ + EM_HP, 4) <= 1:
                ram.wr(MONSTER_OBJ + EM_HP, 4, 3000)  # new monster
            ram.em_hp_add(MONSTER_OBJ, -random.randint(22, 48))
            next_hit = now + random.uniform(0.8, 1.6)
        if now >= next_roar:
            at = PLAYER_OBJ + PL_HP
            ram.wr(at, 2, max(1, ram.rd(at, 2) - random.randint(12, 30)))
            next_roar = now + random.uniform(3.0, 6.0)
        serve_once(mm, ram)
        time.sleep(1 / 60)


def test() -> int:
    """The protocol, ticked by hand: a PortBackend against serve_once."""
    import tomllib
    P.TAG = "mh3-ffxi-test"   # a block of its own: a running port's (and its layer's) is never touched
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok &= bool(cond)

    mm = P.open_block()
    mm[:] = bytes(P.SIZE)
    ram = start(mm)
    CHAIN = {  # fixed addresses and a pointer chain, as found by scanning
        "monster_hp": {"address": MONSTER_PTR, "offsets": [MONSTER_HP_OFF], "type": "u16"},
        "player_hp": {"address": PLAYER_HP, "type": "u16"},
        "player_max_hp": {"address": PLAYER_MAX_HP, "type": "u16"},
    }
    be = P.PortBackend(CHAIN)
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

    be2 = P.PortBackend(CHAIN)  # the layer restarted
    serve_once(mm, ram)
    check(be2.read("monster_hp") == 4000, "a restarted layer starts in step with the port")

    import os, tempfile, threading
    stop = threading.Event()

    def tick():
        while not stop.is_set():
            serve_once(mm, ram)
            time.sleep(0.01)

    th = threading.Thread(target=tick)
    th.start()
    path = os.path.join(tempfile.mkdtemp(), "snap.bin")
    try:
        got = be2.snapshot(path, timeout=10)
    finally:
        stop.set()
        th.join()
    check(got and os.path.getsize(path) == P.MEM1_SIZE + P.MEM2_SIZE, "a snapshot is all of MEM1 and MEM2")
    with open(path, "rb") as f:
        f.seek(MONSTER_DATA + MONSTER_HP_OFF - P.MEM1)
        check(int.from_bytes(f.read(2), "big") == 4000, "...with RAM at its addresses, big-endian")
    os.remove(path)

    # the command menu, driven as the pad would, through the layer's app
    import ffxi_mh3 as M
    cfg = tomllib.loads(CONFIG)
    app = M.App(M.Engine("RDM", "sword_and_shield", 150, levels={"RDM": 20}), P.PortBackend(cfg["addresses"]), cfg)
    serve_once(mm, ram)
    app.step()
    press(mm, P.CL_Y)                                     # open (held, in the port)
    app.step()
    check(app.menu == {"stack": [[0, 0]]}, "the pad's X opens the command menu")
    titles = [r[0] for r in app.menu_view(0)[0][1]]
    check(titles == ["Weapon Skills", "Job Abilities", "Magic", "Items", "Job"], f"its first level ({titles})")
    press(mm, P.CL_DOWN); press(mm, P.CL_DOWN); press(mm, P.CL_B)   # Magic
    app.step()
    view = app.menu_view(0)
    check(len(view) == 2 and [r[0] for r in view[1][1]] == ["White Magic", "Black Magic"],
          "Magic: a list per school, beside the first level")
    press(mm, P.CL_B)                                     # White Magic
    press(mm, P.CL_DOWN); press(mm, P.CL_B)               # its second spell: Cure
    app.step()
    serve_once(mm, ram)
    check(app.menu is None and not app.mem.menu_open(), "a spell chosen closes the menu (the port told)")
    check(app.e.casting == "Cure", f"...and casts it ({app.e.casting})")
    press(mm, P.CL_Y); app.step()                         # open again
    for _ in range(3):
        press(mm, P.CL_DOWN)
    press(mm, P.CL_B)                                     # Items
    app.step(); serve_once(mm, ram)
    check(ram.items_used == 1 and app.menu is None, "Items passes one press to the game's own item use")
    press(mm, P.CL_Y); app.step()
    for _ in range(4):
        press(mm, P.CL_DOWN)
    press(mm, P.CL_B); press(mm, P.CL_DOWN); press(mm, P.CL_B)       # Job, Support Job
    press(mm, P.CL_DOWN); press(mm, P.CL_B)               # past "None": Warrior
    app.step(); serve_once(mm, ram)
    check(app.e.sub == "WAR" and app.menu is None, f"Job: a support job chosen ({app.e.job_label()})")
    press(mm, P.CL_Y); app.step()
    press(mm, P.CL_B); press(mm, P.CL_A)                  # Weapon Skills, then back
    app.step()
    check(app.menu == {"stack": [[0, 0]]}, "back from a list returns to the first level")
    press(mm, P.CL_A); app.step(); serve_once(mm, ram)    # back again: closed
    check(app.menu is None and not app.mem.menu_open(), "back from the first level closes it")

    # the objects the port's hooks find (the real game's layout)
    be3 = P.PortBackend(tomllib.loads(CONFIG)["addresses"])
    serve_once(mm, ram)
    check(be3.read("player_hp") == 150 and be3.read("player_max_hp") == 150, "the hunter's HP through its object")
    check(be3.read("monster_hp") is None, "no monster until the game changes one's HP")
    ram.em_hp_add(MONSTER_OBJ, -40)
    serve_once(mm, ram)
    check(be3.read("monster_hp") == 2960, "the monster the game hit, s32 at +0x7A0")
    o = be3.objects()
    check(o["monster"] == MONSTER_OBJ and o["player"] == PLAYER_OBJ and o["monster_delta"] == -40,
          "the hooks' objects and the last change are published")
    be3.write("monster_hp", 2500)
    serve_once(mm, ram)
    check(ram.rd(MONSTER_OBJ + EM_HP, 4) == 2500, "a write lands in the monster object")
    ram.em_hp_add(MONSTER2_OBJ, -30)  # another monster hit
    serve_once(mm, ram)
    check(be3.read("monster_hp") is None, "a new monster reads as nothing for one tick (not a hit)")
    check(be3.read("monster_hp") == 870, "...then its own HP")
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
