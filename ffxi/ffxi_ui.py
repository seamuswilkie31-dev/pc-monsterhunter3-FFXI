"""The FFXI interface drawn for the game's window: windows in FFXI's classic manner, as one RGBA image.

Our own drawing in the style of FFXI's interface (dark navy translucent windows, a thin light border,
white outlined text); nothing is taken from FFXI's files. The image is 1280x720; the port stretches it
over the game's picture (port_overlay.py).

    status window (bottom right)   name, HP and MP bars with their values, TP, effects
    target window (above it)       the monster's HP, in percent
    combat log (bottom left)       the layer's messages, coloured by kind
    casting bar (bottom centre)    the spell being cast
    macro palette (top right)      the keys and what they do
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
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
KIND = {"ws": (255, 255, 255), "damage": (255, 255, 255), "magic": (170, 205, 255), "heal": (150, 240, 160),
        "skillchain": (255, 226, 120), "burst": (255, 200, 90), "warn": (250, 150, 140), "info": (210, 214, 230)}


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
    job: str = ""
    hp: int | None = None
    max_hp: int = 0
    mp: int = 0
    max_mp: int = 0
    tp: int = 0
    effects: tuple = ()
    target_pct: float | None = None       # 0..1, None: no target
    target_name: str = "Target"
    casting: str | None = None
    cast_frac: float = 0.0
    log: tuple = ()                       # ((text, kind), ...), oldest first
    palette: tuple = ()                   # ((key, name, usable), ...)
    status: str = ""


class FfxiUi:
    def __init__(self):
        self.f_name = font(17)
        self.f_val = font(15)
        self.f_small = font(13)
        self.f_log = font(15)
        self._last: UiState | None = None
        self._img: Image.Image | None = None

    def render(self, s: UiState) -> Image.Image | None:
        """The interface for s; None if it looks as the last one did."""
        if s == self._last:
            return None
        self._last = s
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        self._status(img, d, s)
        if s.target_pct is not None:
            self._target(img, d, s)
        if s.log:
            self._log(img, d, s)
        if s.casting:
            self._cast(img, d, s)
        if s.palette:
            self._palette(img, d, s)
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

    def _bar(self, d: ImageDraw.ImageDraw, x: int, y: int, w: int, frac: float, col) -> None:
        frac = max(0.0, min(1.0, frac))
        d.rectangle([x, y, x + w, y + 5], fill=(10, 14, 30, 230), outline=(70, 82, 120, 255))
        if frac > 0:
            fx = x + 1 + int((w - 2) * frac)
            d.rectangle([x + 1, y + 1, fx, y + 4], fill=(*col, 255))
            d.line([(x + 1, y + 1), (fx, y + 1)], fill=(*[min(255, c + 50) for c in col], 255))

    def _status(self, img, d, s: UiState) -> None:
        x0, y0, x1, y1 = W - 300, H - 82, W - 12, H - 12
        self.window(img, (x0, y0, x1, y1))
        self._text(d, (x0 + 12, y0 + 9), s.name, self.f_name, WHITE)
        tp_col = TP_READY if s.tp >= 1000 else TP_LOW
        self._text(d, (x1 - 12, y0 + 10), f"TP {s.tp}", self.f_val, tp_col, anchor="ra")
        if s.effects:
            self._text(d, (x0 + 14 + d.textlength(s.name, font=self.f_name) + 10, y0 + 12),
                       " ".join(s.effects), self.f_small, KIND["skillchain"])
        # HP and MP: the bar, its value right of it
        y = y0 + 38
        if s.hp is not None and s.max_hp:
            frac = s.hp / s.max_hp
            col = next(c for lim, c in reversed(HP_COL) if frac <= lim) if frac < 1 else HP_COL[0][1]
            self._text(d, (x0 + 12, y - 4), "HP", self.f_small, DIM)
            self._bar(d, x0 + 40, y, 150, frac, col)
            self._text(d, (x1 - 12, y - 5), str(s.hp), self.f_val, col, anchor="ra")
        if s.max_mp:
            y += 18
            self._text(d, (x0 + 12, y - 4), "MP", self.f_small, DIM)
            self._bar(d, x0 + 40, y, 150, s.mp / s.max_mp, MP_BAR)
            self._text(d, (x1 - 12, y - 5), str(s.mp), self.f_val, MP_BAR, anchor="ra")

    def _target(self, img, d, s: UiState) -> None:
        x0, y0, x1, y1 = W - 300, H - 128, W - 12, H - 90
        self.window(img, (x0, y0, x1, y1))
        self._text(d, (x0 + 12, y0 + 9), s.target_name, self.f_name, (250, 150, 140))
        self._bar(d, x0 + 130, y0 + 16, 100, s.target_pct, (240, 110, 100))
        self._text(d, (x1 - 12, y0 + 10), f"{int(round(s.target_pct * 100))}%", self.f_val, WHITE, anchor="ra")

    def _log(self, img, d, s: UiState) -> None:
        lines = s.log[-7:]
        x0, y1 = 12, H - 12
        y0 = y1 - 16 - 20 * len(lines)
        self.window(img, (x0, y0, x0 + 560, y1))
        for i, (text, kind) in enumerate(lines):
            self._text(d, (x0 + 12, y0 + 9 + 20 * i), text, self.f_log, KIND.get(kind, WHITE))

    def _cast(self, img, d, s: UiState) -> None:
        x0, y0 = W // 2 - 130, H - 200
        self.window(img, (x0, y0, x0 + 260, y0 + 40))
        self._text(d, (x0 + 12, y0 + 7), s.casting, self.f_val, KIND["magic"])
        self._bar(d, x0 + 12, y0 + 27, 236, s.cast_frac, MP_BAR)

    def _palette(self, img, d, s: UiState) -> None:
        rows = s.palette
        x1, y0 = W - 12, 34
        x0 = x1 - 230
        self.window(img, (x0, y0, x1, y0 + 14 + 18 * len(rows)))
        for i, (key, name, ok) in enumerate(rows):
            y = y0 + 8 + 18 * i
            self._text(d, (x0 + 12, y), key, self.f_small, KIND["skillchain"] if ok else DIM)
            self._text(d, (x0 + 40, y), name, self.f_small, WHITE if ok else DIM)


def demo() -> UiState:
    return UiState(name="Adventurer", job="RDM", hp=74, max_hp=100, mp=88, max_mp=110, tp=1240,
                   effects=("Protect",), target_pct=0.63, target_name="Great Jaggi",
                   casting="Fire", cast_frac=0.55,
                   log=(("Adventurer uses Fast Blade.", "ws"),
                        ("The Great Jaggi takes 58 points of damage.", "damage"),
                        ("Skillchain: Scission. The Great Jaggi takes 29 points of damage.", "skillchain"),
                        ("Adventurer starts casting Fire.", "magic")),
                   palette=(("3", "Fast Blade", True), ("4", "Burning Blade", True), ("5", "Red Lotus Blade", True),
                            ("6", "Savage Blade", True), ("7", "Cure", True), ("8", "Dia", True),
                            ("9", "Fire", True), ("0", "Protect", False)))


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
