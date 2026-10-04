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
    A one-joint vertex is a position (and normal) in its joint's space; a two-joint vertex holds both
    positions already weighted, and the two weights. A symmetric mesh is drawn twice, the second time
    from flipped joints and axes. The instructions: 0x8000 a texture's 16-character name, 0x8010
    material properties, 0x5453/0x0054 textured strips/lists with UVs, 0x4353/0x0043 coloured ones,
    0xFFFF the end.
  textures: the file's image chunks, named by their 16-character category and name.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ffxi_assets as A  # noqa: E402

SKELETON, MESH, IMAGE = 0x29, 0x2A, 0x20


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


def _ref(v: int) -> tuple[int, int, int]:
    return v & 0x7F, (v >> 7) & 0x7F, (v >> 14) & 3          # joint, its mirror, the axis flipped


def parse_mesh(b: bytes, data: int, name: str) -> dict:
    r = Reader(b, data)
    r.u8(); r.u8()
    flags3 = r.u8()
    cloth, use_table = bool(flags3 & 1), bool(flags3 & 0x80)
    r.u8()
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
    r.pos = data + verts_at
    for i in range(n1):
        p0[i] = r.vec3()
        if normals:
            r.vec3()
    for i in range(n1, n):
        x0, x1, y0, y1, z0, z1, w0, w1 = r.take("8f")
        p0[i], p1[i], w[i] = (x0, y0, z0), (x1, y1, z1), (w0, w1)
        if normals:
            r.take("6f")
    j0 = np.array([joint(a[0]) for a, _ in refs])
    j1 = np.array([joint(bb[0]) if i >= n1 else -1 for i, (_, bb) in enumerate(refs)])
    mj0 = np.array([joint(a[1]) for a, _ in refs])
    mj1 = np.array([joint(bb[1]) if i >= n1 else -1 for i, (_, bb) in enumerate(refs)])
    fa0 = np.array([a[2] for a, _ in refs])
    fa1 = np.array([bb[2] for _, bb in refs])

    pieces, tex = [], ""
    r.pos = data + instr
    while True:
        op = r.u16()
        if op == 0xFFFF:
            break
        if op == 0x8000:
            tex = r.name16()
        elif op == 0x8010:
            r.pos += 4 + 8 + 4 + 4 + 8 + 2 + 4 + 2 + 4 + 4          # material properties
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
            pieces.append({"tex": tex, "tris": tris})
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
            pieces.append({"tex": tex, "tris": tris})
        else:
            raise ValueError(f"{name}: unknown mesh instruction {op:#06x} at {r.pos - 2 - data:#x}")
    return {"name": name, "p0": p0, "p1": p1, "w": w, "j0": j0, "j1": j1, "mj0": mj0, "mj1": mj1,
            "fa0": fa0, "fa1": fa1, "symmetric": symmetric, "pieces": pieces}


def load(path: Path) -> dict:
    b = path.read_bytes()
    out = {"skeletons": [], "meshes": [], "textures": {}, "motions": [], "errors": []}
    for at, nm, typ, size in A.chunks(b):
        name = nm.decode("latin-1")
        try:
            if typ == SKELETON:
                out["skeletons"].append(parse_skeleton(b, at + 16))
            elif typ == MESH:
                out["meshes"].append(parse_mesh(b, at + 16, name))
            elif typ == IMAGE:
                info = A.image_info(b, at, size)
                if info:
                    key = b[at + 17:at + 33].decode("latin-1")             # category and name, 16 chars
                    out["textures"][key] = A.decode(b, info)
            elif typ == 0x2B:
                out["motions"].append(name)
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


def _flip(p: np.ndarray, axis: np.ndarray) -> np.ndarray:
    q = p.copy()
    for a in (1, 2, 3):
        q[axis == a, a - 1] *= -1
    return q


def skin(mesh: dict, world: np.ndarray, mirrored: bool = False) -> np.ndarray:
    """The mesh's vertices in the model's space (one joint: its transform; two: both, pre-weighted)."""
    if mirrored:
        p0, p1 = _flip(mesh["p0"], mesh["fa0"]), _flip(mesh["p1"], mesh["fa1"])
        j0, j1 = mesh["mj0"], mesh["mj1"]
    else:
        p0, p1, j0, j1 = mesh["p0"], mesh["p1"], mesh["j0"], mesh["j1"]
    j0 = np.clip(j0, 0, len(world) - 1)
    m0 = world[j0]
    out = np.einsum("nij,nj->ni", m0[:, :3, :3], p0) + m0[:, :3, 3] * mesh["w"][:, :1]
    two = j1 >= 0
    if two.any():
        m1 = world[np.clip(j1[two], 0, len(world) - 1)]
        out[two] += np.einsum("nij,nj->ni", m1[:, :3, :3], p1[two]) + m1[:, :3, 3] * mesh["w"][two, 1:2]
    return out


def triangles(model: dict, skeleton: int = 0):
    """Every triangle of the model posed: (3 positions, 3 UVs, texture name)."""
    world = joint_world(model["skeletons"][skeleton])
    for mesh in model["meshes"]:
        for mirrored in ((False, True) if mesh["symmetric"] else (False,)):
            v = skin(mesh, world, mirrored)
            for piece in mesh["pieces"]:
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


def render(model: dict, size: int = 512, yaw: float = 0.0) -> np.ndarray:
    """A textured, depth-tested, orthographic picture of the model, turned by yaw (degrees)."""
    tris = list(triangles(model))
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
          f"{len(model['motions'])} motions {model['motions'][:12]}")
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
