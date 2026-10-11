# Monster Hunter Tri × Final Fantasy XI

**Monster Hunter Tri running natively on Windows, played as Final Fantasy XI.** Your FFXI character takes
the hunter's place. FFXI's menus, combat, spells, Trusts, sounds and music come with it, all inside
Monster Hunter's world.

> **A free fan project. No game files are included.** You need your own Monster Hunter Tri disc (the
> European release) and your own FINAL FANTASY XI installation. It is not affiliated with or endorsed by
> Capcom, Square Enix or Nintendo.

## What it is

There are two parts:

- **The port.** Monster Hunter Tri's own PowerPC code is translated to C++ and compiled for the PC
  (static recompilation). It runs on [wiikit](https://github.com/vs-sr-dev/wiikit), which stands in for
  the Wii's hardware and system software. There is no emulator: the game runs natively at 16:9 and its own
  30 frames a second, with an Xbox-style pad as the Classic Controller. The game is built on your PC from
  your own disc, so nothing of it is ever downloaded.
- **The FFXI layer** (`ffxi/`). It runs beside the game and makes it look, sound and fight like FFXI,
  using the files of your own FFXI installation.

## Features

- **Your FFXI character** in the hunter's place. Pick any race, face and gear in the launcher's character
  creator. It comes with FFXI's round shadow, your nameplate, the race's voice and FFXI's level-up banner
  and fanfare.
- **FFXI's interface** in the game's window: the command menu, status, equipment and items with FFXI's
  icons, the combat log, the target window, macro bars and Vana'diel's clock. F9 swaps back to Monster
  Hunter's own HUD at any time.
- **FFXI combat.** Auto-attack, TP, weapon skills with FFXI's own animations, skillchains and magic
  bursts. Spells have cast times and can be interrupted. There are six jobs (WAR, DRK, PLD, RDM, WHM,
  BLM) and a subjob. FFXI's damage lands as real hits: monsters flinch, fall and can be carved.
- **Trusts** fight and cast beside you, and your avatar or wyvern can be seen at your side.
- **Hunting the FFXI way.** The d-pad picks your target, and the next monster is targeted after a kill.
  Fallen monsters are carved for you. Drops go to a treasure pool (`/lot`, `/pass`).
- **FFXI's sounds and music**: each spell's own sound, plus village, field and battle music.
- **Extras**: a linkpearl colour by your name, GM mode (red name, GM aura, `!` commands), the game's own
  menus recoloured in FFXI's colours, and chat-line commands (`/heal`, `/lockstyle`, `/voice`, emotes).
- **Monster Hunter's combat** is one switch away (the menu's Config, or `/combat mh`).

## What you need

- Windows 10 or 11, 64-bit, with a graphics card that supports OpenGL 4.5, and about 10 GB free.
- **Monster Hunter Tri, European release (RMHP08)**, as a disc image made from your own disc (`.rvz`,
  `.iso`, `.wbfs` or `.wia`). The North American (RMHE08) and Japanese releases don't work yet.
- **FINAL FANTASY XI**, installed with Square Enix's official installer. Only its files are read; the game
  never connects to FFXI's servers.
- An Xbox-style controller (recommended).

## Install

1. **Download** this project: the green **Code** button, then **Download ZIP**, and extract it somewhere
   such as your user folder. Or, with Git:
   `git clone --recurse-submodules https://github.com/seamuswilkie31-dev/pc-monsterhunter3-FFXI.git`
2. **Double-click `Setup.bat`.** It:
   - installs what's needed (uv, and MSYS2's compiler)
   - asks for your disc image and builds the game from it (15 to 40 minutes the first time)
   - looks for FFXI, and opens its installer page if it isn't installed
   - starts the launcher

   If it stops, it says why. Fix that and run it again; it carries on where it stopped.
3. **In the launcher**:
   - point it at your FFXI folder
   - click **Extract FFXI interface assets** (once)
   - make your character with **Character…**
   - click **Play**

Next time, just double-click **`Play FFXI-MH3.bat`**. After updating the project, run
**`Build the game.bat`** again. [SETUP-INSTRUCTIONS.txt](SETUP-INSTRUCTIONS.txt) has every step by hand.

## Controls

With an Xbox-style pad, in FFXI combat:

| Control | What it does |
|---|---|
| Hold **X** | FFXI's menu: the d-pad moves, **Y** chooses, **X** goes back |
| **D-pad** | Picks a target |
| Hold **LT** or **RT**, then a d-pad or face button | That macro bar's macro |
| Hold **LB** | Locks on to the target |
| **F9** | Swaps between FFXI's interface and Monster Hunter's HUD |
| **/** on the keyboard | The chat line: `/ma "Cure"`, `/ws`, `/heal`, `/lot`, `/combat mh`… |
| **3 to 6** and **7 to 0, - = [ ]** | Weapon skills, and spells |
| **F11** | Fullscreen |

In Monster Hunter combat, the pad is Monster Hunter's own. The Home button is disabled: its menu crashes
the port.

## How it works

- **The port.** wiikit translates the game's executable and its 21 code modules to C++ from your own disc,
  and clang compiles them. Its runtime draws the Wii's graphics with OpenGL, plays its sound and reads the
  pad. See [docs/](docs/) and the [original port](https://github.com/vs-sr-dev/pc-monsterhunter3).
- **The bridge** ([tools/ffxi_bridge.cpp](tools/ffxi_bridge.cpp)) is built into the port. It hooks the
  game's code (monster damage, the hunter, menus, cameras, the pad) and shares a block of memory with the
  layer. Through it, the layer reads the game and sends back FFXI's hits, targets and button presses.
- **The layer** ([ffxi/](ffxi/), Python) runs FFXI's rules ([ffxi_engine.py](ffxi/ffxi_engine.py),
  [ffxi_data.py](ffxi/ffxi_data.py)). It reads models, animations, effects, sounds and interface art from
  your FFXI installation and has the port draw them into the game's frame.
  [ffxi/NEXT.md](ffxi/NEXT.md) is its change log.

## Credits

- **Seamus** ([seamuswilkie31-dev](https://github.com/seamuswilkie31-dev)): leads the FFXI project, and
  built, play-tested and shipped every version.
- **River**: the ideas and FFXI know-how behind the layer, including the feature lists, the bug reports
  and the testing.
- **Samuele Voltan** ([vs-sr-dev](https://github.com/vs-sr-dev)): wiikit, and the original Monster Hunter
  Tri PC port that all of this is built on.

Special thanks to **Claude Opus** ([Anthropic](https://www.anthropic.com/claude)). Working in Claude Code,
it wrote much of the FFXI layer, the bridge and the setup alongside Seamus and River.

With thanks to:

- [LandSandBoat](https://github.com/LandSandBoat/server). Its public database is the reference for spell,
  ability, Trust and gear numbers. It is downloaded from there when first needed and not included here.
- [Dolphin](https://dolphin-emu.org/). Its database of the Wii SDK's functions and its free fonts are used
  when building, taken from your Dolphin or from Dolphin's GitHub.

## Legal

- This repository holds only its own code and documentation. The game is built from your own disc on your
  own PC, and the layer reads your own FFXI installation. Don't ask for or share game files.
- Monster Hunter Tri is © CAPCOM. FINAL FANTASY XI is © SQUARE ENIX. Wii is a trademark of Nintendo. This is
  a non-commercial fan project, not affiliated with or endorsed by any of them.
- The [MIT licence](LICENSE) covers this repository's own code and documentation only.
