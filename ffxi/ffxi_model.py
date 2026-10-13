# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10"]
# ///
"""FFXI's character models from your own installation: read, posed on their skeleton, exported, previewed.

Stage A of FFXI's characters in Monster Hunter: one model, outside the game.

    uv run ffxi_model.py info ROM/3/6.DAT          # what a model file holds
    uv run ffxi_model.py view ROM/3/6.DAT          # preview pictures (front, side, back) and an OBJ

Paths are relative to your FINAL FANTASY XI folder (the launcher's, or --ffxi). Everything written goes
to the local asset folder (models/), never into the repository.

The formats, as the community's viewers read them (written here from that description, no code taken):
  skeleton (chunk 0x29): u8 joint count at +2; from +4 each joint: u8 parent (itself: the root), a pad
    byte, a rotation quaternion (x, y, z, w) and a translation, relative to its parent.
  mesh (chunk 0x2A): a header of offsets (in 2-byte units) to a joint table, the counts of one-joint and
    two-joint vertices, each vertex's joint references, the vertex data, and an instruction stream.
    A one-joint vertex is a position and a normal in its joint's space; a two-joint vertex holds both
    positions already weighted, the two weights, then both normals (each as its joint turns it). A cloth
    mesh's vertices have no normals. A symmetric mesh is drawn twice, the second time
    from flipped joints and axes. The instructions: 0x8000 a texture's 16-character name, 0x8010
    material properties, 0x5453/0x0054 textured strips/lists with UVs, 0x4353/0x0043 coloured ones,
    0xFFFF the end.
    What a worn piece hides of the others: the mesh header's fourth byte is what it covers (its occlusion
    type), and each group of triangles names what it is (its display type: the material instruction's
    fourteenth byte): 1-3 hair, 4 the face, 5 the wrists, 6 the trousers, 7 the shins. A hat (2, 3) hides
    the hair the face gives under it (1), a helmet (4-6) all of it, a full one (5) the face too; sleeves
    (0x12) the wrists, boots the trousers (0x32) and shins (0x22). Without it a face's hair shows
    through the headgear (occluded).
  skeleton's references: after its joints, a table of named points on it (u16 count, u16, then each a u16
    joint, 12 bytes, an offset): 126 the left hand, 127 the right. A weapon's info chunk names the
    reference of its grip (the sixth byte after the header; 0xFF for none: a shield, worn on its own
    mount): while the character fights, the grip goes to the hand (main: the right, sub: the left), the
    weapon in it; at rest it is where the motions put it (sheathed).
  textures: the file's image chunks, named by their 16-character category and name.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ffxi_assets as A  # noqa: E402

SKELETON, MESH, IMAGE, MOTION = 0x29, 0x2A, 0x20, 0x2B


class Reader:
    def __init__(self, b: bytes, pos: int = 0):
        self.b, self.pos = b, pos

    def take(self, fmt: str):
        v = struct.unpack_from("<" + fmt, self.b, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return v if len(v) > 1 else v[0]

    def u8(self): return self.take("B")
    def u16(self): return self.take("H")
    def i32(self): return self.take("i")
    def u32(self): return self.take("I")
    def f32(self): return self.take("f")
    def vec3(self): return list(self.take("3f"))

    def name16(self) -> str:
        s = self.b[self.pos:self.pos + 16].decode("latin-1")
        self.pos += 16
        return s


# ================================================================================================
# reading
# ================================================================================================
def parse_skeleton(b: bytes, data: int) -> list[dict]:
    r = Reader(b, data + 2)
    n = r.u8()
    r.pos = data + 4
    joints = []
    for i in range(n):
        parent = r.u8()
        r.u8()
        rot = r.take("4f")
        trans = r.vec3()
        joints.append({"parent": -1 if parent == i else parent, "rot": rot, "trans": trans})
    return joints


def skeleton_refs(b: bytes, data: int) -> list[int]:
    """The skeleton's references (its named points: the hands, the weapons' grips): each one's joint."""
    n = b[data + 2]
    at = data + 4 + 30 * n
    if at + 4 > len(b):
        return []
    count = struct.unpack_from("<H", b, at)[0]
    return [struct.unpack_from("<H", b, at + 4 + 26 * i)[0] for i in range(count) if at + 4 + 26 * i + 26 <= len(b)]


def skeleton_points(path: Path) -> list[tuple[int, tuple]]:
    """A model's named points (its first skeleton's references, 26 bytes each: u16 joint, then six floats, the
    last three its offset from that joint, in metres): what an effect's generator means by its joint number.
    (Shantotto II's orbs are on points 28-33: joints 102-107, which her idl1 turns about her; joints 28-33
    are her head's.)"""
    try:
        b = Path(path).read_bytes()
    except OSError:
        return []
    for at, _nm, typ, size in A.chunks(b):
        if typ != SKELETON:
            continue
        data = at + 16
        ref_at = data + 4 + 30 * b[data + 2]
        if ref_at + 4 > at + size:
            return []
        count = struct.unpack_from("<H", b, ref_at)[0]
        out = []
        for i in range(count):
            r = ref_at + 4 + 26 * i
            if r + 26 > at + size:
                break
            off = struct.unpack_from("<3f", b, r + 14)
            out.append((struct.unpack_from("<H", b, r)[0],
                        off if all(math.isfinite(v) and abs(v) < 10 for v in off) else (0.0, 0.0, 0.0)))
        return out
    return []


def weapon_grip(path: Path) -> int | None:
    """A weapon's grip: the skeleton reference that goes to the hand while fighting (None: none, a shield)."""
    try:
        b = Path(path).read_bytes()
    except OSError:
        return None
    for at, _nm, typ, size in A.chunks(b):
        if typ == 0x45 and size >= 23:
            return None if b[at + 22] == 0xFF else b[at + 22]
    return None


LEFT_HAND, RIGHT_HAND = 126, 127                 # the skeleton's references for the hands


def grip_pose(joints: list[dict], q: np.ndarray, t: np.ndarray, s: np.ndarray, grips: list) -> None:
    """Each grip (its joint, its hand's) put in the hand, in place: its own rotation, offset and scale
    made such that it stands as its hand stands (its scale 1), whatever its parent does."""
    if not grips:
        return
    world = pose_world(joints, q, t, s)
    scl = np.ones((len(joints), 3))
    for i, j in enumerate(joints):                                     # the scales pose_world chains
        scl[i] = s[i] * (scl[j["parent"]] if j["parent"] >= 0 else 1.0)
    for grip, hand in grips:
        p = joints[grip]["parent"]
        hw = world[hand]
        hand_rot = hw[:3, :3] / np.maximum(np.linalg.norm(hw[:3, :3], axis=0), 1e-9)
        if p < 0:
            pr, pp, ps = np.eye(3), np.zeros(3), np.ones(3)
        else:
            pw = world[p]
            pr = pw[:3, :3] / np.maximum(np.linalg.norm(pw[:3, :3], axis=0), 1e-9)
            pp, ps = pw[:3, 3], scl[p]
        q[grip] = mat_quat(pr.T @ hand_rot)
        t[grip] = (pr.T @ (hw[:3, 3] - pp)) / np.maximum(ps, 1e-9)
        s[grip] = 1.0 / np.maximum(ps, 1e-9)


def mat_quat(m: np.ndarray) -> np.ndarray:
    """A rotation matrix's quaternion (x, y, z, w)."""
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        k = np.sqrt(tr + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / k, (m[0, 2] - m[2, 0]) / k, (m[1, 0] - m[0, 1]) / k, 0.25 * k]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        k = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * k, (m[0, 1] + m[1, 0]) / k, (m[0, 2] + m[2, 0]) / k, (m[2, 1] - m[1, 2]) / k]
    elif m[1, 1] > m[2, 2]:
        k = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / k, 0.25 * k, (m[1, 2] + m[2, 1]) / k, (m[0, 2] - m[2, 0]) / k]
    else:
        k = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / k, (m[1, 2] + m[2, 1]) / k, 0.25 * k, (m[1, 0] - m[0, 1]) / k]
    q = np.array(q)
    return q / (np.linalg.norm(q) or 1.0)


def _ref(v: int) -> tuple[int, int, int]:
    return v & 0x7F, (v >> 7) & 0x7F, (v >> 14) & 3          # joint, its mirror, the axis flipped


def parse_mesh(b: bytes, data: int, name: str) -> dict:
    r = Reader(b, data)
    r.u8(); r.u8()
    flags3 = r.u8()
    cloth, use_table = bool(flags3 & 1), bool(flags3 & 0x80)
    occlude = r.u8()                                            # what it hides of the other pieces worn
    symmetric = r.u8() == 1
    r.u8()
    instr = 2 * r.i32(); r.u8(); r.u8()
    table_at = 2 * r.i32(); n_table = r.u16()
    counts_at = 2 * r.i32(); n_counts = r.u16()
    refs_at = 2 * r.i32(); r.u16()
    verts_at = 2 * r.i32()
    r.pos = data + table_at
    table = [r.u16() for _ in range(n_table)]
    joint = (lambda i: table[i] if i < len(table) else 0) if use_table else (lambda i: i)
    r.pos = data + counts_at
    if n_counts != 2:
        raise ValueError(f"{name}: {n_counts} vertex counts")
    n1, n2 = r.u16(), r.u16()
    n = n1 + n2
    r.pos = data + refs_at
    refs = [(_ref(r.u16()), _ref(r.u16())) for _ in range(n)]
    normals = not cloth
    p0 = np.zeros((n, 3)); p1 = np.zeros((n, 3)); w = np.zeros((n, 2)); w[:, 0] = 1
    q0 = np.zeros((n, 3)); q1 = np.zeros((n, 3))            # the normals (a cloth's: none; pack works them out)
    r.pos = data + verts_at
    for i in range(n1):
        p0[i] = r.vec3()
        if normals:
            q0[i] = r.vec3()
    for i in range(n1, n):
        x0, x1, y0, y1, z0, z1, w0, w1 = r.take("8f")
        p0[i], p1[i], w[i] = (x0, y0, z0), (x1, y1, z1), (w0, w1)
        if normals:
            a0, a1, b0, b1, c0, c1 = r.take("6f")
            q0[i], q1[i] = (a0, b0, c0), (a1, b1, c1)
    j0 = np.array([joint(a[0]) for a, _ in refs])
    j1 = np.array([joint(bb[0]) if i >= n1 else -1 for i, (_, bb) in enumerate(refs)])
    mj0 = np.array([joint(a[1]) for a, _ in refs])
    mj1 = np.array([joint(bb[1]) if i >= n1 else -1 for i, (_, bb) in enumerate(refs)])
    fa0 = np.array([a[2] for a, _ in refs])
    fa1 = np.array([bb[2] for _, bb in refs])

    pieces, tex, display = [], "", 0
    r.pos = data + instr
    while True:
        op = r.u16()
        if op == 0xFFFF:
            break
        if op == 0x8000:
            tex = r.name16()
        elif op == 0x8010:                                          # material properties: what it is
            at = r.pos
            display = b[at + 13]                                    # (a colour, two floats, a flag, then it)
            r.pos = at + 4 + 8 + 4 + 4 + 8 + 2 + 4 + 2 + 4 + 4
        elif op in (0x5453, 0x4353):                                # strips
            nt = r.u16()
            idx = [r.u16(), r.u16(), r.u16()]
            uv = [r.take("6f")] if op == 0x5453 else [None]
            if op == 0x4353:
                r.u32()
            uvs = [uv[0][0:2], uv[0][2:4], uv[0][4:6]] if op == 0x5453 else [(0, 0)] * 3
            for _ in range(nt - 1):
                idx.append(r.u16())
                uvs.append(r.take("2f") if op == 0x5453 else (0, 0))
            tris = []
            for k in range(nt):                                     # a strip: every other one flipped
                a, bb, c = (k, k + 1, k + 2) if k % 2 == 0 else (k + 1, k, k + 2)
                tris.append(((idx[a], uvs[a]), (idx[bb], uvs[bb]), (idx[c], uvs[c])))
            pieces.append({"tex": tex, "tris": tris, "display": display})
        elif op in (0x0054, 0x0043):                                # lists
            nt = r.u16()
            tris = []
            for _ in range(nt):
                i0, i1, i2 = r.u16(), r.u16(), r.u16()
                if op == 0x0054:
                    u = r.take("6f")
                    tris.append(((i0, u[0:2]), (i1, u[2:4]), (i2, u[4:6])))
                else:
                    r.u32()
                    tris.append(((i0, (0, 0)), (i1, (0, 0)), (i2, (0, 0))))
            pieces.append({"tex": tex, "tris": tris, "display": display})
        else:
            raise ValueError(f"{name}: unknown mesh instruction {op:#06x} at {r.pos - 2 - data:#x}")
    return {"name": name, "p0": p0, "p1": p1, "w": w, "j0": j0, "j1": j1, "mj0": mj0, "mj1": mj1,
            "fa0": fa0, "fa1": fa1, "symmetric": symmetric, "pieces": pieces, "n0": q0, "n1": q1,
            "cloth": cloth, "occlude": occlude}


# a display type, and the occlusion types that hide it (the module's description)
HIDDEN_BY = {1: {2, 3, 4, 5, 6}, 2: {4, 5, 6}, 3: {4, 5, 6}, 4: {5}, 5: {0x12}, 6: {0x32}, 7: {0x22}}


def occluded(model: dict) -> set:
    """What the model's pieces hide of each other: every mesh's occlusion type."""
    return {m.get("occlude", 0) for m in model["meshes"]}


def shown(piece: dict, occl: set) -> bool:
    """A group of triangles drawn: not covered by any piece worn."""
    return not (HIDDEN_BY.get(piece.get("display", 0), set()) & occl)


def parse_motion(b: bytes, data: int, name: str) -> dict:
    """A motion (chunk 0x2B): u16 ?, u16 joints, u16 frames, f32 rate (keyframes per game frame, at
    30 game frames a second); then per joint an i32 joint number and three channel groups (rotation
    x y z w, translation x y z, scale x y z), each its offsets (i32, in floats from the end of the
    header: 0 a constant, > 0 one float per frame there, < 0 the joint held at its rest pose) then
    its constants (f32). Rotations apply after the joint's rest rotation, translations add to its
    rest offset."""
    r = Reader(b, data + 2)
    n, frames, rate = r.u16(), r.u16(), r.f32()
    base = r.pos
    tracks = {}

    def group(k: int):
        offs = [r.i32() for _ in range(k)]
        consts = [r.f32() for _ in range(k)]
        if any(o < 0 for o in offs):
            return None
        out = np.empty((frames, k), np.float32)
        for c, (o, v) in enumerate(zip(offs, consts)):
            out[:, c] = np.frombuffer(b, "<f4", frames, base + 4 * o) if o > 0 else v
        return out

    for _ in range(n):
        j = r.i32()
        q, t, s = group(4), group(3), group(3)
        tracks[j] = None if q is None or t is None or s is None else (q, t, s)
    return {"name": name, "frames": frames, "rate": rate, "tracks": tracks}


def load(path: Path) -> dict:
    b = path.read_bytes()
    out = {"skeletons": [], "meshes": [], "textures": {}, "motions": [], "errors": []}
    for at, nm, typ, size in A.chunks(b):
        name = nm.decode("latin-1")
        try:
            if typ == SKELETON:
                out["skeletons"].append(parse_skeleton(b, at + 16))
                out.setdefault("refs", skeleton_refs(b, at + 16))
            elif typ == MESH:
                out["meshes"].append(parse_mesh(b, at + 16, name))
            elif typ == IMAGE:
                info = A.image_info(b, at, size)
                if info:
                    key = b[at + 17:at + 33].decode("latin-1")             # category and name, 16 chars
                    out["textures"][key] = A.decode(b, info)
            elif typ == MOTION:
                out["motions"].append(parse_motion(b, at + 16, name))
        except Exception as e:                                             # noqa: BLE001
            out["errors"].append(f"{name} ({typ:#x}): {e}")
    return out


# ================================================================================================
# posing: the skeleton's joints in the world, the vertices on them
# ================================================================================================
def quat_matrix(q) -> np.ndarray:
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def joint_world(joints: list[dict]) -> np.ndarray:
    """Each joint's 4x4 transform in the model's space: its parent's, then its own."""
    world = np.zeros((len(joints), 4, 4))
    for i, j in enumerate(joints):
        m = np.eye(4)
        m[:3, :3] = quat_matrix(j["rot"])
        m[:3, 3] = j["trans"]
        world[i] = world[j["parent"]] @ m if j["parent"] >= 0 else m
    return world


def clip(model: dict, prefix: str) -> list[dict]:
    """A motion's parts: FFXI splits one by body region (wlk0 the hips and legs, wlk1 the upper body,
    wlk2 the hands), played together."""
    return [m for m in model["motions"] if m["name"].startswith(prefix)]


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = np.moveaxis(a, -1, 0)
    bx, by, bz, bw = np.moveaxis(b, -1, 0)
    return np.stack([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz], -1)


def local_pose(joints: list[dict], parts: list[dict], phase: float):
    """Each joint's own rotation, offset and scale (n x 4, n x 3, n x 3) at phase 0..1 of a motion:
    its rest pose, the motion's keys interpolated between frames applied after it."""
    n = len(joints)
    rq = np.array([j["rot"] for j in joints], np.float64)
    q, t, s = rq.copy(), np.array([j["trans"] for j in joints], np.float64), np.ones((n, 3))
    for m in parts:
        last = m["frames"] - 1
        pos = min(max(phase, 0.0), 1.0) * last
        lo = min(int(pos), max(last - 1, 0))
        hi, f = min(lo + 1, last), pos - lo
        for j, tr in m["tracks"].items():
            if tr is None or j >= n:
                continue                                                   # held at rest
            aq = tr[0][lo] * (1 - f) + tr[0][hi] * (f if np.dot(tr[0][lo], tr[0][hi]) >= 0 else -f)
            aq /= np.linalg.norm(aq) or 1.0
            q[j] = _qmul(aq, rq[j])
            t[j] += tr[1][lo] * (1 - f) + tr[1][hi] * f
            if j:
                s[j] = tr[2][lo] * (1 - f) + tr[2][hi] * f                 # the root's scale is not used
    t[0] = (-t[0][2], t[0][1], t[0][0])                                    # the root's offset: another axis order
    return q, t, s


def pose_world(joints: list[dict], q: np.ndarray, t: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Each joint's 4x4 transform in the model's space from its local pose: its offset scaled by its
    parent's scale and turned by its rotation, rotations chained, scales multiplied."""
    n = len(joints)
    rot, pos, scl = np.zeros((n, 3, 3)), np.zeros((n, 3)), np.ones((n, 3))
    world = np.tile(np.eye(4), (n, 1, 1))
    for i, j in enumerate(joints):
        r = quat_matrix(q[i])
        p = j["parent"]
        if p < 0:
            rot[i], pos[i], scl[i] = r, t[i], s[i]
        else:
            pos[i] = pos[p] + rot[p] @ (scl[p] * t[i])
            rot[i], scl[i] = rot[p] @ r, scl[p] * s[i]
        world[i, :3, :3] = rot[i] * scl[i]
        world[i, :3, 3] = pos[i]
    return world


def _flip(p: np.ndarray, axis: np.ndarray) -> np.ndarray:
    q = p.copy()
    for a in (1, 2, 3):
        q[axis == a, a - 1] *= -1
    return q


def _skin_inputs(mesh: dict, mirrored: bool, n_joints: int):
    """A mesh's vertices as its joints hold them: p0 on j0, p1 on j1 (-1: one joint); a symmetric
    mesh's second copy from the flipped joints and axes."""
    if mirrored:
        p0, p1 = _flip(mesh["p0"], mesh["fa0"]), _flip(mesh["p1"], mesh["fa1"])
        j0, j1 = mesh["mj0"], mesh["mj1"]
    else:
        p0, p1, j0, j1 = mesh["p0"], mesh["p1"], mesh["j0"], mesh["j1"]
    j0 = np.clip(j0, 0, n_joints - 1)
    j1 = np.where(j1 >= 0, np.clip(j1, 0, n_joints - 1), -1)
    return p0, p1, j0, j1


WEAPON_SLOTS = ("main", "sub", "ranged")     # not what the character stands on (a sword hangs below the soles)


def _normal_inputs(mesh: dict, mirrored: bool, world: np.ndarray):
    """A mesh's normals as its joints hold them (n0 on j0, n1 on j1, as the positions), and whether it
    is lit from both sides. A cloth mesh has none in its file: they're worked out from its faces at rest
    (FFXI's faces wind against their normals; a mirrored copy the other way), and it is two-sided."""
    if not mesh.get("cloth"):
        if mirrored:
            return _flip(mesh["n0"], mesh["fa0"]), _flip(mesh["n1"], mesh["fa1"]), 0.0
        return mesh["n0"], mesh["n1"], 0.0
    p0, p1, j0, j1 = _skin_inputs(mesh, mirrored, len(world))
    v = skin(mesh, world, mirrored)
    acc = np.zeros_like(v)
    for piece in mesh["pieces"]:                                # (a hidden piece's normals don't matter)
        for tri in piece["tris"]:
            idx = [c[0] for c in tri]
            if max(idx) >= len(v):
                continue
            a, b, c = v[idx]
            f = np.cross(b - a, c - a) * (1 if mirrored else -1)
            for i in idx:
                acc[i] += f
    n = acc / np.maximum(np.linalg.norm(acc, axis=1, keepdims=True), 1e-12)
    w = mesh["w"]
    q0 = np.einsum("nji,nj->ni", world[j0, :3, :3], n) * w[:, :1]      # the joints' turns undone
    q1 = np.zeros_like(q0)
    two = j1 >= 0
    q1[two] = np.einsum("nji,nj->ni", world[j1[two], :3, :3], n[two]) * w[two, 1:2]
    q0[~two] = np.einsum("nji,nj->ni", world[j0[~two], :3, :3], n[~two])
    return q0, q1, 1.0


def skin(mesh: dict, world: np.ndarray, mirrored: bool = False) -> np.ndarray:
    """The mesh's vertices in the model's space (one joint: its transform; two: both, pre-weighted)."""
    p0, p1, j0, j1 = _skin_inputs(mesh, mirrored, len(world))
    m0 = world[j0]
    out = np.einsum("nij,nj->ni", m0[:, :3, :3], p0) + m0[:, :3, 3] * mesh["w"][:, :1]
    two = j1 >= 0
    if two.any():
        m1 = world[np.clip(j1[two], 0, len(world) - 1)]
        out[two] += np.einsum("nij,nj->ni", m1[:, :3, :3], p1[two]) + m1[:, :3, 3] * mesh["w"][two, 1:2]
    return out


def triangles(model: dict, skeleton: int = 0, world: np.ndarray | None = None):
    """Every triangle of the model posed (at rest, or by world: pose_world): (3 positions, 3 UVs,
    texture name)."""
    if world is None:
        world = joint_world(model["skeletons"][skeleton])
    occl = occluded(model)
    for mesh in model["meshes"]:
        for mirrored in ((False, True) if mesh["symmetric"] else (False,)):
            v = skin(mesh, world, mirrored)
            for piece in mesh["pieces"]:
                if not shown(piece, occl):
                    continue
                for tri in piece["tris"]:
                    idx = [c[0] for c in tri]
                    if max(idx) >= len(v):
                        continue
                    yield v[idx], np.array([c[1] for c in tri], dtype=float), piece["tex"]


# ================================================================================================
# out: an OBJ, and pictures from a small software renderer
# ================================================================================================
def export_obj(model: dict, out: Path) -> None:
    from PIL import Image
    out.mkdir(parents=True, exist_ok=True)
    mats = {}
    with open(out / "model.obj", "w") as f:
        f.write("mtllib model.mtl\n")
        n = 0
        cur = None
        for pos, uv, tex in triangles(model):
            if tex != cur:
                cur = tex
                mats.setdefault(tex, f"m{len(mats)}")
                f.write(f"usemtl {mats[tex]}\n")
            for p in pos:
                f.write(f"v {p[0]:.4f} {-p[1]:.4f} {p[2]:.4f}\n")
            for t in uv:
                f.write(f"vt {t[0]:.5f} {1 - t[1]:.5f}\n")
            f.write(f"f {n + 1}/{n + 1} {n + 2}/{n + 2} {n + 3}/{n + 3}\n")
            n += 3
    with open(out / "model.mtl", "w") as f:
        for tex, m in mats.items():
            f.write(f"newmtl {m}\n")
            if tex in model["textures"]:
                png = f"{m}.png"
                Image.fromarray(model["textures"][tex], "RGBA").save(out / png)
                f.write(f"map_Kd {png}\n")


def render(model: dict, size: int = 512, yaw: float = 0.0, world: np.ndarray | None = None) -> np.ndarray:
    """A textured, depth-tested, orthographic picture of the model, turned by yaw (degrees)."""
    tris = list(triangles(model, world=world))
    if not tris:
        return np.zeros((size, size, 4), np.uint8)
    c, s = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    allp = np.concatenate([t[0] for t in tris]) @ rot.T
    lo, hi = allp.min(0), allp.max(0)
    scale = 0.9 * size / max(hi[0] - lo[0], hi[1] - lo[1])
    mid = (lo + hi) / 2
    img = np.zeros((size, size, 4), np.float32)
    img[:, :, :3] = (40, 70, 50)
    img[:, :, 3] = 255
    depth = np.full((size, size), np.inf)
    for pos, uv, tex in tris:
        p = (pos @ rot.T - mid) * scale
        sx = p[:, 0] + size / 2
        sy = p[:, 1] + size / 2                    # FFXI's Y points down: down the picture
        x0, x1 = int(max(0, np.floor(sx.min()))), int(min(size - 1, np.ceil(sx.max())))
        y0, y1 = int(max(0, np.floor(sy.min()))), int(min(size - 1, np.ceil(sy.max())))
        if x0 > x1 or y0 > y1:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (sy[1] - sy[2]) * (sx[0] - sx[2]) + (sx[2] - sx[1]) * (sy[0] - sy[2])
        if abs(d) < 1e-9:
            continue
        a = ((sy[1] - sy[2]) * (xs - sx[2]) + (sx[2] - sx[1]) * (ys - sy[2])) / d
        b_ = ((sy[2] - sy[0]) * (xs - sx[2]) + (sx[0] - sx[2]) * (ys - sy[2])) / d
        g = 1 - a - b_
        inside = (a >= 0) & (b_ >= 0) & (g >= 0)
        if not inside.any():
            continue
        z = a * p[0, 2] + b_ * p[1, 2] + g * p[2, 2]
        sub = depth[y0:y1 + 1, x0:x1 + 1]
        draw = inside & (z < sub)
        if not draw.any():
            continue
        t = model["textures"].get(tex)
        if t is not None:
            u = (a * uv[0, 0] + b_ * uv[1, 0] + g * uv[2, 0]) % 1.0
            v = (a * uv[0, 1] + b_ * uv[1, 1] + g * uv[2, 1]) % 1.0
            tx = np.clip((u * t.shape[1]).astype(int), 0, t.shape[1] - 1)
            ty = np.clip((v * t.shape[0]).astype(int), 0, t.shape[0] - 1)
            col = t[ty, tx].astype(np.float32)
            draw &= col[:, :, 3] > 8
        else:
            col = np.full(xs.shape + (4,), (200, 200, 200, 255), np.float32)
        sub[draw] = z[draw]
        img[y0:y1 + 1, x0:x1 + 1][draw] = np.concatenate([col[draw][:, :3], np.full((draw.sum(), 1), 255)], 1)
    return img.astype(np.uint8)


# ================================================================================================
# for the game: the model in the hunter's space, its textures as layers (port_model.py sends them)
# ================================================================================================
MOTIONS = ("idl", "wlk", "run")       # standing, walking, running: the port picks by the hunter's speed
VERT_FLOATS = 20                       # a triangle corner's floats (pack; port_model.py and the port's ModelBlock)

# ================================================================================================
# player characters: a race's skeleton and motions, and its equipment, each slot a file chosen by the
# piece's look (its model number), as FFXI's client finds them
# ================================================================================================
STAND, MOVE, ACTION = 0, 1, 2          # what a motion is for: the port stands or moves by speed; actions on request
PEACE, BATTLE = 0, 1                   # the weapon sheathed, or drawn

# Where a race's files are. gear: per slot, the first file of its looks, look n being file first + n (the
# client's tables, as Atom0s' gear tables give them; the later blocks of newer gear are not used here).
# motions: the race's own (the lower body, the upper, the hands: a motion's parts, by body region);
# weapons: per kind, the battle motions and their hands.
def _gear(face, head, body, hands, legs, feet, main, sub):
    return {"face": face, "head": head, "body": body, "hands": hands, "legs": legs, "feet": feet,
            "main": main, "sub": sub}


# dances: /dance1-4's, the first file's number (dc0 there, dc1 six files on, dc2 and dc3 after them; the waist
# parts, dc02..dc32, a little later); emotes: the race's emotes, the first file's number: six files, their
# waist parts the six after;
# battle: the race's battle motions, (the first's file number as the client writes it, folder * 1000 + file;
# how many; the client's tables, as the community's viewers give them), each file's waist part that + how
# many. Which is a weapon's: the one drawing it as its style does (battle_files)
RACES = {
    "hume_male": {"dances": 191054, "emotes": 32040, "battle": (32013, 9), "label": "Hume (male)", "skeleton": "ROM/27/82.DAT",
                  "motions": ["ROM/27/82.DAT", "ROM/27/83.DAT", "ROM/27/85.DAT"],
                  "gear": _gear(7080, 7112, 7368, 7624, 7880, 8136, 8392, 41199), "faces": 16},
    "hume_female": {"dances": 192002, "emotes": 37013, "battle": (36117, 8), "label": "Hume (female)", "skeleton": "ROM/32/58.DAT",
                    "motions": ["ROM/32/58.DAT", "ROM/32/59.DAT", "ROM/32/61.DAT"],
                    "gear": _gear(10256, 10288, 10544, 10800, 11056, 11312, 11568, 42479), "faces": 16},
    "elvaan_male": {"dances": 192078, "emotes": 41114, "battle": (41084, 10), "label": "Elvaan (male)", "skeleton": "ROM/37/31.DAT",
                    "motions": ["ROM/37/31.DAT", "ROM/37/32.DAT", "ROM/37/34.DAT"],
                    "gear": _gear(13432, 13464, 13720, 13976, 14232, 14488, 14744, 43759), "faces": 16},
    "elvaan_female": {"dances": 193026, "emotes": 46075, "battle": (46057, 6), "label": "Elvaan (female)", "skeleton": "ROM/42/4.DAT",
                      "motions": ["ROM/42/4.DAT", "ROM/42/5.DAT", "ROM/42/7.DAT"],
                      "gear": _gear(16608, 16640, 16896, 17152, 17408, 17664, 17920, 45039), "faces": 16},
    "tarutaru_male": {"dances": 193102, "emotes": 51037, "battle": (51019, 6), "label": "Tarutaru (male)", "skeleton": "ROM/46/93.DAT",
                      "motions": ["ROM/46/93.DAT", "ROM/46/94.DAT", "ROM/46/96.DAT"],
                      "gear": _gear(19784, 19816, 20072, 20328, 20584, 20840, 21096, 46319), "faces": 16},
    "tarutaru_female": {"dances": 193102, "emotes": 51071, "battle": (51019, 6), "label": "Tarutaru (female)", "skeleton": "ROM/46/93.DAT",
                        "motions": ["ROM/46/93.DAT", "ROM/46/94.DAT", "ROM/46/96.DAT"],
                        "gear": _gear(22952, 19816, 20072, 20328, 20584, 20840, 21096, 46319), "faces": 16},
    "mithra": {"dances": 194050, "emotes": 56041, "battle": (56014, 9), "label": "Mithra", "skeleton": "ROM/51/89.DAT",
               "motions": ["ROM/51/89.DAT", "ROM/51/90.DAT", "ROM/51/92.DAT"],
               "gear": _gear(23184, 23216, 23472, 23728, 23984, 24240, 24496, 47599), "faces": 16},
    "galka": {"dances": 195004, "emotes": 61008, "battle": (60112, 8), "label": "Galka", "skeleton": "ROM/56/59.DAT",
              "motions": ["ROM/56/59.DAT", "ROM/56/60.DAT", "ROM/56/62.DAT"],
              "gear": _gear(26360, 26392, 26648, 26904, 27160, 27416, 27672, 48879), "faces": 16},
}
SLOTS = ("face", "head", "body", "hands", "legs", "feet", "main", "sub")
# a weapon's style (its info chunk, 0x45, the byte at +3): the battle motions it's held with
STYLES = {0: "club / staff", 1: "sword", 2: "hand-to-hand", 3: "dagger", 4: "great sword", 5: "axe / scythe",
          6: "katana", 7: "kunai", 8: "polearm"}
# Monster Hunter's weapons, by their motion set (w00_*...: mot_we00 ... mot_we08), as FFXI holds them: a
# style, and a weapon and shield to show (model numbers) when the look's own isn't of that style
MH_WEAPONS = {0: "great_sword", 1: "sword_and_shield", 2: "hammer", 3: "lance", 4: "bowgun", 5: "bowgun",
              6: "bowgun", 7: "long_sword", 8: "switch_axe"}
WEAPON_STYLE = {"great_sword": 4, "sword_and_shield": 1, "hammer": 5, "lance": 8, "bowgun": 0, "long_sword": 6,
                "switch_axe": 5}
WEAPON_LOOK = {"great_sword": (7, None), "sword_and_shield": (4, 24), "hammer": (10, None), "lance": (12, 24),
               "bowgun": (9, None), "long_sword": (13, None), "switch_axe": (11, None)}


def file_number_path(n: int) -> str:
    """A file number as the client writes it (folder * 1000 + file; a file past 127 is the next folder's)."""
    folder, f = divmod(n, 1000)
    folder += f // 128
    return f"ROM/{folder}/{f % 128}.DAT"


# the letter of the motions drawing and sheathing a weapon of each style (in?, ot?): the same in every race's
# files, though the files themselves differ (a male Hume has one per style, a Tarutaru six for them all)
STYLE_DRAW = {0: "d", 1: "b", 2: "a", 3: "c", 4: "e", 5: "f", 6: "g", 7: "i", 8: "k"}


def draw_letters(path: Path) -> set[str]:
    """The letters of the drawing motions in a battle file (its motions' names only: quick)."""
    try:
        b = path.read_bytes()
    except OSError:
        return set()
    return {nm.decode("latin-1")[2] for _at, nm, typ, _size in A.chunks(b)
            if typ == MOTION and nm[:2] == b"in" and chr(nm[2]).isalpha()}


def battle_files(race: str, style: int, root: Path | None = None) -> tuple[str, str] | None:
    """The battle motions (the lower and upper body) and their waist part for a weapon of that style:
    the race's file drawing it, the one drawing nothing else first; without root, the style's place."""
    first, num = RACES[race]["battle"]
    if root is None:
        return (file_number_path(first + style), file_number_path(first + num + style)) if 0 <= style < num else None
    want = STYLE_DRAW.get(style, "b")
    best = None
    for i in range(num):
        letters = draw_letters(root / file_number_path(first + i))
        if want in letters and (best is None or len(letters) < best[1]):
            best = (i, len(letters))
    if best is None:
        return None
    return file_number_path(first + best[0]), file_number_path(first + num + best[0])


def weapon_style(root: Path, race: str, main: int | None, file_path=None) -> int | None:
    """A main-hand weapon's style: its file's info chunk."""
    import ffxi_sound as S
    if main is None:
        return None
    p = (file_path or S.file_path)(root, RACES[race]["gear"]["main"] + int(main))
    if not p or not p.exists():
        return None
    b = p.read_bytes()
    for at, _nm, typ, size in A.chunks(b):
        if typ == 0x45 and size >= 20 and b[at + 19] != 0xFF:
            return b[at + 19]
    return None


def look_for_weapon(root: Path, race: str, look: dict, weapon: str) -> tuple[dict, int]:
    """The weapons held, and the style of the battle motions they're held with: FFXI's own worn (the main
    hand's style, the off hand as it is: a club in each), when the race has motions for that style; else
    one held as FFXI would hold Monster Hunter's weapon (its style's own, and a shield for a sword or a
    lance)."""
    own = weapon_style(root, race, look.get("main")) if look.get("main") else None
    if own is not None and battle_files(race, own, root) is not None:
        return dict(look), own
    style = WEAPON_STYLE.get(weapon, 1)
    out = dict(look)
    if battle_files(race, style, root) is None:                       # the race has no motions for it: a sword
        weapon, style = "sword_and_shield", 1
    if weapon_style(root, race, look.get("main")) != style:
        main, sub = WEAPON_LOOK.get(weapon, (4, 24))
        out["main"], out["sub"] = main, sub
    return out, style
# a look to start from: a Red Mage's colours (the red tabard), a rapier and a kite shield
DEFAULT_LOOK = {"face": 0, "head": 0, "body": 38, "hands": 38, "legs": 38, "feet": 38, "main": 4, "sub": 24}
HUME_MALE_HEIGHT = 1.80                # a male Hume, in FFXI's units: character_height is his height
# magic's motions by school: start, chant (looped), release
CAST_MOTIONS = {"white": "mw", "black": "mb", "ninjutsu": "mn", "summoning": "ms", "song": "mi",
                "blue": "mb", "geomancy": "mw", "trust": "mw"}


# FFXI's emotes: the chat line's command -> (the motion, what the log says, held until moving). The motions
# are the race's emote files' (bow0, bow1, bow2: the lower body, the upper, the waist); /heal kneels to rest
# (the race's own rx0, rx1 looped).
# the chat line's emotes: the motion, the log's words, and whether it's held (True: the motion looped;
# a name: that motion looped after it)
EMOTES = {
    "bow": ("bow", "{me} bows courteously.", False), "kneel": ("kne", "{me} kneels.", False),
    "laugh": ("lau", "{me} laughs heartily.", False), "point": ("poi", "{me} points.", False),
    "salute": ("sl1", "{me} salutes.", False), "cry": ("wee", "{me} cries.", False),
    "cheer": ("che", "{me} cheers!", False), "clap": ("clp", "{me} claps.", False),
    "no": ("den", "{me} shakes {their} head.", False), "stare": ("gla", "{me} stares.", False),
    "nod": ("nod", "{me} nods.", False), "yes": ("nod", "{me} nods.", False),
    "wave": ("wav", "{me} waves.", False), "welcome": ("wel", "{me} welcomes everyone.", False),
    "comfort": ("cmf", "{me} offers comfort.", False), "poke": ("pok", "{me} pokes.", False),
    "sigh": ("sig", "{me} sighs.", False), "stagger": ("sta", "{me} staggers.", False),
    "surprised": ("sur", "{me} is surprised.", False), "angry": ("ang", "{me} is angry.", False),
    "blush": ("blu", "{me} blushes.", False), "panic": ("pan", "{me} panics.", False),
    "upset": ("ups", "{me} is upset.", False), "doubt": ("why", "{me} looks doubtful.", False),
    "fume": ("fum", "{me} fumes.", False), "think": ("thk", "{me} thinks.", False),
    "praise": ("gut", "{me} gives a thumbs up.", False), "sit": ("si0", "{me} sits down.", "si1"),
    "heal": ("rx0", "{me} starts resting.", "rx1"),
    "dance1": ("dc0", "{me} dances.", False), "dance2": ("dc1", "{me} dances.", False),
    "dance3": ("dc2", "{me} dances.", False), "dance4": ("dc3", "{me} dances.", False),
    "dance": ("dc0", "{me} dances.", False),
}


def dance_files(root: Path, race: str) -> list[str]:
    """The race's dance files: dc0, dc1, dc2, dc3's lower and upper body, then their waist parts (the first
    files holding dc02...dc32 after them)."""
    first = RACES[race].get("dances")
    if not first:
        return []
    out = [file_number_path(first + i) for i in (0, 6, 12, 13)]
    want = {"dc02", "dc12", "dc22", "dc32"}
    for n in range(first + 14, first + 90):
        p = root / file_number_path(n)
        if not want or not p.exists():
            continue
        names = {nm.decode("latin-1") for _at, nm, typ, _size in A.chunks(p.read_bytes()) if typ == MOTION}
        if names & want:
            out.append(file_number_path(n))
            want -= names
    return out


def emote_clips(own: list[list[dict]], emote_sources: list[list[dict]]) -> list[dict]:
    """The emotes' motions: each from its parts in the emote files (the race's own files for /heal's), and
    what a held one loops after it (/sit: si0 sitting down, si1 seated; /heal: rx0, rx1)."""
    def pick(sources, name):
        out, seen = [], set()
        for src in sources:
            for m in src:
                if m["name"].startswith(name) and len(m["name"]) == len(name) + 1 and m["name"] not in seen:
                    seen.add(m["name"])
                    out.append(m)
        return out
    clips, done = [], set()
    for code, _msg, hold in EMOTES.values():
        for c in (code, hold) if isinstance(hold, str) else (code,):
            if c in done:
                continue
            done.add(c)
            parts = pick(own if c.startswith("rx") else emote_sources + own, c)     # (sitting: the race's own)
            if parts:
                clips.append({"name": c, "parts": parts, "kind": ACTION, "set": PEACE})
    return clips


def ws_clips(root: Path, race: str, animations: dict, file_path=None) -> tuple[list[dict], dict]:
    """The race's own motions for weapon skills (animations: a skill's name -> its animation number): each
    skill's file (ffxi_fx.WS_BASE + the number - 1) holds them, its `main` routine playing them on the
    character. Clips named ws<n><motion> (battle actions), and each skill's plan: (the seconds after it
    begins each is asked for, the clip) and when its blow lands."""
    import hashlib
    import pickle
    import ffxi_fx as FX
    import ffxi_sound as S
    file_path = file_path or S.file_path
    base = FX.WS_BASE.get(race)
    clips, plans, same = [], {}, {}
    if base is None:
        return clips, plans
    for i, (name, anim) in enumerate(sorted(animations.items())):
        p = file_path(root, base + int(anim) - 1)
        if not p or not p.exists():
            continue
        try:
            b = p.read_bytes()
            motions = load(p)["motions"]
        except (OSError, ValueError):
            continue
        order, lands = FX.motion_schedule(b)
        steps = []
        for tick, m3, _dur in order:
            parts = [m for m in motions if m["name"][:3] == m3 and len(m["name"]) == 4]
            if not parts:
                continue
            # many skills share a motion (a sword's b00, b02): one clip for all of them (the port holds 96)
            key = hashlib.sha1(pickle.dumps([(m["name"], m["frames"], m["rate"], m["tracks"]) for m in parts])).digest()
            label = same.get(key)
            if label is None:
                label = same[key] = f"ws{i:02d}{m3}"
                clips.append({"name": label, "parts": parts, "kind": ACTION, "set": BATTLE})
            steps.append((tick / 60.0, label))
        if steps:
            plans[name] = {"steps": steps, "lands": (lands / 60.0) if lands is not None else None}
    return clips, plans


def load_character(root: Path, race: str, look: dict, style: int | None = None, file_path=None,
                   loader=None, motions: bool = True) -> dict:
    """A player character: the race's skeleton, its equipment's meshes and textures (look: slot ->
    model number), and its motions as pack's clips (character_clips; motions False: none), its battle
    motions those of a weapon of that style (None: its main hand's). loader: load, or one that keeps
    what it read (character.py's previews)."""
    import ffxi_sound as S
    file_path = file_path or S.file_path
    load_ = loader or load
    r = RACES[race]
    base = load_(root / r["skeleton"])
    model = {"skeletons": base["skeletons"], "meshes": [], "textures": {}, "motions": [], "errors": [],
             "grips": []}
    refs = base.get("refs") or []
    for slot in SLOTS:
        if slot not in look or look[slot] is None:
            continue
        p = file_path(root, r["gear"][slot] + int(look[slot]))
        if not p or not p.exists():
            model["errors"].append(f"{slot} {look[slot]}: no file")
            continue
        part = load_(p)
        model["meshes"] += [{**m, "slot": slot} for m in part["meshes"]]     # pack keeps the weapons off the ground
        if slot in ("main", "sub"):                                   # a weapon's grip, to its hand when fighting
            g, hand = weapon_grip(p), RIGHT_HAND if slot == "main" else LEFT_HAND
            if g is not None and g < len(refs) and hand < len(refs):
                model["grips"].append((refs[g], refs[hand]))
        model["textures"].update(part["textures"])
        model["errors"] += part["errors"]
    if not motions:
        return model
    own = [load_(root / f)["motions"] for f in r["motions"]]
    if style is None:
        style = weapon_style(root, race, look.get("main"), file_path)
    files = battle_files(race, style if style is not None else 1, root) or battle_files(race, 1, root)
    arms = [load_(root / f)["motions"] for f in files] if files else []
    draws = sorted({m["name"][2] for m in (arms[0] if arms else []) if m["name"][:2] == "in" and m["name"][2].isalpha()})
    model["clips"] = character_clips(own, arms, draws[0] if draws else "")
    first = r.get("emotes")
    if first:
        files = [file_number_path(first + i) for i in list(range(6)) + list(range(6, 12))]
        emotes = [load_(root / f)["motions"] for f in files if (root / f).exists()]
        emotes += [load_(root / f)["motions"] for f in dance_files(root, race)]
        model["clips"] += emote_clips(own, emotes)
    model["style"] = style
    return model


def character_clips(own: list[list[dict]], arms: list[list[dict]], letter: str = "") -> list[dict]:
    """The motions a character plays, each from its parts (lower body, upper body, hands): own, the
    race's files in that order; arms, its weapon's battle file and that file's hands; letter, the
    weapon's in its drawing and sheathing (inc/otc: a sword)."""
    def pick(sources, name):
        out, seen = [], set()
        for src in sources:                                   # the first file holding a part wins
            for m in src:
                if m["name"].startswith(name) and len(m["name"]) == len(name) + 1 and m["name"] not in seen:
                    seen.add(m["name"])
                    out.append(m)
        return out

    def clip_of(name, kind, mode, sources, label=None, hold=0.0, then=None):
        """hold: its first part lasts at least that many seconds (a pose held); then: a motion played
        after it, in the same clip (knocked back, then getting up)."""
        parts = pick(sources, name)
        if not parts:
            return None
        spec = {"name": label or name, "parts": parts, "kind": kind, "set": mode, "hold": hold}
        if then:
            spec["then"] = pick(sources, then)
        return spec

    battle = arms + own                                       # the weapon's upper body over the race's legs
    clips = [clip_of("idl", STAND, PEACE, own), clip_of("wlk", MOVE, PEACE, own), clip_of("run", MOVE, PEACE, own),
             clip_of("btl", STAND, BATTLE, battle), clip_of("wlk", MOVE, BATTLE, battle, "bwlk"),
             clip_of("run", MOVE, BATTLE, battle, "brun")]
    for n in ("at0", "at1", "at2"):                           # attacks
        clips.append(clip_of(n, ACTION, BATTLE, battle))
    if letter:                                                # drawing and sheathing the weapon
        clips.append(clip_of("in" + letter, ACTION, BATTLE, battle, "draw"))
        clips.append(clip_of("ot" + letter, ACTION, BATTLE, battle, "sheathe"))
    for school in sorted(set(CAST_MOTIONS.values())):         # magic: start, chant, release
        for i in range(3):
            clips.append(clip_of(f"{school}{i}", ACTION, PEACE, own))
    # hurt: a flinch held a moment; knocked back, then up again; falling dead, then lying there (looped).
    # Each in peace and in battle (the weapon in hand or away), the port picking by the hunter's
    for prefix, mode, src in (("", PEACE, own), ("b", BATTLE, battle)):
        clips.append(clip_of("dfm", ACTION, mode, src, prefix + "hit", hold=0.3))
        clips.append(clip_of("bb0", ACTION, mode, src, prefix + "knock", hold=0.45, then="bb1"))
        clips.append(clip_of("ded", ACTION, mode, src, prefix + "ded"))
        clips.append(clip_of("cor", ACTION, mode, src, prefix + "cor"))
    return [c for c in clips if c]


def pack(model: dict, height: float = 175.0, yaw: float = 0.0, mirror: bool = True, size: int = 256,
         motions: tuple[str, ...] = MOTIONS, scale: float | None = None, origin_ground: bool = False) -> dict:
    """The model for the port to skin and animate (port_model.py):
      verts   N x 20 float32, each triangle corner: p0 xyz, p1 xyz (in its joints' spaces, weighted),
              joint 0, joint 1 (-1: one joint), weight 0, weight 1, u, v, texture layer, its normal
              n0 xyz, n1 xyz (as the positions: each joint turns its own), and 1 if it is lit from both
              sides (cloth), else 0
      layers  L x size x size x 4 uint8, the textures
      place   3 x 4, FFXI's model space to Monster Hunter's convention for the hunter's own: Y up, the
              soles at 0 (the weapons not counted), `height` tall, facing +Z (FFXI's models face +X with Y down), turned by yaw degrees.
              mirror: FFXI's space is left-handed, the GX's right-handed, so one axis flips
      parents each joint's (-1: the root)
      clips   per motion found: name, frames, rate (keyframes per game frame, 30 a second), speed
              (the ground speed a moving one was made for, units a second), kind and set (STAND, MOVE
              or ACTION; PEACE or BATTLE), data (frames x joints x 10: the joint's own rotation x y z
              w, offset x y z, scale x y z; an action's joints it doesn't move: NaN, the port keeping
              those as standing or moving has them)
    The motions: a character's own (load_character's clips), else the first of `motions` standing
    and the others moving. scale: the hunter's units per the model's, else the model made `height`
    tall (a race's size against another's is kept by giving them the same scale). origin_ground: its
    origin on the ground, as FFXI stands an actor (an NPC's motions carry its feet there, or keep it
    floating, a Moogle), rather than its rest pose's soles.
    """
    from PIL import Image
    joints = model["skeletons"][0]
    rest = joint_world(joints)
    rows, names, body = [], {}, []
    occl = occluded(model)                                             # what the worn pieces hide
    for mesh in model["meshes"]:
        weapon = mesh.get("slot") in WEAPON_SLOTS
        for mirrored in ((False, True) if mesh["symmetric"] else (False,)):
            p0, p1, j0, j1 = _skin_inputs(mesh, mirrored, len(joints))
            n0, n1, sides = _normal_inputs(mesh, mirrored, rest)
            for piece in mesh["pieces"]:
                if piece["tex"] not in model["textures"] or not shown(piece, occl):
                    continue
                lay = names.setdefault(piece["tex"], len(names))
                for tri in piece["tris"]:
                    if max(c[0] for c in tri) >= len(p0):
                        continue
                    for i, uv in tri:
                        rows.append((*p0[i], *p1[i], j0[i], j1[i], *mesh["w"][i], uv[0], uv[1], lay,
                                     *n0[i], *n1[i], sides))
                        body.append(not weapon)
    verts = np.array(rows, np.float32).reshape(-1, VERT_FLOATS)

    corners = skin_corners(verts, rest)                               # height and ground at rest
    axes = np.array([[0, 0, -1 if mirror else 1], [0, -1, 0], [1, 0, 0]], np.float64)   # forward +Z, up +Y
    up = corners @ axes.T
    body = np.array(body, bool)
    stands = up[body] if body.any() else up                           # the soles, not a hanging sword's tip
    lo, hi = stands[:, 1].min(), stands[:, 1].max()
    k = scale if scale else height / (hi - lo)
    c, s_ = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    turn = np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])
    place = np.zeros((3, 4))
    place[:, :3] = turn @ (k * axes)
    place[1, 3] = 0.0 if origin_ground else -lo * k                    # the feet on the ground

    clips = []
    specs = model.get("clips") or [{"name": n, "parts": clip(model, n), "kind": STAND if i == 0 else MOVE,
                                    "set": PEACE} for i, n in enumerate(motions)]
    for spec in specs:
        name, parts = spec["name"], spec["parts"]
        if not parts:
            continue
        frames, rate = max(m["frames"] for m in parts), parts[0]["rate"] or 1.0
        then = spec.get("then") or []
        if then:                                                       # played at the second's rate
            rate = then[0]["rate"] or 1.0
        frames = max(frames, int(round(spec.get("hold", 0.0) * 30 * rate)) + 1)

        grips = model.get("grips") if spec["set"] == BATTLE and name not in ("draw", "sheathe") else None

        def sampled(ps, n):
            out = []
            for f in range(n):
                q, t, s = local_pose(joints, ps, f / max(n - 1, 1))
                grip_pose(joints, q, t, s, grips)                      # fighting: the weapons in hand
                out.append(np.concatenate([q, t, s], 1))
            return np.stack(out)
        data = sampled(parts, frames)
        if then:
            more = max(m["frames"] for m in then)
            data = np.concatenate([data, sampled(then, more)])
            frames += more
        data = data.astype(np.float32)
        if spec["kind"] == ACTION:                                     # an action moves only its own joints:
            driven = {j for m in parts + then for j in m["tracks"]}    # the others stay as the hunter moves
            driven |= {g for g, _ in (grips or [])}
            data[:, [j for j in range(len(joints)) if j not in driven]] = np.nan
        for f in range(1, frames):                                     # q and -q turn alike: keep each on
            flip = (data[f, :, :4] * data[f - 1, :, :4]).sum(1) < 0    # the previous frame's side, so
            data[f, flip, :4] *= -1                                    # frames blend the short way
        clips.append({"name": name, "frames": frames, "rate": rate, "data": data, "kind": spec["kind"],
                      "set": spec["set"],
                      "speed": ground_speed(joints, verts, data, rate, place) if spec["kind"] == MOVE else 0.0})
    if not clips:                                                      # no motion: the rest pose, held
        q = np.array([j["rot"] for j in joints])
        t = np.array([j["trans"] for j in joints])
        data = np.concatenate([q, t, np.ones((len(joints), 3))], 1)[None].astype(np.float32)
        clips.append({"name": "rest", "frames": 1, "rate": 1.0, "speed": 0.0, "data": data, "kind": STAND,
                      "set": PEACE})

    order = sorted(names, key=names.get)
    layers = np.stack([np.array(Image.fromarray(ps2_alpha(model["textures"][n]), "RGBA").resize((size, size), Image.LANCZOS))
                       for n in order]) if order else np.zeros((1, size, size, 4), np.uint8)
    return {"verts": verts, "layers": layers, "place": place.astype(np.float32),
            "parents": [j["parent"] for j in joints], "clips": clips}


SWIM = 2                                 # a third set (the port's kSwim): in the water, which FFXI's people never are


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Quaternions (x y z w, the motions' own) a after b, b's rows each."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz], -1)


def swim_clips(packed: dict, joints: list[dict], points: list | None = None, pitch: float = 80.0) -> None:
    """Swimming, which FFXI has none of (2026-10-12: Monster Hunter's hunter swims; the character ran upright
    through the sea): two clips added to pack's, set SWIM. FFXI's people never swim, so the strokes are made
    here on the skeleton's own limbs (_limbs: the hands its points 126/127 name, the feet its lowest joints):
    the crawl, each arm a whole turn about its shoulder half a stroke from the other, the elbow high as it comes
    back, a six-beat flutter kick, the body rolling a little, laid forward `pitch` degrees about its hips at
    its feet's height (the hunter's place in the water; the game's own tilt as it dives or climbs comes on
    top); treading water, upright, sunk to the shoulders, the arms out sculling to and fro, the legs pedalling.
    A skeleton without such limbs (a Moogle) gets its run laid flat and its idle sunk instead. The model's
    space is FFXI's: it faces +X, Y down, Z its side."""
    clips = packed["clips"]
    run = next((c for c in clips if c["name"] == "run" and c["set"] == PEACE), None) or \
        next((c for c in clips if c["kind"] == MOVE and c["set"] == PEACE), None)
    idle = next((c for c in clips if c["kind"] == STAND and c["set"] == PEACE), None)
    if idle is None or not len(joints):
        return
    d0 = np.asarray(idle["data"][0], np.float64)
    q0 = d0[:, :4] / np.maximum(np.linalg.norm(d0[:, :4], axis=1, keepdims=True), 1e-9)
    at = np.array([m[:3, 3] for m in pose_world(joints, q0, d0[:, 4:7], d0[:, 7:10])])
    height = max(float(-at[:, 1].min()), 0.3)                          # its top, above its feet (Y down)
    hips = at[1] if len(at) > 1 and 0.3 * height < -at[1, 1] < 0.75 * height else np.array([0.0, -0.5 * height, 0.0])

    def lay(data: np.ndarray, degrees, centre_y: float, roll=None, bob: float = 0.0) -> np.ndarray:
        """The root turned forward about the hips (degrees: one for all, or one a frame; and rolled about its
        length), the hips set at centre_y."""
        target = np.array([hips[0], centre_y, hips[2]])
        n = len(data)
        for f in range(n):
            a = np.radians(degrees[f] if np.ndim(degrees) else degrees) / 2
            qp = np.array([0.0, 0.0, np.sin(a), np.cos(a)])           # about Z: the head (-Y) toward +X
            q = data[f, 0, :4].astype(np.float64)
            t = data[f, 0, 4:7].astype(np.float64) - hips
            if roll is not None:                                       # about its length (Y, upright)
                r = np.radians(roll[f]) / 2
                qr = np.array([0.0, np.sin(r), 0.0, np.cos(r)])
                q, t = _qmul(qr, q), quat_matrix(qr) @ t
            data[f, 0, :4] = _qmul(qp, q)
            t = quat_matrix(qp) @ t + target
            t[1] += bob * height * np.sin(2 * np.pi * f / max(n - 1, 1))
            data[f, 0, 4:7] = t
        return data

    limbs = _limbs(joints, at, points)
    if limbs is None:                                                  # (no arms and legs to swim with)
        if run is not None:
            clips.append({"name": "swim", "frames": run["frames"], "rate": run["rate"] * 0.6, "kind": MOVE,
                          "set": SWIM, "data": lay(np.array(run["data"], np.float32), pitch, 0.0), "speed": 250.0})
        clips.append({"name": "swid", "frames": idle["frames"], "rate": idle["rate"], "kind": STAND, "set": SWIM,
                      "data": lay(np.array(idle["data"], np.float32), 10.0, hips[1] + 0.75 * height, bob=0.03),
                      "speed": 0.0})
        return

    def rot(axis: str, degrees: float) -> np.ndarray:
        a = np.radians(degrees) / 2
        v = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}[axis]
        return np.array([v[0] * np.sin(a), v[1] * np.sin(a), v[2] * np.sin(a), np.cos(a)])

    base = _neutral(joints, np.asarray(idle["data"][0], np.float64), limbs)

    def frames(n: int, turns) -> np.ndarray:
        """n frames of the neutral pose (the idle's, its arms and legs hanging straight: _neutral), each joint
        `turns(f)` names (joint: a turn in the model's space, about the joint) turned so, its children with it."""
        out = np.repeat(base.astype(np.float32)[None], n, 0)
        for f in range(n):
            want = turns(f)
            qw = np.zeros((len(joints), 4))
            for j, jj in enumerate(joints):
                p = jj["parent"]
                local = out[f, j, :4].astype(np.float64)
                local /= max(np.linalg.norm(local), 1e-9)
                world = _qmul(qw[p], local) if p >= 0 else local
                if j in want:
                    world = _qmul(want[j], world)
                    local = _qmul(_qinv(qw[p]), world) if p >= 0 else world
                    out[f, j, :4] = local
                qw[j] = world
        for f in range(1, n):                                          # q and -q alike: the short way round
            flip = (out[f, :, :4] * out[f - 1, :, :4]).sum(1) < 0
            out[f, flip, :4] *= -1
        return out

    q_n = base[:, :4] / np.maximum(np.linalg.norm(base[:, :4], axis=1, keepdims=True), 1e-9)
    at_n = np.array([mm[:3, 3] for mm in pose_world(joints, q_n, base[:, 4:7], base[:, 7:10])])

    def path(keys: list, u: float) -> np.ndarray:
        """A closed smooth path through keys [(u, (a, b, c))...], u from 0 to 1."""
        us = [k[0] for k in keys]
        ps = [np.asarray(k[1], np.float64) for k in keys]
        m_ = len(keys)
        u %= 1.0
        i = max(k for k in range(m_) if us[k] <= u)
        j = (i + 1) % m_
        span = (us[j] if j else 1.0) - us[i]
        t = (u - us[i]) / span if span > 0 else 0.0
        p0, p1, p2, p3 = ps[(i - 1) % m_], ps[i], ps[j], ps[(j + 1) % m_]
        return 0.5 * (2 * p1 + (p2 - p0) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                      + (3 * p1 - p0 - 3 * p2 + p3) * t ** 3)

    def reach(want: dict, top: int, mid: int, end: int, goal: np.ndarray, pole: np.ndarray) -> None:
        """Turns for top and mid (into want) so end, below them, comes to top's place + goal (the model's space),
        mid bending toward pole."""
        s0, e0, w0 = at_n[top], at_n[mid], at_n[end]
        l1, l2 = np.linalg.norm(e0 - s0), np.linalg.norm(w0 - e0)
        d = float(np.clip(np.linalg.norm(goal), 0.05 * (l1 + l2), 0.995 * (l1 + l2)))
        way = goal / max(np.linalg.norm(goal), 1e-9)
        side_ = pole - np.dot(pole, way) * way
        side_ /= max(np.linalg.norm(side_), 1e-9)
        cos_a = np.clip((l1 * l1 + d * d - l2 * l2) / (2 * l1 * d), -1.0, 1.0)
        elbow = s0 + l1 * (cos_a * way + np.sqrt(1 - cos_a * cos_a) * side_)
        q_top = _arc(e0 - s0, elbow - s0)
        want[top] = q_top
        want[mid] = _arc(quat_matrix(q_top) @ (w0 - e0), s0 + way * d - elbow)

    # The strokes as Monster Hunter's hunter swims in Tri and 3 Ultimate (2026-10-13, a video of its underwater
    # fighting: upright in the water, leaning into the way it goes, its legs kicking below, never lying flat),
    # moving as FFXI's people do, smooth and unhurried, nothing locked straight. Each hand and foot follows a
    # smooth closed path (periodic Catmull-Rom through a few places) and its shoulder and elbow, hip and knee
    # turn to reach it (two-bone IK from the neutral pose). The places are in the upright frame, as fractions
    # of the limb's length from the shoulder or the hip: (ahead +X, down +Y, out).
    neck = _neck(joints, at_n, limbs)

    def goal(ahead: float, down: float, out: float, side: float, length: float) -> np.ndarray:
        return length * np.array([ahead, down, out * side])

    def arm_len(sh: int, el: int, wr: int) -> float:
        return float(np.linalg.norm(at_n[el] - at_n[sh]) + np.linalg.norm(at_n[wr] - at_n[el]))

    # swimming fast, as the hunter swims (2026-10-13, the game's own hunter watched swimming side on): flat out, the
    # head up a little; a smooth crawl, each hand reaching far ahead, pulled down under the body to the hip, brought
    # back over the back with the elbow high, half a stroke from the other; a flutter kick, the knees bending as
    # the heels come up; the body rolling a little. 55 frames a stroke (1.8 s at its pace). The places in the
    # swimmer's own terms: (ahead, out, below its belly) for the hands, (back, out, below) for the feet.
    def swimmer(ahead: float, out: float, below: float, side: float, length: float) -> np.ndarray:
        return length * np.array([below, -ahead, out * side])          # the upright frame: front +X, up -Y

    def kicker(back: float, out: float, below: float, side: float, length: float) -> np.ndarray:
        return length * np.array([below, back, out * side])

    nf = 55
    # (2026-10-13: "his arms are rolling into a ball": the hand never nearer the shoulder than 0.77 of the arm,
    # so the arm stays long; the reach a little out, to show beside a Tarutaru's head; the recovery wide)
    crawl_hand = [(0.00, (0.95, 0.20, 0.08)), (0.18, (0.78, 0.26, 0.45)), (0.38, (0.30, 0.30, 0.78)),
                  (0.54, (-0.62, 0.28, 0.40)), (0.68, (-0.55, 0.45, -0.30)), (0.84, (0.40, 0.55, -0.50))]

    def crawl(f: int) -> dict:
        u = f / (nf - 1)
        want = {}
        for k, (sh, el, side, wr) in enumerate(limbs["arms"]):
            a, b, c = path(crawl_hand, u + 0.5 * k)
            reach(want, sh, el, wr, swimmer(a, b, c, side, arm_len(sh, el, wr)),
                  swimmer(0.0, 0.6, -0.8, side, 1.0))                  # the elbow up over the back, and out
        for k, (hp, kn, side, an) in enumerate(limbs["legs"]):
            w = 3 * 2 * np.pi * u + np.pi * k
            up = 0.5 + 0.5 * np.sin(w)                                 # 1: the heel up
            reach(want, hp, kn, an, kicker(0.93 - 0.14 * up, 0.07, 0.10 - 0.26 * up, side, arm_len(hp, kn, an)),
                  kicker(0.0, 0.1, 1.0, side, 1.0))                    # the knee bending toward the sea floor
        for j, share in neck:
            want[j] = rot("z", -22.0 * share)                          # looking ahead
        return want

    roll = [9.0 * np.sin(2 * np.pi * f / (nf - 1)) for f in range(nf)]
    clips.append({"name": "swim", "frames": nf, "rate": 1.0, "kind": MOVE, "set": SWIM,
                  "data": lay(frames(nf, crawl), 84.0, 0.0, roll=roll), "speed": 300.0})

    # swimming slowly: 61 frames a stroke (2 s at its pace), leaning 45 degrees into it (the hunter between flat
    # out and stopped). The hands reach ahead, sweep out
    # and round, draw back past the hips and come in close to the chest to reach ahead again; the legs a steady
    # scissor kick, two beats a stroke, the knees soft.
    n = 61
    hands = [(0.00, (0.85, 0.28, 0.20)), (0.24, (0.60, 0.40, 0.55)), (0.48, (0.25, 0.78, 0.40)),
             (0.68, (0.45, 0.50, 0.28)), (0.86, (0.80, 0.30, 0.18))]

    def swim(f: int) -> dict:
        u = f / (n - 1)
        want = {}
        for sh, el, side, wr in limbs["arms"]:
            a, b, c = path(hands, u)
            reach(want, sh, el, wr, goal(a, b, c, side, arm_len(sh, el, wr)), np.array([-0.5, 0.2, 1.0 * side]))
        for k, (hp, kn, side, an) in enumerate(limbs["legs"]):
            w = 2 * 2 * np.pi * u + np.pi * k
            reach(want, hp, kn, an, goal(0.02 + 0.16 * np.sin(w), 0.93 - 0.03 * (1 - np.cos(w)), 0.08, side,
                                         arm_len(hp, kn, an)), np.array([1.0, 0.0, 0.2 * side]))   # the knees ahead
        for j, share in neck:                                          # looking where it goes
            want[j] = rot("z", -14.0 * share)
        return want

    lean = [45.0 + 3.0 * np.cos(2 * np.pi * f / (n - 1)) for f in range(n)]
    clips.append({"name": "swsl", "frames": n, "rate": 1.0, "kind": MOVE, "set": SWIM,
                  "data": lay(frames(n, swim), lean, hips[1] + 0.5 * height), "speed": 120.0})

    # treading water: 61 frames (2 s), upright: the hands sculling in small loops at the waist, the elbows soft;
    # the legs a slow scissor kick, small.
    m = 61

    def tread(f: int) -> dict:
        u = 2 * np.pi * f / (m - 1)
        want = {}
        for sh, el, side, wr in limbs["arms"]:
            reach(want, sh, el, wr, goal(0.32 + 0.07 * np.sin(u), 0.74, 0.36 + 0.05 * np.sin(2 * u), side,
                                         arm_len(sh, el, wr)), np.array([-0.6, 0.2, 0.8 * side]))   # elbows back, out
        for k, (hp, kn, side, an) in enumerate(limbs["legs"]):
            w = u + np.pi * k
            reach(want, hp, kn, an, goal(0.03 + 0.10 * np.sin(w), 0.93 - 0.02 * (1 - np.cos(w)), 0.09, side,
                                         arm_len(hp, kn, an)), np.array([1.0, 0.0, 0.25 * side]))
        return want

    clips.append({"name": "swid", "frames": m, "rate": 1.0, "kind": STAND, "set": SWIM,
                  "data": lay(frames(m, tread), 4.0, hips[1] + 0.75 * height, bob=0.015), "speed": 0.0})

    # its swings in the water (2026-10-13: attacking while swimming, it stood up to swing): each "at0".."at2" a
    # twin "~at0".. the port plays in its place while the hunter is in the water: the swing's upper body as on
    # land, upright in the water as Monster Hunter's hunter fights there (leaning 10 degrees), the legs left to the
    # stroke or the treading beneath (not driven). The port's clips are 96 at most.
    legs = set()
    for hip, _knee, _side, _ankle in limbs["legs"]:
        todo = [hip]
        while todo:
            j = todo.pop()
            legs.add(j)
            todo.extend(k for k, kk in enumerate(joints) if kk["parent"] == j)
    for c in [c for c in clips if c["kind"] == ACTION and c["name"] in ("at0", "at1", "at2")]:
        if len(clips) >= 96:
            break
        data = lay(np.array(c["data"], np.float32), 10.0, hips[1] + 0.75 * height)   # upright, as the tread
        data[:, sorted(legs)] = np.nan
        clips.append({"name": "~" + c["name"], "frames": c["frames"], "rate": c["rate"], "kind": ACTION,
                      "set": c["set"], "data": data, "speed": 0.0})


def _neck(joints: list[dict], at: np.ndarray, limbs: dict) -> list[tuple[int, float]]:
    """The joints from the chest up to the head (the chain to its top, under the chest where the arms meet), with
    each one's share of a nod: a swimmer lying flat lifts its head with its whole neck, not one joint."""
    def chain(j: int) -> list[int]:
        out = []
        while 0 <= j < len(joints) and len(out) < 64:
            out.append(j)
            j = joints[j]["parent"]
        return out
    shoulders = [sh for sh, _el, _s, _w in limbs["arms"]]
    arms = [set(chain(sh)) for sh in shoulders]
    chest = next((j for j in chain(shoulders[0]) if j in arms[1]), None)
    if chest is None:
        return []
    top = min(range(len(joints)), key=lambda j: at[j, 1])               # the head's top (Y down)
    c = chain(top)
    if chest not in c:
        return []
    up = [j for j in c[1:c.index(chest)] if not any(j in a for a in arms)][::-1][:3]   # the neck, then the head
    return [(j, 1.0 / len(up)) for j in up] if up else []


def _qinv(q: np.ndarray) -> np.ndarray:
    return np.array([-q[0], -q[1], -q[2], q[3]])


def _arc(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """The least turn (a quaternion, x y z w) taking direction u to direction v."""
    u = u / max(np.linalg.norm(u), 1e-9)
    v = v / max(np.linalg.norm(v), 1e-9)
    d = float(np.dot(u, v))
    if d < -0.9999:                                                    # opposite: about any axis across them
        axis = np.cross(u, (1.0, 0.0, 0.0) if abs(u[0]) < 0.9 else (0.0, 0.0, 1.0))
        axis /= np.linalg.norm(axis)
        return np.array([axis[0], axis[1], axis[2], 0.0])
    q = np.array([*np.cross(u, v), 1.0 + d])
    return q / np.linalg.norm(q)


def _neutral(joints: list[dict], frame: np.ndarray, limbs: dict) -> np.ndarray:
    """A pose (joints x 10, local) with its arms and legs hanging straight down (+Y): each shoulder and hip
    turned so its bone to the elbow or knee points down, then each elbow and knee so its bone to the wrist or
    ankle does: a stance of any idle's (Ayame's, one foot forward; Trion's arm raised with his weapon) made the
    same relaxed one, the strokes made from it."""
    out = frame.copy()
    levels = [[(sh, el) for sh, el, _s, _w in limbs["arms"]] + [(hp, kn) for hp, kn, _s, _a in limbs["legs"]],
              [(el, w) for _sh, el, _s, w in limbs["arms"]] + [(kn, a) for _hp, kn, _s, a in limbs["legs"]]]
    down = np.array([0.0, 1.0, 0.0])
    for level in levels:
        q = out[:, :4] / np.maximum(np.linalg.norm(out[:, :4], axis=1, keepdims=True), 1e-9)
        at = np.array([m[:3, 3] for m in pose_world(joints, q, out[:, 4:7], out[:, 7:10])])
        qw = np.zeros((len(joints), 4))
        for j, jj in enumerate(joints):
            p = jj["parent"]
            qw[j] = _qmul(qw[p], q[j]) if p >= 0 else q[j]
        for j, end in level:
            world = _qmul(_arc(at[end] - at[j], down), qw[j])
            p = joints[j]["parent"]
            out[j, :4] = _qmul(_qinv(qw[p]), world) if p >= 0 else world
    return out


def _limbs(joints: list[dict], at: np.ndarray, points: list | None) -> dict | None:
    """A skeleton's arms and legs, as swim_clips turns them, from its idle pose (at: each joint's place): each arm
    (shoulder, elbow, its side: +1 or -1) and each leg (hip, knee, side). The legs: the pelvis is the deepest joint
    two of whose children run down to the ground as long chains (legs, not a foot's toes), the two furthest apart
    along their length (not a coat's tail between them); each one's lowest end is its foot. The arms: from the hands
    the skeleton's points 126/127 name, else from the wrists (a joint holding four to six fingers, between the
    knees and the head, not on a leg), the two furthest apart; the chest is where their chains meet, the collarbone
    just under it, the shoulder under that. The elbow and the knee are the joints halfway down, by height. None
    if it hasn't two of each."""
    n = len(joints)
    kids: dict = {}
    for j, jj in enumerate(joints):
        kids.setdefault(jj["parent"], []).append(j)
    height = max(float(-at[:, 1].min()), 1e-3)

    def chain(j: int) -> list[int]:
        out = []
        while 0 <= j < n and len(out) < 64:
            out.append(j)
            j = joints[j]["parent"]
        return out

    def under(j: int) -> list[int]:
        out, todo = [], [j]
        while todo:
            k = todo.pop()
            out.append(k)
            todo.extend(kids.get(k, []))
        return out

    def middle(top: int, low: int) -> int | None:
        """The joint halfway along the bones from top down to low (the elbow, the knee), whatever the pose."""
        c = chain(low)
        if top not in c:
            return None
        path = c[:c.index(top) + 1][::-1]                              # top first
        if len(path) < 3:
            return None
        along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(at[path], axis=0), axis=1))])
        half = 0.5 * along[-1]
        inner = range(1, len(path) - 1)
        return path[min(inner, key=lambda i: (abs(along[i] - half), i))]

    # the legs
    def named(a: int, b: int) -> list[int] | None:                       # two of its points, if it names them
        if not points or len(points) <= max(a, b):
            return None
        j = [points[a][0], points[b][0]]
        return j if all(0 < x < n for x in j) and j[0] != j[1] else None

    feet = named(8, 9)                                                 # FFXI's skeletons: 8 and 9 the feet
    if feet:
        ca, cb = chain(feet[0]), chain(feet[1])
        pelvis = next((j for j in ca if j in cb), None)
        if pelvis is not None and ca.index(pelvis) >= 3 and cb.index(pelvis) >= 3:
            runs = [(c[c.index(pelvis) - 1], f, float(np.mean(at[c[:c.index(pelvis)], 2]))) for f, c in
                    ((feet[0], ca), (feet[1], cb))]
            legs = []
            for side, (hip, foot, _z) in zip((-1.0, 1.0), sorted(runs, key=lambda r: r[2])):
                ankle = joints[foot]["parent"] if joints[foot]["parent"] != hip else foot
                knee = middle(hip, ankle)
                if knee is None:
                    break
                legs.append((hip, knee, side, ankle))
            if len(legs) == 2:
                return _arms(joints, at, points, kids, chain, under, middle, named, legs, height)
    # the lowest end of a long chain (a foot; not a stray joint parked under the ground beside the root; floating,
    # Shantotto II's, not the ground)
    ends = [j for j in range(n) if not kids.get(j) and len(chain(j)) >= 6]
    if not ends:
        return None
    bottom = float(-at[ends, 1].max())
    low = {j for j in ends if -at[j, 1] < bottom + 0.1 * height}
    legs_found = None
    for j in sorted(range(n), key=lambda j: -len(chain(j))):            # the deepest first
        runs = []
        for k in kids.get(j, []):
            if -at[k, 1] <= bottom + 0.2 * height:
                continue
            ends = [x for x in under(k) if x in low]
            if not ends:
                continue
            foot = max(ends, key=lambda x: at[x, 1])
            path = chain(foot)[:chain(foot).index(k) + 1]
            if len(path) >= 4:                                         # hip, knee, ankle, foot at least
                runs.append((k, foot, float(np.mean(at[path, 2]))))
        if len(runs) >= 2:
            a, b = max(((p, q) for i, p in enumerate(runs) for q in runs[i + 1:]), key=lambda pq: abs(pq[0][2] - pq[1][2]))
            if abs(a[2] - b[2]) > (0.005 if len(runs) == 2 else 0.02) * height:   # (feet together: Trion)
                legs_found = sorted((a, b), key=lambda r: r[2])
                break
    if legs_found is None:
        return None
    legs = []
    for side, (hip, foot, _z) in zip((-1.0, 1.0), legs_found):
        ankle = joints[foot]["parent"]
        knee = middle(hip, ankle) if ankle != hip else None
        if knee is None:
            return None
        legs.append((hip, knee, side, ankle))
    return _arms(joints, at, points, kids, chain, under, middle, named, legs, height)


def _arms(joints, at, points, kids, chain, under, middle, named, legs, height) -> dict | None:
    """_limbs' arms, its legs found: from the wrists its points 10 and 11 name, else the hands its points 126/127
    name, else the wrists by their fingers (a joint holding three to seven, between the knees and the head, not
    on a leg), the two furthest apart; the chest where their chains meet, the collarbone under it, the shoulder
    under that, the elbow halfway down."""
    n = len(joints)
    leg_joints = set()
    for hip, _knee, _side, _ankle in legs:
        leg_joints |= set(under(hip)) | set(chain(hip))
    bottom = min(-at[a, 1] for _h, _k, _s, a in legs)              # (about the feet)
    wrists = named(10, 11)
    hands = None
    if wrists is None:
        h = named(126, 127)
        if h:
            wrists = [joints[x]["parent"] for x in h]
    if wrists is None:
        cands = [j for j in range(n) if 3 <= len(kids.get(j, [])) <= 7 and j not in leg_joints
                 and bottom + 0.45 * height < -at[j, 1] < 0.9 * height and all(not kids.get(k) or len(kids[k]) <= 2
                                                                              for k in kids[j])]
        if len(cands) < 2:
            return None
        wrists = list(max(((p, q) for i, p in enumerate(cands) for q in cands[i + 1:]),
                          key=lambda pq: abs(at[pq[0], 2] - at[pq[1], 2])))
    ca, cb = chain(wrists[0]), chain(wrists[1])
    chest = next((j for j in ca if j in cb), None)
    arms = []
    for c in (ca, cb):
        i = c.index(chest) if chest in c else -1
        if i < 3:
            return None
        shoulder, wrist = c[i - 2], c[0]
        elbow = middle(shoulder, wrist)
        if elbow is None:
            return None
        arms.append([shoulder, elbow, 0.0, wrist])
    a, b = arms                                                        # each arm's side: the other's opposite
    if abs(at[a[0], 2] - at[b[0], 2]) < 0.02 * height:
        return None
    a[2], b[2] = (1.0, -1.0) if at[a[0], 2] > at[b[0], 2] else (-1.0, 1.0)
    return {"arms": [tuple(a), tuple(b)], "legs": legs}


def skin_corners(verts: np.ndarray, world: np.ndarray) -> np.ndarray:
    """pack's vertices placed by the joints' transforms (4x4, the model's space)."""
    j0 = verts[:, 6].astype(int)
    out = np.einsum("nij,nj->ni", world[j0, :3, :3], verts[:, 0:3]) + world[j0, :3, 3] * verts[:, 8:9]
    two = verts[:, 7] >= 0
    j1 = verts[two, 7].astype(int)
    out[two] += np.einsum("nij,nj->ni", world[j1, :3, :3], verts[two, 3:6]) + world[j1, :3, 3] * verts[two, 9:10]
    return out


def ground_speed(joints: list[dict], verts: np.ndarray, data: np.ndarray, rate: float, place: np.ndarray,
                 samples: int = 48) -> float:
    """The speed over the ground a walking or running motion was made for, in the hunter's units a
    second: it is played in place, so the sole on the ground slides back at that speed. The vertices
    within 2 units of the lowest are the sole; its speed is taken over those on the ground in two
    samples running."""
    frames = len(data)
    seconds = max(frames - 1, 1) / rate / 30.0
    pos = []
    for i in range(samples + 1):
        x = i / samples * (frames - 1)
        lo = min(int(x), max(frames - 2, 0))
        f = x - lo
        d = data[lo] * (1 - f) + data[min(lo + 1, frames - 1)] * f
        q = d[:, :4] / np.linalg.norm(d[:, :4], axis=1, keepdims=True)
        pos.append(skin_corners(verts, pose_world(joints, q, d[:, 4:7], d[:, 7:10])) @ place[:, :3].T)
    pos = np.array(pos)                                                # samples+1 x corners x 3
    # the feet only: of what comes near the ground, what swings to and fro with the stride. A robe's hem at the
    # ground goes along with the body, all but still: taken for the sole while the feet were up, it once made
    # Iroha's run seem a fifth of its pace, her legs whirling to keep up (2026-10-12)
    low = pos[:, :, 1].min(0) < pos[:, :, 1].min() + 0.15 * max(float(np.ptp(pos[0, :, 1])), 1.0)
    travel = np.hypot(np.ptp(pos[:, :, 0], 0), np.ptp(pos[:, :, 2], 0))
    if low.any():
        feet = low & (travel >= 0.5 * travel[low].max())
        if feet.sum() >= 4:
            pos = pos[:, feet]
    down = pos[:, :, 1] < pos[:, :, 1].min(1, keepdims=True) + 2.0
    v = []
    for i in range(samples):
        both = down[i] & down[i + 1]
        if both.any():
            d = pos[i + 1, both][:, [0, 2]] - pos[i, both][:, [0, 2]]
            v.append(np.median(np.hypot(d[:, 0], d[:, 1])) / (seconds / samples))
    return float(np.median(v)) if v else 0.0


def ps2_alpha(img: np.ndarray) -> np.ndarray:
    """FFXI keeps the PlayStation 2's alpha, 0x80 opaque: a texture whose alpha stays in that range
    (DXT's rounding a little over it) is put on the usual scale, 0xFF opaque. Read as it is, half
    its pixels fall under the renderer's cut-out and the cloth shows a lattice of holes.
    A texture already on the usual scale keeps only its lowest steps as cut-outs (DXT3's 0 and 17),
    the rest whole: armour marks other things than holes in its alpha (the Judge's Cuisses a band of
    0x55 across the thighs, its Greaves 0x44-0x55 nearly all over), which the renderer's cut-out at
    half took away (2026-10-09: a GM's legs missing above the knees)."""
    a = img[..., 3]
    if a.max() > 0x90:
        out = img.copy()
        out[..., 3] = np.where(a >= 0x20, 255, a).astype(np.uint8)
        return out
    out = img.copy()
    out[..., 3] = np.minimum(a.astype(np.int32) * 2, 255).astype(np.uint8)
    return out


# ================================================================================================
def ffxi_root(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    try:
        return Path(json.loads((A.asset_dir() / "launcher.json").read_text())["ffxi"])
    except (OSError, ValueError, KeyError):
        try:
            return Path(A.load_catalogue()["root"])
        except SystemExit:
            sys.exit("where is FFXI? --ffxi PATH (the FINAL FANTASY XI folder)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ffxi", help="your FINAL FANTASY XI folder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("info", "view"):
        s = sub.add_parser(c)
        s.add_argument("dat", help="a model file, e.g. ROM/3/6.DAT")
    a = ap.parse_args()
    root = ffxi_root(a.ffxi)
    path = root / a.dat
    model = load(path)
    print(f"{a.dat}: {len(model['skeletons'])} skeleton(s) "
          f"{[len(s) for s in model['skeletons']]} joints, {len(model['meshes'])} meshes "
          f"{[m['name'] for m in model['meshes']]}, {len(model['textures'])} textures, "
          f"{len(model['motions'])} motions {[m['name'] for m in model['motions']][:16]}")
    for e in model["errors"]:
        print("  could not read:", e)
    if a.cmd == "view":
        from PIL import Image
        if not model["skeletons"]:
            sys.exit("no skeleton in this file")
        out = A.asset_dir() / "models" / a.dat.replace("/", "_").replace(".DAT", "")
        export_obj(model, out)
        views = [render(model, 512, y) for y in (0, 90, 180)]
        Image.fromarray(np.concatenate(views, 1), "RGBA").save(out / "preview.png")
        print(f"-> {out / 'preview.png'} and model.obj")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
