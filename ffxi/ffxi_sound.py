# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26"]
# ///
"""FFXI's sound effects and music from your own installation: read, decoded, played.

    uv run ffxi_sound.py wav 3037            # sound 3037 (sound/win/se/se003/se003037.spw) -> a WAV
    uv run ffxi_sound.py spell 1             # the sounds spell animation 1 (Cure) plays, as WAVs
    uv run ffxi_sound.py music 101           # music 101 (sound/win/music/data/music101.bgw) -> a WAV

What's decoded goes to the local asset folder (sounds/, music/), never into the repository.

The formats, as the community describes them (written here from that description, no code taken):
  a sound effect (.spw): "SeWave\\0\\0", u32 file size, u32 format (0: ADPCM), u32 its number, u32
    blocks, u32 loop start (in blocks; -1: none), two u32 whose sum (mod 2^32) is the sample rate,
    u32 the data's offset, then bytes: ?, ?, channels, ...; the data from there. ADPCM as the
    PlayStation's: blocks of 9 bytes, 16 samples, the first byte a filter (high nibble) and a shift
    (low nibble), then 16 four-bit steps, the low nibble first; channels' blocks interleaved.
  a spell's effect (file 2800 + its animation number, through FTABLE/VTABLE): a 0x3D chunk "SeSep"
    names the sound it plays (u32 after the 8-byte tag).
  a piece of music (.bgw): "BGMStream" padded to 16 bytes, u32 file size, u32 its number, u32 blocks
    (a channel's), u32 loop start (in blocks), two u32 whose sum is the sample rate, u32 the data's
    offset, then bytes: ?, ?, channels, ?; the data: the same ADPCM, the channels' blocks alternating.
"""
from __future__ import annotations

import argparse
import io
import struct
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ffxi_assets as A  # noqa: E402

SPELL_FILE_BASE = 2800                     # a spell's effect: file 2800 + its animation number
LEVEL_UP, LEVEL_DOWN = "ROM/13/35.DAT", "ROM/13/34.DAT"   # the client's own effects for them ("lvup", "lvdw")
# the filters, as fractions of 64 of the two samples before
FILTERS = np.array([(0, 0), (60, 0), (115, -52), (98, -55), (122, -60)], np.int64)


def sound_path(root: Path, n: int) -> Path:
    return root / "sound" / "win" / "se" / f"se{n // 1000:03d}" / f"se{n:06d}.spw"


# A character's voice (grunts attacking, hurt, falling): a set of 16 sounds (se2NN001-016: idle 1-2,
# attacking 3-6, hurt 7-10, staggering 11-13, falling 14-16). The race's effect file names se236's, a
# stand-in the client fills with the voice its NPC is given: the sets se201-se328 are FFXI's NPCs' voices,
# not one per race and face (Gessho's, a Galka's, is se214; Babban's, a Mithra's, se220; Fablinix's, a
# goblin's, se211; King of Hearts' se268). FFXI's own player characters have none, so each race takes one
# that suits it: by the voice's pitch, measured across the readable sets (2026-10-09: a Tarutaru given
# se267, the deepest of all, sounded like an ogre), its faces through the race's list in turn. A few sets
# are ATRAC3, not read here: the next in the list then. /voice in the game tries others.
VOICE_RACES = ["hume_male", "hume_female", "elvaan_male", "elvaan_female", "tarutaru_male", "tarutaru_female",
               "mithra", "galka"]
VOICE_CHOICES = {
    "hume_male": [201, 222, 228, 254, 305, 232],          # a man's (about 120-160 Hz)
    "elvaan_male": [221, 303, 215, 254, 305, 222],
    "hume_female": [290, 307, 249, 223, 280, 234],        # a woman's (about 210-350 Hz)
    "elvaan_female": [249, 234, 307, 290, 223],
    "mithra": [220, 238, 231, 234, 249],                   # Babban's first, then the brighter women's
    "tarutaru_male": [236, 212, 243, 241, 242, 268],      # the highest, a child's (about 530-700 Hz)
    "tarutaru_female": [316, 311, 213, 242, 241, 243],
    "galka": [214, 302, 219, 218, 309],                    # Gessho's first, then the deepest
}


def readable_voice(root: Path, vset: int) -> bool:
    try:
        read_spw(sound_path(root, int(vset) * 1000 + 3).read_bytes())
        return True
    except Exception:                                                  # noqa: BLE001  ATRAC3, or missing
        return False


def voice_set(root: Path, race: str, face: int) -> int | None:
    """The voice set (se2NN) for a race and face (1-16): the race's list (VOICE_CHOICES), its faces in turn,
    the next readable one if that isn't."""
    choices = VOICE_CHOICES.get(race)
    if not choices:
        return None
    start = (max(1, int(face)) - 1) % len(choices)
    for i in range(len(choices)):
        vset = choices[(start + i) % len(choices)]
        if readable_voice(root, vset):
            return vset
    return None


def voice_sets(root: Path) -> list[int]:
    """Every readable voice set, se201-se328 (for /voice to step through)."""
    return [v for v in range(201, 329) if readable_voice(root, v)]


MUSIC_ROOTS = ("sound", "sound2", "sound3", "sound4", "sound5", "sound6", "sound9")   # the base game, then each expansion's


def music_path(root: Path, n: int) -> Path | None:
    for r in MUSIC_ROOTS:
        p = root / r / "win" / "music" / "data" / f"music{n:03d}.bgw"
        if p.exists():
            return p
    return None


def read_bgw(b: bytes) -> tuple[np.ndarray, int, int]:
    """(samples: frames x channels int16, the sample rate, the loop start in frames or -1)."""
    if b[:9] != b"BGMStream":
        raise ValueError("not a BGMStream")
    _size, _num, blocks, loop, hi, lo, data_at = struct.unpack_from("<IIIiIII", b, 0x10)
    rate = (hi + lo) & 0xFFFFFFFF
    channels = max(1, b[0x2E])
    n = min(blocks * channels, (len(b) - data_at) // 9)
    raw = np.frombuffer(b, np.uint8, n * 9, data_at).reshape(n, 9)
    out = np.zeros((n // channels * 16, channels), np.int16)
    for c in range(channels):
        out[:, c] = _decode(raw[c::channels])
    return out, rate, (loop * 16 if loop >= 0 else -1)


def read_spw(b: bytes) -> tuple[np.ndarray, int, int]:
    """(samples: frames x channels int16, the sample rate, the loop start in frames or -1)."""
    if b[:6] != b"SeWave":
        raise ValueError("not a SeWave sound")
    size, fmt, _num, blocks, loop = struct.unpack_from("<IIIIi", b, 8)
    hi, lo, data_at = struct.unpack_from("<III", b, 0x1C)
    rate = (hi + lo) & 0xFFFFFFFF
    channels = max(1, b[0x2A])
    if fmt == 1:                                                       # plain 16-bit
        pcm = np.frombuffer(b, "<i2", (len(b) - data_at) // 2, data_at)
        return pcm[:len(pcm) // channels * channels].reshape(-1, channels), rate, loop
    if fmt != 0:
        raise ValueError(f"sound format {fmt} not read (ATRAC3?)")
    n = min(blocks * channels, (len(b) - data_at) // 9)
    raw = np.frombuffer(b, np.uint8, n * 9, data_at).reshape(n, 9)
    out = np.zeros((n // channels * 16, channels), np.int16)
    for c in range(channels):
        out[:, c] = _decode(raw[c::channels])
    return out, rate, (loop * 16 if loop >= 0 else -1)


def _decode(blocks: np.ndarray) -> np.ndarray:
    head = blocks[:, 0]
    shift, flt = (head & 0x0F).astype(np.int64), np.minimum(head >> 4, 4).astype(np.int64)
    nib = np.empty((len(blocks), 16), np.int64)
    nib[:, 0::2] = blocks[:, 1:] & 0x0F
    nib[:, 1::2] = blocks[:, 1:] >> 4
    nib = np.where(nib >= 8, nib - 16, nib)                            # four bits, signed
    step = (nib << 12) >> shift[:, None]
    k = FILTERS[flt]
    out = np.empty(step.size, np.int64)
    s1 = s2 = 0
    for i in range(len(blocks)):                                       # the filter runs on: a loop
        k0, k1 = k[i]
        row = step[i]
        for j in range(16):
            s = row[j] + ((s1 * k0 + s2 * k1) >> 6)
            s = -32768 if s < -32768 else 32767 if s > 32767 else s
            out[i * 16 + j] = s
            s2, s1 = s1, s
    return out.astype(np.int16)


def to_wav(samples: np.ndarray, rate: int) -> bytes:
    f = io.BytesIO()
    with wave.open(f, "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())
    return f.getvalue()


# ------------------------------------------------------------------------------------------------
def file_path(root: Path, file_id: int) -> Path | None:
    """A file number through the tables: the first ROM whose VTABLE has it, its FTABLE the folder
    (high bits) and file (low 7)."""
    for rom in range(1, 10):
        d = root if rom == 1 else root / f"ROM{rom}"
        vt_path = d / ("VTABLE.DAT" if rom == 1 else f"VTABLE{rom}.DAT")
        if not vt_path.exists():
            continue
        vt = np.fromfile(vt_path, np.uint8)
        if file_id < len(vt) and vt[file_id]:
            ft = np.fromfile(d / ("FTABLE.DAT" if rom == 1 else f"FTABLE{rom}.DAT"), "<u2")
            v = int(ft[file_id])
            return root / ("ROM" if rom == 1 else f"ROM{rom}") / str(v >> 7) / f"{v & 0x7F}.DAT"
    return None


def effect_sounds(b: bytes) -> list[int]:
    """The sounds an effect file names (its 0x3D "SeSep" chunks), in order."""
    out = []
    for at, _nm, typ, size in A.chunks(b):
        body = b[at + 16:at + size]
        if typ == 0x3D and body[:5] == b"SeSep" and len(body) >= 12:
            out.append(struct.unpack_from("<I", body, 8)[0])
    return out


def spell_sounds(root: Path, animation: int) -> list[int]:
    p = file_path(root, SPELL_FILE_BASE + animation)
    return effect_sounds(p.read_bytes()) if p and p.exists() else []


class Player:
    """FFXI's sounds played as the layer asks, several at once, mixed into one output at 48 kHz.
    Each sound is decoded once and kept as a WAV in the asset folder; a spell's are found and
    decoded ahead (prepare) on a thread of their own, so a cast doesn't wait for them."""

    RATE = 48000
    MUSIC_GAIN = 0.08          # FFXI's music is mastered louder: at 0.08, as loud as Monster Hunter's own (measured)
    GAME_PROCESS = "wiiboot.exe"

    def __init__(self, root: Path, volume: float = 0.7, music_volume: float = 1.0):
        import threading
        self.root, self.volume, self.music_volume = root, volume, music_volume
        self.follow = 1.0          # the game's own level in Windows' volume mixer: FFXI's sounds follow it
        self.cache: dict[int, np.ndarray | None] = {}
        self.spells: dict[int, list[int]] = {}
        self.voices: list[list] = []                                   # [samples, position]
        self.music: list[dict] = []                                    # playing: the newest last, the others fading
        self.music_want: int | None = None                             # the piece asked for last
        self.music_cache: dict[int, tuple[np.ndarray, int] | None] = {}
        self.lock = threading.Lock()
        self.dir = A.asset_dir() / "sounds"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.music_dir = A.asset_dir() / "music"
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.stream = None
        threading.Thread(target=self._follow_game, daemon=True).start()
        try:
            import sounddevice as sd
            self.stream = sd.OutputStream(samplerate=self.RATE, channels=2, dtype="float32", callback=self._fill)
            self.stream.start()
        except Exception as e:                                         # noqa: BLE001
            print(f"[FFXI sounds: no audio output ({e})]")

    def _fill(self, out, frames, _time, _status):
        mix = np.zeros(frames, np.float32)
        stereo = np.zeros((frames, 2), np.float32)
        with self.lock:
            keep = []
            for v in self.voices:
                s, at = v
                n = min(frames, len(s) - at)
                mix[:n] += s[at:at + n]
                v[1] += n
                if v[1] < len(s):
                    keep.append(v)
            self.voices = keep
            playing = []
            for m in self.music:                                       # looped from the piece's loop point
                pcm, loop, at = m["pcm"], m["loop"], m["at"]
                got = 0
                while got < frames:
                    n = min(frames - got, len(pcm) - at)
                    gain = m["gain"] + (np.arange(n, dtype=np.float32) + 1) * m["step"]
                    np.clip(gain, 0.0, 1.0, out=gain)
                    stereo[got:got + n] += pcm[at:at + n] * gain[:, None]
                    m["gain"] = float(gain[-1]) if n else m["gain"]
                    got += n
                    at += n
                    if at >= len(pcm):
                        at = loop
                m["at"] = at
                if m["gain"] > 0.0 or m["step"] >= 0:
                    playing.append(m)
            self.music = playing
        stereo *= self.music_volume * self.MUSIC_GAIN
        stereo += (mix * self.volume)[:, None]
        stereo *= self.follow
        np.clip(stereo, -1.0, 1.0, out=stereo)
        out[:] = stereo

    def _follow_game(self) -> None:
        """Every 2 s, the game's level in Windows' volume mixer (pycaw; 1 without it): set it lower for the
        game, and FFXI's sounds and music are as much lower."""
        import time
        try:
            import comtypes
            from pycaw.pycaw import AudioUtilities
            comtypes.CoInitialize()
        except Exception:                                              # noqa: BLE001
            return
        while True:
            try:
                for sess in AudioUtilities.GetAllSessions():
                    if sess.Process and sess.Process.name().lower() == self.GAME_PROCESS:
                        v = sess.SimpleAudioVolume
                        self.follow = 0.0 if v.GetMute() else float(v.GetMasterVolume())
                        break
            except Exception:                                          # noqa: BLE001
                pass
            time.sleep(2)

    def sound(self, n: int) -> np.ndarray | None:
        """Sound n as float samples at 48 kHz, mono (decoded once, then from the asset folder)."""
        if n in self.cache:
            return self.cache[n]
        s = None
        try:
            wav = self.dir / f"se{n:06d}.wav"
            if wav.exists():
                with wave.open(str(wav), "rb") as w:
                    rate, ch = w.getframerate(), w.getnchannels()
                    pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, ch)
            else:
                pcm, rate, _ = read_spw(sound_path(self.root, n).read_bytes())
                tmp = wav.with_suffix(".tmp")
                tmp.write_bytes(to_wav(pcm, rate))
                tmp.replace(wav)
            s = pcm.astype(np.float32).mean(1) / 32768.0
            if rate != self.RATE and len(s) > 1:
                t = np.arange(int(len(s) * self.RATE / rate)) * (rate / self.RATE)
                s = np.interp(t, np.arange(len(s)), s).astype(np.float32)
        except Exception as e:                                         # noqa: BLE001
            print(f"[FFXI sound {n}: {e}]")
        self.cache[n] = s
        return s

    def spell_sounds(self, animation: int) -> list[int]:
        if animation not in self.spells:
            try:
                self.spells[animation] = spell_sounds(self.root, animation)
            except Exception as e:                                     # noqa: BLE001
                print(f"[FFXI spell effect {animation}: {e}]")
                self.spells[animation] = []
        return self.spells[animation]

    def prepare(self, animations) -> None:
        """Find and decode these spells' sounds now, on a thread of its own."""
        import threading

        def run():
            for rel in (LEVEL_UP, LEVEL_DOWN):
                for n in self.effect_sounds(rel):
                    self.sound(n)
            for a in animations:
                for n in self.spell_sounds(a):
                    self.sound(n)
        threading.Thread(target=run, daemon=True).start()

    def play(self, n: int) -> None:
        s = self.sound(n)
        if s is not None and self.stream is not None:
            with self.lock:
                self.voices.append([s, 0])

    def play_spell(self, animation: int) -> None:
        for n in self.spell_sounds(animation):
            self.play(n)

    def effect_sounds(self, rel: str) -> list[int]:
        """The sounds an effect file of the installation names (rel: ROM/.../n.DAT), found once."""
        key = ("file", rel)
        if key not in self.spells:
            try:
                self.spells[key] = effect_sounds((self.root / rel).read_bytes())
            except Exception as e:                                     # noqa: BLE001
                print(f"[FFXI effect {rel}: {e}]")
                self.spells[key] = []
        return self.spells[key]

    def play_effect(self, rel: str) -> None:
        for n in self.effect_sounds(rel):
            self.play(n)

    # --- music --------------------------------------------------------------------------------------
    def music_track(self, n: int) -> tuple[np.ndarray, int] | None:
        """Music n as float stereo at 48 kHz and its loop start: decoded once, kept in the asset folder."""
        if n in self.music_cache:
            return self.music_cache[n]
        got = None
        try:
            npy = self.music_dir / f"music{n:03d}.npy"
            if npy.exists():
                pcm = np.load(npy)
                meta = npy.with_suffix(".loop")
                loop = int(meta.read_text()) if meta.exists() else 0
            else:
                p = music_path(self.root, n)
                if p is None:
                    raise FileNotFoundError(f"music{n:03d}.bgw")
                pcm, rate, loop = read_bgw(p.read_bytes())
                if pcm.shape[1] == 1:
                    pcm = np.repeat(pcm, 2, 1)
                if rate != self.RATE:                                  # to the output's rate
                    k = self.RATE / rate
                    t = np.arange(int(len(pcm) * k)) / k
                    pcm = np.stack([np.interp(t, np.arange(len(pcm)), pcm[:, c]) for c in range(2)], 1).astype(np.int16)
                    loop = int(loop * k) if loop >= 0 else 0
                tmp = self.music_dir / f"music{n:03d}.tmp.npy"
                np.save(tmp, pcm)
                tmp.replace(npy)
                npy.with_suffix(".loop").write_text(str(max(loop, 0)))
            got = (pcm.astype(np.float32) / 32768.0, max(0, min(loop, len(pcm) - 1)))
        except Exception as e:                                         # noqa: BLE001
            print(f"[FFXI music {n}: {e}]")
        self.music_cache[n] = got
        return got

    def play_music(self, n: int | None, fade: float = 1.5) -> None:
        """Music n in place of what's playing (None: silence), crossfading over fade seconds; decoded
        on a thread of its own the first time, so the game doesn't wait."""
        import threading
        self.music_want = n
        if self.stream is None:
            return

        def start():
            track = self.music_track(n) if n is not None else None
            with self.lock:
                if self.music_want != n:                               # asked for another meanwhile
                    return
                step = 1.0 / max(1.0, fade * self.RATE)
                for m in self.music:
                    m["step"] = -step
                if track is not None:
                    self.music.append({"n": n, "pcm": track[0], "loop": track[1], "at": 0, "gain": 0.0, "step": step})
        if n is not None and n not in self.music_cache:
            threading.Thread(target=start, daemon=True).start()
        else:
            start()

    def prepare_music(self, numbers) -> None:
        """Decode these pieces now, one after another, on a thread of their own."""
        import threading
        todo = [n for n in numbers if n is not None]

        def run():
            for n in todo:
                self.music_track(n)
        threading.Thread(target=run, daemon=True).start()

    def playing_music(self) -> int | None:
        with self.lock:
            live = [m for m in self.music if m["step"] >= 0]
            return live[-1]["n"] if live else None

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()


def main(argv=None) -> int:
    import ffxi_model as M
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ffxi", help="the FINAL FANTASY XI folder (else the launcher's)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wav")
    w.add_argument("number", type=int)
    mu = sub.add_parser("music")
    mu.add_argument("number", type=int)
    s = sub.add_parser("spell")
    s.add_argument("animation", type=int)
    a = ap.parse_args(argv)
    root = M.ffxi_root(a.ffxi)
    out = A.asset_dir() / "sounds"
    out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "music":
        p = music_path(root, a.number)
        if p is None:
            print(f"no music{a.number:03d}.bgw")
            return 1
        pcm, rate, loop = read_bgw(p.read_bytes())
        dest = A.asset_dir() / "music" / f"music{a.number:03d}.wav"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(to_wav(pcm, rate))
        print(f"music {a.number}: {len(pcm) / rate:.1f} s at {rate} Hz, {pcm.shape[1]} channel(s), loop at "
              f"{loop / rate:.1f} s, peak {np.abs(pcm.astype(np.int32)).max()} -> {dest}")
        return 0
    numbers = [a.number] if a.cmd == "wav" else spell_sounds(root, a.animation)
    if not numbers:
        print("no sounds")
    for n in numbers:
        pcm, rate, loop = read_spw(sound_path(root, n).read_bytes())
        dest = out / f"se{n:06d}.wav"
        dest.write_bytes(to_wav(pcm, rate))
        print(f"sound {n}: {len(pcm) / rate:.2f} s at {rate} Hz, {pcm.shape[1]} channel(s), "
              f"loop {loop}, peak {np.abs(pcm.astype(np.int32)).max()} -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
