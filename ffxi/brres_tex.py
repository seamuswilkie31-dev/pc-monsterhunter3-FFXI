# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10"]
# ///
"""Textures in Monster Hunter Tri's NW4R resources (.brres): list, export to PNG, import from PNG.

The port reads the game's files from build/extract, so a texture imported there shows in the game at
the next launch. Imports are in place: the texture keeps its size and format, and a palette texture
(CI4/CI8) its palette's length, so nothing else in the file moves. The first change to a file keeps
the original in build/extract-orig, and `restore` puts it back.

    uv run brres_tex.py list   04/cock_eng.brres
    uv run brres_tex.py export 04/cock_eng.brres ../build/ui/cock      # every texture as a PNG
    uv run brres_tex.py import 04/cock_eng.brres cock00 new.png
    uv run brres_tex.py clear  04/cock_eng.brres cock00                # fully transparent
    uv run brres_tex.py restore 04/cock_eng.brres
    uv run brres_tex.py test   04/cock_eng.brres 04/menu_eng.brres     # decode, encode, compare

Paths are relative to build/extract/files (or absolute).
"""
from __future__ import annotations

import argparse
import shutil
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
FILES = HERE.parent / "build" / "extract" / "files"
ORIG = HERE.parent / "build" / "extract-orig" / "files"

NAMES = {0: "I4", 1: "I8", 2: "IA4", 3: "IA8", 4: "RGB565", 5: "RGB5A3", 6: "RGBA8",
         8: "CI4", 9: "CI8", 10: "CI14X2", 14: "CMPR"}
BLOCK = {0: (8, 8), 1: (8, 4), 2: (8, 4), 3: (4, 4), 4: (4, 4), 5: (4, 4), 6: (4, 4),
         8: (8, 8), 9: (8, 4), 14: (8, 8)}          # tile width, height
BITS = {0: 4, 1: 8, 2: 8, 3: 16, 4: 16, 5: 16, 6: 32, 8: 4, 9: 8, 14: 4}
PAL_NAMES = {0: "IA8", 1: "RGB565", 2: "RGB5A3"}


def tex_size(fmt: int, w: int, h: int) -> int:
    bw, bh = BLOCK[fmt]
    return (-(-w // bw) * bw) * (-(-h // bh) * bh) * BITS[fmt] // 8


# ------------------------------------------------------------------------------------------------
# tiles: the GX stores pixels tile by tile, raster order inside each tile
# ------------------------------------------------------------------------------------------------
def to_tiles(a: np.ndarray, bw: int, bh: int) -> np.ndarray:
    """(H, W, ...) padded to whole tiles -> pixels in GX order, (N, ...)."""
    h, w = a.shape[:2]
    ph, pw = -(-h // bh) * bh, -(-w // bw) * bw
    pad = [(0, ph - h), (0, pw - w)] + [(0, 0)] * (a.ndim - 2)
    a = np.pad(a, pad)
    a = a.reshape(ph // bh, bh, pw // bw, bw, *a.shape[2:]).swapaxes(1, 2)
    return a.reshape(-1, *a.shape[4:])


def from_tiles(p: np.ndarray, w: int, h: int, bw: int, bh: int) -> np.ndarray:
    """Pixels in GX order -> (H, W, ...) cropped to the image."""
    ph, pw = -(-h // bh) * bh, -(-w // bw) * bw
    a = p.reshape(ph // bh, pw // bw, bh, bw, *p.shape[1:]).swapaxes(1, 2)
    return a.reshape(ph, pw, *p.shape[1:])[:h, :w]


# ------------------------------------------------------------------------------------------------
# colours
# ------------------------------------------------------------------------------------------------
def x5(v): return (v << 3) | (v >> 2)
def x6(v): return (v << 2) | (v >> 4)
def x4(v): return (v << 4) | v
def x3(v): return (v << 5) | (v << 2) | (v >> 1)


def dec_rgb565(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.uint32)
    return np.stack([x5((v >> 11) & 31), x6((v >> 5) & 63), x5(v & 31), np.full_like(v, 255)], -1).astype(np.uint8)


def enc_rgb565(c: np.ndarray) -> np.ndarray:
    c = c.astype(np.uint32)
    return ((c[..., 0] >> 3) << 11 | (c[..., 1] >> 2) << 5 | (c[..., 2] >> 3)).astype(">u2")


def dec_rgb5a3(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.uint32)
    op = (v & 0x8000) != 0
    r = np.where(op, x5((v >> 10) & 31), x4((v >> 8) & 15))
    g = np.where(op, x5((v >> 5) & 31), x4((v >> 4) & 15))
    b = np.where(op, x5(v & 31), x4(v & 15))
    a = np.where(op, 255, x3((v >> 12) & 7))
    return np.stack([r, g, b, a], -1).astype(np.uint8)


def enc_rgb5a3(c: np.ndarray) -> np.ndarray:
    c = c.astype(np.uint32)
    r, g, b, a = c[..., 0], c[..., 1], c[..., 2], c[..., 3]
    op = 0x8000 | (r >> 3) << 10 | (g >> 3) << 5 | (b >> 3)
    tr = (a >> 5) << 12 | (r >> 4) << 8 | (g >> 4) << 4 | (b >> 4)
    # Opaque from alpha 224 up, in the 5-bit form; but an opaque colour exact only in the 4-bit form
    # (the translucent form at alpha 7, which games use for opaque colours too) stays there, exactly.
    exact5 = (x5(r >> 3) == r) & (x5(g >> 3) == g) & (x5(b >> 3) == b)
    exact4 = (x4(r >> 4) == r) & (x4(g >> 4) == g) & (x4(b >> 4) == b) & (a == 255)
    return np.where((a >= 0xE0) & (exact5 | ~exact4), op, tr).astype(">u2")


def dec_ia8(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.uint32)
    i, a = v & 0xFF, v >> 8
    return np.stack([i, i, i, a], -1).astype(np.uint8)


def enc_ia8(c: np.ndarray) -> np.ndarray:
    c = c.astype(np.uint32)
    return (c[..., 3] << 8 | lum(c)).astype(">u2")


def lum(c: np.ndarray) -> np.ndarray:
    c = c.astype(np.uint32)
    return (c[..., 0] * 299 + c[..., 1] * 587 + c[..., 2] * 114 + 500) // 1000


PAL_DEC = {0: dec_ia8, 1: dec_rgb565, 2: dec_rgb5a3}
PAL_ENC = {0: enc_ia8, 1: enc_rgb565, 2: enc_rgb5a3}


# ------------------------------------------------------------------------------------------------
# decode: bytes -> (H, W, 4) RGBA
# ------------------------------------------------------------------------------------------------
def decode(fmt: int, w: int, h: int, data: bytes, palette: np.ndarray | None = None) -> np.ndarray:
    bw, bh = BLOCK[fmt]
    n = tex_size(fmt, w, h)
    d = np.frombuffer(data[:n], np.uint8)
    if fmt == 0:                                       # I4: intensity in alpha too
        i = x4(np.stack([d >> 4, d & 15], -1).reshape(-1))
        p = np.stack([i, i, i, i], -1)
    elif fmt == 1:                                     # I8
        p = np.stack([d, d, d, d], -1)
    elif fmt == 2:                                     # IA4: alpha high, intensity low
        i, a = x4(d & 15), x4(d >> 4)
        p = np.stack([i, i, i, a], -1)
    elif fmt == 3:
        p = dec_ia8(np.frombuffer(data[:n], ">u2"))
    elif fmt == 4:
        p = dec_rgb565(np.frombuffer(data[:n], ">u2"))
    elif fmt == 5:
        p = dec_rgb5a3(np.frombuffer(data[:n], ">u2"))
    elif fmt == 6:                                     # RGBA8: per tile 16 AR pairs, then 16 GB pairs
        t = d.reshape(-1, 2, 16, 2)
        p = np.stack([t[:, 0, :, 1], t[:, 1, :, 0], t[:, 1, :, 1], t[:, 0, :, 0]], -1).reshape(-1, 4)
    elif fmt in (8, 9):
        idx = np.stack([d >> 4, d & 15], -1).reshape(-1) if fmt == 8 else d
        p = palette[np.minimum(idx, len(palette) - 1)]
    elif fmt == 14:
        p = dec_cmpr(d)
    else:
        raise ValueError(f"format {NAMES.get(fmt, fmt)} not handled")
    return from_tiles(p.astype(np.uint8), w, h, bw, bh)


def dec_cmpr(d: np.ndarray) -> np.ndarray:
    """CMPR: 8x8 tiles of four 4x4 DXT1 blocks; weights 5/8, 3/8 as the hardware (wiikit.gxtex)."""
    blocks = d.reshape(-1, 8)
    c0 = blocks[:, 0].astype(np.uint32) << 8 | blocks[:, 1]
    c1 = blocks[:, 2].astype(np.uint32) << 8 | blocks[:, 3]
    e0, e1 = dec_rgb565(c0).astype(np.int32), dec_rgb565(c1).astype(np.int32)
    four = c0 > c1
    pal = np.stack([e0, e1,
                    np.where(four[:, None], (5 * e0 + 3 * e1) // 8, (e0 + e1) // 2),
                    np.where(four[:, None], (3 * e0 + 5 * e1) // 8, 0)], 1)
    pal[~four, 3, 3] = 0
    bits = blocks[:, 4:8]
    idx = np.stack([(bits >> s) & 3 for s in (6, 4, 2, 0)], -1).reshape(-1, 16)
    px = np.take_along_axis(pal, idx[:, :, None].repeat(4, 2), 1)          # (blocks, 16, 4)
    # four 4x4 blocks per 8x8 tile, in 2x2 order -> the tile's raster order
    px = px.reshape(-1, 2, 2, 4, 4, 4).transpose(0, 1, 3, 2, 4, 5).reshape(-1, 4)
    return px.astype(np.uint8)


# ------------------------------------------------------------------------------------------------
# encode: (H, W, 4) RGBA -> bytes (and a palette for CI)
# ------------------------------------------------------------------------------------------------
def encode(fmt: int, rgba: np.ndarray, pal_fmt: int = 2, pal_len: int = 256) -> tuple[bytes, bytes | None]:
    h, w = rgba.shape[:2]
    bw, bh = BLOCK[fmt]
    if fmt in (8, 9):
        idx, pal = quantize(rgba, pal_fmt, min(pal_len, 16 if fmt == 8 else 256))
        p = to_tiles(idx, bw, bh)
        data = (p[0::2] << 4 | p[1::2]).astype(np.uint8) if fmt == 8 else p.astype(np.uint8)
        pal_bytes = PAL_ENC[pal_fmt](pal).tobytes() + bytes(2 * (pal_len - len(pal)))
        return data.tobytes(), pal_bytes
    p = to_tiles(rgba.astype(np.uint8), bw, bh).astype(np.uint32)
    if fmt == 0:
        i = lum(p) >> 4
        out = (i[0::2] << 4 | i[1::2]).astype(np.uint8)
    elif fmt == 1:
        out = lum(p).astype(np.uint8)
    elif fmt == 2:
        out = ((p[:, 3] >> 4) << 4 | (lum(p) >> 4)).astype(np.uint8)
    elif fmt == 3:
        out = enc_ia8(p)
    elif fmt == 4:
        out = enc_rgb565(p)
    elif fmt == 5:
        out = enc_rgb5a3(p)
    elif fmt == 6:
        t = p.reshape(-1, 16, 4)
        ar = np.stack([t[:, :, 3], t[:, :, 0]], -1)
        gb = np.stack([t[:, :, 1], t[:, :, 2]], -1)
        out = np.stack([ar, gb], 1).astype(np.uint8)
    elif fmt == 14:
        out = enc_cmpr(rgba)
    else:
        raise ValueError(f"format {NAMES.get(fmt, fmt)} not handled")
    return out.tobytes(), None


def quantize(rgba: np.ndarray, pal_fmt: int, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Indices and a palette of at most n colours, as the palette's format holds them."""
    # first to the palette format's precision, so equal colours stay equal
    c = PAL_DEC[pal_fmt](PAL_ENC[pal_fmt](rgba.reshape(-1, 4))).reshape(rgba.shape)
    flat = c.reshape(-1, 4)
    uniq, inv = np.unique(flat.view(np.uint32), return_inverse=True)
    if len(uniq) <= n:
        return inv.reshape(c.shape[:2]), uniq.view(np.uint8).reshape(-1, 4)
    q = Image.fromarray(c, "RGBA").quantize(colors=n, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    pal = np.array(q.getpalette(rawmode="RGBA")[:4 * n], np.uint8).reshape(-1, 4)
    return np.array(q), pal


def enc_cmpr(rgba: np.ndarray) -> np.ndarray:
    """A plain CMPR encoder: per 4x4 block the two most distant colours as ends; transparent blocks
    use the 3-colour mode with index 3 transparent."""
    p = to_tiles(rgba.astype(np.uint8), 8, 8).reshape(-1, 2, 4, 2, 4, 4)       # tile, by, y, bx, x
    blocks = p.transpose(0, 1, 3, 2, 4, 5).reshape(-1, 16, 4).astype(np.int32)
    out = np.zeros((len(blocks), 8), np.uint8)
    for k, b in enumerate(blocks):
        clear = b[:, 3] < 128
        solid = b[~clear, :3] if (~clear).any() else b[:, :3]
        lo, hi = solid.min(0), solid.max(0)
        c0 = int(enc_rgb565(np.append(hi, 255)))
        c1 = int(enc_rgb565(np.append(lo, 255)))
        e0, e1 = dec_rgb565(np.array(c0)).astype(np.int32)[:3], dec_rgb565(np.array(c1)).astype(np.int32)[:3]
        if clear.any():                                 # 3 colours + transparent: c0 <= c1
            if c0 > c1:
                c0, c1, e0, e1 = c1, c0, e1, e0
            pal = np.stack([e0, e1, (e0 + e1) // 2])
        else:
            if c0 == c1:
                c1 = c0 - 1 if c0 else 0                # keep the 4-colour mode
                e1 = dec_rgb565(np.array(c1)).astype(np.int32)[:3]
            if c0 < c1:
                c0, c1, e0, e1 = c1, c0, e1, e0
            pal = np.stack([e0, e1, (5 * e0 + 3 * e1) // 8, (3 * e0 + 5 * e1) // 8])
        idx = ((b[:, None, :3] - pal[None]) ** 2).sum(-1).argmin(1)
        if clear.any():
            idx[clear] = 3
        bits = 0
        for i in idx:
            bits = bits << 2 | int(i)
        out[k] = [c0 >> 8, c0 & 255, c1 >> 8, c1 & 255, *bits.to_bytes(4, "big")]
    return out


# ------------------------------------------------------------------------------------------------
# .brres: TEX0 and PLT0 sections, found by their magic; edited in place
# ------------------------------------------------------------------------------------------------
class Brres:
    def __init__(self, path: Path):
        self.path = path
        self.b = bytearray(path.read_bytes())
        if self.b[:4] != b"bres":
            raise ValueError(f"{path} is not a .brres")
        self.tex, self.pal = {}, {}
        for magic, table in ((b"TEX0", self.tex), (b"PLT0", self.pal)):
            i = 0
            while (i := self.b.find(magic, i)) >= 0:
                _, _, _, doff, noff = struct.unpack(">IIiii", self.b[i + 4:i + 24])
                name = self.b[i + noff:self.b.index(b"\0", i + noff)].decode("ascii")
                if magic == b"TEX0":
                    flags, w, h, fmt, n = struct.unpack(">IHHII", self.b[i + 24:i + 40])
                    table[name] = dict(at=i, data=i + doff, w=w, h=h, fmt=fmt, images=n, ci=bool(flags & 1))
                else:
                    pfmt, count = struct.unpack(">IH", self.b[i + 24:i + 30])
                    table[name] = dict(at=i, data=i + doff, fmt=pfmt, count=count)
                i += 4

    def palette(self, name: str) -> np.ndarray | None:
        p = self.pal.get(name)
        if not p:
            return None
        raw = np.frombuffer(bytes(self.b[p["data"]:p["data"] + 2 * p["count"]]), ">u2")
        return PAL_DEC[p["fmt"]](raw)

    def image(self, name: str) -> np.ndarray:
        t = self.tex[name]
        n = tex_size(t["fmt"], t["w"], t["h"])
        return decode(t["fmt"], t["w"], t["h"], bytes(self.b[t["data"]:t["data"] + n]), self.palette(name))

    def put(self, name: str, rgba: np.ndarray) -> None:
        t = self.tex[name]
        if rgba.shape[:2] != (t["h"], t["w"]):
            raise ValueError(f"{name} is {t['w']}x{t['h']}; the image is {rgba.shape[1]}x{rgba.shape[0]}")
        if t["images"] != 1:
            raise ValueError(f"{name} has {t['images']} mip levels: not handled yet")
        p = self.pal.get(name)
        data, pal = encode(t["fmt"], rgba, p["fmt"] if p else 2, p["count"] if p else 256)
        assert len(data) == tex_size(t["fmt"], t["w"], t["h"])
        self.b[t["data"]:t["data"] + len(data)] = data
        if pal is not None:
            if not p:
                raise ValueError(f"{name} is a palette texture without a PLT0 of its name")
            self.b[p["data"]:p["data"] + len(pal)] = pal

    def save(self) -> None:
        keep_original(self.path)
        self.path.write_bytes(self.b)


def resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else FILES / path


def keep_original(path: Path) -> None:
    rel = path.resolve().relative_to(FILES.resolve())
    dst = ORIG / rel
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)


# ------------------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").add_argument("brres")
    e = sub.add_parser("export")
    e.add_argument("brres")
    e.add_argument("prefix", help="output path prefix: PREFIX_<texture>.png")
    i = sub.add_parser("import")
    i.add_argument("brres")
    i.add_argument("texture")
    i.add_argument("png")
    c = sub.add_parser("clear")
    c.add_argument("brres")
    c.add_argument("texture")
    sub.add_parser("restore").add_argument("brres")
    t = sub.add_parser("test")
    t.add_argument("brres", nargs="+")
    a = ap.parse_args()

    if a.cmd == "restore":
        path = resolve(a.brres)
        orig = ORIG / path.resolve().relative_to(FILES.resolve())
        if not orig.exists():
            sys.exit(f"no original kept for {a.brres}: it was never changed")
        shutil.copy2(orig, path)
        print(f"restored {a.brres}")
        return 0
    if a.cmd == "test":
        return test([resolve(p) for p in a.brres])

    br = Brres(resolve(a.brres))
    if a.cmd == "list":
        for name, t in br.tex.items():
            p = br.pal.get(name)
            pal = f", palette {p['count']} x {PAL_NAMES[p['fmt']]}" if p else ""
            print(f"{name:<16} {t['w']}x{t['h']} {NAMES.get(t['fmt'], t['fmt'])}{pal}")
    elif a.cmd == "export":
        Path(a.prefix).parent.mkdir(parents=True, exist_ok=True)
        for name in br.tex:
            Image.fromarray(br.image(name), "RGBA").save(f"{a.prefix}_{name}.png")
            print(f"{a.prefix}_{name}.png")
    elif a.cmd == "import":
        br.put(a.texture, np.array(Image.open(a.png).convert("RGBA")))
        br.save()
        print(f"{a.texture} replaced in {a.brres} (original kept in build/extract-orig)")
    elif a.cmd == "clear":
        t = br.tex[a.texture]
        br.put(a.texture, np.zeros((t["h"], t["w"], 4), np.uint8))
        br.save()
        print(f"{a.texture} cleared in {a.brres} (original kept in build/extract-orig)")
    return 0


def test(paths: list[Path]) -> int:
    """Every texture: decode, encode, decode again; the second decode must equal the first (CMPR may
    differ: it is lossy), and the encoded bytes must equal the file's for the lossless formats."""
    ok = True
    for path in paths:
        br = Brres(path)
        for name, t in br.tex.items():
            img = br.image(name)
            p = br.pal.get(name)
            data, pal = encode(t["fmt"], img, p["fmt"] if p else 2, p["count"] if p else 256)
            again = decode(t["fmt"], t["w"], t["h"], data,
                           PAL_DEC[p["fmt"]](np.frombuffer(pal, ">u2")) if pal is not None else None)
            n = tex_size(t["fmt"], t["w"], t["h"])
            same_px = np.array_equal(img, again)
            same_bytes = data == bytes(br.b[t["data"]:t["data"] + n])
            if t["fmt"] == 14:
                err = np.abs(img.astype(int) - again.astype(int)).mean()
                verdict = f"lossy, mean error {err:.1f}"
                good = err < 12
            else:
                if same_px:
                    verdict = "pixels equal" + (", bytes equal" if same_bytes else
                                                      " (palette reordered)" if t["ci"] else ", bytes differ")
                else:
                    diff = np.abs(img.astype(int) - again.astype(int)).max(-1)
                    verdict = f"{int((diff > 0).sum())} pixels differ, by up to {int(diff.max())}"
                good = same_px
            ok &= good
            print(f"{'PASS' if good else 'FAIL'} {path.name}:{name:<14} {NAMES[t['fmt']]:<7} {verdict}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
