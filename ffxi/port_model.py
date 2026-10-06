"""FFXI's character in your hunter's place: the model handed to the port, which draws it in the scene.

The port (MH3_FFXI=1) maps a shared block, "mh3-ffxi-model" (tools/ffxi_bridge.cpp, struct ModelBlock),
version 7: the model's triangle corners on its joints, with their normals (ffxi_model.pack), the joints' parents, its place
in the hunter's space, its motions (each standing, moving or an action, of the peace set or the battle
set, with the ground speed a moving one was made for and its name's first four letters), its textures as
256x256 layers, and flags: draw it, hide Monster Hunter's hunter. The port poses it every frame: the
motion that fits the hunter's speed, from the battle set while the hunter's weapon is out, its attacks
as they happen; and the actions the layer asks for (request: a spell's casting).
keep_alive() while running: the port stops drawing it and shows the hunter again 3 s after the layer
stops.
"""
from __future__ import annotations

import mmap
import struct

import numpy as np

TAG = "mh3-ffxi-model"
MAGIC, VERSION = 0x4D48334D, 7  # 'MH3M'
MAX_VERTS, VERT_FLOATS, MAX_LAYERS, TEX = 100000, 20, 16, 256    # VERT_FLOATS: ffxi_model.VERT_FLOATS
MAX_JOINTS, MAX_CLIPS, POOL = 128, 96, 1 << 22
NONE = 0xFFFFFFFF
# magic, version, serial, n_verts, layers, tex_w, tex_h, flags, alive, n_joints, n_clips,
# act_seq, act_clip, act_next, battle, light_gain and ground_shade (floats), swings (the port's), shadow,
# shadow_radius (a float)
HEADER = struct.Struct("<20I")
CLIP = struct.Struct("<IIffIIII")  # frames, first float in the pool, rate, speed, kind, set, tag, reserved
MOTION_AT = HEADER.size                                 # the port's: Monster Hunter's hunter's motion's name
PLACE_AT = MOTION_AT + 16
PARENTS_AT = PLACE_AT + 12 * 4
CLIPS_AT = PARENTS_AT + MAX_JOINTS * 4
VERTS_AT = CLIPS_AT + MAX_CLIPS * CLIP.size
POOL_AT = VERTS_AT + MAX_VERTS * VERT_FLOATS * 4
TEX_AT = POOL_AT + POOL * 4
SIZE = TEX_AT + MAX_LAYERS * TEX * TEX * 4
DRAW, HIDE_HUNTER = 1, 2


def fourcc(name: str) -> int:
    return int.from_bytes(name.encode("latin-1")[:4].ljust(4, b" "), "little")


class PortModel:
    def __init__(self, slot: int = 0):
        """slot 0: the player's character (the port makes its block); 1-5: a companion's (a Trust's), its
        block "mh3-ffxi-model-N" made here, which the port opens once the layer shows it (port_bridge's
        set_companion)."""
        self.slot = slot
        self.mm = mmap.mmap(-1, SIZE, tagname=TAG if slot == 0 else f"{TAG}-{slot}")
        if slot:
            self._set(1, VERSION)
            self._set(0, MAGIC)
        self.clips: dict[str, int] = {}                 # a sent clip's name: its number, for request

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
        first, n_clips, self.clips = 0, 0, {}
        for c in packed["clips"][:MAX_CLIPS]:
            data = np.ascontiguousarray(c["data"], np.float32).reshape(-1)
            if first + data.size > POOL:
                print(f"[FFXI character: no room for the motion {c['name']} and those after it]")
                break
            self.mm[POOL_AT + 4 * first:POOL_AT + 4 * (first + data.size)] = data.tobytes()
            self.mm[CLIPS_AT + CLIP.size * n_clips:CLIPS_AT + CLIP.size * (n_clips + 1)] = CLIP.pack(
                int(c["frames"]), first, float(c["rate"]), float(c["speed"]), int(c.get("kind", 0)),
                int(c.get("set", 0)), fourcc(c["name"]), 0)
            self.clips.setdefault(c["name"], n_clips)
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

    def request(self, clip: str | None, then: str | None = None) -> bool:
        """Play the action clip once, then loop then (None: back to standing and moving); clip None
        ends the one playing. False if the character has no such motion."""
        if clip is not None and clip not in self.clips:
            return False
        self._set(12, NONE if clip is None else self.clips[clip])
        self._set(13, self.clips.get(then, NONE) if then else NONE)
        self._set(11, self._get(11) + 1)          # the sequence last: the port reads after it moves
        return True

    def set_light(self, gain: float, ground: float = 0.0) -> None:
        """How bright the textures are under the game's light: texel x light x gain (FFXI's are made for
        twice the light: 2; 0: not lit, the textures as they are); and how much the scenery's baked light
        under it dims it (0: not at all, as the game's own hunter; 1: as dark as the ground)."""
        self.mm[15 * 4:17 * 4] = struct.pack("<ff", float(gain), float(ground))

    def set_shadow(self, hide_mh: bool, round_shadow: bool, radius: float, darkness: float) -> None:
        """Monster Hunter's foot shadows under the hunter hidden; FFXI's round shadow drawn under the
        character instead, `radius` across in the game's units, `darkness` 0 to 1 in its middle."""
        dark = int(max(0.0, min(1.0, darkness)) * 255)
        self.mm[18 * 4:20 * 4] = struct.pack("<If", (1 if hide_mh else 0) | (2 if round_shadow else 0) | dark << 8,
                                             float(radius))

    def swings(self) -> int:
        """How many attacks Monster Hunter's hunter has begun (the port counts): FFXI's voice for each."""
        return self._get(17)

    def battle(self) -> bool:
        """Whether Monster Hunter's hunter has its weapon out (the port's reading)."""
        return bool(self._get(14))

    def motion(self) -> str:
        """Monster Hunter's hunter's motion now: its animation's name ("co_0313", "w01_0105"...)."""
        return bytes(self.mm[MOTION_AT:MOTION_AT + 16]).split(b"\0")[0].decode("latin-1")

    def set_flags(self, draw: bool, hide_hunter: bool) -> None:
        self._set(7, (DRAW if draw else 0) | (HIDE_HUNTER if hide_hunter else 0))

    def keep_alive(self) -> None:
        self._set(8, self._get(8) + 1)
