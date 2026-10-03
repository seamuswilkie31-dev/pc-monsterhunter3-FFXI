# FFXI layer — next session

Where it stands (2026-10-03): the port builds and boots on this PC with the bridge on (`MH3_FFXI=1`); the
layer reads the running game's memory through it; `memscan.py` takes snapshots of RAM and compares them.
`config.toml` has no addresses yet: that is the next step.

1. **The hunting session.** Launch the port with the bridge, play to a hunt with a **large** monster
   (Great Jaggi; a small Jaggi dies too fast to scan), and scan as in README section 2a: snapshot before
   hitting, after hits (`dec`), after waiting (`same`), until a few candidates are left; then `ptr` for a
   static pointer to the monster's data, checked across two quests. Then the player's HP.
2. **The first real test:** the layer with those addresses: TP from hits, a weapon skill landing.

## Found from the European Gecko codes (codes.rc24.xyz, RMHP08, Bully@Wiiplaza), checked in the disassembly

| | where | size | the game's function that changes it |
|---|---|---|---|
| hunter HP | hunter object (`_PLW*`) + `0x370` | s16 | `fn_802762F0(PLW*, short delta)`: adds, clamps at 0 and at max |
| hunter max HP | + `0x372` | s16 | |
| hunter red (recoverable) HP, likely | + `0x376` | s16 | |
| monster HP | monster object + `0x7A0` | s32 | `fn_8011E6BC(em*, delta, cap)`, called by `fn_8011E750` only |
| monster max HP, likely | + `0x7A4` | s32 | `+0x7B0` (float) is a ratio of it HP can't fall below |

The codes: Infinite Health `04276344` (the `add` in `fn_802762F0`), One Hit Kill `0411E6D4` (the `add` in
`fn_8011E6BC`). `Pl_master_ck(_PLW*)` (`8026FE38`) says whether a hunter object is the local player's.

Known problems in the port (not the layer):
- **Home crashes the game.** The Xbox button (or H) opens the Wii's Home Button menu, which crashes
  (a read at 0x00000008 in `GXCallDisplayList`, after `HBMCreate`). Avoid it; Esc is the port's own menu.
- **Saving is untested**, so each launch starts from a new character.
- The sound has short gaps (`docs/07-next-session.md`).

Setup on this PC: MSYS2 at `C:\msys64` (mingw64: clang, CMake, Ninja, SDL3), Dolphin 2609 at
`C:\Users\seamu\Tools\Dolphin-x64`. Run from `build/run1` with `C:\msys64\mingw64\bin` on the PATH.
