"""FFXI's character in your hunter's place: the model handed to the port, which draws it in the scene.

The port (MH3_FFXI=1) maps a shared block, "mh3-ffxi-model" (tools/ffxi_bridge.cpp, struct ModelBlock),
version 2: the model's triangle corners on its joints (ffxi_model.pack), the joints' parents, its place
in the hunter's space, its motions (standing first, then the moving ones, each with the ground speed it
was made for), its textures as 256x256 layers, and flags: draw it, hide Monster Hunter's hunter. The
port poses it every frame: the motion that fits the hunter's speed, played at that speed.
keep_alive() while running: the port stops drawing it and shows the hunter again 3 s after the layer
stops.
"""
from __future__ import annotations

import mmap
import struct

import numpy as np

TAG = "mh3-ffxi-model"
MAGIC, VERSION = 0x4D48334D, 2  # 'MH3M'
MAX_VERTS, VERT_FLOATS, MAX_LAYERS, TEX = 100000, 13, 16, 256
MAX_JOINTS, MAX_CLIPS, POOL = 128, 8, 1 << 20
# magic, version, serial, n_verts, layers, tex_w, tex_h, flags, alive, n_joints, n_clips, reserved x5
HEADER = struct.Struct("<16I")
CLIP = struct.Struct("<IIff")    # frames, first float in the pool, rate (keyframes a game frame), speed
PLACE_AT = HEADER.size
PARENTS_AT = PLACE_AT + 12 * 4
CLIPS_AT = PARENTS_AT + MAX_JOINTS * 4
VERTS_AT = CLIPS_AT + MAX_CLIPS * CLIP.size
POOL_AT = VERTS_AT + MAX_VERTS * VERT_FLOATS * 4
TEX_AT = POOL_AT + POOL * 4
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
        return self._get(0) == MAGIC and self._get(1) == VERSION

    def send(self, packed: dict) -> None:
        """ffxi_model.pack's model, written whole; the serial last, which the port reads after."""
        verts = np.ascontiguousarray(packed["verts"][:MAX_VERTS], np.float32)
        layers = np.ascontiguousarray(packed["layers"][:MAX_LAYERS], np.uint8)
        if verts.shape[1:] != (VERT_FLOATS,):
            raise ValueError(f"vertices must have {VERT_FLOATS} floats")
        if layers.shape[1:] != (TEX, TEX, 4):
            raise ValueError(f"layers must be {TEX}x{TEX} RGBA")
        parents = list(packed["parents"])
        if len(parents) > MAX_JOINTS:
            raise ValueError(f"{len(parents)} joints, at most {MAX_JOINTS}")
        place = np.ascontiguousarray(packed["place"], np.float32).reshape(12)
        self.mm[PLACE_AT:PLACE_AT + 48] = place.tobytes()
        self.mm[PARENTS_AT:PARENTS_AT + 4 * len(parents)] = struct.pack(f"<{len(parents)}i", *parents)
        first, n_clips = 0, 0
        for c in packed["clips"][:MAX_CLIPS]:
            data = np.ascontiguousarray(c["data"], np.float32).reshape(-1)
            if first + data.size > POOL:
                break
            self.mm[POOL_AT + 4 * first:POOL_AT + 4 * (first + data.size)] = data.tobytes()
            self.mm[CLIPS_AT + CLIP.size * n_clips:CLIPS_AT + CLIP.size * (n_clips + 1)] = \
                CLIP.pack(int(c["frames"]), first, float(c["rate"]), float(c["speed"]))
            first += data.size
            n_clips += 1
        self.mm[VERTS_AT:VERTS_AT + verts.nbytes] = verts.tobytes()
        self.mm[TEX_AT:TEX_AT + layers.nbytes] = layers.tobytes()
        self._set(3, len(verts))
        self._set(4, len(layers))
        self._set(5, TEX)
        self._set(6, TEX)
        self._set(9, len(parents))
        self._set(10, n_clips)
        self._set(2, self._get(2) + 1)            # serial last: the port reads after it moves

    def set_flags(self, draw: bool, hide_hunter: bool) -> None:
        self._set(7, (DRAW if draw else 0) | (HIDE_HUNTER if hide_hunter else 0))

    def keep_alive(self) -> None:
        self._set(8, self._get(8) + 1)
