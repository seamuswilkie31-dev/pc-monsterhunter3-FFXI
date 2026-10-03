# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10"]
# ///
"""The FFXI interface in the game's own window: images handed to the port, laid over its picture.

The port (MH3_FFXI=1) maps a shared block, "mh3-ffxi-overlay" (tools/ffxi_bridge.cpp, struct
OverlayBlock): two RGBA slots. show() fills the slot not shown, then flips; the renderer stretches the
image over the game's picture (16:9), so draw at 1280x720. keep_alive() while running: the port takes
the overlay away 3 s after the layer stops.

    uv run port_overlay.py --test          # a sample FFXI-style window over the game, for 10 s
"""
from __future__ import annotations

import mmap
import struct
import time

from PIL import Image

TAG = "mh3-ffxi-overlay"
MAGIC = 0x4D48334F  # 'MH3O'
MAX_W, MAX_H = 1920, 1080
HEADER = struct.Struct("<8I")  # magic, version, serial, slot, w, h, alive, pad
SLOT = MAX_W * MAX_H * 4
SIZE = HEADER.size + 2 * SLOT
W, H = 1280, 720  # the picture is 16:9; the port stretches the image over it


class PortOverlay:
    def __init__(self):
        self.mm = mmap.mmap(-1, SIZE, tagname=TAG)

    def _get(self, i: int) -> int:
        return int.from_bytes(self.mm[4 * i:4 * i + 4], "little")

    def _set(self, i: int, v: int) -> None:
        self.mm[4 * i:4 * i + 4] = (v & 0xFFFFFFFF).to_bytes(4, "little")

    def connected(self) -> bool:
        return self._get(0) == MAGIC

    def show(self, img: Image.Image | None) -> None:
        """Lay img (RGBA) over the game; None takes it away."""
        if img is None:
            self._set(4, 0)
            self._set(5, 0)
            self._set(2, self._get(2) + 1)
            return
        img = img.convert("RGBA")
        w, h = img.size
        if w > MAX_W or h > MAX_H:
            raise ValueError(f"the overlay is {MAX_W}x{MAX_H} at most")
        slot = self._get(3) ^ 1                       # the one not shown
        at = HEADER.size + slot * SLOT
        self.mm[at:at + w * h * 4] = img.tobytes()
        self._set(4, w)
        self._set(5, h)
        self._set(3, slot)
        self._set(2, self._get(2) + 1)                # serial last: the port reads after it moves

    def keep_alive(self) -> None:
        self._set(6, self._get(6) + 1)


def sample() -> Image.Image:
    """A window in FFXI's manner: dark blue gradient, a thin light border, white text."""
    from PIL import ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0, y0, x1, y1 = 880, 520, 1250, 690
    grad = Image.new("RGBA", (x1 - x0, y1 - y0))
    gd = ImageDraw.Draw(grad)
    for y in range(y1 - y0):
        t = y / (y1 - y0)
        gd.line([(0, y), (x1 - x0, y)], fill=(int(20 + 10 * t), int(30 + 15 * t), int(90 - 30 * t), 215))
    img.alpha_composite(grad, (x0, y0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0, y0, x1, y1], radius=6, outline=(200, 210, 235, 255), width=2)
    d.rounded_rectangle([x0 + 3, y0 + 3, x1 - 3, y1 - 3], radius=5, outline=(70, 90, 150, 255), width=1)
    d.text((x0 + 16, y0 + 14), "FFXI interface test", fill=(255, 255, 255, 255))
    for i, (label, frac, col) in enumerate([("HP", 0.9, (230, 120, 120)), ("MP", 0.6, (120, 170, 255)),
                                            ("TP", 0.3, (140, 220, 120))]):
        y = y0 + 50 + i * 34
        d.text((x0 + 16, y), label, fill=(220, 225, 240, 255))
        d.rectangle([x0 + 50, y + 4, x1 - 20, y + 12], fill=(25, 30, 55, 255))
        d.rectangle([x0 + 50, y + 4, x0 + 50 + int((x1 - 70 - x0) * frac), y + 12], fill=(*col, 255))
    return img


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--seconds", type=float, default=10)
    a = ap.parse_args()
    o = PortOverlay()
    if not o.connected():
        print("the port isn't running with MH3_FFXI=1 (no overlay block yet); showing anyway")
    o.show(sample())
    end = time.monotonic() + a.seconds
    while time.monotonic() < end:
        o.keep_alive()
        time.sleep(0.2)
    o.show(None)
    print("done")
