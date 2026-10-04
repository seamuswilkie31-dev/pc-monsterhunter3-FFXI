# FFXI layer — next session

Where it stands (2026-10-04): **the FFXI interface runs in the game, with an FFXI character of any race in your hunter's place.** Monster Hunter's HUD is switched
off (`MH3_HIDE_HUD=1`); the layer draws FFXI's classic windows into the game's own window (status, target
with the monster's HP percent, combat log, casting bar, macro palette); the pad's **X held** opens the FFXI
command menu (a tap of X is still the game's: sheathe, use an item) (Weapon Skills / Magic / Items: d-pad, A to choose, B back), Items passing one press to the
game's own item use. Saving works. Checked in play.

Run: the port with `MH3_FFXI=1 MH3_HIDE_HUD=1`, then `uv run ffxi_mh3.py` (in-game interface by default).

**FFXI's character in the hunter's place** (stages A-D done, 2026-10-04, checked in play). A player
character of any race (`character.py`, the launcher's Character... button: race, face, each slot's look by
model number; saved as `character_race`/`character_look` in config.toml) is built as FFXI's client does
(`ffxi_model.load_character`, `RACES`): the race's skeleton and motions (lower body, upper body, hands: a
file each, e.g. Hume male `ROM/27/82`, `83`, `85`), each equipment slot's piece through the client's gear
tables (look n = the slot's first file + n), and the sword's battle motions and their hands (Hume male
`ROM/32/16`, `25`; the draw letter `c`). `pack` gives the port the corners on their joints, the place (a
male Hume `character_height` tall, the races to scale), and 27 motions, each standing, moving or an action,
of the peace or the battle set (`port_model.py`, block version 3). The bridge's `Animator` picks standing
or moving motions by the hunter's speed, from the battle set while Monster Hunter's hunter has its weapon
out, and plays actions over them: the layer's (a spell's casting: start, chant looped, release, by its
school) and the hunter's own, read from its body's animation (ScnMdl `+0x124`, an NW4R AnmObjChrBlend; the
heaviest slot's CHR0 name: `co_*` the weapon away, `w01_*` sword and shield drawn, `w01_01xx` an attack):
the draw and sheathe between the sets, an FFXI swing per attack. The model is posed and recorded with each
frame (wiikit's frame hook, VC_MODEL), shown only in a frame where the hunter's body was placed. Its
nameplate goes 25 units above its head. Monster Hunter's hunter is hidden by its scene models' draw flags
(`hook_scn_world`; the armour's pieces found through their callback, ScnMdl `+0x11C`).

**FFXI's target and lock-on** (2026-10-04, checked in play in Moga Woods). Monsters are the scene models
named `em<number>` (`note_enemy`); the target is the locked one, else the nearest within 2500 units; its
arrow (drawn by the layer, `ffxi_ui._arrow`) goes above its highest joint, projected for the layer (main
block version 6). The Classic's L held (the pad's LB; a tap stays the game's) locks on and off: the
gameplay camera behind the hunter facing the target, at the game's distance and height, tilted only to keep
the arrow in sight (`lock_camera`, after the hooked `Camera::SetPosition`/`SetPosture`; the game's posture
is kind 2, aim). The game reads its camera for the stick, so forward runs at the target. Known: the
locked camera doesn't avoid walls (Monster Hunter's own does, in its own direction), so in a narrow place
it can sit inside scenery.

**Sounds** (2026-10-04): each spell's own as it goes off (its effect file, 2800 + its animation number,
names it: `ffxi_sound.py`), the level-up fanfare and the level-down (`ROM/13/35`, `34`).

Next: spell visual effects (FFXI's particle generators, 0x05, with their meshes and textures, scheduled by
a 0x07 routine: a renderer of their own in the port); hit, faint and death motions from Monster Hunter's
motion names (`co_*` numbers to map); monster names for the target window (`em027`: which monster);
the lock-on's camera against walls; more weapons than the sword (each race's battle file and draw letter).

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
