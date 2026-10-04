# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10"]
# ///
"""Your character: FFXI's race, face and equipment for the one who stands in your hunter's place.

    uv run character.py          (or the launcher's Character... button)

Every race of FFXI, and each slot's look by its model number, read from your own installation and shown
as you choose them (front and side, standing, in battle, casting or swinging). Save writes them into
config.toml (character_race, character_look); the layer uses them the next time it starts.
"""
from __future__ import annotations

import random
import re
import sys
import threading
import tkinter as tk
from functools import lru_cache
from pathlib import Path
from tkinter import ttk

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ffxi_model as M  # noqa: E402

CONFIG = HERE / "config.toml"
# each slot's numbers: armour 0-255, weapons 0-511 (the first block of the client's tables)
SLOT_RANGE = {"face": 15, "head": 255, "body": 255, "hands": 255, "legs": 255, "feet": 255, "main": 511, "sub": 511}
POSES = {"Standing": ("idl", 0.0), "In battle": ("btl", 0.0), "Casting": ("mw1", 0.5), "Swinging": ("at0", 0.45)}
VIEW = 280


def face_name(n: int) -> str:
    """FFXI's own name for a face: 1A, 1B ... 8B."""
    return f"{n // 2 + 1}{'AB'[n % 2]}"


def read_config() -> dict:
    import tomllib
    try:
        return tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def toml_value(v) -> str:
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    return str(v)


def write_config(values: dict) -> None:
    """Each key's line in config.toml given its new value, its comment kept; a key not there added."""
    text = CONFIG.read_text(encoding="utf-8")
    for key, v in values.items():
        line = re.compile(rf"^({re.escape(key)}\s*=\s*)([^#\n]*?)(\s*#[^\n]*)?$", re.M)
        new = toml_value(v)
        if line.search(text):
            text = line.sub(lambda m: m.group(1) + new + (m.group(3) or ""), text, count=1)
        else:
            text = text.rstrip("\n") + f"\n{key} = {new}\n"
    CONFIG.write_text(text, encoding="utf-8")


@lru_cache(maxsize=512)
def _load(path: str) -> dict:
    return M.load(Path(path))


def cached_load(path: Path) -> dict:
    return _load(str(path))


class Creator:
    def __init__(self, master: tk.Misc | None = None):
        cfg = read_config()
        self.root_dir = M.ffxi_root(cfg.get("ffxi_path"))
        self.win = tk.Toplevel(master) if master else tk.Tk()
        self.win.title("FFXI-MH3: your character")
        self.win.configure(bg="#0b1030")
        self.win.resizable(False, False)
        style = ttk.Style(self.win)
        if not master:
            style.theme_use("clam")
        for w in ("TLabel", "TCheckbutton", "TRadiobutton"):
            style.configure(w, background="#0b1030", foreground="#e8ecf6", font=("Segoe UI", 10))
        style.configure("Head.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("TButton", font=("Segoe UI", 10, "bold"))

        f = ttk.Frame(self.win, padding=14, style="TLabel")
        f.pack()
        ttk.Label(f, text="Your character", style="Head.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        race = cfg.get("character_race") or "hume_male"
        look = {**M.DEFAULT_LOOK, **cfg.get("character_look", {})}
        self.labels = {r["label"]: k for k, r in M.RACES.items()}
        self.race = tk.StringVar(value=M.RACES.get(race, M.RACES["hume_male"])["label"])
        ttk.Label(f, text="Race").grid(row=1, column=0, sticky="w", pady=(10, 2))
        box = ttk.Combobox(f, textvariable=self.race, values=list(self.labels), state="readonly", width=20)
        box.grid(row=1, column=1, columnspan=2, sticky="w", pady=(10, 2))
        box.bind("<<ComboboxSelected>>", lambda _e: self.changed())

        self.vars: dict[str, tk.IntVar] = {}
        row = 2
        for slot in M.SLOTS:
            ttk.Label(f, text=slot.capitalize()).grid(row=row, column=0, sticky="w", pady=2)
            v = self.vars[slot] = tk.IntVar(value=int(look.get(slot, 0)))
            sp = ttk.Spinbox(f, from_=0, to=SLOT_RANGE[slot], textvariable=v, width=6, command=self.changed)
            sp.grid(row=row, column=1, sticky="w")
            sp.bind("<Return>", lambda _e: self.changed())
            sp.bind("<FocusOut>", lambda _e: self.changed())
            if slot == "face":
                self.face_label = ttk.Label(f, text="")
                self.face_label.grid(row=row, column=2, sticky="w")
            row += 1
        ttk.Label(f, text="Outfit").grid(row=row, column=0, sticky="w", pady=(8, 2))
        self.outfit = tk.IntVar(value=int(look.get("body", 0)))
        sp = ttk.Spinbox(f, from_=0, to=255, textvariable=self.outfit, width=6, command=self.outfit_changed)
        sp.grid(row=row, column=1, sticky="w", pady=(8, 2))
        sp.bind("<Return>", lambda _e: self.outfit_changed())
        ttk.Label(f, text="body, hands, legs, feet together").grid(row=row, column=2, sticky="w", pady=(8, 2))
        row += 1

        ttk.Label(f, text="Preview").grid(row=row, column=0, sticky="w", pady=(10, 2))
        self.pose = tk.StringVar(value="Standing")
        pf = ttk.Frame(f, style="TLabel")
        pf.grid(row=row, column=1, columnspan=3, sticky="w", pady=(10, 2))
        for name in POSES:
            ttk.Radiobutton(pf, text=name, value=name, variable=self.pose, command=self.changed).pack(side="left", padx=(0, 6))
        row += 1
        buttons = ttk.Frame(f, style="TLabel")
        buttons.grid(row=row, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Button(buttons, text="Random", command=self.randomise).pack(side="left")
        ttk.Button(buttons, text="Save", command=self.save).pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=self.win.destroy).pack(side="left")
        row += 1
        self.status = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.status, wraplength=420).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.canvas = tk.Canvas(f, width=2 * VIEW, height=VIEW, bg="#283c30", highlightthickness=0)
        self.canvas.grid(row=1, column=3, rowspan=row, padx=(16, 0), sticky="n")
        self.photo = None
        self.pending = None
        self.busy = False
        self.again = False
        self.changed()

    # ---------------------------------------------------------------------------------------------
    def look(self) -> dict:
        out = {}
        for slot, v in self.vars.items():
            try:
                out[slot] = max(0, min(SLOT_RANGE[slot], int(v.get())))
            except (tk.TclError, ValueError):
                out[slot] = 0
        return out

    def race_key(self) -> str:
        return self.labels.get(self.race.get(), "hume_male")

    def outfit_changed(self):
        try:
            n = int(self.outfit.get())
        except (tk.TclError, ValueError):
            return
        for slot in ("body", "hands", "legs", "feet"):
            self.vars[slot].set(n)
        self.changed()

    def randomise(self):
        n = random.randint(1, 48)
        for slot in ("body", "hands", "legs", "feet", "head"):
            self.vars[slot].set(n if slot != "head" else random.choice([0, n]))
        self.vars["face"].set(random.randint(0, 15))
        self.outfit.set(n)
        self.changed()

    def changed(self):
        self.face_label.config(text=f"FFXI's {face_name(self.look()['face'])}")
        if self.pending:
            self.win.after_cancel(self.pending)
        self.pending = self.win.after(250, self.render)

    def render(self):
        self.pending = None
        if self.busy:                                 # one at a time: the newest choice after this one
            self.again = True
            return
        self.busy, self.again = True, False
        race, look, pose = self.race_key(), self.look(), POSES[self.pose.get()]
        self.status.set("Drawing…")

        def work():
            try:
                img = self.picture(race, look, pose)
                msg = f"{M.RACES[race]['label']}, face {face_name(look['face'])}."
            except Exception as e:                    # noqa: BLE001
                img, msg = None, f"Couldn't draw it: {e}"
            self.win.after(0, lambda: self.show(img, msg))
        threading.Thread(target=work, daemon=True).start()

    def picture(self, race: str, look: dict, pose: tuple[str, float]) -> np.ndarray:
        model = M.load_character(self.root_dir, race, look, loader=cached_load)
        sk = model["skeletons"][0]
        clip = next((c for c in model["clips"] if c["name"] == pose[0]), None)
        world = M.pose_world(sk, *M.local_pose(sk, clip["parts"], pose[1])) if clip else M.joint_world(sk)
        front = M.render(model, VIEW, yaw=90, world=world)
        side = M.render(model, VIEW, yaw=40, world=world)
        return np.concatenate([front, side], 1)

    def show(self, img, msg):
        from PIL import Image, ImageTk
        self.busy = False
        if img is not None:
            self.photo = ImageTk.PhotoImage(Image.fromarray(img))
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.status.set(msg)
        if self.again:
            self.render()

    def save(self):
        look = self.look()
        write_config({"character_race": self.race_key(), "character_look": look})
        self.status.set("Saved to config.toml: it shows the next time you press Play (or the layer restarts).")

    def run(self):
        self.win.mainloop()


if __name__ == "__main__":
    Creator().run()
