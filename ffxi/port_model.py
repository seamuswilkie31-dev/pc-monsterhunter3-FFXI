"""FFXI's character in your hunter's place: the model handed to the port, which draws it in the scene.

The port (MH3_FFXI=1) maps a shared block, "mh3-ffxi-model" (tools/ffxi_bridge.cpp, struct ModelBlock):
the model's vertices (in the hunter's own space: ffxi_model.pack), its textures as 256x256 layers, and
flags: draw it, hide Monster Hunter's hunter. keep_alive() while running: the port stops drawing it and
shows the hunter again 3 s after the layer stops.
"""
from __future__ import annotations

import mmap
import struct

import numpy as np

TAG = "mh3-ffxi-model"
MAGIC = 0x4D48334D  # 'MH3M'
MAX_VERTS, MAX_LAYERS, TEX = 200000, 16, 256
HEADER = struct.Struct("<16I")   # magic, version, serial, n_verts, layers, tex_w, tex_h, flags, alive, reserved x7
VERTS_AT = HEADER.size
TEX_AT = VERTS_AT + MAX_VERTS * 6 * 4
SIZE = TEX_AT + MAX_LAYERS * TEX * TEX * 4
DRAW, HIDE_HUNTER = 1, 2


class PortModel:
    def __init__(self):
        self.mm = mmap.mmap(-1, SIZE, tagname=TAG)

    def _get(self, i: int) -> int:
        return int.from_bytes(self.mm[4 * i:4 * i + 4], "little")

    def _set(self, i: int, v: int) -> None:
        self.mm[4 * i:4 * i + 4] = (v & 0xFFFFFFFF).to_bytes(4, "little")

    def connected(self) -> bool:
        return self._get(0) == MAGIC

    def send(self, verts: np.ndarray, layers: np.ndarray) -> None:
        verts = np.ascontiguousarray(verts[:MAX_VERTS], np.float32)
        layers = np.ascontiguousarray(layers[:MAX_LAYERS], np.uint8)
        if layers.shape[1:] != (TEX, TEX, 4):
            raise ValueError(f"layers must be {TEX}x{TEX} RGBA")
        self.mm[VERTS_AT:VERTS_AT + verts.nbytes] = verts.tobytes()
        self.mm[TEX_AT:TEX_AT + layers.nbytes] = layers.tobytes()
        self._set(3, len(verts))
        self._set(4, len(layers))
        self._set(5, TEX)
        self._set(6, TEX)
        self._set(2, self._get(2) + 1)            # serial last: the port reads after it moves

    def set_flags(self, draw: bool, hide_hunter: bool) -> None:
        self._set(7, (DRAW if draw else 0) | (HIDE_HUNTER if hide_hunter else 0))

    def keep_alive(self) -> None:
        self._set(8, self._get(8) + 1)
