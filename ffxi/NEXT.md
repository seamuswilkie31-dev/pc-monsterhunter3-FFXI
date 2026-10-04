# FFXI layer — next session

Where it stands (2026-10-04): **the FFXI interface runs in the game.** Monster Hunter's HUD is switched
off (`MH3_HIDE_HUD=1`); the layer draws FFXI's classic windows into the game's own window (status, target
with the monster's HP percent, combat log, casting bar, macro palette); the pad's **X held** opens the FFXI
command menu (a tap of X is still the game's: sheathe, use an item) (Weapon Skills / Magic / Items: d-pad, A to choose, B back), Items passing one press to the
game's own item use. Saving works. Checked in play.

Run: the port with `MH3_FFXI=1 MH3_HIDE_HUD=1`, then `uv run ffxi_mh3.py` (in-game interface by default).

**FFXI's character in the hunter's place** (stages A and B done, 2026-10-04, checked in play): the layer
reads `character_model` (a male Hume NPC, `ROM/3/6.DAT`) from the FFXI installation (`ffxi_model.py`),
packs it in the hunter's space (`pack`: feet at 0, 175 tall, FFXI's PS2 alpha put on the usual scale) and
hands it to the port (`port_model.py`, block `mh3-ffxi-model`); wiikit draws it into the scene before
each XFB copy (`video_model_*`), depth-tested, with the gameplay camera's view and projection. The bridge
hooks `ScnMdl::CalcWorldMtx` (`mh3_ScnMdlCalcWorldMtx`): the body's world matrix places the Hume, and
every piece of Monster Hunter's hunter gets the no-draw flags (ScnObj `+0xCC` |= 0x60). The body and
weapons stand at the hunter; the armour's pieces stand at the origin and are found through their
callback (ScnMdl `+0x11C`, whose `+8`, or `+4` for a weapon, is the model owner with the hunter's
position at `+4`). Skeletons still run: hits and weapons as before.
Next for it: **stage C**, FFXI's motions (`idl0`, `wlk0`, `run0` in the same DAT) chosen by the hunter's
speed, skinned each frame (the mesh is sent once; poses as joint matrices); **stage D**, a player
character built from equipment DATs, weapons in hand, attack motions.

**Open: a crash on entering a quest's area the second time** (2026-10-03, in play). The Deserted
Island (`map01`) loaded for a quest, back to the village, then `map01` again: during that load the game
stopped, `g3d_restex_ac.h:38 Panic: ResTex::ref: Object not valid` (a model's texture not loaded), from
`fn_800CE704` <- `fn_800416F8` <- `fn_80046D98`. The log is kept in `build/run1/crash_20261003_restex.log`.
Not yet known whether it is the port's (only one quest load was tried before) or `MH3_HIDE_HUD`'s (the
one change near the game's resources). The panic comes from finishing an asynchronous disc read
(`fn_800CE704`: `DVDReadAsync`, then the file's resources registered, `nwAddResource`, and its textures
checked): the file's data was damaged in memory after loading. Not the layer's writes: none in the ~26
minutes before (the recording). Still, the bridge now believes a hook's object only while it looks like
one (a hunter: HP <= max, max 50-200; a monster: HP <= max, max 10-1000000), so a freed object's memory,
reused for a file, is never written. Next step: the same sequence with `MH3_RES_TRACE=1` (logs every
registered resource: the damaged file's name), with and without `MH3_HIDE_HUD`.

A fight recording (`--record`) of the session is in `build/run1/fight_20261003_1536.jsonl` (village,
Moga Woods, the start of a mushroom quest; no large monster yet).

Next:
1. **The target's name** ("Great Jaggi", not "Target"): which monster an object is.
2. **Reskinning Monster Hunter's menus** in FFXI's manner: frames (`waku`), panels and words (`menu`),
   with `brres_tex.py` (`UI-MAP.md`).
3. Spells and skillchains in a long fight (a Great Jaggi), and balance (`ffxi_data.py`).
4. Small monsters share the HP function, so the last one hit is the target; filter by max HP if needed.

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
