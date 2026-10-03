# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26"]
# ///
"""Find addresses in the running port: snapshots of RAM through the bridge, compared.

The port must run with MH3_FFXI=1. A scan lives in a folder (build/scan by default): its snapshots, and
the candidates left after each filter.

    uv run memscan.py snap s0                 # RAM now -> build/scan/s0.bin
    uv run memscan.py filter s0 s1 dec --type u16 --range 1 60000
                                              # keep what decreased from s0 to s1 (the first filter
                                              # starts from every aligned address)
    uv run memscan.py filter s1 s2 same       # then what stayed the same
    uv run memscan.py list                    # the candidates, their value in every snapshot
    uv run memscan.py ptr 0x9012A2A4          # words pointing just below an address: a struct holding it
    uv run memscan.py reset                   # forget the candidates (keep the snapshots)

Relations: dec, inc, same, changed, eq=V, lt=V, gt=V (on the second snapshot's value).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np

import port_bridge as P

HERE = Path(__file__).resolve().parent
DEFAULT_DIR = HERE.parent / "build" / "scan"
TYPES = {"u8": ">u1", "u16": ">u2", "u32": ">u4", "s16": ">i2", "s32": ">i4", "f32": ">f4"}
SNAP_SIZE = P.MEM1_SIZE + P.MEM2_SIZE


def addr_of(off: int) -> int:
    return P.MEM1 + off if off < P.MEM1_SIZE else P.MEM2 + off - P.MEM1_SIZE


def off_of(addr: int) -> int:
    if P.MEM1 <= addr < P.MEM1 + P.MEM1_SIZE:
        return addr - P.MEM1
    if P.MEM2 <= addr < P.MEM2 + P.MEM2_SIZE:
        return addr - P.MEM2 + P.MEM1_SIZE
    raise ValueError(f"{addr:#x} is not in RAM")


class Scan:
    def __init__(self, folder: Path):
        self.dir = folder.resolve()  # the port writes snapshots from its own working folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"snaps": []}

    def save(self):
        self.state_path.write_text(json.dumps(self.state, indent=1))

    def snap_path(self, name: str) -> Path:
        return self.dir / f"{name}.bin"

    def load(self, name: str, dtype: str) -> np.ndarray:
        p = self.snap_path(name)
        if not p.exists():
            sys.exit(f"no snapshot {name} in {self.dir}")
        return np.memmap(p, dtype=dtype, mode="r")

    @property
    def cand_path(self) -> Path:
        return self.dir / "candidates.npy"


def cmd_snap(scan: Scan, a) -> int:
    be = P.PortBackend({})
    if not be.connected():
        import time
        time.sleep(0.5)
        if not be.connected():
            sys.exit("the port isn't running with MH3_FFXI=1")
    path = scan.snap_path(a.name)
    if not be.snapshot(str(path)):
        sys.exit("the port didn't write the snapshot (see its log)")
    if a.name not in scan.state["snaps"]:
        scan.state["snaps"].append(a.name)
    scan.save()
    print(f"snapshot {a.name}: {path.stat().st_size // (1 << 20)} MB")
    return 0


def relation(rel: str, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    if rel == "dec":
        return y < x
    if rel == "inc":
        return y > x
    if rel == "same":
        return y == x
    if rel == "changed":
        return y != x
    op, _, v = rel.partition("=")
    v = float(v, ) if "." in v else int(v, 0)
    if op == "eq":
        return y == v
    if op == "lt":
        return y < v
    if op == "gt":
        return y > v
    sys.exit(f"unknown relation {rel}")


def cmd_filter(scan: Scan, a) -> int:
    st = scan.state
    if scan.cand_path.exists():
        t = st["type"]
        if a.type and a.type != t:
            sys.exit(f"the scan is of {t}; reset to change the type")
    else:
        t = a.type or "u16"
    dt = TYPES[t]
    size = np.dtype(dt).itemsize
    x, y = scan.load(a.a, dt), scan.load(a.b, dt)
    if scan.cand_path.exists():
        idx = np.load(scan.cand_path)            # element indices
        xs, ys = x[idx], y[idx]
        keep = relation(a.rel, xs, ys)
    else:
        idx = None
        xs, ys = x, y
        keep = relation(a.rel, xs, ys)
    if a.range:
        lo, hi = a.range
        keep &= (ys >= lo) & (ys <= hi)
    if t == "f32":
        keep &= np.isfinite(ys)
    new = np.nonzero(keep)[0] if idx is None else idx[keep]
    np.save(scan.cand_path, new.astype(np.int64))
    st["type"] = t
    st.setdefault("history", []).append(f"{a.a} -> {a.b} {a.rel}" + (f" in {a.range}" if a.range else ""))
    scan.save()
    print(f"{len(new)} candidates ({t}, {size} bytes)")
    if len(new) <= a.show:
        show(scan, new, t)
    return 0


def show(scan: Scan, idx: np.ndarray, t: str, limit: int | None = None) -> None:
    dt = TYPES[t]
    size = np.dtype(dt).itemsize
    snaps = scan.state["snaps"]
    cols = [scan.load(s, dt) for s in snaps]
    print("address     " + "".join(f"{s:>10}" for s in snaps))
    for i in idx[:limit]:
        vals = "".join(f"{(round(float(c[i]), 2) if t == 'f32' else int(c[i])):>10}" for c in cols)
        print(f"{addr_of(int(i) * size):#010x}  {vals}")


def cmd_list(scan: Scan, a) -> int:
    if not scan.cand_path.exists():
        sys.exit("no candidates yet: filter first")
    idx = np.load(scan.cand_path)
    print(f"{len(idx)} candidates ({scan.state['type']}); " + " | ".join(scan.state.get("history", [])))
    show(scan, idx, scan.state["type"], a.limit)
    return 0


def dol_ranges(dol: Path) -> list[tuple[int, int, str]]:
    """The executable's sections: where its static data lives."""
    h = dol.read_bytes()[:0x100]
    offs = struct.unpack(">18I", h[0:0x48])
    addrs = struct.unpack(">18I", h[0x48:0x90])
    sizes = struct.unpack(">18I", h[0x90:0xD8])
    out = [(addrs[i], sizes[i], f"text{i}" if i < 7 else f"data{i - 7}") for i in range(18) if sizes[i]]
    bss_addr, bss_size = struct.unpack(">2I", h[0xD8:0xE0])
    out.append((bss_addr, bss_size, "bss"))
    return out


def cmd_ptr(scan: Scan, a) -> int:
    target = int(a.addr, 0)
    name = a.snap or scan.state["snaps"][-1]
    words = scan.load(name, ">u4")
    lo = target - a.window
    hits = np.nonzero((words >= lo) & (words <= target))[0]
    dol = HERE.parent / "build" / "extract" / "sys" / "main.dol"
    ranges = dol_ranges(dol) if dol.exists() else []

    def where(addr: int) -> str:
        # bss overlaps data sections in some executables: name the narrowest
        best = None
        for s, n, label in ranges:
            if s <= addr < s + n and (best is None or n < best[0]):
                best = (n, label)
        return best[1] if best else ""

    print(f"{len(hits)} words in {name} pointing into [{lo:#x}, {target:#x}]")
    rows = sorted(((addr_of(int(i) * 4), int(words[i])) for i in hits), key=lambda r: (where(r[0]) == "", r[0]))
    for at, v in rows[:a.limit]:
        print(f"{at:#010x} -> {v:#010x}  offset {target - v:#x}  {where(at)}")
    return 0


def cmd_reset(scan: Scan, a) -> int:
    if scan.cand_path.exists():
        scan.cand_path.unlink()
    scan.state.pop("type", None)
    scan.state.pop("history", None)
    scan.save()
    print("candidates cleared")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snap")
    s.add_argument("name")
    f = sub.add_parser("filter")
    f.add_argument("a")
    f.add_argument("b")
    f.add_argument("rel")
    f.add_argument("--type", choices=list(TYPES))
    f.add_argument("--range", nargs=2, type=lambda v: float(v) if "." in v else int(v, 0))
    f.add_argument("--show", type=int, default=40, help="list the candidates when this few are left")
    li = sub.add_parser("list")
    li.add_argument("--limit", type=int, default=60)
    pt = sub.add_parser("ptr")
    pt.add_argument("addr")
    pt.add_argument("--window", type=lambda v: int(v, 0), default=0x2000)
    pt.add_argument("--snap")
    pt.add_argument("--limit", type=int, default=40)
    sub.add_parser("reset")
    a = ap.parse_args()
    scan = Scan(a.dir)
    return {"snap": cmd_snap, "filter": cmd_filter, "list": cmd_list, "ptr": cmd_ptr, "reset": cmd_reset}[a.cmd](scan, a)


if __name__ == "__main__":
    raise SystemExit(main())
