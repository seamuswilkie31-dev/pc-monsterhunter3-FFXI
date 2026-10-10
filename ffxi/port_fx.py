"""FFXI's spell effects drawn in the game's scene: the triangles handed to the port each frame.

The port (MH3_FFXI=1) maps a shared block, "mh3-ffxi-fx" (tools/ffxi_bridge.cpp, struct FxBlock),
version 1. The layer writes the effects' textures (64x64 layers) and, each frame, their triangles in
the world (ffxi_fx.Effects.geometry: 10 floats a corner, by blending), into one of two slots in turn;
the port draws the newest with the frame's camera. The port writes where things are, each frame: the
camera, the hunter, the target. keep_alive() while running: 3 s without, nothing is drawn.
"""
from __future__ import annotations

import mmap
import struct

import numpy as np

TAG = "mh3-ffxi-fx"
MAGIC, VERSION = 0x4633484D, 2          # 'MH3F'
MAX_VERTS, VERT_FLOATS, MAX_LAYERS, TEX = 60000, 10, 128, 256   # TEX: the level banner's 256 across kept
COUNTS_AT = 28                          # magic, version, alive, tex_serial, tex_layers, seq, front
SCENE_AT = COUNTS_AT + 2 * 8 * 4         # scene_seq, scene_ok, target_ok
CAMERA_AT = SCENE_AT + 12
HUNTER_AT = CAMERA_AT + 48
TARGET_AT = HUNTER_AT + 48
VERTS_AT = 256
SLOT = MAX_VERTS * VERT_FLOATS * 4
TEX_AT = VERTS_AT + 2 * SLOT
SIZE = TEX_AT + MAX_LAYERS * TEX * TEX * 4
assert SIZE == 38354688, "the layout must match struct FxBlock in tools/ffxi_bridge.cpp"


class PortFx:
    def __init__(self):
        self.mm = mmap.mmap(-1, SIZE, tagname=TAG)

    def _get(self, i: int) -> int:
        return int.from_bytes(self.mm[4 * i:4 * i + 4], "little")

    def _set(self, i: int, v: int) -> None:
        self.mm[4 * i:4 * i + 4] = (v & 0xFFFFFFFF).to_bytes(4, "little")

    def connected(self) -> bool:
        return self._get(0) == MAGIC and self._get(1) == VERSION

    def keep_alive(self) -> None:
        self._set(2, self._get(2) + 1)

    def scene(self) -> dict | None:
        """Where things are, as the port last wrote: the camera (world to view, 3x4), the hunter's world
        matrix (3x4), the target's feet and height (None: no target)."""
        seq, ok, target_ok = struct.unpack_from("<3I", self.mm, SCENE_AT)
        if not ok:
            return None
        cam = np.array(struct.unpack_from("<12f", self.mm, CAMERA_AT), np.float64).reshape(3, 4)
        hunter = np.array(struct.unpack_from("<12f", self.mm, HUNTER_AT), np.float64).reshape(3, 4)
        target = struct.unpack_from("<4f", self.mm, TARGET_AT)
        return {"seq": seq, "camera": cam, "hunter": hunter, "target": target if target_ok else None}

    def set_textures(self, layers: list[np.ndarray]) -> None:
        """The textures, each TEX x TEX RGBA, in order (a corner names its layer)."""
        n = min(len(layers), MAX_LAYERS)
        for i in range(n):
            px = np.ascontiguousarray(layers[i], np.uint8)
            at = TEX_AT + i * TEX * TEX * 4
            self.mm[at:at + px.nbytes] = px.tobytes()
        self._set(4, n)
        self._set(3, self._get(3) + 1)          # the serial last: the port reads after it moves

    def send(self, verts: np.ndarray, counts: list[int]) -> None:
        """A frame's triangles: (n, 10) corners in order of their blending, counts[5] of each."""
        verts = np.ascontiguousarray(verts[:MAX_VERTS], np.float32)
        slot = 1 - (self._get(6) & 1)
        at = VERTS_AT + slot * SLOT
        self.mm[at:at + verts.nbytes] = verts.tobytes()
        left, cs = len(verts), []
        for c in list(counts)[:5] + [0] * (5 - len(counts)):
            cs.append(min(c, left))
            left -= cs[-1]
        struct.pack_into("<8I", self.mm, COUNTS_AT + slot * 32, *cs, 0, 0, 0)
        self._set(6, slot)
        self._set(5, self._get(5) + 1)
