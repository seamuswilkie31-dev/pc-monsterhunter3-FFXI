# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26"]
# ///
"""FFXI's equipment from your own installation: every armor piece and weapon, its level, jobs, races,
slots, stats and look, for the layer's inventory, equipment and monsters' drops.

    uv run ffxi_items.py build            # read them and write the local asset folder's items.json
    uv run ffxi_items.py show "Leather Vest"

What's read stays in the local asset folder (items.json), never in the repository.

The item files (the community's reading, written here from that description, no code taken): blocks of
0xC00 bytes (0x1400 since the September 2026 update), each byte rotated left 3 bits; a block's header
(id u32 at 0, then flags (u16 at 4, u32 since the update), stack, type, resource; for equipment level, slots, races, jobs, as the format's
offsets put them), and a string table found by its shape (a u32 count of 1-8, then count x (offset, flag);
a text's bytes 0x1C past its offset): the name, then the description, whose prose states the stats
("DEF:10 HP+15 AGI+2"). Armor: ROM/118/109.DAT and ROM/286/73.DAT; weapons: ROM/118/108.DAT.
The look each piece shows (its model number in its slot's gear table) isn't in those files: LandSandBoat's
item_equipment table has it (MId), fetched once into the asset folder too.
Each block holds the item's icon too (icon): at 0x280 its size (u32), then an image as the DATs' are (a flag
0x91, 16 letters naming it, a BITMAPINFOHEADER: 32 x 32, 8 bits), a palette of 256 BGRA (FFXI's alpha, 128
opaque), the 32 x 32 indices, rows from the bottom; cut out the first time one is shown, kept as a PNG.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ffxi_assets as A  # noqa: E402

ARMOR_FILES = ("ROM/118/109.DAT", "ROM/286/73.DAT")
WEAPON_FILES = ("ROM/118/108.DAT",)
LSB_EQUIPMENT = "https://raw.githubusercontent.com/LandSandBoat/server/base/sql/item_equipment.sql"
# the header's offsets: the old format, and the one since the September 2026 update
OFFSETS = {
    0xC00: {"type": 0x08, "level": 0x0E, "slots": 0x10, "races": 0x12, "jobs": 0x14, "damage": 0x1C, "delay": 0x1E,
            "skill": 0x22, "jobs_u32": True},
    0x1400: {"type": 0x0A, "level": 0x10, "slots": 0x12, "races": 0x14, "jobs": 0x18, "damage": 0x20, "delay": 0x22,
             "skill": 0x26, "jobs_u32": True},
}
# the equipment slots' bits, as the item files and LandSandBoat write them
SLOT_BITS = {"main": 0x0001, "sub": 0x0002, "ranged": 0x0004, "ammo": 0x0008, "head": 0x0010, "body": 0x0020,
             "hands": 0x0040, "legs": 0x0080, "feet": 0x0100, "neck": 0x0200, "waist": 0x0400, "ear1": 0x0800,
             "ear2": 0x1000, "ring1": 0x2000, "ring2": 0x4000, "back": 0x8000}
# the item flags that make a piece special, not one a monster drops at random: Rare, Ex, not for the auction
# house, and 0x0002 (only the GMs' Judge's set has it)
SPECIAL_FLAGS = 0x8000 | 0x4000 | 0x0040 | 0x0002
# the jobs' bits (bit 1 WAR ... as FFXI orders them)
JOB_ORDER = ["WAR", "MNK", "WHM", "BLM", "RDM", "THF", "PLD", "DRK", "BST", "BRD", "RNG", "SAM", "NIN", "DRG", "SMN",
             "BLU", "COR", "PUP", "DNC", "SCH", "GEO", "RUN"]
# the races' bits
RACE_BITS = {"hume_male": 0x02, "hume_female": 0x04, "elvaan_male": 0x08, "elvaan_female": 0x10,
             "tarutaru_male": 0x20, "tarutaru_female": 0x40, "mithra": 0x80, "galka": 0x100}
STAT_RE = re.compile(r'("[^"]+"|[A-Za-z][A-Za-z.\' ]{0,28}?)\s*([:+-])\s*([+-]?)(\d+)(%?)')
KNOWN_STATS = {"def": "DEF", "dmg": "DMG", "delay": "Delay", "hp": "HP", "mp": "MP", "str": "STR", "dex": "DEX",
               "vit": "VIT", "agi": "AGI", "int": "INT", "mnd": "MND", "chr": "CHR", "accuracy": "Accuracy",
               "attack": "Attack", "evasion": "Evasion", "magic accuracy": "Magic Accuracy",
               "magic atk. bonus": "Magic Atk. Bonus", "enmity": "Enmity", "haste": "Haste",
               "ranged accuracy": "Ranged Accuracy", "ranged attack": "Ranged Attack", "magic def. bonus": "Magic Def. Bonus",
               "store tp": "Store TP", "regen": "Regen", "refresh": "Refresh"}


def rotate(block: bytes) -> bytes:
    a = np.frombuffer(block, np.uint8)
    return ((a << 3) | (a >> 5)).astype(np.uint8).tobytes()


def stride_of(b: bytes) -> int:
    for s in (0x1400, 0xC00):
        if len(b) % s == 0 and len(b) >= s:
            # the right stride gives ids counting up block by block
            ids = [struct.unpack_from("<I", rotate(b[i * s:i * s + 4]))[0] for i in range(min(8, len(b) // s))]
            if all(ids[i + 1] == ids[i] + 1 for i in range(len(ids) - 1)):
                return s
    return 0xC00


def strings_of(blk: bytes) -> list:
    for off in range(0x08, 0x82, 2):
        n = struct.unpack_from("<I", blk, off)[0]
        if not 1 <= n <= 8:
            continue
        first = struct.unpack_from("<I", blk, off + 4)[0]
        if first != 4 + n * 8:
            continue
        entries = [struct.unpack_from("<II", blk, off + 4 + 8 * i) for i in range(n)]
        if any(o < first or f > 1 or off + o + 4 > 0x280 for o, f in entries):
            continue
        out = []
        for o, f in entries:
            if f == 0:
                raw = blk[off + o + 0x1C:]
                raw = raw[:raw.find(b"\0")] if b"\0" in raw else raw[:256]
                out.append(text_of(raw))
            else:
                out.append(struct.unpack_from("<I", blk, off + o)[0])
        return out
    return []


def text_of(raw: bytes) -> str:
    """Item text: Shift-JIS, its line breaks kept; FFXI's own glyph codes (element icons) dropped."""
    raw = re.sub(rb"[\xef\xfd].", b"", raw)
    return raw.decode("cp932", "replace").replace("\r", "")


def parse_stats(desc: str) -> dict:
    stats = {}
    for m in STAT_RE.finditer(desc.replace("\n", " ")):
        name = m.group(1).strip().strip('"').lower()
        sign = -1 if (m.group(2) == "-" or m.group(3) == "-") else 1
        key = KNOWN_STATS.get(name)
        if key and key not in stats:
            stats[key] = sign * int(m.group(4)) * (1 if not m.group(5) else 1)
    return stats


_ICON_FILES: list = []                  # (first id, count, stride, path) of each item file, once read


def icon(iid: int, root: Path | None = None):
    """The item's icon (a 32 x 32 RGBA PIL image) from your installation, kept in the asset folder's icons;
    None if it can't be found."""
    from PIL import Image
    p = A.asset_dir() / "icons" / f"{int(iid)}.png"
    if p.exists():
        try:
            return Image.open(p).convert("RGBA")
        except OSError:
            pass
    try:
        if not _ICON_FILES:
            import ffxi_model as M
            base = M.ffxi_root(None) if root is None else Path(root)
            for rel in WEAPON_FILES + ARMOR_FILES:
                f = base / rel
                if f.exists():
                    with open(f, "rb") as fh:
                        head = fh.read(0x1400 * 8)
                    s = stride_of(head)
                    first = struct.unpack_from("<I", rotate(head[:4]))[0]
                    _ICON_FILES.append((first, f.stat().st_size // s, s, f))
        for first, count, s, f in _ICON_FILES:
            if first <= iid < first + count:
                with open(f, "rb") as fh:
                    fh.seek((iid - first) * s)
                    blk = rotate(fh.read(s))
                if struct.unpack_from("<I", blk, 0)[0] != iid:
                    return None
                at = blk.find(b"\x91", 0x280, 0x2A0)
                hdr = at + 17
                if at < 0 or struct.unpack_from("<IiiHH", blk, hdr) != (40, 32, 32, 1, 8):
                    return None
                pal = np.frombuffer(blk, np.uint8, 1024, hdr + 40).reshape(256, 4)
                px = np.frombuffer(blk, np.uint8, 32 * 32, hdr + 40 + 1024).reshape(32, 32)
                rgba = pal[px][::-1][..., [2, 1, 0, 3]].copy()
                rgba[..., 3] = np.minimum(255, rgba[..., 3].astype(np.int32) * 2).astype(np.uint8)
                img = Image.fromarray(rgba, "RGBA")
                p.parent.mkdir(parents=True, exist_ok=True)
                img.save(p)
                return img
    except (OSError, ValueError, struct.error):
        return None
    return None


def read_items(root: Path, files: tuple[str, ...], weapons: bool) -> dict[int, dict]:
    out = {}
    for rel in files:
        p = root / rel
        if not p.exists():
            continue
        b = p.read_bytes()
        s = stride_of(b)
        o = OFFSETS[s]
        for i in range(len(b) // s):
            blk = rotate(b[i * s:(i + 1) * s])
            iid = struct.unpack_from("<I", blk, 0)[0]
            strs = strings_of(blk)
            if not strs or not isinstance(strs[0], str) or not strs[0].strip(". "):
                continue
            desc = next((x for x in reversed(strs) if isinstance(x, str) and x != strs[0]), "")
            u16 = lambda k: struct.unpack_from("<H", blk, o[k])[0]
            flags = struct.unpack_from("<I" if s == 0x1400 else "<H", blk, 0x04)[0]
            item = {"id": iid, "name": strs[0], "flags": flags, "level": u16("level"), "slots": u16("slots"), "races": u16("races"),
                    "jobs": struct.unpack_from("<I", blk, o["jobs"])[0], "desc": desc, "stats": parse_stats(desc)}
            if weapons:
                item["damage"], item["delay"], item["skill"] = u16("damage"), u16("delay"), blk[o["skill"]]
            out[iid] = item
    return out


def lsb_models(dest: Path) -> dict[int, int]:
    """Each piece's look (MId), from LandSandBoat's item_equipment table, fetched once."""
    sql = dest / "lsb_item_equipment.sql"
    if not sql.exists():
        with urllib.request.urlopen(LSB_EQUIPMENT, timeout=120) as r:
            data = r.read()
        sql.write_bytes(data)
    out = {}
    for m in re.finditer(r"INSERT INTO `item_equipment` VALUES \((\d+),'[^']*',\d+,\d+,\d+,(\d+),", sql.read_text("utf-8")):
        out[int(m.group(1))] = int(m.group(2))
    return out


def build(root: Path) -> Path:
    dest = A.asset_dir()
    dest.mkdir(parents=True, exist_ok=True)
    items = read_items(root, ARMOR_FILES, False)
    items.update(read_items(root, WEAPON_FILES, True))
    try:
        models = lsb_models(dest)
    except Exception as e:                                              # noqa: BLE001
        print(f"[the looks unavailable: {e}]")
        models = {}
    for iid, it in items.items():
        it["model"] = models.get(iid)
    out = dest / "items.json"
    out.write_text(json.dumps({str(k): v for k, v in items.items()}), encoding="utf-8")
    return out


def load_items() -> dict[int, dict]:
    p = A.asset_dir() / "items.json"
    try:
        return {int(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def job_list(bits: int) -> list[str]:
    return [j for i, j in enumerate(JOB_ORDER) if bits & (1 << (i + 1))]


def slot_names(bits: int) -> list[str]:
    return [s for s, bit in SLOT_BITS.items() if bits & bit]


def main(argv=None) -> int:
    import ffxi_model as M
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ffxi", help="the FINAL FANTASY XI folder (else the launcher's)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sh = sub.add_parser("show")
    sh.add_argument("name")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        out = build(M.ffxi_root(a.ffxi))
        items = load_items()
        print(f"{len(items)} pieces of equipment, {sum(1 for i in items.values() if i.get('model') is not None)} "
              f"with their look -> {out}")
        return 0
    items = load_items()
    for it in items.values():
        if a.name.lower() in it["name"].lower():
            print(f"{it['id']} {it['name']}: Lv.{it['level']} {'/'.join(job_list(it['jobs']))} "
                  f"{','.join(slot_names(it['slots']))} look {it.get('model')} {it['stats']}\n   {it['desc']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
