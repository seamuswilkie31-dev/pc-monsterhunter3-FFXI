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

import os
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
PARTY_NAME = (150, 215, 255)                      # a party member's name: light blue, as FFXI's
HP_COL = [(1.0, (238, 240, 250)), (0.75, (246, 232, 120)), (0.5, (250, 170, 80)), (0.25, (240, 90, 80))]
MP_BAR, TP_READY, TP_LOW = (122, 168, 255), (160, 236, 140), (190, 200, 220)
EXP_BAR = (230, 200, 110)
KIND = {"ws": (255, 255, 255), "damage": (255, 255, 255), "magic": (170, 205, 255), "heal": (150, 240, 160),
        "skillchain": (255, 226, 120), "burst": (255, 200, 90), "warn": (250, 150, 140), "info": (210, 214, 230),
        "say": (255, 255, 255), "emote": (230, 220, 255)}
UP, DOWN = "\u25b2", "\u25bc"


def font(size: int, bold: bool = True, italic: bool = False) -> ImageFont.FreeTypeFont:
    names = ["arialbd.ttf", "segoeuib.ttf", "tahomabd.ttf"] if bold else ["arial.ttf", "segoeui.ttf", "tahoma.ttf"]
    if italic:                                    # the slanted ones first, the upright ones if there are none
        names = (["arialbi.ttf", "seguibi.ttf"] if bold else ["ariali.ttf", "segoeuii.ttf"]) + names
    for n in names:
        for d in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            if (d / n).exists():
                return ImageFont.truetype(str(d / n), size)
    return ImageFont.load_default(size)


def ui_assets() -> Path:
    """Where your own FFXI's interface pieces are (ffxi_assets.py ui): never in the repository."""
    d = os.environ.get("FFXI_MH3_ASSETS") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ffxi-mh3" / "assets"
    return Path(d) / "ui"


class Skin:
    """FFXI's own window theme and pointer, from your installation: a window is nine slices, the
    corner piece's quarters, the edges' halves, the background tiled; FFXI's windows are a little
    see-through."""

    def __init__(self, folder: Path, theme: int = 1, bg_scale: int = 2, alpha: float = 0.88):
        def load(name):
            return Image.open(folder / f"{name}.png").convert("RGBA")
        bg = load(f"theme{theme}_bg")
        self.bg = bg.resize((bg.width * bg_scale, bg.height * bg_scale), Image.NEAREST)
        self.bg.putalpha(self.bg.getchannel("A").point(lambda v: int(v * alpha)))
        corner, hfr, vfr = load(f"theme{theme}_corner"), load(f"theme{theme}_hfr"), load(f"theme{theme}_vfr")
        q = self.q = corner.width // 2
        self.tl, self.tr = corner.crop((0, 0, q, q)), corner.crop((q, 0, 2 * q, q))
        self.bl, self.br = corner.crop((0, q, q, 2 * q)), corner.crop((q, q, 2 * q, 2 * q))
        self.top, self.bottom = hfr.crop((0, 0, hfr.width, q)), hfr.crop((0, q, hfr.width, 2 * q))
        self.left, self.right = vfr.crop((0, 0, q, vfr.height)), vfr.crop((q, 0, 2 * q, vfr.height))
        self.pointer = load("pointer").resize((24, 24), Image.LANCZOS)
        self._cache: dict = {}                          # a window's layer by its size

    def frame(self, img: Image.Image, box: tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        layer = self._cache.get((w, h))
        if layer is None:
            if len(self._cache) > 64:
                self._cache.clear()
            layer = self._cache[(w, h)] = self._layer(w, h)
        img.alpha_composite(layer, (x0, y0))

    def _layer(self, w: int, h: int) -> Image.Image:
        q = self.q
        layer = Image.new("RGBA", (w, h))
        inset = 3                                       # the background stops under the frame
        for ty in range(inset, h - inset, self.bg.height):
            for tx in range(inset, w - inset, self.bg.width):
                tile = self.bg.crop((0, 0, min(self.bg.width, w - inset - tx), min(self.bg.height, h - inset - ty)))
                layer.alpha_composite(tile, (tx, ty))
        for tx in range(q, w - q, self.top.width):     # the edges between the corners
            cw = min(self.top.width, w - q - tx)
            layer.alpha_composite(self.top.crop((0, 0, cw, q)), (tx, 0))
            layer.alpha_composite(self.bottom.crop((0, 0, cw, q)), (tx, h - q))
        for ty in range(q, h - q, self.left.height):
            ch = min(self.left.height, h - q - ty)
            layer.alpha_composite(self.left.crop((0, 0, q, ch)), (0, ty))
            layer.alpha_composite(self.right.crop((0, 0, q, ch)), (w - q, ty))
        layer.alpha_composite(self.tl, (0, 0))
        layer.alpha_composite(self.tr, (w - q, 0))
        layer.alpha_composite(self.bl, (0, h - q))
        layer.alpha_composite(self.br, (w - q, h - q))
        return layer


def load_skin(theme: int = 1):
    try:
        return Skin(ui_assets(), theme)
    except (OSError, ValueError):
        return None                                     # not extracted: our own lookalike


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
    linkpearl: tuple | None = None        # a linkpearl's colour (r, g, b) by your name, as a linkshell's member
    target_arrow: tuple | None = None     # (x, y, locked, bob 0..1): FFXI's arrow above the target's head
    target_locked: bool = False
    chat_input: str | None = None         # the chat line being typed (None: closed)
    menu_help: str = ""                   # the menu's highlighted entry, described (an item's stats)
    party: tuple = ()                     # the Trusts with you: ((name, HP 0..1, MP 0..1 or None, TP), ...)
    gm: bool = False                      # a GM: FFXI's GM icon by your name
    party_names: tuple = ()               # the Trusts' names above their heads: ((name, x, y), ...), -1..1, y up
    status: str = ""


class FfxiUi:
    def __init__(self, theme: int = 1, use_assets: bool = True):
        self.skin = load_skin(theme) if use_assets else None
        self.f_name = font(17)
        self.f_tag = font(17, italic=True)    # the name above your head: slanted, as FFXI's
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
        for name, nx, ny in s.party_names:            # your party's names, as FFXI shows a party member's
            px, py = (nx + 1) / 2 * W, (1 - ny) / 2 * H
            if 0 <= px < W and 0 <= py < H:
                self._text(d, (int(px), int(py)), name, self.f_tag, PARTY_NAME, anchor="ms")
        if s.nameplate:
            self._nameplate(img, d, s)
        if s.target_arrow:
            self._arrow(d, s)
        self._status(img, d, s)
        if s.effects:
            self._effects(img, d, s)
        if s.target_pct is not None:
            self._target(img, d, s)
        if s.party:
            self._party(img, d, s)
        if s.log or s.chat_input is not None:
            self._log(img, d, s)
        if s.casting:
            self._cast(img, d, s)
        if s.palette:
            self._palette(img, d, s)
        if s.menu:
            self._menu(img, d, s)
        if s.menu and s.menu_help:
            self._help(img, d, s)
        if s.status:
            self._text(d, (W - 14, 10), s.status, self.f_small, DIM, anchor="ra")
        return img

    # --- pieces -----------------------------------------------------------------------------------
    def window(self, img: Image.Image, box: tuple[int, int, int, int]) -> None:
        if self.skin:
            self.skin.frame(img, box)
            return
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

    def _nameplate(self, img, d, s: UiState) -> None:
        """Your name above your head, as FFXI shows it: white, slanted, outlined, centred; a GM's icon
        before it, a linkpearl before that."""
        x = (s.nameplate[0] + 1) / 2 * W
        y = (1 - s.nameplate[1]) / 2 * H
        if 0 <= x < W and 0 <= y < H:
            self._text(d, (int(x), int(y)), s.name, self.f_tag, WHITE, anchor="ms")
            left = x - d.textlength(s.name, font=self.f_tag) / 2
            icon = gm_icon() if s.gm else None
            if icon:                                 # FFXI's GM icon, left of the name
                left -= icon.width + 3
                img.alpha_composite(icon, (int(left), int(y - 7 - icon.height / 2)))
            if s.linkpearl:                          # the linkpearl, left of those
                _pearl(d, int(left - 12), int(y - 7), s.linkpearl)

    def _arrow(self, d, s: UiState) -> None:
        """FFXI's target arrow: pointing down at the target's head, bobbing; ringed while locked on."""
        import math
        ax, ay, locked, bob = s.target_arrow
        x = (ax + 1) / 2 * W
        y = (1 - ay) / 2 * H - 6 - 7 * math.sin(bob * 2 * math.pi)
        if not (0 <= x < W and 0 <= y < H):
            return
        w, h = 16, 32
        tip, left, right = (x, y), (x - w, y - h), (x + w, y - h)
        notch = (x, y - h + 8)
        if locked:                                   # the lock: a light rim, pulsing
            glow = int(150 + 100 * math.sin(bob * 4 * math.pi))
            d.polygon([(x, y + 5), (x - w - 6, y - h - 4), (x, y - h + 3), (x + w + 6, y - h - 4)],
                      outline=(255, 245, 200, glow), width=3)
        d.polygon([tip, left, notch, right], fill=(222, 64, 36, 245), outline=(255, 214, 120, 255), width=2)
        d.polygon([tip, (x - w * 0.45, y - h * 0.62), (x, y - h * 0.5)], fill=(255, 150, 90, 200))

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

    def _party(self, img, d, s: UiState) -> None:
        """FFXI's party list: the others in your party (the Trusts), above your own window and the
        target's, a row each: the name, HP and MP as bars, TP."""
        rows = s.party
        x0, x1 = W - 300, W - 12
        y1 = (H - 158) if s.target_pct is not None else (H - 104)
        y0 = y1 - 12 - 24 * len(rows)
        self.window(img, (x0, y0, x1, y1))
        for i, (name, hp, mp, tp) in enumerate(rows):
            y = y0 + 7 + 24 * i
            self._text(d, (x0 + 12, y), name, self.f_small, WHITE)
            self._bar(d, x0 + 130, y + 4, 70, hp, HP_COL[0][1], h=4)
            if mp is not None:
                self._bar(d, x0 + 130, y + 11, 70, mp, MP_BAR, h=3)
            self._text(d, (x1 - 12, y), f"TP {tp}", self.f_small, TP_READY if tp >= 1000 else TP_LOW, anchor="ra")

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
        if s.target_locked:
            self._text(d, (x1 - 12, y0 + 27), "LOCK", self.f_small, (255, 214, 120), anchor="ra")

    def _log(self, img, d, s: UiState) -> None:
        lines = s.log[-7:]
        typing = s.chat_input is not None
        x0, y1 = 12, H - 12
        y0 = y1 - 16 - 20 * (len(lines) + (1 if typing else 0))
        self.window(img, (x0, y0, x0 + 600, y1))
        for i, (text, kind) in enumerate(lines):
            self._text(d, (x0 + 12, y0 + 9 + 20 * i), text, self.f_log, KIND.get(kind, WHITE))
        if typing:                                   # the input line, as FFXI's: its own row, a caret after it
            y = y0 + 9 + 20 * len(lines)
            d.line([(x0 + 8, y - 3), (x0 + 592, y - 3)], fill=(110, 125, 170, 200))
            shown = s.chat_input
            while shown and d.textlength(shown + "_", font=self.f_log) > 570:
                shown = shown[1:]
            self._text(d, (x0 + 12, y), shown + "_", self.f_log, WHITE)

    def _cast(self, img, d, s: UiState) -> None:
        """The spell being cast and how far along: above the target window (the command menu, bottom left,
        opens toward the middle)."""
        x0, y0 = W - 300, H - 200
        self.window(img, (x0, y0, x0 + 288, y0 + 40))
        self._text(d, (x0 + 12, y0 + 7), s.casting, self.f_val, KIND["magic"])
        self._bar(d, x0 + 12, y0 + 27, 264, s.cast_frac, MP_BAR)

    def _log_top(self, s: UiState) -> int | None:
        """Where the chat log's window begins (None: no log shown), as _log draws it."""
        if not (s.log or s.chat_input is not None):
            return None
        n = len(s.log[-7:]) + (1 if s.chat_input is not None else 0)
        return H - 12 - 16 - 20 * n

    def _menu(self, img, d, s: UiState) -> None:
        """FFXI's command menu, bottom left: on the chat log, rising as the log fills (at the bottom with no
        log), the first level leftmost, each deeper one to the right of it."""
        top = self._log_top(s)
        x0, y1 = 12, (top - 6) if top is not None else H - 12
        for title, rows, cursor, up, down in s.menu:
            label_w = max([int(d.textlength(t, font=self.f_val)) for t, _, _ in rows]
                          + [int(d.textlength(title, font=self.f_small))])
            detail_w = max([int(d.textlength(x, font=self.f_small)) for _, _, x in rows if x] + [0])
            width = (50 if self.skin else 44) + label_w + (detail_w + 16 if detail_w else 0) + (12 if up or down else 0)
            x1 = x0 + width
            y0 = max(40, y1 - (34 + 22 * max(1, len(rows))))
            self.window(img, (x0, y0, x1, y1))
            self._text(d, (x0 + 12, y0 + 8), title, self.f_small, DIM)
            if up:
                self._text(d, (x1 - 10, y0 + 8), UP, self.f_small, DIM, anchor="ra")
            if down:
                self._text(d, (x1 - 10, y1 - 20), DOWN, self.f_small, DIM, anchor="ra")
            for i, (label, ok, detail) in enumerate(rows):
                y = y0 + 28 + 22 * i
                indent = 34 if self.skin else 28
                if i == cursor:
                    if self.skin:                       # FFXI's glove, and nothing else
                        img.alpha_composite(self.skin.pointer, (x0 + 6, y - 3))
                    else:
                        d.rectangle([x0 + 6, y - 2, x1 - 7, y + 18], fill=(70, 90, 160, 150))
                        _menu_pointer(d, x0 + 10, y + 3)
                self._text(d, (x0 + indent, y), label, self.f_val, WHITE if ok else DIM)
                if detail:
                    self._text(d, (x1 - (24 if up or down else 12), y + 2), detail, self.f_small,
                               KIND["magic"] if ok else DIM, anchor="ra")
            x0 = x1 + 6

    def _help(self, img, d, s: UiState) -> None:
        """An item's description, as FFXI shows it: its name first, then its slots, stats and jobs."""
        lines = [ln for ln in s.menu_help.splitlines() if ln.strip()][:9]
        width = max(int(d.textlength(ln, font=self.f_small)) for ln in lines) + 28
        width = max(260, min(width, 560))
        x0, y0 = W // 2 - width // 2, 46
        self.window(img, (x0, y0, x0 + width, y0 + 16 + 20 * len(lines)))
        for i, ln in enumerate(lines):
            col = WHITE if i == 0 else (KIND["warn"] if ln.startswith("(") else KIND["info"])
            self._text(d, (x0 + 14, y0 + 8 + 20 * i), ln, self.f_val if i == 0 else self.f_small, col)

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


_GM_ICON: list = []


def gm_icon():
    """FFXI's GM icon (its name-status icons, ROM/0/1.DAT's "ustats": the third row's second), cut from your
    installation the first time and kept with the interface's pieces; None without it."""
    if _GM_ICON:
        return _GM_ICON[0]
    p = ui_assets() / "gm_icon.png"
    img = None
    try:
        if not p.exists():
            import ffxi_assets as A
            import ffxi_model as M
            b = (M.ffxi_root(None) / "ROM" / "0" / "1.DAT").read_bytes()
            for at, _nm, typ, size in A.chunks(b):
                info = A.image_info(b, at, size) if typ == 0x20 else None
                if info and info["name"] == "ustats":
                    p.parent.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(A.decode(b, info)).crop((16, 32, 32, 48)).save(p)
                    break
        img = Image.open(p).convert("RGBA").resize((19, 19), Image.LANCZOS)
    except Exception:                                    # noqa: BLE001  no FFXI here: no icon
        img = None
    _GM_ICON.append(img)
    return img


def _pearl(d: ImageDraw.ImageDraw, cx: int, cy: int, rgb: tuple, r: int = 6) -> None:
    """A linkpearl: a small shining sphere of its colour, darker at its rim, a highlight up and left."""
    dark = tuple(int(c * 0.45) for c in rgb)
    d.ellipse([cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1], fill=(10, 10, 20, 230))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*dark, 255))
    d.ellipse([cx - r + 1, cy - r + 1, cx + r - 2, cy + r - 2], fill=(*rgb, 255))
    lite = tuple(min(255, int(c + (255 - c) * 0.55)) for c in rgb)
    d.ellipse([cx - r + 2, cy - r + 2, cx - 1, cy - 1], fill=(*lite, 255))
    d.ellipse([cx - r + 3, cy - r + 3, cx - r + 5, cy - r + 5], fill=(255, 255, 255, 255))


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
                   nameplate=(0.0, 0.35), linkpearl=(60, 140, 255),
                   menu=(("Command", (("Attack", True, ""), ("Magic", True, ""), ("Abilities", True, ""),
                                      ("Items", True, "13"), ("Equipment", True, ""), ("Pouch Item", True, ""),
                                      ("Job", True, "RDM40/WHM20")), 1, False, False),
                         ("Magic", (("White Magic", True, ""), ("Black Magic", True, "")), 1, False, False),
                         ("Black Magic", (("Stone", True, "4 MP"), ("Water", True, "5 MP"), ("Aero", True, "6 MP"),
                                          ("Fire", True, "7 MP"), ("Blizzard", True, "8 MP"), ("Thunder", True, "9 MP"),
                                          ("Stone II", True, "16 MP"), ("Water II", False, "8s"),
                                          ("Aero II", True, "22 MP"), ("Fire II", True, "26 MP")), 9, False, True)))


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "ffxi_ui_demo.png"
    t = time.perf_counter()
    img = FfxiUi(theme=int(sys.argv[2]) if len(sys.argv) > 2 else 1).render(demo())
    print(f"rendered in {1000 * (time.perf_counter() - t):.1f} ms")
    bg = Image.new("RGBA", (W, H), (88, 110, 70, 255))
    bg.alpha_composite(img)
    bg.save(out)
    print(out)
