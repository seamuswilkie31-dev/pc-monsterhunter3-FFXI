"""Monster Hunter Tri's own words, read from your extracted disc (the port's build/extract), never kept in
the repository.

monster_names: the game's names for its monsters by their number, the one their models carry (em027:
the Aptonoth). They are a list in files/00/data00_eng.bin, after "NO_DATA" (number 0), each a NUL-ended
string, padded with NULs, up to the monster classes' short names (BrdWyv, FlyWyv...).
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTRACT = HERE.parent / "build" / "extract"


def monster_names(extract: Path | None = None, language: str = "eng") -> dict[int, str]:
    p = Path(extract or EXTRACT) / "files" / "00" / f"data00_{language}.bin"
    try:
        b = p.read_bytes()
    except OSError:
        return {}
    at = b.find(b"NO_DATA\0")
    if at < 0:
        return {}
    names, i = [], at
    while len(names) < 128:
        end = b.find(b"\0", i)
        if end < 0:
            break
        s = b[i:end].decode("latin-1")
        i = end + 1
        if not s:
            continue
        if s == "BrdWyv":                                  # the classes' short names: the list is over
            break
        names.append(s)
    return {n: s for n, s in enumerate(names) if n}


def model_number(name: str) -> int | None:
    """em027 -> 27"""
    if not name.startswith("em"):
        return None
    digits = "".join(c for c in name[2:5] if c.isdigit())
    return int(digits) if digits else None


if __name__ == "__main__":
    for n, s in monster_names().items():
        print(f"em{n:03d} {s}")
