"""The FFXI interface drawn for the game's window: windows in FFXI's classic manner, as one RGBA image.

Our own drawing in the style of FFXI's interface (dark navy translucent windows, a thin light border,
white outlined text); nothing is taken from FFXI's files. The image is 1280x720; the port stretches it
over the game's picture (port_overlay.py).

    status window (bottom right)   name, job and levels, HP and MP bars, TP, EXP
    effects (left of it)           your buffs, your pet, the skillchain and magic burst windows, timed
    target window (above it)       the monster: /check colour, HP in percent, its debuffs
    command menu (above that)      Weapon Skills, Job Abilities, Magic, Items, Job: one level beside another
    combat log (bottom left)       the layer's messages, coloured by kind
    casting bar (bottom centre)    the spell being cast
    macro bar (top right)          the keys and what they do, recasts
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720

# the classic palette
BG_TOP, BG_BOTTOM = (18, 24, 62), (8, 12, 34)
BG_ALPHA = 200
BORDER, BORDER_IN = (214, 220, 236), (64, 78, 128)
WHITE, DIM, SHADOW = (255, 255, 255), (150, 158, 184), (0, 0, 0)
HP_COL = [(1.0, (238, 240, 250)), (0.75, (246, 232, 120)), (0.5, (250, 170, 80)), (0.25, (240, 90, 80))]
MP_BAR, TP_READY, TP_LOW = (122, 168, 255), (160, 236, 140), (190, 200, 220)
EXP_BAR = (230, 200, 110)
KIND = {"ws": (255, 255, 255), "damage": (255, 255, 255), "magic": (170, 205, 255), "heal": (150, 240, 160),
        "skillchain": (255, 226, 120), "burst": (255, 200, 90), "warn": (250, 150, 140), "info": (210, 214, 230)}
UP, DOWN = "\u25b2", "\u25bc"


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    names = ["arialbd.ttf", "segoeuib.ttf", "tahomabd.ttf"] if bold else ["arial.ttf", "segoeui.ttf", "tahoma.ttf"]
    for n in names:
        for d in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            if (d / n).exists():
                return ImageFont.truetype(str(d / n), size)
    return ImageFont.load_default(size)


@dataclass
class UiState:
    """What the interface shows; everything a frame depends on."""
    name: str = "Adventurer"
    job: str = ""                         # "RDM40/WHM20"
    hp: int | None = None
    max_hp: int = 0
    mp: int = 0
    max_mp: int = 0
    tp: int = 0
    exp: int = 0
    tnl: int = 0                          # 0: at the level cap
    effects: tuple = ()                   # ((name, seconds left or None), ...): buffs, pet, chain windows
    target_pct: float | None = None       # 0..1, None: no target
    target_name: str = "Target"
    target_check: tuple | None = None     # (label, colour): /check
    target_effects: tuple = ()            # the monster's debuffs
    casting: str | None = None
    cast_frac: float = 0.0
    log: tuple = ()                       # ((text, kind), ...), oldest first
    palette: tuple = ()                   # ((key, name, usable, detail), ...)
    menu: tuple = ()                      # per level: (title, ((label, usable, detail), ...), cursor, more above, more below)
    nameplate: tuple | None = None        # where your head is in the picture (-1..1, y up): your name above it
    status: str = ""


class FfxiUi:
    def __init__(self):
        self.f_name = font(17)
        self.f_val = font(15)
        self.f_small = font(13)
        self.f_log = font(15)
        self._last: UiState | None = None

    def render(self, s: UiState) -> Image.Image | None:
        """The interface for s; None if it looks as the last one did."""
        if s == self._last:
            return None
        self._last = s
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if s.nameplate:
            self._nameplate(d, s)
        self._status(img, d, s)
        if s.effects:
            self._effects(img, d, s)
        if s.target_pct is not None:
            self._target(img, d, s)
        if s.log:
            self._log(img, d, s)
        if s.casting:
            self._cast(img, d, s)
        if s.palette:
            self._palette(img, d, s)
        if s.menu:
            self._menu(img, d, s)
        if s.status:
            self._text(d, (W - 14, 10), s.status, self.f_small, DIM, anchor="ra")
        return img

    # --- pieces -----------------------------------------------------------------------------------
    def window(self, img: Image.Image, box: tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        grad = Image.new("RGBA", (w, h))
        gd = ImageDraw.Draw(grad)
        for y in range(h):
            t = y / max(1, h - 1)
            c = tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
            gd.line([(0, y), (w, y)], fill=(*c, BG_ALPHA))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=5, fill=255)
        grad.putalpha(Image.composite(grad.getchannel("A"), mask, mask))
        img.alpha_composite(grad, (x0, y0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([x0, y0, x1 - 1, y1 - 1], radius=5, outline=(*BORDER, 235), width=1)
        d.rounded_rectangle([x0 + 2, y0 + 2, x1 - 3, y1 - 3], radius=4, outline=(*BORDER_IN, 200), width=1)

    def _text(self, d: ImageDraw.ImageDraw, xy, text: str, f, col, anchor="la") -> None:
        d.text(xy, text, font=f, fill=(*col, 255), stroke_width=2, stroke_fill=(*SHADOW, 220), anchor=anchor)

    def _bar(self, d: ImageDraw.ImageDraw, x: int, y: int, w: int, frac: float, col, h: int = 5) -> None:
        frac = max(0.0, min(1.0, frac))
        d.rectangle([x, y, x + w, y + h], fill=(10, 14, 30, 230), outline=(70, 82, 120, 255))
        if frac > 0:
            fx = x + 1 + int((w - 2) * frac)
            d.rectangle([x + 1, y + 1, fx, y + h - 1], fill=(*col, 255))
            d.line([(x + 1, y + 1), (fx, y + 1)], fill=(*[min(255, c + 50) for c in col], 255))

    def _nameplate(self, d, s: UiState) -> None:
        """Your name above your head, as FFXI shows it: white, outlined, centred."""
        x = (s.nameplate[0] + 1) / 2 * W
        y = (1 - s.nameplate[1]) / 2 * H
        if 0 <= x < W and 0 <= y < H:
            self._text(d, (int(x), int(y)), s.name, self.f_name, WHITE, anchor="ms")

    def _status(self, img, d, s: UiState) -> None:
        x0, y0, x1, y1 = W - 300, H - 98, W - 12, H - 12
        self.window(img, (x0, y0, x1, y1))
        self._text(d, (x0 + 12, y0 + 8), s.name, self.f_name, WHITE)
        if s.job:
            self._text(d, (x0 + 18 + d.textlength(s.name, font=self.f_name), y0 + 11), s.job, self.f_small, DIM)
        tp_col = TP_READY if s.tp >= 1000 else TP_LOW
        self._text(d, (x1 - 12, y0 + 9), f"TP {s.tp}", self.f_val, tp_col, anchor="ra")
        y = y0 + 36                                    # HP and MP: the bar, its value right of it
        if s.hp is not None and s.max_hp:
            frac = s.hp / s.max_hp
            col = next(c for lim, c in reversed(HP_COL) if frac <= lim) if frac < 1 else HP_COL[0][1]
            self._text(d, (x0 + 12, y - 4), "HP", self.f_small, DIM)
            self._bar(d, x0 + 40, y, 160, frac, col)
            self._text(d, (x1 - 12, y - 5), str(s.hp), self.f_val, col, anchor="ra")
        y += 18
        if s.max_mp:
            self._text(d, (x0 + 12, y - 4), "MP", self.f_small, DIM)
            self._bar(d, x0 + 40, y, 160, s.mp / s.max_mp, MP_BAR)
            self._text(d, (x1 - 12, y - 5), str(s.mp), self.f_val, MP_BAR, anchor="ra")
        y += 18
        if s.tnl:                                      # EXP: a thin bar, the numbers small
            self._text(d, (x0 + 12, y - 4), "EXP", self.f_small, DIM)
            self._bar(d, x0 + 46, y + 1, 154, s.exp / s.tnl, EXP_BAR, h=3)
            self._text(d, (x1 - 12, y - 5), f"{s.exp}/{s.tnl}", self.f_small, DIM, anchor="ra")
        else:
            self._text(d, (x0 + 12, y - 4), "Level cap", self.f_small, DIM)

    def _effects(self, img, d, s: UiState) -> None:
        """Your effects, FFXI's buff icons as words: left of the status window."""
        rows = s.effects
        x1, y1 = W - 308, H - 12
        x0 = x1 - 170
        y0 = y1 - 14 - 18 * len(rows)
        self.window(img, (x0, y0, x1, y1))
        for i, (name, left) in enumerate(rows):
            y = y0 + 7 + 18 * i
            self._text(d, (x0 + 10, y), name, self.f_small, KIND["heal"])
            if left is not None:
                t = f"{left // 60}:{left % 60:02d}" if left >= 60 else f"{left}s"
                self._text(d, (x1 - 10, y), t, self.f_small, DIM, anchor="ra")

    def _target(self, img, d, s: UiState) -> None:
        x0, y0, x1, y1 = W - 300, H - 152, W - 12, H - 106
        self.window(img, (x0, y0, x1, y1))
        col = s.target_check[1] if s.target_check else (250, 150, 140)
        self._text(d, (x0 + 12, y0 + 7), s.target_name, self.f_name, col)
        self._bar(d, x0 + 150, y0 + 14, 80, s.target_pct, (240, 110, 100))
        self._text(d, (x1 - 12, y0 + 8), f"{int(round(s.target_pct * 100))}%", self.f_val, WHITE, anchor="ra")
        line = " ".join(([s.target_check[0]] if s.target_check else []) + list(s.target_effects))
        if line:
            self._text(d, (x0 + 12, y0 + 27), line[:44], self.f_small, DIM)

    def _log(self, img, d, s: UiState) -> None:
        lines = s.log[-7:]
        x0, y1 = 12, H - 12
        y0 = y1 - 16 - 20 * len(lines)
        self.window(img, (x0, y0, x0 + 600, y1))
        for i, (text, kind) in enumerate(lines):
            self._text(d, (x0 + 12, y0 + 9 + 20 * i), text, self.f_log, KIND.get(kind, WHITE))

    def _cast(self, img, d, s: UiState) -> None:
        x0, y0 = W // 2 - 130, H - 200
        self.window(img, (x0, y0, x0 + 260, y0 + 40))
        self._text(d, (x0 + 12, y0 + 7), s.casting, self.f_val, KIND["magic"])
        self._bar(d, x0 + 12, y0 + 27, 236, s.cast_frac, MP_BAR)

    def _menu(self, img, d, s: UiState) -> None:
        """The command menu above the target window: the first level rightmost, each deeper one left of it."""
        x1, y1 = W - 12, H - 160
        for title, rows, cursor, up, down in s.menu:
            label_w = max([int(d.textlength(t, font=self.f_val)) for t, _, _ in rows]
                          + [int(d.textlength(title, font=self.f_small))])
            detail_w = max([int(d.textlength(x, font=self.f_small)) for _, _, x in rows if x] + [0])
            width = 44 + label_w + (detail_w + 16 if detail_w else 0) + (12 if up or down else 0)
            x0 = x1 - width
            y0 = y1 - (34 + 22 * max(1, len(rows)))
            self.window(img, (x0, y0, x1, y1))
            self._text(d, (x0 + 12, y0 + 8), title, self.f_small, DIM)
            if up:
                self._text(d, (x1 - 10, y0 + 8), UP, self.f_small, DIM, anchor="ra")
            if down:
                self._text(d, (x1 - 10, y1 - 20), DOWN, self.f_small, DIM, anchor="ra")
            for i, (label, ok, detail) in enumerate(rows):
                y = y0 + 28 + 22 * i
                if i == cursor:
                    d.rectangle([x0 + 6, y - 2, x1 - 7, y + 18], fill=(70, 90, 160, 150))
                    _menu_pointer(d, x0 + 10, y + 3)
                self._text(d, (x0 + 28, y), label, self.f_val, WHITE if ok else DIM)
                if detail:
                    self._text(d, (x1 - (24 if up or down else 12), y + 2), detail, self.f_small,
                               KIND["magic"] if ok else DIM, anchor="ra")
            x1 = x0 - 6

    def _palette(self, img, d, s: UiState) -> None:
        """The macro bar: your keys and what they do."""
        rows = s.palette
        x1, y0 = W - 12, 34
        x0 = x1 - 250
        self.window(img, (x0, y0, x1, y0 + 14 + 18 * len(rows)))
        for i, (key, name, ok, detail) in enumerate(rows):
            y = y0 + 8 + 18 * i
            self._text(d, (x0 + 12, y), key, self.f_small, KIND["skillchain"] if ok else DIM)
            self._text(d, (x0 + 40, y), name, self.f_small, WHITE if ok else DIM)
            if detail:
                self._text(d, (x1 - 10, y), detail, self.f_small, DIM, anchor="ra")


def _menu_pointer(d: ImageDraw.ImageDraw, x: int, y: int) -> None:
    """FFXI's pointer, drawn: a small white arrow."""
    d.polygon([(x, y), (x + 11, y + 6), (x, y + 12)], fill=(255, 255, 255, 255), outline=(40, 40, 60, 255))


def demo() -> UiState:
    return UiState(name="12345", job="RDM40/WHM20", hp=74, max_hp=100, mp=88, max_mp=110, tp=1240,
                   exp=2350, tnl=6800,
                   effects=(("Protect", 1712), ("Phalanx", 154), ("Enfire", 96), ("Haste", 61), ("MB Scission", 6)),
                   target_pct=0.63, target_name="Great Jaggi", target_check=("Tough", (255, 220, 120)),
                   target_effects=("Dia II", "Paralyze"),
                   casting="Fire II", cast_frac=0.55,
                   log=(("12345 uses Fast Blade.", "ws"),
                        ("The Great Jaggi takes 58 points of damage.", "damage"),
                        ("Skillchain: Scission. The Great Jaggi takes 29 points of damage.", "skillchain"),
                        ("12345 starts casting Fire II.", "magic"),
                        ("12345 gains 360 experience points.", "heal")),
                   palette=(("3", "Red Lotus Blade", True, ""), ("4", "Burning Blade", True, ""),
                            ("5", "Fast Blade", True, ""), ("6", "Savage Blade", False, ""),
                            ("7", "Cure IV", True, ""), ("8", "Haste", False, "12s"), ("9", "Fire II", True, ""),
                            ("0", "Refresh", True, "")),
                   menu=(("Command", (("Weapon Skills", True, ""), ("Job Abilities", True, ""), ("Magic", True, ""),
                                      ("Items", True, ""), ("Job", True, "RDM40/WHM20")), 2, False, False),
                         ("Magic", (("White Magic", True, ""), ("Black Magic", True, "")), 1, False, False),
                         ("Black Magic", (("Stone", True, "4 MP"), ("Water", True, "5 MP"), ("Aero", True, "6 MP"),
                                          ("Fire", True, "7 MP"), ("Blizzard", True, "8 MP"), ("Thunder", True, "9 MP"),
                                          ("Stone II", True, "16 MP"), ("Water II", False, "8s"),
                                          ("Aero II", True, "22 MP"), ("Fire II", True, "26 MP")), 9, False, True)))


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "ffxi_ui_demo.png"
    t = time.perf_counter()
    img = FfxiUi().render(demo())
    print(f"rendered in {1000 * (time.perf_counter() - t):.1f} ms")
    bg = Image.new("RGBA", (W, H), (88, 110, 70, 255))
    bg.alpha_composite(img)
    bg.save(out)
    print(out)
