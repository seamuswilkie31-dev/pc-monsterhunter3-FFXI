# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10", "pynput>=1.7", "dolphin-memory-engine>=1.3", "sounddevice>=0.4", "pycaw>=20240210"]
# ///
"""The launcher: your FFXI's assets first, then Monster Hunter Tri with the FFXI layer.

    uv run launcher.py          (or double-click "Play FFXI-MH3.bat" in the repository)

1. FFXI's interface art comes from your own installation of FINAL FANTASY XI (Square Enix's official
   download, playonline.com): point the launcher at its folder, and it catalogues and extracts what the
   layer uses into your local asset folder (ffxi_assets.py). Nothing of FFXI is in the repository, and
   nothing extracted goes into it. Without them, the layer draws its own lookalike.
2. Your character (character.py): FFXI's race, face and equipment for the one in your hunter's place.
3. Play: the port (build/recomp-build/wiiboot.exe) with the bridge and Monster Hunter's HUD switched
   off, then the layer; closing the game closes the layer.

Its own settings (the FFXI folder) live beside the extracted assets, not in the repository.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, ttk

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import ffxi_assets as A  # noqa: E402

SETTINGS = A.asset_dir() / "launcher.json"
DEFAULT_FFXI = [Path(r"C:\Program Files (x86)\PlayOnline\SquareEnix\FINAL FANTASY XI"),
                Path(r"C:\Program Files\PlayOnline\SquareEnix\FINAL FANTASY XI")]
MSYS = Path(r"C:\msys64\mingw64\bin")


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(s: dict) -> None:
    SETTINGS.write_text(json.dumps(s, indent=1), encoding="utf-8")


def assets_ready() -> bool:
    return (A.asset_dir() / A.CATALOGUE).exists()


class Launcher:
    def __init__(self):
        self.s = load_settings()
        if not self.s.get("ffxi"):
            self.s["ffxi"] = next((str(p) for p in DEFAULT_FFXI if (p / "ROM").is_dir()), "")
        self.root = tk.Tk()
        self.root.title("FFXI-MH3")
        self.root.configure(bg="#0b1030")
        self.root.resizable(False, False)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        for w in ("TLabel", "TCheckbutton"):
            style.configure(w, background="#0b1030", foreground="#e8ecf6", font=("Segoe UI", 10))
        style.configure("Head.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("TButton", font=("Segoe UI", 10, "bold"))
        f = ttk.Frame(self.root, padding=16, style="TLabel")
        f.pack()
        ttk.Label(f, text="Monster Hunter Tri  ×  FINAL FANTASY XI", style="Head.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="1. Your FFXI installation (the folder holding ROM):").grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 2))
        self.path = tk.StringVar(value=self.s["ffxi"])
        ttk.Entry(f, textvariable=self.path, width=62).grid(row=2, column=0, columnspan=2, sticky="we")
        ttk.Button(f, text="Browse…", command=self.browse).grid(row=2, column=2, padx=(6, 0))
        self.extract_btn = ttk.Button(f, text="Extract FFXI interface assets", command=self.extract)
        self.extract_btn.grid(row=3, column=0, sticky="w", pady=6)
        self.asset_state = tk.StringVar()
        ttk.Label(f, textvariable=self.asset_state).grid(row=3, column=1, columnspan=2, sticky="w")
        ttk.Label(f, text="2. Your character: race, face and equipment").grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 2))
        ttk.Button(f, text="Character…", command=self.character).grid(row=5, column=0, sticky="w")
        ttk.Label(f, text="3. Play:").grid(row=6, column=0, sticky="w", pady=(12, 2))
        self.hide_hud = tk.BooleanVar(value=self.s.get("hide_hud", True))
        ttk.Checkbutton(f, text="Hide Monster Hunter's HUD (the FFXI interface replaces it; F9 in the game swaps them)",
                        variable=self.hide_hud).grid(row=7, column=0, columnspan=3, sticky="w")
        self.ffxi_menus = tk.BooleanVar(value=self.s.get("ffxi_menus", True))
        ttk.Checkbutton(f, text="FFXI's colours on Monster Hunter's own menus and dialog boxes",
                        variable=self.ffxi_menus).grid(row=8, column=0, columnspan=3, sticky="w")
        # your name, as FFXI's interface and its combat log call you (else config.toml's name)
        named = ttk.Frame(f, style="TLabel")
        named.grid(row=9, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(named, text="Your name:").pack(side="left")
        self.name = tk.StringVar(value=self.s.get("name", ""))
        ttk.Entry(named, textvariable=self.name, width=18).pack(side="left", padx=(8, 6))
        ttk.Label(named, text="(15 letters at most; empty: the config's)").pack(side="left")
        # a linkpearl by your name, as a linkshell's member wears one: its colour chosen
        pearl = ttk.Frame(f, style="TLabel")
        pearl.grid(row=10, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.pearl_on = tk.BooleanVar(value=bool(self.s.get("linkpearl")))
        self.pearl_colour = self.s.get("linkpearl") or self.s.get("linkpearl_last") or "#3c8cff"
        ttk.Checkbutton(pearl, text="Show a linkpearl by your name", variable=self.pearl_on).pack(side="left")
        ttk.Button(pearl, text="Colour…", command=self.choose_pearl).pack(side="left", padx=(8, 4))
        self.pearl_swatch = tk.Canvas(pearl, width=22, height=22, bg="#0b1030", highlightthickness=0)
        self.pearl_swatch.pack(side="left")
        self.draw_pearl()
        self.gm = tk.BooleanVar(value=bool(self.s.get("gm")))         # a GM: the icon by your name, GM commands
        ttk.Checkbutton(pearl, text="GM (FFXI's GM icon, red name and aura; !additem in the chat line)",
                        variable=self.gm).pack(side="left", padx=(18, 0))
        self.play_btn = ttk.Button(f, text="Play", command=self.play)
        self.play_btn.grid(row=11, column=0, sticky="w", pady=8)
        self.status = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.status, wraplength=520).grid(row=12, column=0, columnspan=3, sticky="w")
        self.setup_btn = ttk.Button(f, text="Open the setup steps", command=self.open_setup)
        self.refresh()
        self.check_setup()

    def check_setup(self) -> bool:
        """Everything this PC needs (prereqs.py): what's missing named, SETUP-NEEDED.txt written for it."""
        import prereqs as P
        missing = P.check(self.path.get())
        self.setup_file = P.write_report(missing)
        if missing:
            names = "; ".join(m.what for m in missing)
            self.status.set(f"This PC still needs: {names}. See {self.setup_file.name} in the project folder.")
            self.setup_btn.grid(row=13, column=0, sticky="w", pady=(4, 0))
        else:
            self.setup_btn.grid_remove()
        return not missing

    def open_setup(self):
        p = getattr(self, "setup_file", None) or ROOT / "SETUP-INSTRUCTIONS.txt"
        os.startfile(str(p))

    def choose_pearl(self):
        rgb, hexa = colorchooser.askcolor(color=self.pearl_colour, title="Your linkpearl's colour", parent=self.root)
        if hexa:
            self.pearl_colour = hexa
            self.pearl_on.set(True)
            self.draw_pearl()

    def draw_pearl(self):
        c = self.pearl_swatch
        c.delete("all")
        c.create_oval(3, 3, 19, 19, fill=self.pearl_colour, outline="#1a1a2a")
        c.create_oval(6, 6, 11, 11, fill="#ffffff", outline="")

    def browse(self):
        d = filedialog.askdirectory(title="Your FINAL FANTASY XI folder (it holds ROM)",
                                    initialdir=self.path.get() or "C:/")
        if d:
            self.path.set(d)

    def character(self):
        p = Path(self.path.get())
        if not (p / "ROM").is_dir():
            self.status.set(f"{p} has no ROM folder. Point at the FINAL FANTASY XI folder of your installation first.")
            return
        import character as C
        C.Creator(self.root)

    def refresh(self):
        if assets_ready():
            n = len(A.load_catalogue()["images"])
            self.asset_state.set(f"ready: {n} images catalogued in {A.asset_dir()}")
        else:
            self.asset_state.set("not yet: the layer draws its own lookalike until then")

    def extract(self):
        p = Path(self.path.get())
        if not (p / "ROM").is_dir():
            self.status.set(f"{p} has no ROM folder. Point at the FINAL FANTASY XI folder of your installation.")
            return
        self.s["ffxi"] = str(p)
        save_settings(self.s)
        self.extract_btn.state(["disabled"])
        self.status.set("Cataloguing FFXI's images: a few minutes…")

        def work():
            r = subprocess.run([sys.executable, str(HERE / "ffxi_assets.py"), "scan", str(p)],
                               capture_output=True, text=True)
            if r.returncode == 0:                      # then the pieces the interface uses
                r = subprocess.run([sys.executable, str(HERE / "ffxi_assets.py"), "ui"], capture_output=True, text=True)
            msg = (r.stdout.strip().splitlines() or [""])[-1] if r.returncode == 0 else (r.stderr.strip() or "failed")
            self.root.after(0, lambda: (self.status.set(msg), self.extract_btn.state(["!disabled"]), self.refresh()))
        threading.Thread(target=work, daemon=True).start()

    def play(self):
        if not self.check_setup():
            return
        exe = ROOT / "build" / "recomp-build" / "wiiboot.exe"
        run = ROOT / "build" / "run1"
        if not exe.exists():
            self.status.set(f"The port isn't built: {exe} is missing (see the repository's README).")
            return
        self.s["hide_hud"] = self.hide_hud.get()
        self.s["linkpearl"] = self.pearl_colour if self.pearl_on.get() else ""
        self.s["linkpearl_last"] = self.pearl_colour
        self.s["gm"] = self.gm.get()
        self.s["name"] = self.name.get().strip()[:15]
        self.s["ffxi_menus"] = self.ffxi_menus.get()
        save_settings(self.s)
        try:                                           # the game's frames, before it reads them
            import brres_tex as B
            B.ffxi_look(self.s["ffxi_menus"])
        except Exception as ex:                        # noqa: BLE001  the game plays either way
            print(f"[the game's frames left as they are: {ex}]")
        run.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, MH3_FFXI="1")
        if self.hide_hud.get():
            env["MH3_HIDE_HUD"] = "1"
        if MSYS.is_dir():
            env["PATH"] = f"{MSYS};{env.get('PATH', '')}"
        game = subprocess.Popen([str(exe), "../extract", "--symbols", "../recomp/symbols.tsv"], cwd=run, env=env,
                                stdout=open(run / "wiiboot.log", "w"), stderr=subprocess.STDOUT)
        if self.s["linkpearl"]:
            env["FFXI_LINKPEARL"] = self.s["linkpearl"]
        if self.s.get("gm"):
            env["FFXI_GM"] = "1"
        if self.s["name"]:
            env["FFXI_NAME"] = self.s["name"]
        layer = subprocess.Popen([sys.executable, str(HERE / "ffxi_mh3.py")], cwd=HERE, env=env,
                                 stdout=open(run / "ffxi.log", "w"), stderr=subprocess.STDOUT)
        self.play_btn.state(["disabled"])
        self.status.set("Playing. Closing the game closes the FFXI layer.")

        def wait():
            game.wait()
            layer.terminate()
            self.root.after(0, lambda: (self.status.set(f"The game closed (exit {game.returncode})."),
                                        self.play_btn.state(["!disabled"])))
        threading.Thread(target=wait, daemon=True).start()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Launcher().run()
