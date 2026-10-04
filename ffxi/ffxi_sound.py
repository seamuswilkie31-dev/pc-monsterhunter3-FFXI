# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26"]
# ///
"""FFXI's sound effects from your own installation: read, decoded, played.

    uv run ffxi_sound.py wav 3037            # sound 3037 (sound/win/se/se003/se003037.spw) -> a WAV
    uv run ffxi_sound.py spell 1             # the sounds spell animation 1 (Cure) plays, as WAVs

WAVs go to the local asset folder (sounds/), never into the repository.

The formats, as the community describes them (written here from that description, no code taken):
  a sound effect (.spw): "SeWave\\0\\0", u32 file size, u32 format (0: ADPCM), u32 its number, u32
    blocks, u32 loop start (in blocks; -1: none), two u32 whose sum (mod 2^32) is the sample rate,
    u32 the data's offset, then bytes: ?, ?, channels, ...; the data from there. ADPCM as the
    PlayStation's: blocks of 9 bytes, 16 samples, the first byte a filter (high nibble) and a shift
    (low nibble), then 16 four-bit steps, the low nibble first; channels' blocks interleaved.
  a spell's effect (file 2800 + its animation number, through FTABLE/VTABLE): a 0x3D chunk "SeSep"
    names the sound it plays (u32 after the 8-byte tag).
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
# the filters, as fractions of 64 of the two samples before
FILTERS = np.array([(0, 0), (60, 0), (115, -52), (98, -55), (122, -60)], np.int64)


def sound_path(root: Path, n: int) -> Path:
    return root / "sound" / "win" / "se" / f"se{n // 1000:03d}" / f"se{n:06d}.spw"


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

    def __init__(self, root: Path, volume: float = 0.7):
        import threading
        self.root, self.volume = root, volume
        self.cache: dict[int, np.ndarray | None] = {}
        self.spells: dict[int, list[int]] = {}
        self.voices: list[list] = []                                   # [samples, position]
        self.lock = threading.Lock()
        self.dir = A.asset_dir() / "sounds"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.stream = None
        try:
            import sounddevice as sd
            self.stream = sd.OutputStream(samplerate=self.RATE, channels=2, dtype="float32", callback=self._fill)
            self.stream.start()
        except Exception as e:                                         # noqa: BLE001
            print(f"[FFXI sounds: no audio output ({e})]")

    def _fill(self, out, frames, _time, _status):
        mix = np.zeros(frames, np.float32)
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
        np.clip(mix * self.volume, -1.0, 1.0, out=mix)
        out[:, 0] = mix
        out[:, 1] = mix

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
    s = sub.add_parser("spell")
    s.add_argument("animation", type=int)
    a = ap.parse_args(argv)
    root = M.ffxi_root(a.ffxi)
    out = A.asset_dir() / "sounds"
    out.mkdir(parents=True, exist_ok=True)
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
