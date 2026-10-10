# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10"]
# ///
"""FFXI's spell effects from your own installation, run as FFXI runs them, for the port to draw.

    uv run ffxi_fx.py list 2801               # an effect file: its routines, generators, curves, meshes
    uv run ffxi_fx.py routine 2801 main       # what a routine plays, when
    uv run ffxi_fx.py preview Cure            # pictures of a spell's effect, cast by a Hume (asset folder)

An effect file (a spell's: 2800 + its animation number; the shared one: ROM/0/0.DAT; a race's own:
ROM/10/0-7.DAT) is a tree of chunks under directories (the format as the community's viewers read it,
written here from that description, no code taken):
  routine (0x07)    a schedule, 60 ticks a second: from body+0x14 (body-relative, minus 16) its entries,
                    each u8 op, u16 size in words (low 5 bits), u16 delay at +4 (the wait after it: an
                    entry runs at the sum of the delays before it), u16 duration at +6, a 4-letter id at
                    +8. 0x02 spawns a generator for `duration` ticks; 0x03/0x09/0x3B/0x57 run another
                    routine (this file's, else the shared file's), 0x3C one of the caster's (its race's
                    file); 0x3B/0x3C hold the rest of the routine until theirs ends; 0x1E stops a
                    generator; 0x69/0x6A bracket a block, 0x3D/0x3E a set of which one runs, 0x64/0x67
                    if and else over 0x6B's operands.
  generator (0x05)  where it attaches (u16 at body+0: low 4 bits, 1 the caster, 2 the target...), how it
                    emits (at chunk+0x74: u16 variance, u16 ticks between emissions - 1, u8 particles - 1,
                    u8 flags), and four opcode streams (offsets at chunk+0x80): the generator's updaters,
                    each particle's initialisers, its updaters, what happens when it expires. An opcode:
                    u32 (low byte the opcode, bits 8-12 its size in words, bits 13+ a slot an initialiser
                    allocates and an updater reads), then its words.
  curve (0x19)      (time, value) floats from chunk+0x10 until time 1: piecewise linear over a
                    particle's life.
  particle mesh (0x1F), sprite sheet (0x21), texture (0x20): what a particle draws.
A particle's colour: 4 x the mesh's vertex colour x the texel x its colour (alpha 8 x), blended as its
0x1E opcode says (added, by alpha, subtracted).

The effects run in FFXI's own space (Y down, a unit a metre, a model facing +X); the port's world is
Monster Hunter's (Y up, a unit a centimetre): `to_mh` turns one into the other, as the character's place
(ffxi_model.pack) does, so an effect sits on the character as it would in FFXI.
"""
from __future__ import annotations

import math
import random
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ffxi_assets as A  # noqa: E402

TICKS = 60                               # the effect engine's clock: ticks a second
GLOBAL_FILE = "ROM/0/0.DAT"              # the shared effects: hits, casting, releases
RACE_FILES = {"hume_male": "ROM/10/0.DAT", "hume_female": "ROM/10/1.DAT", "elvaan_male": "ROM/10/2.DAT",
              "elvaan_female": "ROM/10/3.DAT", "tarutaru_male": "ROM/10/4.DAT", "tarutaru_female": "ROM/10/5.DAT",
              "mithra": "ROM/10/6.DAT", "galka": "ROM/10/7.DAT"}
# casting, by school: the race's routine while a spell is cast (its release is the spell's own `main`)
CAST_ROUTINE = {"white": "cawh", "black": "cabk", "summoning": "casm", "ninjutsu": "canj", "song": "caso",
                "blue": "cabk", "geomancy": "cawh", "trust": "cawh"}
# a blow landing, by Monster Hunter's weapon: the shared file's spark for FFXI's like of it (its `atpr`
# picks by weapon type: 3 a sword hit9, 4 a great sword hit8, 6 a great axe hi10, 8 a polearm hit7, 10 a
# great katana hi16, 11 a club hi17; a dagger's hit5 for a bowgun's shot)
HIT_ROUTINE = {"sword_and_shield": "hit9", "great_sword": "hit8", "switch_axe": "hi10", "lance": "hit7",
               "long_sword": "hi16", "hammer": "hi17", "bowgun": "hit5"}
# a critical blow: the shared file's `crtl` (its first case)
CRITICAL_ROUTINE = "hi14"
# a race's weapon skills: its first file (animation 1); the skill's file is that + its animation - 1, its
# directory the number in hex and a 1 ("0011" Fast Blade), its routine `main` (the race's motions in it)
WS_BASE = {"hume_male": 33228, "hume_female": 33996, "elvaan_male": 34764, "elvaan_female": 35532,
           "tarutaru_male": 36300, "tarutaru_female": 36300, "mithra": 37068, "galka": 37836}
# a skillchain's burst on the monster: FFXI keeps none apart, so the target's part of a spell of its
# element (a level 2's the stronger, a level 3's the strongest); the spells' animation numbers
SKILLCHAIN_LOOK = {"Liquefaction": 145, "Scission": 160, "Reverberation": 170, "Detonation": 155,
                   "Induration": 150, "Impaction": 165, "Transfixion": 29, "Compression": 245,
                   "Fusion": 146, "Fragmentation": 166, "Gravitation": 161, "Distortion": 151,
                   "Light": 21, "Darkness": 246}
DIR, END, GEN, ROUTINE, CURVE, PMESH, TEX, SPRITES = 0x01, 0x00, 0x05, 0x07, 0x19, 0x1F, 0x20, 0x21
# FFXI's space to Monster Hunter's: FFXI's +X (a model's forward) Monster Hunter's +Z, its Y (down) up,
# its Z the -X; and a metre 97.2 units (a Hume 1.8 tall, the hunter 175: ffxi_model.pack's scale)
AXES = np.array([[0, 0, -1], [0, -1, 0], [1, 0, 0]], np.float64)
SCALE = 175.0 / 1.8
# blending, as the port draws them: opaque, by alpha, added, subtracted, darkened
OPAQUE, ALPHA, ADD, SUBTRACT, DARKEN = range(5)
BLEND_NAMES = ("opaque", "alpha", "add", "subtract", "darken")


def to_mh(p: np.ndarray) -> np.ndarray:
    return (np.asarray(p, np.float64) @ AXES.T) * SCALE


def from_mh(p: np.ndarray) -> np.ndarray:
    return (np.asarray(p, np.float64) / SCALE) @ AXES


def rand() -> float:                     # -1..1
    return random.random() * 2 - 1


def posrand(x: float = 1.0) -> float:    # 0..x
    return random.random() * x


# ================================================================================================
# 4x4 matrices, column-major in effect (a point is m @ [x, y, z, 1]); built as FFXI's engine builds
# them: each `*_in` multiplies on the right, so the last applied is innermost
# ================================================================================================
def ident() -> np.ndarray:
    return np.eye(4)


def translate_in(m: np.ndarray, v) -> np.ndarray:
    t = np.eye(4)
    t[:3, 3] = v
    return m @ t


def scale_in(m: np.ndarray, v) -> np.ndarray:
    return m @ np.diag([v[0], v[1], v[2], 1.0])


def rot_xyz(x: float, y: float, z: float) -> np.ndarray:
    sx, sy, sz, cx, cy, cz = math.sin(x), math.sin(y), math.sin(z), math.cos(x), math.cos(y), math.cos(z)
    r = np.eye(4)                        # the engine's XYZ order (written as its columns)
    r[:3, 0] = (cy * cz, sx * sy * cz + cx * sz, sx * sz - cx * sy * cz)
    r[:3, 1] = (cy * -sz, cx * cz - sx * sy * sz, cx * sy * sz + sx * cz)
    r[:3, 2] = (sy, -sx * cy, cx * cy)
    return r


def rot_zyx(x: float, y: float, z: float) -> np.ndarray:
    sx, sy, sz, cx, cy, cz = math.sin(x), math.sin(y), math.sin(z), math.cos(x), math.cos(y), math.cos(z)
    r = np.eye(4)
    r[:3, 0] = (cy * cz, cy * sz, -sy)
    r[:3, 1] = (sx * sy * cz - cx * sz, sx * sy * sz + cx * cz, sx * cy)
    r[:3, 2] = (cx * sy * cz + sx * sz, cx * sy * sz - sx * cz, cx * cy)
    return r


def rot_axis(axis: int, a: float) -> np.ndarray:
    s, c = math.sin(a), math.cos(a)
    r = np.eye(4)
    if axis == 0:
        r[1, 1], r[2, 1], r[1, 2], r[2, 2] = c, s, -s, c
    elif axis == 1:
        r[0, 0], r[2, 0], r[0, 2], r[2, 2] = c, -s, s, c
    else:
        r[0, 0], r[1, 0], r[0, 1], r[1, 1] = c, s, -s, c
    return r


def axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    a = axis / (np.linalg.norm(axis) or 1.0)
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    r = np.eye(4)
    r[:3, 0] = (t * a[0] * a[0] + c, t * a[0] * a[1] + s * a[2], t * a[0] * a[2] - s * a[1])
    r[:3, 1] = (t * a[0] * a[1] - s * a[2], t * a[1] * a[1] + c, t * a[1] * a[2] + s * a[0])
    r[:3, 2] = (t * a[0] * a[2] + s * a[1], t * a[1] * a[2] - s * a[0], t * a[2] * a[2] + c)
    return r


def movement_orientation(d: np.ndarray) -> np.ndarray:
    """Turns +X along d (a particle facing where it goes, or toward the camera)."""
    n = np.linalg.norm(d)
    if n < 1e-9:
        return np.eye(4)
    d = d / n
    if abs(d[1]) < 0.999:
        left = np.cross([0.0, 1.0, 0.0], d)
        left /= np.linalg.norm(left)
        up = np.cross(d, left)
        up /= np.linalg.norm(up)
        angle = -math.acos(max(-1.0, min(1.0, up[1]))) * math.copysign(1.0, d[1])
        return axis_angle(left, angle) @ rot_axis(1, -math.atan2(d[2], d[0]))
    return rot_axis(2, math.copysign(1.0, d[1]) * math.pi / 2)


# ================================================================================================
# the file: its chunks under their directories, looked up as the engine looks (this directory, its
# parents, the file, then the shared file)
# ================================================================================================
@dataclass
class Dir:
    name: str
    parent: "Dir | None"
    items: dict = field(default_factory=dict)       # (type, name) -> (at, size)


@dataclass
class Mesh:
    pos: np.ndarray                      # (n, 3) triangles' corners, three a triangle
    col: np.ndarray                      # (n, 4) 0..1
    uv: np.ndarray                       # (n, 2)
    tex: str | None                      # its texture's 16-character name


class EffectFile:
    def __init__(self, path: Path, data: bytes | None = None):
        self.path = Path(path)
        self.b = data if data is not None else self.path.read_bytes()
        self.root = Dir("", None)
        self.flat: dict = {}
        stack = [self.root]
        for at, nm, typ, size in A.chunks(self.b):
            name = nm.decode("latin-1").rstrip("\0 ")
            if typ == DIR:
                d = Dir(name, stack[-1])
                stack.append(d)
                continue
            if typ == END:
                if len(stack) > 1:
                    stack.pop()
                continue
            if typ == TEX:                                            # textures by their 16-character name
                name = self.b[at + 17:at + 33].decode("latin-1")
            stack[-1].items.setdefault((typ, name), (at, size))
            self.flat.setdefault((typ, name), (at, size, stack[-1]))
        self._cache: dict = {}

    def find(self, typ: int, name: str, d: Dir | None = None):
        """(at, size, its directory) of the chunk of that type and name nearest d, else the file's first."""
        while d is not None:
            if (typ, name) in d.items:
                at, size = d.items[(typ, name)]
                return at, size, d
            d = d.parent
        return self.flat.get((typ, name))

    def names(self, typ: int) -> list[str]:
        return [n for (t, n) in self.flat if t == typ]

    def _get(self, key, make):
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def u16(self, p: int) -> int:
        return struct.unpack_from("<H", self.b, p)[0]

    def curve(self, at: int, size: int) -> "Curve":
        def make():
            out, p = [], at + 16
            while p + 8 <= at + size:
                t, v = struct.unpack_from("<ff", self.b, p)
                out.append((t, v))
                p += 8
                if t == 1.0:
                    break
            return Curve(out or [(0.0, 0.0), (1.0, 0.0)])
        return self._get(("curve", at), make)

    def particle_mesh(self, at: int, size: int) -> list[Mesh]:
        def make():
            b, p = self.b, at + 16
            version = struct.unpack_from("<I", b, p)[0]
            if version not in (3, 5, 6):
                return []
            with_tex, without = b[p + 4], b[p + 5]
            total = with_tex + without
            p += 8
            slots = next((n for n in (3, 4, 7, 8, 11, 12, 15) if total <= n), None)
            if slots is None:
                return []
            counts = struct.unpack_from(f"<{slots}H", b, p)
            p += 2 * slots + (2 if version == 3 else 0)
            names = []
            for _ in range(4 if version == 3 else with_tex):
                names.append(b[p:p + 16].decode("latin-1"))
                p += 16
            meshes = []
            for k in range(total):
                n = 3 * counts[k]
                if p + n * 36 > at + size:
                    break
                raw = np.frombuffer(b, np.uint8, n * 36, p).reshape(n, 36)
                fl = raw.view(np.float32).reshape(n, 9)
                col = raw[:, 24:28][:, [2, 1, 0, 3]].astype(np.float32) / 255.0       # BGRA
                meshes.append(Mesh(fl[:, 0:3].astype(np.float64), col, fl[:, 7:9].astype(np.float64),
                                   names[k] if k < with_tex and k < len(names) else None))
                p += n * 36
            return meshes
        return self._get(("pmesh", at), make)

    def sprite_sheet(self, at: int, size: int) -> list[Mesh]:
        def make():
            b, p = self.b, at + 16
            unk, num = struct.unpack_from("<HH", b, p)
            flare, norm = b[p + 4] == 1, b[p + 7]
            scale = 1 / 256 if (unk == 1 and norm == 0) else 1.0
            tex = b[p + 8:p + 24].decode("latin-1")
            p += 24
            meshes = []
            for _ in range(num):
                if struct.unpack_from("<H", b, p)[0] != 1:
                    break
                quads = b[p + 2]
                p += 4 + (16 if flare else 0)
                n = 6 * quads
                raw = np.frombuffer(b, np.uint8, n * 24, p).reshape(n, 24)
                fl = raw.view(np.float32).reshape(n, 6)
                col = raw[:, 12:16].astype(np.float32) / 255.0
                meshes.append(Mesh(fl[:, 0:3].astype(np.float64), col, fl[:, 4:6].astype(np.float64) * scale, tex))
                p += n * 24
            return meshes
        return self._get(("sprites", at), make)

    def texture(self, at: int, size: int) -> np.ndarray | None:
        def make():
            info = A.image_info(self.b, at, size)
            return A.decode(self.b, info, ps2_alpha=False) if info else None
        return self._get(("tex", at), make)

    def generator(self, at: int, size: int, d: Dir) -> "GenDef":
        return self._get(("gen", at), lambda: GenDef(self, at, size, d))

    def routine(self, at: int, size: int) -> list["Entry"]:
        return self._get(("routine", at), lambda: routine_entries(self.b, at, size))


@dataclass
class Curve:
    keys: list

    def value(self, progress: float, initial: float | None = None) -> float:
        k = self.keys
        if progress >= 1:
            return k[-1][1]
        i = next((i for i, (t, _) in enumerate(k) if t > progress), -1)
        if i <= 0:
            return k[0][1]
        (t0, v0), (t1, v1) = k[i - 1], k[i]
        if i - 1 == 0 and initial is not None:
            v0 = initial
        f = 0.0 if t1 == t0 else (progress - t0) / (t1 - t0)
        return (1 - f) * v0 + f * v1


# ================================================================================================
# routines
# ================================================================================================
@dataclass
class Entry:
    op: int
    at: int
    words: int
    delay: int
    dur: int
    ref: str | None
    body: list | None = None


SPAWN, STOP_GEN, BLOCK_OPEN, BLOCK_CLOSE, RANDOM_OPEN, RANDOM_CLOSE = 0x02, 0x1E, 0x69, 0x6A, 0x3D, 0x3E
IF, ELSE, PUSH = 0x64, 0x67, 0x6B
CALLS = {0x03, 0x09, 0x3B, 0x3C, 0x57}
HOLDS = {0x3B, 0x3C}                     # the rest of the routine waits for theirs
ON_CASTER = {0x3C}                       # the caster's own files


def routine_entries(b: bytes, at: int, size: int) -> list[Entry]:
    """A routine's entries, nested on their brackets."""
    end = at + size
    if size < 0x30:
        return []
    body = at + 16
    p = body + struct.unpack_from("<i", b, body + 0x14)[0] - 16
    flat = []
    for _ in range(256):
        if p + 8 > end or p < body:
            break
        op = b[p]
        if op == 0:
            break
        n = (b[p + 1] | b[p + 2] << 8) & 0x1F
        delay, dur = struct.unpack_from("<HH", b, p + 4)
        ref = None
        if p + 16 <= end:
            raw = b[p + 8:p + 12]
            txt = raw.rstrip(b"\0 ").decode("latin-1")
            if txt and all(32 <= c < 127 for c in raw.rstrip(b"\0")):
                ref = txt
        flat.append(Entry(op, p, n, delay, dur, ref))
        p += max(1, n) * 4
    root, stack = [], None
    stack = [root]
    for e in flat:
        if e.op in (BLOCK_CLOSE, RANDOM_CLOSE):
            if len(stack) > 1:
                stack.pop()
            continue
        stack[-1].append(e)
        if e.op in (BLOCK_OPEN, RANDOM_OPEN):
            e.body = []
            stack.append(e.body)
    return root


MOTION, LANDS = 0x05, "mdam"             # a motion on the caster (its name's three letters and ?: every
                                         # part); the routine that shows the target hurt: the blow landing


def motion_schedule(b: bytes) -> tuple[list[tuple[int, str, int]], int | None]:
    """A weapon skill's file (the race's): what its `main` routine plays on the caster, in order, (tick from
    its start, the motion's three letters, its ticks), and the tick its blow lands (its call of mdam)."""
    for at, nm, typ, size in A.chunks(b):
        if typ == ROUTINE and nm == b"main":
            out, lands, clock = [], None, 0

            def walk(entries):
                nonlocal clock, lands
                for e in entries:
                    if e.op == MOTION and e.ref and len(e.ref) >= 3:
                        out.append((clock, e.ref[:3], e.dur))
                    elif e.op in CALLS and e.ref == LANDS and lands is None:
                        lands = clock
                    clock += e.delay
                    if e.body and e.op == BLOCK_OPEN:
                        walk(e.body)
            walk(routine_entries(b, at, size))
            return out, lands
    return [], None


@dataclass
class Spawn:
    tick: float
    file: EffectFile
    dir: Dir
    gen: str
    dur: int


@dataclass
class Stop:
    tick: float
    gen: str


class Library:
    """The effect files: a spell's, the shared one, the caster's race's; read once each."""

    def __init__(self, root: Path, race: str = "hume_male"):
        import ffxi_sound as S
        self.root, self.race = Path(root), race
        self._files: dict = {}
        self.file_path = lambda n: S.file_path(self.root, n)
        self.shared = self.file(self.root / GLOBAL_FILE)
        self.caster = self.file(self.root / RACE_FILES.get(race, RACE_FILES["hume_male"]))

    def file(self, path: Path) -> EffectFile:
        path = Path(path)
        if path not in self._files:
            self._files[path] = EffectFile(path)
        return self._files[path]

    def spell(self, animation: int) -> EffectFile | None:
        p = self.file_path(2800 + animation)
        return self.file(p) if p and p.exists() else None

    def weapon_skill(self, animation: int) -> EffectFile | None:
        """The race's own file for a weapon skill (its motion and its effects)."""
        base = WS_BASE.get(self.race)
        p = self.file_path(base + animation - 1) if base else None
        return self.file(p) if p and p.exists() else None

    def find_routine(self, name: str, here: EffectFile, op: int):
        """(file, at, size, dir) of a routine a call names: the caster's files for 0x3C, else this file,
        then the shared one (and the caster's, where a cast's own routines are)."""
        order = [self.caster, self.shared] if op in ON_CASTER else [here, self.shared, self.caster]
        for f in order:
            r = f.find(ROUTINE, name)
            if r:
                return (f, *r)
        return None

    def schedule(self, file: EffectFile, name: str, op: int = 0x03, start: float = 0.0, depth: int = 0,
                 regs: dict | None = None) -> tuple[list, float]:
        """What a routine plays (Spawn and Stop, ticks from its start, calls followed) and how long it
        runs (its delays, and the holds of its blocking calls)."""
        found = self.find_routine(name, file, op)
        if not found or depth > 8:
            return [], 0.0
        f, at, size, d = found
        regs = {} if regs is None else regs
        out: list = []
        clock = [start]
        operands: list = []

        def run(nodes):
            i = 0
            while i < len(nodes):
                e = nodes[i]
                if e.op == PUSH:
                    clock[0] += e.delay
                    operands.append(tuple(struct.unpack_from("<HHH", f.b, e.at + 8)))
                elif e.op == BLOCK_OPEN:
                    clock[0] += e.delay
                    run(e.body)
                elif e.op == RANDOM_OPEN:
                    clock[0] += e.delay
                    if e.body:
                        run([random.choice(e.body)])
                elif e.op == IF:
                    clock[0] += e.delay
                    taken = decide(operands, regs)
                    operands.clear()
                    j = i + 1
                    then = nodes[j] if j < len(nodes) and nodes[j].op == BLOCK_OPEN else None
                    j += then is not None
                    other = None
                    if j < len(nodes) and nodes[j].op == ELSE:
                        clock[0] += nodes[j].delay
                        j += 1
                        if j < len(nodes) and nodes[j].op == BLOCK_OPEN:
                            other = nodes[j]
                            j += 1
                    branch = then if taken else other
                    if branch:
                        clock[0] += branch.delay
                        run(branch.body)
                    i = j
                    continue
                else:
                    t = clock[0]
                    clock[0] += e.delay
                    if e.op == SPAWN and e.ref:
                        out.append(Spawn(t, f, d, e.ref, e.dur))
                    elif e.op == STOP_GEN and e.ref:
                        out.append(Stop(t, e.ref))
                    elif e.op in CALLS and e.ref:
                        sub, ticks = self.schedule(f, e.ref, e.op, t, depth + 1, regs)
                        out.extend(sub)
                        if e.op in HOLDS:
                            clock[0] += ticks
                i += 1

        run(f.routine(at, size))
        return out, clock[0] - start


SOUND_OPS = {0x0A, 0x0B, 0x4A, 0x53, 0x60}


def voice_options(f: EffectFile, routine: str) -> list:
    """A race's voice routine (vatk: attacking, vdam: hurt, vded: falling): the sounds it picks one of at
    random (its 0x3D block), a silent choice as None; each a sound's number (its 0x3D SeSep chunk's)."""
    r = f.find(ROUTINE, routine)
    if not r:
        return []
    ids = {}
    for at, nm, typ, size in A.chunks(f.b):
        body = f.b[at + 16:at + size]
        if typ == 0x3D and body[:5] == b"SeSep" and len(body) >= 12:
            ids[nm.decode("latin-1").rstrip("\0 ")] = struct.unpack_from("<I", body, 8)[0]
    out = []
    for e in f.routine(r[0], r[1]):
        for c in (e.body or [e]) if e.op == RANDOM_OPEN else [e]:
            if c.op in SOUND_OPS and c.ref in ids:
                out.append(ids[c.ref])
            elif e.op == RANDOM_OPEN:
                out.append(None)                                 # a choice that says nothing
    return out


def decide(operands: list, regs: dict) -> bool:
    """An if over a register and a literal (the engine's combat state, which isn't here): the first case
    taken, the register bound to it, so a switch's later cases are false."""
    if len(operands) != 3:
        return True
    (op_l, kind_l, val_l), (op_r, kind_r, val_r), (cmp, _, _) = operands
    if kind_l != 3 or kind_r != 1 or cmp != 0x0C:
        return True
    if val_l not in regs:
        regs[val_l] = val_r
        return True
    return regs[val_l] == val_r


# ================================================================================================
# generators
# ================================================================================================
KEYFRAME_INIT = set(range(0x21, 0x30)) | {0x33, 0x34, 0x35, 0x36, 0x37, 0x39, 0x50, 0x51, 0x52} | \
    set(range(0x59, 0x67)) | {0x68, 0x69, 0x6C, 0x6D, 0x6E, 0x6F, 0x70, 0x74, 0x75, 0x76, 0x77, 0x78, 0x7C, 0x7D,
                              0x80, 0x81, 0x83, 0x84, 0x85, 0x8B, 0x8C, 0x8D, 0x95, 0x96, 0x97}
# billboards
NONE, XYZ, XZ, CAMERA, MOVEMENT, MOVEMENT_H = range(6)
# what a particle draws (0x01's linked type)
STATIC_MESH, SPRITE_SHEET, RING_MESH = 0x0B, 0x0E, 0x24


@dataclass
class Op:
    op: int
    slot: int
    args: bytes

    def f(self, i: int) -> float:
        return struct.unpack_from("<f", self.args, 4 * i)[0]

    def u(self, i: int) -> int:
        return struct.unpack_from("<I", self.args, 4 * i)[0]

    def vec(self, i: int = 0) -> np.ndarray:
        return np.array(struct.unpack_from("<3f", self.args, 4 * i), np.float64)

    def ref(self, i: int) -> str:
        return self.args[4 * i:4 * i + 4].rstrip(b"\0 ").decode("latin-1")

    def i16(self, n: int = 4) -> tuple:
        return struct.unpack_from(f"<{n}h", self.args, 0)


class GenDef:
    def __init__(self, f: EffectFile, at: int, size: int, d: Dir):
        self.file, self.dir = f, d
        b, body = f.b, at + 16
        flags = struct.unpack_from("<H", b, body)[0]
        self.attach = flags & 0x0F
        self.joint0, self.joint1 = (flags & 0x03F0) >> 4, (flags & 0xFC00) >> 10
        self.variance, fpe = struct.unpack_from("<HH", b, at + 0x74)
        self.ticks_per_emission = fpe + 1
        self.per_emission = b[at + 0x78] + 1
        gflags = b[at + 0x79]
        self.singleton = bool(gflags & 0x04)
        self.auto_run = bool(gflags & 0x10)
        offs = struct.unpack_from("<4I", b, at + 0x80)
        self.streams: list[list[Op]] = [[], [], [], []]
        for k, off in enumerate(offs):
            if not off:
                continue
            p = at + off
            while p + 4 <= at + size:
                cfg = struct.unpack_from("<I", b, p)[0]
                op, words = cfg & 0xFF, (cfg >> 8) & 0x1F
                if op == 0 or words == 0:
                    break
                self.streams[k].append(Op(op, cfg >> 13, b[p + 4:p + 4 * words]))
                p += 4 * words
        self.setup = None                # 0x01's
        for o in self.streams[1]:
            if o.op == 0x01 and len(o.args) >= 44:
                bb, rs = struct.unpack_from("<HH", o.args, 0)
                life, life_var = struct.unpack_from("<HH", o.args, 30)
                bill = NONE
                if bb & 0x00C0 == 0xC0:
                    bill = CAMERA
                elif bb & 0x0081 == 0x81:
                    bill = MOVEMENT
                elif bb & 0x0080:
                    bill = MOVEMENT_H
                elif bb & 0x0040:
                    bill = MOVEMENT
                elif bb & 0x4000:
                    bill = XZ
                elif bb & 0x0001:
                    bill = XYZ
                self.setup = {
                    "billboard": bill, "scale_first": bool(bb & 0x0002), "zyx": bool(bb & 0x0200),
                    "follow_camera": bool(bb & 0x0004) and self.attach != 2,
                    "depth_write": bool(bb & 0x1000), "decal": bool(rs & 0x0020),
                    "follow": not (rs & 0x0080), "ignore_alpha": bool(rs & 0x1000),
                    "linked": o.args[8:12].rstrip(b"\0 ").decode("latin-1"), "linked_type": o.args[29],
                    "base": np.array(struct.unpack_from("<3f", o.args, 16), np.float64),
                    "life": life if life else math.inf, "life_var": life_var,
                }
                if not life:
                    self.ticks_per_emission = math.inf
        self.valid = self.setup is not None

    def curve(self, name: str) -> Curve | None:
        r = self.file.find(CURVE, name, self.dir)
        return self.file.curve(r[0], r[1]) if r else None


# ================================================================================================
# the running effects
# ================================================================================================
@dataclass
class Actor:
    """Where a caster or a target is, in FFXI's space: its feet, its facing (FFXI's +X turned to where it
    looks), its height."""
    pos: np.ndarray
    facing: np.ndarray
    height: float = 1.8


class Transform:                         # a slot's velocity, as the engine keeps it
    __slots__ = ("vel", "rel", "vrot", "damp")

    def __init__(self):
        self.vel, self.rel, self.vrot, self.damp = np.zeros(3), np.zeros(3), np.zeros(3), None


class KeyRef:
    __slots__ = ("curve", "cycles", "initial")

    def __init__(self, curve, cycles):
        self.curve, self.cycles, self.initial = curve, cycles, None


class Particle:
    __slots__ = ("gen", "age", "max_age", "pos", "rot", "scale", "init_pos", "color", "mult", "slots", "tex_uv",
                 "blend", "alpha_override", "last_move", "prev_pos", "assoc_pos", "assoc_rot", "neg_y", "frame",
                 "ring")

    def __init__(self, gen: "Generator"):
        self.gen = gen
        self.age, self.max_age = 0.0, 0.0
        self.pos, self.rot, self.scale, self.init_pos = np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)
        self.color = np.ones(4)
        self.mult = np.ones(4)
        self.slots: dict = {}
        self.tex_uv = np.zeros(2)
        self.blend, self.alpha_override = ADD, None
        self.last_move, self.prev_pos = np.zeros(3), np.zeros(3)
        self.assoc_pos, self.assoc_rot = gen.assoc_pos.copy(), gen.assoc_rot.copy()
        self.neg_y = False
        self.frame = 0
        self.ring = None

    def progress(self) -> float:
        return min(1.0, max(0.0, self.age / self.max_age)) if self.max_age else 1.0

    def transform(self) -> Transform | None:
        return next((v for v in self.slots.values() if isinstance(v, Transform)), None)

    def total_velocity(self, t: Transform | None) -> np.ndarray:
        if t is None:
            return np.zeros(3)
        r = rot_zyx(t.vrot[0], t.vrot[1] * (-1 if self.neg_y else 1), t.vrot[2])
        return r[:3, :3] @ (t.vel + t.rel)


class Generator:
    def __init__(self, fx: "Effects", d: GenDef, emit_ticks: float, play: "Play", parent: "Particle | None" = None):
        """parent: a particle this is a child generator of (its 0x3C or 0x44): it stands where that does."""
        self.fx, self.d, self.play, self.parent = fx, d, play, parent
        self.max_emit = float(emit_ticks)
        self.life = 0.0
        self.until_next = 0.0
        self.ticks_per_emission = d.ticks_per_emission
        self.emitted = 0
        self.stopped = False
        self.particles: list[Particle] = []
        self.assoc_pos, self.assoc_rot = np.zeros(3), np.eye(4)
        self._frozen = False
        self.place()

    def actor(self) -> Actor | None:
        if self.parent is not None:
            return self.parent.gen.actor()
        src, tgt = self.play.source, self.play.target or self.play.source
        return tgt if self.d.attach in (2, 4, 6) else src

    def place(self):
        """Where it is: its actor's feet (a joint named: half its height up), turned as it faces; the
        target's sampled once, the caster's every tick."""
        if self.parent is not None:                  # a child: where its particle is, turned as that is
            if self.parent.age < self.parent.max_age or not self._frozen:
                w, _ = particle_matrices(self.parent, None)
                self.assoc_pos = w[:3, 3].copy()
                self.assoc_rot = self.parent.assoc_rot.copy()
                self._frozen = True
            return
        if self._frozen and self.d.attach in (2, 4, 6):
            return
        a = self.actor()
        if a is None:
            return
        joint = self.d.joint1 if self.d.attach in (2, 4, 6) else self.d.joint0
        self.assoc_pos = a.pos + (np.array([0.0, -0.5 * a.height, 0.0]) if joint else 0.0)
        self.assoc_rot = np.eye(4)
        self.assoc_rot[:3, :3] = a.facing
        self._frozen = True

    def done_emitting(self) -> bool:
        return self.stopped or (not self.d.auto_run and self.life >= self.max_emit and self.emitted > 0)

    def expired(self) -> bool:
        return not self.d.valid or (not self.particles and self.done_emitting())

    def update(self, ticks: float):
        if self.d.attach in (1, 3, 5, 9, 0) or self.parent is not None:
            self.place()
        self.emit(ticks)
        for p in self.particles:
            update_particle(p, ticks)
        self.particles = [p for p in self.particles if p.age < p.max_age]

    def emit(self, ticks: float):
        if not self.d.valid:
            return
        self.life += ticks
        for o in self.d.streams[0]:
            gen_update(self, o)
        if self.done_emitting():
            return
        self.until_next -= ticks
        while self.until_next <= 0:
            if self.d.singleton and self.particles:
                break
            self.until_next += self.ticks_per_emission + posrand(self.d.variance)
            if not math.isfinite(self.ticks_per_emission) and self.emitted > 0:
                break
            for _ in range(1 if self.d.singleton else self.d.per_emission):
                self.particles.append(new_particle(self))
                self.emitted += 1
            if not math.isfinite(self.until_next) or len(self.particles) > 400:
                break


def gen_update(g: Generator, o: Op):
    """The generator's own updaters: its emission rate along a curve."""
    if o.op == 0x04 and len(o.args) >= 12:
        c = g.d.curve(o.ref(1))
        if c:
            limit = g.max_emit if math.isfinite(g.max_emit) and g.max_emit > 0 else 1.0
            freq = c.value(min(1.0, g.life / limit), 60.0 / g.d.ticks_per_emission if math.isfinite(g.d.ticks_per_emission) else None)
            if freq and freq > 0:
                g.ticks_per_emission = 60.0 / freq


def keyref(g: Generator, o: Op) -> KeyRef | None:
    c = g.d.curve(o.ref(1)) if len(o.args) >= 12 else None
    if not c:
        return None
    return KeyRef(c, max(1, (o.u(2) & 0xFFFF) >> 5))


def sphere_offset(radius_var, base, sx=1.0, sy=1.0, sz=1.0, rot_z=0.0, rot_y=0.0, tilt=0.0, tilt_var=math.pi,
                  divisor=1, n=0) -> np.ndarray:
    phi = random.random() * 2 * math.pi if divisor == 1 else math.pi + (2 * math.pi / divisor) * (n % divisor)
    r = 0.0 if radius_var == 0 else posrand(1.0) ** (1 / 3)
    m = rot_axis(1, rot_y) @ rot_axis(2, rot_z) @ np.diag([sx, sy, sz, 1.0]) @ rot_axis(1, phi) @ \
        rot_axis(2, tilt + tilt_var * rand())
    return (m @ np.array([base + radius_var * r, 0.0, 0.0, 1.0]))[:3]


CHILD_ONCE, CHILD_FOLLOW = 0x3C, 0x44     # a particle's child generator: emitting once where it's born (a
                                         # flare's crossing twin), or following it while it lives (a trail)


def new_particle(g: Generator) -> Particle:
    p = Particle(g)
    s = g.d.setup
    p.max_age = s["life"] + posrand(s["life_var"])
    for o in g.d.streams[1]:
        init_op(p, o)
    p.prev_pos = p.pos.copy()
    for o in g.d.streams[1]:                      # its child generators, once it stands where it was born
        if o.op in (CHILD_ONCE, CHILD_FOLLOW) and len(o.args) >= 8 and len(g.play.generators) < 300:
            r = g.d.file.find(GEN, o.ref(1), g.d.dir)
            if r:
                d = g.d.file.generator(r[0], r[1], r[2])
                if d.valid and d is not g.d:
                    span = 0.0 if o.op == CHILD_ONCE else max(1.0, p.max_age)
                    g.fx.born.append((g.play, Generator(g.fx, d, span, g.play, parent=p)))
    return p


def init_op(p: Particle, o: Op):
    op, n = o.op, len(o.args)
    g = p.gen
    if op in KEYFRAME_INIT:
        k = keyref(g, o)
        if k:
            p.slots[o.slot] = k
    elif op == 0x02 and n >= 12:                                  # velocity
        t = p.slots.setdefault(o.slot, Transform())
        if isinstance(t, Transform):
            t.vel = o.vec()
    elif op in (0x03, 0x0C, 0x13) and n >= 12:                    # velocity variance
        t = p.slots.get(o.slot)
        if isinstance(t, Transform):
            v = o.vec()
            t.vel = t.vel + np.array([v[0] * rand(), v[1] * rand(), v[2] * rand()])
    elif op in (0x0B, 0x12) and n >= 12:                          # rotation / scale velocity
        t = p.slots.setdefault(o.slot, Transform())
        if isinstance(t, Transform):
            t.vel = o.vec()
    elif op == 0x06 and n >= 8:
        p.init_pos = p.init_pos + sphere_offset(o.f(0), o.f(1), n=g.emitted)
    elif op == 0x07 and n >= 28:
        p.init_pos = p.init_pos + sphere_offset(o.f(0), o.f(1), o.f(2), o.f(3), o.f(4), rot_y=o.f(6), n=g.emitted)
    elif op == 0x1F and n >= 44:
        p.init_pos = p.init_pos + sphere_offset(o.f(0), o.f(1), o.f(2), o.f(3), o.f(4), o.f(5), o.f(6), o.f(7),
                                                o.f(8), 1 + o.u(10), g.emitted)
    elif op == 0x08 and n >= 4:                                   # along the spawn offset
        t = p.slots.get(o.slot)
        if isinstance(t, Transform):
            m = np.linalg.norm(p.init_pos)
            t.rel = p.init_pos / m * o.f(0) if m > 0 else np.zeros(3)
    elif op == 0x41 and n >= 4:
        t = p.slots.get(o.slot)
        m = np.linalg.norm(p.init_pos)
        if isinstance(t, Transform) and m > 0:
            t.rel = t.rel + p.init_pos / m * o.f(0) * rand()
    elif op == 0x31 and n >= 4:
        t = p.slots.get(o.slot)
        if isinstance(t, Transform):
            v = o.f(0) * rand()
            t.vel = np.array([v, v, v])
    elif op == 0x67:                                              # run backwards from the end
        t = p.slots.get(o.slot)
        if isinstance(t, Transform) and math.isfinite(p.max_age):
            p.pos = p.pos + p.total_velocity(t) * p.max_age
            t.vel, t.rel = -t.vel, -t.rel
    elif op == 0x09 and n >= 12:
        p.rot = o.vec()
    elif op == 0x0A and n >= 12:
        v = o.vec()
        p.rot = p.rot + np.array([v[0] * rand(), v[1] * rand(), v[2] * rand()])
    elif op == 0x3B and n >= 12:
        p.rot = p.rot + o.vec() * (1 + g.emitted)
        p.neg_y = True
    elif op == 0x0F and n >= 12:
        p.scale = o.vec()
    elif op == 0x10 and n >= 12:
        v = o.vec()
        p.scale = p.scale + np.array([posrand(v[0]), posrand(v[1]), posrand(v[2])])
    elif op == 0x11 and n >= 4:
        p.scale = p.scale + posrand(o.f(0))
    elif op == 0x16 and n >= 4:
        p.color = np.frombuffer(o.args[:4], np.uint8).astype(np.float64) / 255.0
    elif op == 0x17 and n >= 4:
        v = np.frombuffer(o.args[:4], np.uint8).astype(np.float64) / 255.0
        p.color = p.color + v * random.random()
    elif op == 0x18 and n >= 4:
        p.color = p.color + (o.u(0) & 0xFF) / 255.0 * random.random()
    elif op == 0x19 and n >= 8:
        p.slots[o.slot] = np.array(o.i16(), np.float64)
    elif op == 0x1A and n >= 8:
        ct = p.slots.get(o.slot)
        if isinstance(ct, np.ndarray):
            p.slots[o.slot] = ct + np.round(np.array(o.i16(), np.float64) * random.random())
    elif op == 0x1E and n >= 2:
        p0, p1 = o.args[0], o.args[1]
        p.alpha_override = min(255, p1 * 2) / 255.0 if p0 & 0x20 else None
        if (p0 >> 4) & 0b1101 & 1:
            p.blend = OPAQUE
        else:
            p.blend = {1: SUBTRACT, 2: SUBTRACT, 4: ALPHA, 6: DARKEN, 8: ADD}.get(p0 & 0x0F, ADD)
    elif op == 0x43 and n >= 4:
        pass                                                       # a deferred blend: the drawn one stays
    elif op == 0x3A and n >= 36:                                   # a ring
        radii = struct.unpack_from("<4f", o.args, 0)
        cols = [np.frombuffer(o.args[16 + 4 * i:20 + 4 * i], np.uint8).astype(np.float64) / 255.0 for i in range(4)]
        per, layers = o.args[32], 2 + o.args[33]
        p.ring = (radii[:layers], cols[:layers], per, layers)
    elif op == 0x3D:
        p.slots[o.slot] = {"acc": np.zeros(3), "prev": np.zeros(3)}
    elif op in (0x3E, 0x3F, 0x40) and n >= 8:
        osc = p.slots.get(o.slot)
        if isinstance(osc, dict):
            osc["acc"][op - 0x3E] = o.f(0) + o.f(1) * rand()
    elif op == 0x42:                                               # down on the ground: its actor's feet
        a = g.actor()
        if a is not None:
            world_y = g.assoc_pos[1] + g.d.setup["base"][1] + p.init_pos[1] + p.pos[1]
            p.pos[1] -= world_y - a.pos[1]


def curve_value(p: Particle, k: KeyRef, initial=None) -> float:
    if initial is not None and k.initial is None:
        k.initial = initial
    prog = (k.cycles * p.progress()) % 1.0 if p.progress() < 1 else 1.0
    return k.curve.value(prog, k.initial)


def update_particle(p: Particle, ticks: float):
    if p.age >= p.max_age:
        return
    p.age += ticks
    if p.age >= p.max_age:
        if any(o.op == 0x05 for o in p.gen.d.streams[3]):                 # it repeats
            p.age = 1e-7
        else:
            return
    p.prev_pos = p.pos.copy()
    p.mult = np.ones(4)
    for o in p.gen.d.streams[2]:
        update_op(p, o, ticks)
    p.last_move = p.pos - p.prev_pos


def update_op(p: Particle, o: Op, ticks: float):
    op, slot = o.op, p.slots.get(o.slot)
    if op == 0x02:
        if isinstance(slot, Transform):
            p.pos = p.pos + p.total_velocity(slot) * ticks
    elif op == 0x05:
        if isinstance(slot, Transform):
            p.rot = p.rot + slot.vel * ticks
    elif op == 0x08:
        if isinstance(slot, Transform):
            p.scale = p.scale + slot.vel * ticks
    elif op in (0x03, 0x06, 0x09) and len(o.args) >= 12:
        if isinstance(slot, Transform):
            slot.vel = slot.vel + o.vec() * ticks
    elif op == 0x0B:
        if isinstance(slot, np.ndarray):
            ct = np.floor(slot / 128.0) / 255.0                   # 8.8 fixed point in colour bytes
            p.color = p.color + ct * 0.5 * ticks
    elif op == 0x0C and len(o.args) >= 8:
        if isinstance(slot, np.ndarray):
            p.slots[o.slot] = slot + np.floor(np.array(o.i16(), np.float64) * ticks / 30)
    elif op == 0x0D:
        p.frame = int(p.progress() * 1000)                         # (the sheet's length picks it)
    elif isinstance(slot, KeyRef):
        if 0x0F <= op <= 0x11:
            p.pos[op - 0x0F] = curve_value(p, slot)
        elif 0x12 <= op <= 0x14:
            a = op - 0x12
            p.rot[a] = curve_value(p, slot, p.rot[a] / math.pi) * math.pi
        elif 0x15 <= op <= 0x17:
            a = op - 0x15
            p.scale[a] = curve_value(p, slot, p.scale[a])
        elif 0x18 <= op <= 0x1B:
            a = op - 0x18
            p.color[a] = curve_value(p, slot, p.color[a])
        elif op in (0x1C, 0x1D):
            p.tex_uv[op - 0x1C] = curve_value(p, slot)
        elif 0x30 <= op <= 0x32:
            t = p.transform()
            if t is not None:
                t.vel[op - 0x30] = curve_value(p, slot)
        elif op == 0x44:
            t = p.transform()
            if t is not None:
                t.damp = curve_value(p, slot)
        elif op in (0x54, 0x55):
            p.tex_uv[op - 0x54] += curve_value(p, slot) * ticks
        elif 0x56 <= op <= 0x58:
            p.rot[op - 0x56] += curve_value(p, slot) * ticks * math.pi
    elif op == 0x26 and len(o.args) >= 12:
        if isinstance(slot, Transform):
            r = o.vec()
            r = np.array([-r[2], r[1], r[0]])                       # actor-attached: the axes as the engine has them
            slot.vrot = slot.vrot + r * 0.5 * ticks
    elif op in (0x27, 0x28) and len(o.args) >= 4:
        p.tex_uv[op - 0x27] += o.f(0) * ticks
    elif op in (0x29, 0x2A, 0x2B) and len(o.args) >= 8:
        if isinstance(slot, dict) and o.f(0):
            axis = op - 0x29
            rate = 180.0 / o.f(0)
            freq = math.pi * (p.age / rate)
            base = 0.5 * (math.sin(o.f(1) + freq - math.pi / 2) + math.cos(o.f(1)))
            amp = 0.5 * slot["acc"][axis] * base * rate
            delta = amp - slot["prev"][axis]
            d = np.zeros(3)
            d[axis] = 1.0
            p.pos = p.pos + d * delta
            slot["prev"][axis] = amp
    elif op == 0x2C and len(o.args) >= 4:
        if isinstance(slot, Transform):
            f = (slot.damp if slot.damp is not None else o.f(0)) ** ticks
            slot.vel, slot.rel = slot.vel * f, slot.rel * f
    elif op == 0x2F:
        if isinstance(slot, Transform):
            m = np.linalg.norm(slot.vel) + np.linalg.norm(slot.rel)
            slot.vel, slot.rel = np.array([m, 0.0, 0.0]), np.zeros(3)
            slot.vrot = p.rot.copy()


def ring_mesh(ring) -> Mesh:
    radii, cols, per, layers = ring
    per = max(3, min(64, per))
    pos, col, uv = [], [], []
    rings = layers - 1
    for L in range(rings):
        r0, r1 = radii[L], radii[L + 1]
        c0, c1 = cols[L], cols[L + 1]
        for s in range(per):
            a0, a1 = 2 * math.pi * s / per, 2 * math.pi * (s + 1) / per
            corners = [(r0, a0, c0, s / per, L / rings), (r1, a0, c1, s / per, (L + 1) / rings),
                       (r1, a1, c1, (s + 1) / per, (L + 1) / rings), (r0, a0, c0, s / per, L / rings),
                       (r1, a1, c1, (s + 1) / per, (L + 1) / rings), (r0, a1, c0, (s + 1) / per, L / rings)]
            for r, a, c, u, v in corners:
                pos.append((r * math.cos(a), r * math.sin(a), 0.0))      # in X-Y: its size (x, y) scales it
                col.append(c)
                uv.append((u, v))
    return Mesh(np.array(pos), np.array(col), np.array(uv), None)


@dataclass
class Play:
    """One routine played: its spawns still to come, its generators."""
    name: str
    source: Actor
    target: Actor | None
    pending: list
    started: float
    generators: list = field(default_factory=list)
    stopped: bool = False
    alpha: float = 1.0                   # its particles' alpha times this (a status's faint pulse)


@dataclass
class Camera:
    """The game's camera, in FFXI's space: where it is, its view's rotation (world to view: rows)."""
    pos: np.ndarray
    rot_mh: np.ndarray                   # Monster Hunter's world to view (3x3)


class Effects:
    """The effects running: routines played on a caster and a target, their generators and particles,
    stepped at the engine's 60 ticks a second; drawn as triangles in Monster Hunter's world."""

    def __init__(self, library: Library):
        self.lib = library
        self.plays: list[Play] = []
        self.clock = 0.0
        self.camera: Camera | None = None
        self.born: list = []                     # child generators made this step: (their play, it)

    def play(self, file: EffectFile, routine: str, source: Actor, target: Actor | None = None,
             op: int = 0x03) -> Play:
        events, ticks = self.lib.schedule(file, routine, op)
        pl = Play(routine, source, target, sorted(events, key=lambda e: e.tick), self.clock)
        self.plays.append(pl)
        return pl

    def pulse(self, file: EffectFile, routine: str, source: Actor, target: Actor, alpha: float = 0.5,
              length: float = 0.5) -> Play | None:
        """What a routine shows on its target alone (none of the caster's), at once, its generators
        emitting `length` as long, `alpha` as bright: a status lasting on a monster, its spell's look."""
        events, _ = self.lib.schedule(file, routine)
        keep = []
        for e in events:
            if not isinstance(e, Spawn):
                continue
            r = e.file.find(GEN, e.gen, e.dir)
            if r and e.file.generator(r[0], r[1], r[2]).attach in (2, 4, 6):
                keep.append(e)
        if not keep:
            return None
        t0 = min(e.tick for e in keep)
        spawns = [Spawn((e.tick - t0) * length, e.file, e.dir, e.gen, max(1, int(e.dur * length))) for e in keep]
        pl = Play(routine, source, target, sorted(spawns, key=lambda e: e.tick), self.clock, alpha=alpha)
        self.plays.append(pl)
        return pl

    def stop(self, pl: Play):
        """No more of it: its spawns to come dropped, its generators stop emitting (what's out fades)."""
        pl.stopped = True
        pl.pending = []
        for g in pl.generators:
            g.stopped = True

    def step(self, seconds: float):
        ticks = min(seconds, 0.25) * TICKS
        self.clock += ticks
        for pl in self.plays:
            while pl.pending and pl.pending[0].tick <= self.clock - pl.started:
                e = pl.pending.pop(0)
                if isinstance(e, Spawn):
                    r = e.file.find(GEN, e.gen, e.dir) or self.lib.shared.find(GEN, e.gen)
                    owner = e.file if e.file.find(GEN, e.gen, e.dir) else self.lib.shared
                    if r:
                        d = owner.generator(r[0], r[1], r[2])
                        if d.valid:
                            pl.generators.append(Generator(self, d, e.dur, pl))
                else:
                    for g in pl.generators:
                        if g.d is not None and g.d.file.find(GEN, e.gen, g.d.dir) and not g.stopped:
                            g.stopped = True
            for g in pl.generators:
                g.update(ticks)
            pl.generators = [g for g in pl.generators if not g.expired()]
        for pl, g in self.born:                  # the children, from their next step
            pl.generators.append(g)
        self.born = []
        self.plays = [pl for pl in self.plays if pl.pending or pl.generators]

    def busy(self) -> bool:
        return bool(self.plays)

    # --- drawing ------------------------------------------------------------------------------------
    def particles(self):
        for pl in self.plays:
            for g in pl.generators:
                yield from g.particles

    def geometry(self) -> tuple[np.ndarray, list[int], list[tuple[str, str]]]:
        """The triangles to draw: (n, 10) float32 corners (Monster Hunter's world x y z, u, v, the texture
        (its index in `textures`, -1 none; +1000: its alpha ignored), r g b a as the shader multiplies the
        texel by), counts per blending (OPAQUE...), and the textures named (file path, 16-char name)."""
        by_blend: list[list[np.ndarray]] = [[] for _ in range(5)]
        textures: list = []
        index: dict = {}
        cam = self.camera
        view_rot = from_mh_rot(cam.rot_mh) if cam is not None else None
        for p in self.particles():
            if p.age <= 0 or p.age >= p.max_age:
                continue
            meshes = particle_meshes(p)
            if not meshes:
                continue
            s = p.gen.d.setup
            world, local = particle_matrices(p, cam)
            tf = np.clip(p.color * p.mult, 0.0, 1.0)
            if p.alpha_override is not None:
                tf[3] = p.alpha_override
            tf[3] *= p.gen.play.alpha
            for m in meshes:
                v = np.c_[m.pos, np.ones(len(m.pos))]
                if s["billboard"] in (XYZ, XZ) and cam is not None:
                    # facing the camera: the particle's own turn and size in the view's axes (FFXI's Y down
                    # the view's down), placed where it is
                    centre = to_mh(world[:3, 3])
                    if s["billboard"] == XYZ:
                        # (FFXI's Y down the view's down; its X the view's right, as XZ's: the level banner's
                        # echoes, XYZ, read mirrored over its XZ letters with the X turned too)
                        basis = np.diag([1.0, -1.0, 1.0])
                    else:
                        up_view = cam.rot_mh @ np.array([0.0, 1.0, 0.0])
                        basis = np.eye(3)
                        basis[:, 1] = -up_view
                        basis[:, 2] = (0.0, 0.0, -1.0)
                    off_view = (basis @ local[:3, :3] @ m.pos.T).T * SCALE
                    out = centre + off_view @ cam.rot_mh                  # the view's axes into the world's
                else:
                    out = to_mh((world @ local @ v.T).T[:, :3])
                rgba = np.empty((len(m.pos), 4))
                rgba[:, :3] = 4.0 * m.col[:, :3] * tf[:3]
                rgba[:, 3] = 8.0 * m.col[:, 3] * tf[3]
                tex = -1.0
                if m.tex:
                    key = texture_key(p.gen.d, m.tex, self.lib)
                    if key is not None:
                        if key not in index:
                            index[key] = len(textures)
                            textures.append(key)
                        tex = float(index[key]) + (1000.0 if s["ignore_alpha"] else 0.0)
                rows = np.empty((len(m.pos), 10), np.float32)
                rows[:, 0:3] = out
                rows[:, 3:5] = m.uv + p.tex_uv
                rows[:, 5] = tex
                rows[:, 6:10] = rgba
                by_blend[p.blend].append(rows)
        counts = [sum(len(r) for r in rs) for rs in by_blend]
        parts = [r for rs in by_blend for r in rs]
        verts = np.concatenate(parts) if parts else np.zeros((0, 10), np.float32)
        return verts, counts, textures


def from_mh_rot(r: np.ndarray) -> np.ndarray:
    return AXES.T @ r @ AXES


def particle_meshes(p: Particle) -> list[Mesh]:
    s = p.gen.d.setup
    t = s["linked_type"]
    if t == RING_MESH:
        return [ring_mesh(p.ring)] if p.ring else []
    d = p.gen.d
    f = d.file
    if t == STATIC_MESH:
        r = f.find(PMESH, s["linked"], d.dir) or p.gen.fx.lib.shared.find(PMESH, s["linked"])
        owner = f if f.find(PMESH, s["linked"], d.dir) else p.gen.fx.lib.shared
        return owner.particle_mesh(r[0], r[1]) if r else []
    if t == SPRITE_SHEET:
        r = f.find(SPRITES, s["linked"], d.dir) or p.gen.fx.lib.shared.find(SPRITES, s["linked"])
        owner = f if f.find(SPRITES, s["linked"], d.dir) else p.gen.fx.lib.shared
        sheet = owner.sprite_sheet(r[0], r[1]) if r else []
        if not sheet:
            return []
        i = min(len(sheet) - 1, int(p.progress() * (len(sheet) + 1)))
        return [sheet[max(0, i)]]
    return []


def texture_key(d: GenDef, name: str, lib: Library):
    """(the file, the texture's 16-char name) a particle's mesh names: its generator's directory, up, the
    file, then the shared file."""
    if d.file.find(TEX, name, d.dir):
        return (str(d.file.path), name)
    if lib.shared.find(TEX, name):
        return (str(lib.shared.path), name)
    return None


def particle_matrices(p: Particle, cam: Camera | None) -> tuple[np.ndarray, np.ndarray]:
    """Where it is (the world: its actor, its base, its own way) and how it's turned and sized."""
    s = p.gen.d.setup
    w = ident()
    w[:3, 3] = p.assoc_pos
    w = w @ p.assoc_rot
    w = translate_in(w, s["base"])
    w = translate_in(w, p.init_pos + p.pos)
    if s["billboard"] == CAMERA:
        w[:3, :3] = np.eye(3)
    if s["decal"]:
        a = p.gen.actor()
        if a is not None:
            w[1, 3] = a.pos[1]
    m = ident()
    d = None
    if s["billboard"] == MOVEMENT:
        d = p.last_move
    elif s["billboard"] == MOVEMENT_H:
        d = np.array([p.last_move[0], 0.0, p.last_move[2]])
    elif s["billboard"] == CAMERA and cam is not None:
        d = from_mh(cam.pos) - w[:3, 3]
    if d is not None:
        m = m @ movement_orientation(d)
    if s["scale_first"]:
        m = scale_in(m, p.scale)
    y = p.rot[1] * (-1 if p.neg_y else 1)
    m = m @ (rot_zyx(p.rot[0], y, p.rot[2]) if s["zyx"] else rot_xyz(p.rot[0], y, p.rot[2]))
    if not s["scale_first"]:
        m = scale_in(m, p.scale)
    return w, m


# ================================================================================================
# the actors, from Monster Hunter's world
# ================================================================================================
def actor_from_mh(pos_mh, rot_mh: np.ndarray, height_mh: float = 175.0) -> Actor:
    """An actor at a Monster Hunter position, turned as a Monster Hunter rotation turns its +Z."""
    facing = from_mh_rot(np.asarray(rot_mh, np.float64))
    return Actor(from_mh(np.asarray(pos_mh, np.float64)), facing, height_mh / SCALE)


def yaw_matrix(yaw: float) -> np.ndarray:
    """Monster Hunter's turn about its up axis (yaw 0: facing +Z)."""
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], np.float64)


# ================================================================================================
# commands
# ================================================================================================
def _file(lib: Library, arg: str) -> EffectFile:
    if arg.isdigit():
        n = int(arg)
        return lib.file(lib.file_path(n))
    return lib.file(lib.root / arg)


def main(argv=None) -> int:
    import argparse
    import ffxi_model as M
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ffxi", help="the FINAL FANTASY XI folder (else the launcher's)")
    ap.add_argument("--race", default="hume_male")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").add_argument("file")
    r = sub.add_parser("routine")
    r.add_argument("file")
    r.add_argument("name")
    pv = sub.add_parser("preview")
    pv.add_argument("spell")
    pv.add_argument("--cast", action="store_true", help="the casting aura instead of the spell")
    a = ap.parse_args(argv)
    lib = Library(M.ffxi_root(a.ffxi), a.race)
    if a.cmd == "list":
        f = _file(lib, a.file)
        for typ, label in ((ROUTINE, "routines"), (GEN, "generators"), (CURVE, "curves"), (PMESH, "meshes"),
                           (SPRITES, "sprite sheets"), (TEX, "textures")):
            print(f"{label}: {f.names(typ)}")
        return 0
    if a.cmd == "routine":
        f = _file(lib, a.file)
        events, ticks = lib.schedule(f, a.name)
        for e in sorted(events, key=lambda e: e.tick):
            if isinstance(e, Spawn):
                d = e.file.find(GEN, e.gen, e.dir)
                gd = e.file.generator(d[0], d[1], d[2]) if d else None
                what = (f"attach {gd.attach} joints {gd.joint0}/{gd.joint1} every {gd.ticks_per_emission} x"
                        f"{gd.per_emission} life {gd.setup['life'] if gd and gd.setup else '?'} "
                        f"draws {gd.setup['linked'] if gd and gd.setup else '?'}/{gd.setup['linked_type'] if gd and gd.setup else '?'}"
                        if gd and gd.valid else "(not found)")
                print(f"  {e.tick:6.0f}  spawn {e.gen} ({e.file.path.name}) for {e.dur}: {what}")
            else:
                print(f"  {e.tick:6.0f}  stop {e.gen}")
        print(f"  ({ticks:.0f} ticks)")
        return 0
    if a.cmd == "preview":
        return preview(lib, a.spell, a.cast)
    return 0


def preview(lib: Library, spell: str, cast: bool) -> int:
    """A spell's effect stepped through its life, drawn from the side (a camera 4 m off), as pictures."""
    import ffxi_data as D
    from PIL import Image
    anim = D.SPELL_ANIMATION.get(spell)
    if anim is None and not cast:
        print(f"no animation for {spell}")
        return 1
    me = actor_from_mh([0, 0, 0], yaw_matrix(0.0))
    them = actor_from_mh([0, 0, 400], yaw_matrix(math.pi), 200.0)
    fx = Effects(lib)
    eye = np.array([500.0, 120.0, 200.0])
    look = np.array([0.0, 90.0, 200.0])
    fwd = (look - eye) / np.linalg.norm(look - eye)
    right = np.cross(fwd, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    rot = np.array([right, up, -fwd])                     # world to view
    fx.camera = Camera(eye, rot)
    if cast:
        school = D.SPELLS.get(spell, {}).get("school", "white")
        fx.play(lib.caster, CAST_ROUTINE.get(school, "cawh"), me, None, 0x3C)
    else:
        f = lib.spell(anim)
        fx.play(f, "main", me, them)
    out = A.asset_dir() / "fx"
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    tex_cache: dict = {}
    for i in range(0, 240):
        fx.step(1 / 60)
        if i % 8:
            continue
        verts, counts, texs = fx.geometry()
        img = draw_preview(verts, counts, texs, rot, eye, lib, tex_cache)
        frames.append(img)
    sheet = Image.new("RGB", (8 * 160, ((len(frames) + 7) // 8) * 120))
    for k, im in enumerate(frames):
        sheet.paste(Image.fromarray(im).resize((160, 120)), ((k % 8) * 160, (k // 8) * 120))
    path = out / f"{'cast_' if cast else ''}{spell.replace(' ', '_')}.png"
    sheet.save(path)
    print(f"{len(frames)} frames -> {path}")
    return 0


# The level's banners (the client's effects ROM/13/35 "Level Up !!", 13/34 the level lost): their letters stand
# on a box of white at alpha 75 (of 128), which the client cuts away (as an alpha test would): cut here, these
# alone (a soft particle's alpha left as it is)
CUT_BOX_FILES = (("13", "35.DAT"), ("13", "34.DAT"))


def load_texture(lib: Library, key, cache: dict) -> np.ndarray | None:
    if key not in cache:
        f = lib.file(Path(key[0]))
        r = f.find(TEX, key[1])
        img = f.texture(r[0], r[1]) if r else None
        if img is not None and tuple(Path(key[0]).parts[-2:]) in CUT_BOX_FILES:
            img = img.copy()
            img[img[..., 3] < 100] = 0
        cache[key] = img
    return cache[key]


def draw_preview(verts, counts, texs, rot, eye, lib, cache, w=320, h=240) -> np.ndarray:
    """A small software drawing of the triangles: perspective, textured, blended (no depth)."""
    img = np.zeros((h, w, 3), np.float32)
    img[:] = (0.08, 0.1, 0.12)
    # the actors' feet: a line on the ground
    focal = 300.0
    start = 0
    for blend, n in enumerate(counts):
        tris = verts[start:start + n].reshape(-1, 3, 10)
        start += n
        for t in tris:
            v = (t[:, :3] - eye) @ rot.T
            if np.any(v[:, 2] > -10):
                continue
            sx = w / 2 + focal * v[:, 0] / -v[:, 2]
            sy = h / 2 - focal * v[:, 1] / -v[:, 2]
            x0, x1 = int(max(0, np.floor(sx.min()))), int(min(w - 1, np.ceil(sx.max())))
            y0, y1 = int(max(0, np.floor(sy.min()))), int(min(h - 1, np.ceil(sy.max())))
            if x0 > x1 or y0 > y1:
                continue
            xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            d = (sy[1] - sy[2]) * (sx[0] - sx[2]) + (sx[2] - sx[1]) * (sy[0] - sy[2])
            if abs(d) < 1e-9:
                continue
            a = ((sy[1] - sy[2]) * (xs - sx[2]) + (sx[2] - sx[1]) * (ys - sy[2])) / d
            b = ((sy[2] - sy[0]) * (xs - sx[2]) + (sx[0] - sx[2]) * (ys - sy[2])) / d
            c = 1 - a - b
            inside = (a >= 0) & (b >= 0) & (c >= 0)
            if not inside.any():
                continue
            col = a[..., None] * t[0, 6:10] + b[..., None] * t[1, 6:10] + c[..., None] * t[2, 6:10]
            ti = t[0, 5]
            if ti >= 0:
                key = texs[int(ti) % 1000]
                tex = load_texture(lib, key, cache)
                if tex is not None:
                    u = (a * t[0, 3] + b * t[1, 3] + c * t[2, 3]) % 1.0
                    vv = (a * t[0, 4] + b * t[1, 4] + c * t[2, 4]) % 1.0
                    tx = np.clip((u * tex.shape[1]).astype(int), 0, tex.shape[1] - 1)
                    ty = np.clip((vv * tex.shape[0]).astype(int), 0, tex.shape[0] - 1)
                    texel = tex[ty, tx].astype(np.float32) / 255.0
                    if ti >= 1000:
                        texel[..., 3] = 0.5
                    col = col * texel
            col = np.clip(col, 0, 1)
            region = img[y0:y1 + 1, x0:x1 + 1]
            m = inside[..., None]
            if blend == ADD:
                region += np.where(m, col[..., :3] * col[..., 3:4], 0)
            elif blend == SUBTRACT:
                region -= np.where(m, col[..., :3] * col[..., 3:4], 0)
            elif blend == ALPHA:
                region[:] = np.where(m, region * (1 - col[..., 3:4]) + col[..., :3] * col[..., 3:4], region)
            elif blend == DARKEN:
                region[:] = np.where(m, region * (1 - col[..., 3:4]), region)
            else:
                region[:] = np.where(m, col[..., :3], region)
            np.clip(region, 0, 1, out=region)
    return (img * 255).astype(np.uint8)


if __name__ == "__main__":
    raise SystemExit(main())
