# FFXI layer for Monster Hunter Tri

Adds Final Fantasy XI combat to **Monster Hunter Tri**: TP from your hits, weapon skills, skillchains,
magic bursts, MP, spells with cast times and interrupts, and six jobs. A small FFXI-style overlay sits on
top of the game.

It runs beside the **native port** in this repository (the European release, `RMHP08`), which serves it
the game's memory through a small bridge (`tools/ffxi_bridge.cpp`). It can also run beside **Dolphin**
with the same disc, which is how you find the addresses it needs.

Personal use, single-player only. Nothing here touches FFXI's client, files or servers. The rules are
written from FFXI's published numbers.

## 1. Try it right now (no game needed)

Open PowerShell in this folder and run:

```powershell
uv run ffxi_mh3.py --sim
```

A fake fight starts: the "monster" loses HP every second as if you were hitting it, and hits you back now
and then. Watch TP build, then press **Numpad 1-4** for weapon skills and **Numpad 5-9, 0, /, \*** for spells.
Turn Num Lock on. Drag the overlay to move it, and double-click it to quit.

Try other jobs and weapons: `uv run ffxi_mh3.py --sim --job BLM --weapon hammer`

To try the port's bridge without the port, `fake_port.py` stands in for it with a fake hunt in fake RAM:

```powershell
uv run fake_port.py --write-config fake.toml
uv run ffxi_mh3.py --config fake.toml       # in a second window
uv run fake_port.py --test                  # the bridge's protocol checks
```

## 2a. Find the addresses in the port (no Dolphin)

`memscan.py` takes snapshots of the port's RAM through the bridge (88 MB each, in `build/scan`) and
compares them. Run the port with `MH3_FFXI=1`, start a hunt, then:

```powershell
uv run memscan.py snap s0        # before hitting the monster
uv run memscan.py snap s1        # after a few hits
uv run memscan.py filter s0 s1 dec --type u16 --range 1 60000
uv run memscan.py snap s2        # after waiting, no hits, not hit yourself
uv run memscan.py filter s1 s2 same
uv run memscan.py list           # repeat hit/wait until a few are left
uv run memscan.py ptr 0x90123456 # a static pointer to the struct holding it: address + offset for config.toml
```

## 2b. Find the addresses in Dolphin

The port has no memory scanner, so the addresses are found in Dolphin, with the same disc the port runs
(the `.rvz` in `build/`). The port runs the game's own code in the same address space, so the addresses
should be the same in both; check the first one you find in the port before relying on it.

You need:
- **Dolphin** with your own copy of MH Tri (Europe, `RMHP08`).
- **Dolphin Memory Engine** (github.com/aldelaro5/dolphin-memory-engine).

Addresses from the US release (`RMHE08`), and US cheat codes, will not match: look for European ones.

### The monster's HP
1. Start a hunt in Dolphin. In Dolphin Memory Engine, click **Hook**.
2. Scan type **Unknown initial value**, type **2 bytes**, then **First scan**.
3. Hit the monster, then scan **Decreased**. Wait without hitting, then scan **Unchanged**.
   Repeat until only a few results are left.
4. Add the candidates to the watch list and change one with a small edit. If the monster dies sooner or the
   number tracks your hits, you've found it.
5. Monster data lives on the heap and moves between quests, so the address will change in the next quest.
   Find a **pointer** to it: a static address (in the executable's data) that holds the start of the
   monster's data, plus the offset of HP inside it. Put the base address and offsets in `config.toml`.
   Check it across two quests.

Do the same for **your HP** (scan **Decreased** after the monster hits you, **Increased** after a potion).
Your HP is often a simple, fixed address.

**Shortcut:** existing MH Tri **European** cheat codes like "Infinite Health" or "One-Hit Kill" already point
at these values. The address inside the code is a good place to start looking.

**Better, later:** the instruction that writes the monster's HP (Dolphin's debugger, a memory breakpoint
on the address) names the game's damage function. Hooking it in the port (`tools/mh3-hooks.txt`) gives
every hit exactly, with no pointer chain: that is phase 2.

## 3. Run it with the port

Run the port with the bridge on (it is off otherwise), then the layer:

```powershell
$env:MH3_FFXI = "1"
$env:PATH = "C:\msys64\mingw64\bin;$env:PATH"     # SDL3 and the other DLLs the build links
cd build\run1; & ..\recomp-build\wiiboot.exe ..\extract --symbols ..\recomp\symbols.tsv
```

```powershell
uv run ffxi_mh3.py
```

The port logs `ffxi: serving guest memory to the FFXI layer`, and the overlay shows "connected" once the
game is running. Either may start first.

With Dolphin instead: `uv run ffxi_mh3.py --backend dolphin`.

## Settings (`config.toml`)
- `backend`: `port` (default) or `dolphin`.
- `job` and `weapon`: match the MH weapon you're using so the right weapon skills show up.
- `[addresses.*]`: what you found above. `type` is usually `u16`. The bridge serves 8 addresses at most,
  each with up to 4 offsets.
- `[hotkeys]`: Windows key codes. The F-keys are avoided because Dolphin uses them for save states, and the
  port uses F11 and F12.

Edit `ffxi_data.py` to rebalance anything: TP rates, weapon skill strength, spell costs, jobs, the
skillchain table.

## How it works
- **TP:** every drop in the monster's HP counts as one of your hits and gives TP based on your weapon's
  FFXI delay. Getting hit gives a little TP too.
- **Weapon skills** need 1000 TP and use all of it. Damage = your recent average hit × the weapon skill's
  fTP at your TP level × your job's multiplier, so it scales with your gear and the monster.
- **Skillchains:** a second weapon skill 2-8 seconds after the first, with matching properties, adds
  50-100% bonus damage and opens a magic-burst window.
- **Magic bursts:** a nuke of a matching element inside that window does +35%.
- **Spells** have cast times. Taking damage interrupts them, and MP is only spent when they go off.
- By default the layer won't land the killing blow (it stops at 1 HP), so the game's own death and carve
  logic always runs. Use `--allow-kills` to change that.
- **Numpad .** pauses and resumes the layer.

### The bridge
`tools/ffxi_bridge.cpp` is linked into the port (`tools/mh3.cmake`) and starts only with `MH3_FFXI=1`.
It makes a shared memory block named `mh3-ffxi`; the layer (`port_bridge.py`) writes what to watch into
it, and a thread in the port resolves each address and pointer chain 60 times a second, publishes the
values, and applies the layer's writes. Like Dolphin Memory Engine, it is not in step with the game's
frames.

## Commands
| Command | What it does |
|---|---|
| `uv run ffxi_mh3.py --sim` | Fake fight, no game |
| `uv run ffxi_mh3.py` | The port (run with `MH3_FFXI=1`) |
| `--backend dolphin` | Dolphin instead of the port |
| `--job WAR/DRK/PLD/RDM/WHM/BLM` | Pick a job |
| `--weapon great_sword/long_sword/...` | Pick a weapon |
| `--no-overlay` | Text output in the console instead |
| `--selftest` | Check the rules engine |
| `uv run fake_port.py --test` | Check the bridge's protocol |

## Next steps
- **Phase 2:** the rules in the port itself (C++), run on a hooked per-frame function; hits from the game's
  damage function; weapon skills and spells on Classic Controller chords (`wpad_set_classic_filter`).
- **Phase 3:** the overlay drawn in the port's own window (a general overlay hook in wiikit's renderer).
- Find **stamina** and **attack/defense** addresses to make Haste, Protect and Berserk real stat changes.
