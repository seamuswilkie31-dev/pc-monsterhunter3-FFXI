# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10"]
# ///
"""Your own FFXI's interface art, for the layer: found and extracted from your installation, kept local.

Bring your own assets, as the port does with the Monster Hunter disc: nothing of FFXI is in this
repository. Install FINAL FANTASY XI from Square Enix's official download (playonline.com), then:

    uv run ffxi_assets.py scan "C:/Program Files (x86)/PlayOnline/SquareEnix/FINAL FANTASY XI"
                            # every image in every DAT, catalogued (name, size, format, where)
    uv run ffxi_assets.py sheets            # contact sheets of the catalogue, to find what is what
    uv run ffxi_assets.py export NAME...    # images to PNG, by catalogue name (or a regex with --re)
    uv run ffxi_assets.py ui                # the pieces the layer's interface uses (window themes, the glove)

Everything goes to the local asset folder (%LOCALAPPDATA%/ffxi-mh3/assets, or FFXI_MH3_ASSETS), never
into the repository. The layer's interface uses what is there, and draws its own lookalike without it.

The DAT format, as the community's tools read it: a file is a sequence of chunks, each with a 16-byte
header: a 4-character name, then a little-endian word whose low 7 bits are the chunk's type and the
rest its size in 16-byte units (header included). Type 0x20 is an image: a format byte, a 16-character
category and name, a BITMAPINFOHEADER, then the pixels: DXT1/DXT3 ("1TXD"/"3TXD", the FourCC
reversed, then the data's size and one more word), 8-bit indices after a 256-colour BGRA palette, or 32-bit BGRA, rows bottom first.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def asset_dir() -> Path:
    d = os.environ.get("FFXI_MH3_ASSETS") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ffxi-mh3" / "assets"
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


CATALOGUE = "catalogue.json"
IMAGE = 0x20


# ================================================================================================
# chunks and images
# ================================================================================================
def chunks(b: bytes):
    """(offset, name, type, size) of each chunk, while they hold together."""
    at = 0
    while at + 16 <= len(b):
        name = b[at:at + 4]
        v = struct.unpack_from("<I", b, at + 4)[0]
        typ, size = v & 0x7F, ((v >> 7) & 0x7FFFF) * 16
        if size < 16 or at + size > len(b):
            return
        yield at, name, typ, size
        at += size


def image_info(b: bytes, at: int, size: int) -> dict | None:
    """An image chunk's header: its format and size, where its pixels are."""
    d = at + 16
    if size < 16 + 1 + 16 + 40:
        return None
    flag = b[d]
    cat = b[d + 1:d + 9].decode("latin-1").strip("\0 ")
    name = b[d + 9:d + 17].decode("latin-1").strip("\0 ")
    hdr = d + 17
    hsize, w, h, planes, bits, comp = struct.unpack_from("<IiiHHI", b, hdr)
    if hsize != 40 or not (0 < w <= 4096 and 0 < h <= 4096):
        return None
    px = hdr + 40
    info = {"flag": flag, "cat": cat, "name": name, "w": w, "h": h, "bits": bits}
    four = b[px:px + 4]
    if four in (b"1TXD", b"3TXD", b"5TXD"):
        info["fmt"] = "DXT" + chr(four[0])
        info["px"] = px + 12                     # the FourCC, the data's size, one more word
    elif bits == 8:
        info["fmt"], info["px"] = "P8", px
    elif bits == 32:
        info["fmt"], info["px"] = "BGRA", px
    elif bits == 4:
        info["fmt"], info["px"] = "P4", px
    else:
        return None
    return info


def _dxt_block_colours(c0: np.ndarray, c1: np.ndarray, four: np.ndarray) -> np.ndarray:
    def rgb(c):
        c = c.astype(np.int32)
        return np.stack([((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31], -1)
    e0, e1 = rgb(c0), rgb(c1)
    p2 = np.where(four[:, None], (2 * e0 + e1) // 3, (e0 + e1) // 2)
    p3 = np.where(four[:, None], (e0 + 2 * e1) // 3, 0)
    return np.stack([e0, e1, p2, p3], 1)          # (blocks, 4, 3)


def decode(b: bytes, info: dict, ps2_alpha: bool = True) -> np.ndarray:
    """RGBA, rows top first. ps2_alpha: an alpha in FFXI's 0-0x80 put on the usual scale (False: as stored,
    for the effects, whose blending counts on it)."""
    w, h, at = info["w"], info["h"], info["px"]
    fmt = info["fmt"]
    if fmt in ("DXT1", "DXT3"):
        bw, bh = (w + 3) // 4, (h + 3) // 4
        n = bw * bh
        step = 8 if fmt == "DXT1" else 16
        raw = np.frombuffer(b, np.uint8, n * step, at).reshape(n, step)
        col = raw[:, -8:]
        c0 = col[:, 0].astype(np.uint16) | col[:, 1].astype(np.uint16) << 8
        c1 = col[:, 2].astype(np.uint16) | col[:, 3].astype(np.uint16) << 8
        four = (c0 > c1) | (fmt == "DXT3")
        pal = _dxt_block_colours(c0, c1, four)
        bits = col[:, 4:8].astype(np.uint32)
        idx = np.stack([(bits[:, i // 4] >> (2 * (i % 4))) & 3 for i in range(16)], 1)   # (n, 16)
        rgb = np.take_along_axis(pal, idx[:, :, None].repeat(3, 2), 1)
        if fmt == "DXT3":
            a = raw[:, :8]
            alpha = np.stack([(a[:, i // 2] >> (4 * (i % 2))) & 15 for i in range(16)], 1) * 17
        else:
            alpha = np.where((~four[:, None]) & (idx == 3), 0, 255)
        px = np.concatenate([rgb, alpha[:, :, None]], 2).astype(np.uint8)   # (n, 16, 4)
        img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)[:h, :w]
        return img                                 # DXT blocks run top first
    if fmt == "P8":
        pal = np.frombuffer(b, np.uint8, 1024, at).reshape(256, 4)[:, [2, 1, 0, 3]]
        idx = np.frombuffer(b, np.uint8, w * h, at + 1024).reshape(h, w)
        img = pal[idx]
    elif fmt == "P4":
        pal = np.frombuffer(b, np.uint8, 64, at).reshape(16, 4)[:, [2, 1, 0, 3]]
        raw = np.frombuffer(b, np.uint8, (w * h + 1) // 2, at + 64)
        idx = np.stack([raw >> 4, raw & 15], 1).reshape(-1)[:w * h].reshape(h, w)
        img = pal[idx]
    else:
        img = np.frombuffer(b, np.uint8, w * h * 4, at).reshape(h, w, 4)[:, :, [2, 1, 0, 3]]
    img = img[::-1].copy()                         # bitmaps run bottom first
    if ps2_alpha and img[:, :, 3].max() <= 0x80:    # FFXI's alpha runs 0-0x80
        img[:, :, 3] = np.minimum(255, img[:, :, 3].astype(np.uint16) * 2).astype(np.uint8)
    return img


# ================================================================================================
# commands
# ================================================================================================
def find_dats(root: Path):
    for rom in sorted(root.glob("ROM*")):
        if rom.is_dir():
            yield from sorted(rom.rglob("*.DAT"))


def cmd_scan(a) -> int:
    root = Path(a.ffxi)
    if not (root / "ROM").is_dir():
        sys.exit(f"{root} has no ROM folder: point at the FINAL FANTASY XI folder of your installation")
    cat, n_files = [], 0
    for dat in find_dats(root):
        n_files += 1
        try:
            b = dat.read_bytes()
        except OSError:
            continue
        for at, name, typ, size in chunks(b):
            if typ == IMAGE:
                info = image_info(b, at, size)
                if info:
                    info.update(file=str(dat.relative_to(root)).replace("\\", "/"), at=at)
                    info.pop("px", None)
                    cat.append(info)
        if n_files % 2000 == 0:
            print(f"  {n_files} files, {len(cat)} images")
    out = asset_dir() / CATALOGUE
    out.write_text(json.dumps({"root": str(root), "images": cat}), encoding="utf-8")
    print(f"{n_files} DAT files, {len(cat)} images -> {out}")
    return 0


def load_catalogue() -> dict:
    p = asset_dir() / CATALOGUE
    if not p.exists():
        sys.exit("no catalogue yet: run scan first")
    return json.loads(p.read_text(encoding="utf-8"))


def image_of(root: Path, entry: dict) -> np.ndarray:
    b = (root / entry["file"]).read_bytes()
    v = struct.unpack_from("<I", b, entry["at"] + 4)[0]
    info = image_info(b, entry["at"], ((v >> 7) & 0x7FFFF) * 16)
    return decode(b, info)


def cmd_sheets(a) -> int:
    from PIL import Image, ImageDraw
    c = load_catalogue()
    root = Path(c["root"])
    imgs = [e for e in c["images"] if (not a.match or re.search(a.match, f"{e['file']} {e['cat']} {e['name']}"))]
    out = asset_dir() / "sheets"
    out.mkdir(exist_ok=True)
    T, COLS, PER = 128, 10, 60
    for s in range(0, len(imgs), PER):
        chunk = imgs[s:s + PER]
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * T, rows * (T + 26)), (60, 0, 60))
        d = ImageDraw.Draw(sheet)
        for i, e in enumerate(chunk):
            try:
                im = Image.fromarray(image_of(root, e), "RGBA")
            except Exception:
                continue
            im.thumbnail((T, T))
            bg = Image.new("RGBA", im.size, (90, 90, 90, 255))
            bg.alpha_composite(im)
            x, y = (i % COLS) * T, (i // COLS) * (T + 26)
            sheet.paste(bg.convert("RGB"), (x, y))
            d.text((x + 2, y + T), f"{e['cat']} {e['name']}", fill=(255, 255, 0))
            d.text((x + 2, y + T + 12), f"{e['file'][-14:]} {e['w']}x{e['h']}", fill=(180, 180, 180))
        sheet.save(out / f"sheet_{s // PER:04d}.png")
    print(f"{len(imgs)} images, {(len(imgs) + PER - 1) // PER} sheets -> {out}")
    return 0


def cmd_export(a) -> int:
    from PIL import Image
    c = load_catalogue()
    root = Path(c["root"])
    out = asset_dir() / "images"
    out.mkdir(exist_ok=True)
    n = 0
    for e in c["images"]:
        key = f"{e['cat']} {e['name']}"
        if (a.re and any(re.search(p, key) for p in a.names)) or key in a.names or e["name"] in a.names:
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{e['cat']}_{e['name']}")
            Image.fromarray(image_of(root, e), "RGBA").save(out / f"{safe}.png")
            n += 1
    print(f"{n} images -> {out}")
    return 0


# The pieces the layer's interface uses, by role: (file, category, name) in your installation.
# The window themes are FFXI's selectable window types, one file each.
UI_PIECES = {
    "pointer": ("ROM/0/1.DAT", "menu", "yubi"),       # the glove
    "gauge": ("ROM/0/1.DAT", "menu", "gauge"),
    "compass": ("ROM/0/1.DAT", "menu", "compass"),
    "font_small": ("ROM/0/1.DAT", "font", "font"),
    "font_dg": ("ROM/0/1.DAT", "other", "dg_font"),
}
for _t, _f in enumerate(range(14, 20), 1):
    for _role, _name in (("bg", "newtex"), ("corner", "corner"), ("hfr", "hfr1"), ("vfr", "vfr1")):
        UI_PIECES[f"theme{_t}_{_role}"] = (f"ROM/0/{_f}.DAT", "menu", _name)


def cmd_ui(a) -> int:
    """The interface's pieces, by role, to the local asset folder's ui/: what ffxi_ui.py loads."""
    from PIL import Image
    c = load_catalogue()
    root = Path(c["root"])
    out = asset_dir() / "ui"
    out.mkdir(exist_ok=True)
    found = 0
    for role, (f, cat, name) in UI_PIECES.items():
        e = next((i for i in c["images"] if i["file"] == f and i["cat"] == cat and i["name"] == name), None)
        if not e:
            print(f"  missing: {role} ({f} {cat} {name})")
            continue
        Image.fromarray(image_of(root, e), "RGBA").save(out / f"{role}.png")
        found += 1
    print(f"{found} of {len(UI_PIECES)} interface pieces -> {out}")
    return 0 if found else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("ffxi", help="the FINAL FANTASY XI folder of your installation (it holds ROM)")
    sh = sub.add_parser("sheets")
    sh.add_argument("--match", help="only images whose file, category or name match this regex")
    e = sub.add_parser("export")
    e.add_argument("names", nargs="+")
    e.add_argument("--re", action="store_true", help="the names are regexes")
    sub.add_parser("ui", help="the interface's pieces, for the layer")
    a = ap.parse_args()
    return {"scan": cmd_scan, "sheets": cmd_sheets, "export": cmd_export, "ui": cmd_ui}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
