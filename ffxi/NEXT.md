# FFXI layer — next session

Where it stands (2026-10-04): **phase 1 works in the game.** The port's hooks find the hunter (HP 100/100
on a new character) and the monster the game last hurt (Aptonoth, Jaggi); TP builds from hits, and a
weapon skill's damage is written back and applied (a Jaggi taken down, the game's own death running).
Hotkeys are on the number row (3-6 weapon skills, 7-0 - = [ ] spells, P pause): no numpad needed.

Next:
1. **Spells and skillchains in play**: Cure on a hit hunter (player HP written), a nuke, two weapon skills
   2-8 s apart, a magic burst. A large monster (Great Jaggi) to have the time.
2. **Phase 2**: weapon skills and spells on Classic Controller chords instead of the keyboard
   (`wpad_set_classic_filter`), the rules in the port, the overlay in the game's window (phase 3).
3. Small monsters share the HP function, so the last one hit is the target; filter by max HP (+0x7A4)
   if that gets in the way.

## Found from the European Gecko codes (codes.rc24.xyz, RMHP08, Bully@Wiiplaza), checked in the disassembly

| | where | size | the game's function that changes it |
|---|---|---|---|
| hunter HP | hunter object (`_PLW*`) + `0x370` | s16 | `fn_802762F0(PLW*, short delta)`: adds, clamps at 0 and at max |
| hunter max HP | + `0x372` | s16 | |
| hunter red (recoverable) HP, likely | + `0x376` | s16 | |
| monster HP | monster object + `0x7A0` | s32 | `fn_8011E6BC(em*, delta, cap)`, called by `fn_8011E750` only |
| monster max HP | + `0x7A4` | s32 | confirmed in play (the target window's percent); `+0x7B0` (float) is a ratio of it HP can't fall below |

The codes: Infinite Health `04276344` (the `add` in `fn_802762F0`), One Hit Kill `0411E6D4` (the `add` in
`fn_8011E6BC`). `Pl_master_ck(_PLW*)` (`8026FE38`) says whether a hunter object is the local player's.

**Saving works** (2026-10-04): a save in Moga Village writes `build/nand/title/00010000/524d4850/data/nocopy/data00`
(plain, unencrypted) and loads back after a restart. Tests start from the save, not a new character.

Known problems in the port (not the layer):
- **Home crashes the game.** The Xbox button (or H) opens the Wii's Home Button menu, which crashes
  (a read at 0x00000008 in `GXCallDisplayList`, after `HBMCreate`). Avoid it; Esc is the port's own menu.
- The sound has short gaps (`docs/07-next-session.md`).

Setup on this PC: MSYS2 at `C:\msys64` (mingw64: clang, CMake, Ninja, SDL3), Dolphin 2609 at
`C:\Users\seamu\Tools\Dolphin-x64`. Run from `build/run1` with `C:\msys64\mingw64\bin` on the PATH.
