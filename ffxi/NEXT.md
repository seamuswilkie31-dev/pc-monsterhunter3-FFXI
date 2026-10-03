# FFXI layer — next session

Where it stands (2026-10-03): the port builds and boots on this PC with the bridge on (`MH3_FFXI=1`); the
layer reads the running game's memory through it; `memscan.py` takes snapshots of RAM and compares them.
`config.toml` has no addresses yet: that is the next step.

1. **The hunting session.** Launch the port with the bridge, play to a hunt with a **large** monster
   (Great Jaggi; a small Jaggi dies too fast to scan), and scan as in README section 2a: snapshot before
   hitting, after hits (`dec`), after waiting (`same`), until a few candidates are left; then `ptr` for a
   static pointer to the monster's data, checked across two quests. Then the player's HP.
2. **The first real test:** the layer with those addresses: TP from hits, a weapon skill landing.

Known problems in the port (not the layer):
- **Home crashes the game.** The Xbox button (or H) opens the Wii's Home Button menu, which crashes
  (a read at 0x00000008 in `GXCallDisplayList`, after `HBMCreate`). Avoid it; Esc is the port's own menu.
- **Saving is untested**, so each launch starts from a new character.
- The sound has short gaps (`docs/07-next-session.md`).

Setup on this PC: MSYS2 at `C:\msys64` (mingw64: clang, CMake, Ninja, SDL3), Dolphin 2609 at
`C:\Users\seamu\Tools\Dolphin-x64`. Run from `build/run1` with `C:\msys64\mingw64\bin` on the PATH.
