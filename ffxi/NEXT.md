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

**Hits, music, weapons** (2026-10-04, checked in play). Hurt (the bridge's `hook_pl_hp`: the game's own
damage), the character flinches (`dfm` held 0.3 s over the moving legs: an action moves only its joints,
the rest NaN in its frames), is knocked back for a fifth of its health or more (`bb0` then `bb1`), and
fainted falls and lies there (`ded`, `cor` looped). FFXI's music (`ffxi_sound.read_bgw`, `Player.play_music`)
plays by the map (the bridge's `serve_map`: wiikit's RSO table, `map_village`, `map01`...) and while
engaged (weapon out near a monster, blows in the last 6 s): config.toml's `music_tracks`; the game's own
music is held silent meanwhile (`set_str_bgm_main_vol`'s variable, r13-0x4460, at 0) and given back when the
layer stops; FFXI's audio follows the game's level in Windows' mixer (pycaw). The battle motions are the
weapon style's (`weapon_style`: the weapon file's info chunk; `battle_files`: the race's file drawing that
style), Monster Hunter's weapon read from its motion set (w00-w08: `MH_WEAPONS`) and the character rebuilt
when it changes. Known: some races' polearm and great sword files aren't found by the draw letter (they
hold a sword and shield then); the music's track numbers are from memory (config.toml to change them).

**Chat line and emotes** (River's list, 2026-10-04, checked in play). "/" in the game's window opens FFXI's
chat line (the bridge's `chat_key` through wiikit's `video_set_key_hook`/`video_text_capture`: the game sees no
keys meanwhile, nor the Enter that sends; the macro hotkeys pause). `App.chat_command`: the 33 emotes
(`ffxi_model.EMOTES`: every race's emote files after its battle files, the dances in ROM/191-195, /heal the
race's resting kneel), /say and plain words, /ma /ja /ws by name, /echo.

**Equipment, inventory, drops** (River's list, 2026-10-04, checked in play). `ffxi_items.py` reads FFXI's
14,099 armor pieces and weapons from the installation (stats from their descriptions; their looks from
LandSandBoat's item_equipment MId, fetched once) into the asset folder's items.json; `ffxi_gear.py` decides
who wears what, the starter kit, stats (`Engine.set_gear`: DEF, STR, Attack, AGI, INT, MND, MP, Regen,
Refresh, Haste, Store TP act), the look (worn pieces over the creator's), and drops (the monster's level to
three above; a large monster always). Menus: Equipment (16 slots) and Items, the inventory (equip, drop),
the highlighted piece's stats beside them; Monster Hunter's own item use is Pouch Item, apart from them. Ideas next: FFXI's item icons (each item block holds a 32x32 one at
0x280), the Equipment window as FFXI's 4x4 grid, HP+ gear (Monster Hunter's max HP), a treasure pool.

**The character on the ground** (2026-10-04, checked in play). `pack` puts the soles at 0 without the
weapons: a sword hangs a quarter of a metre below them at rest, and the character floated by that much.
`/hunter` swaps in Monster Hunter's own hunter in the same place, and back (for comparing the two).

**The character lit by the game's lights** (2026-10-04: parked, then back on at Seamus's word: gain 2,
no ground shade, being tuned by eye against `/hunter`). What's built: `pack` sends each corner's normals (model block
version 7, 20 floats: FFXI's two-joint normals interleave as their positions; a cloth mesh's worked out
from its faces); wiikit's renderer catches the lights of the game's skinned, lit draws (the nearest to
the character; the lights only, not their material colour, which is a model's own paint: a red-clothed
villager tinted it red), keeps them in the world's space by the camera (`video_model_frame`'s camera),
and lights the model by GX's equation; a ground probe casts a ray down through the scenery's draws and
reads its baked vertex colour (`video_model_shading(gain, ground)`; the layer's `character_light` and
`character_ground_shade`, `/light` and `/shade` in the chat line). What was learned, from F12 traces:
- Monster Hunter lights its characters (monsters, villagers, the hunter alike) with the area's set: a
  sun, a fill, sometimes a light from the camera, ambient ~0.05, material white, TEV texture x light,
  scale 1. Its scenery isn't lit: its light and shade are baked into vertex colours (Moga Woods: ambient
  x vertex colour x 2; the village: the vertex colour as it is).
- The hunter isn't dimmed in a dark corner: it stands out as the FFXI character did unlit.
- FFXI's textures are made for twice the light (its PS2 heritage); lit as the game lights its own
  (gain 1), the character came out at half the hunter's brightness, and the ground probe's shade made
  it darker still. Tuning gain and shade by eye against `/hunter` was where it stopped.

**Spell effects** (2026-10-04, checked in play). `ffxi_fx.py` reads FFXI's effect files and runs them as
FFXI does (routines on the 60-tick clock with their calls, holds and branches; generators through their
opcode streams; drawn 4x vertex colour x texel x particle colour, camera-facing sprites); `port_fx.py`
hands each frame's triangles to the port (block `mh3-ffxi-fx`; wiikit's `video_fx_*` draws them after the
character). Shown: the school's casting about the character (the race file's `cawh`, `cabk`...), the
spell's `main` (the shared release, then on the monster or the character), a blow's spark by weapon
(`HIT_ROUTINE`: the shared file's `atpr` cases), and a debuff's spell look pulsing faintly on the monster
every 3 s while it lasts (FFXI has none of its own). `uv run ffxi_fx.py preview Cure` renders one offline.
Not yet: child generators (0x44, 0x53, 0x6A, 0x3C), point lights, distortion, weapon skills and
skillchains (the shared file's `sb00`-`sb09`), the monster's own hit reactions (`damg`, `sdam`).
The format, as studied first:
- A spell's effect is file 2800 + its animation number (`ffxi_sound.file_path`); across the 154 spell
  files: 2152 generators (0x05), 2252 keyframe curves (0x19), 1082 particle meshes (0x1F), 1000 textures
  (0x20), 351 routines (0x07), a few sprite sheets (0x21).
- Routine (0x07): its command list at data+0x14 (relative to data - 16); entries `u8 op`, size in words
  (low 5 bits of the u16 at +1), `u16 delay` at +4 (it trails its command: a command runs at the sum of the
  delays before it), `u16 duration` at +6, a 4-letter id at +8; 60 ticks a second. 0x02 spawns a
  generator for `duration`; 0x03/0x09 run another routine (Cure's `main` is a sound and `0x03 tgt0`);
  0x0A/0x0B/0x4A/0x53/0x60 sounds; 0x69/0x6A a block, 0x3D/0x3E pick one at random, 0x64/0x67/0x6B if/else.
- Generator (0x05): data+0 u16 attach (low 4 bits: 1 caster, 2 target...; joints in bits 4-15);
  chunk+0x74 u16 emission variance, u16 ticks per emission (+1), u8 particles per emission, u8 flags;
  chunk+0x80 four offsets (from the chunk) to opcode streams: generator updaters, initialisers,
  per-particle updaters, expiry. Each opcode: u32 config (low byte the opcode, bits 8-12 its size in
  words with the config, bits 13+ an allocation slot linking an initialiser to its updater).
- The opcodes used (census): initialisers 0x01 (billboard flags, render flags, linked mesh id, base
  position, life in ticks + variance) 0x09/0x0A rotation (+variance) 0x0F/0x10/0x11 scale 0x16/0x17
  colour (bytes/255) 0x02/0x03/0x08 velocity 0x0B/0x0C spin 0x12 scale velocity 0x1E blend
  (low nibble 8 additive, 4 alpha, 1/2 subtractive) 0x1F/0x06/0x07 spawn shell 0x2D/0x27/0x28 and
  0x21-0x2F keyframe refs; updaters 0x0E (age) 0x1B alpha curve 0x02 move 0x05 spin 0x08 scale
  0x15/0x16/0x17 scale curves 0x12-0x14 rotation curves (half-turns) 0x18-0x1A colour curves 0x27/0x28 UV
  scroll 0x2C damping 0x0B colour change 0x03/0x06/0x09 acceleration.
- Keyframe curve (0x19): from chunk+0x10, (time, value) float pairs until time 1.0. Particle mesh (0x1F):
  from chunk+0x10 u32 version (3/5/6), u8 meshes with texture, u8 without, u16, a padded u16 triangle-count
  array (3/4/7/8/11/12/15 long), texture names (16 chars), then per vertex position, normal, BGRA colour,
  UV.
- Built as planned, but the layer expands the particles to triangles in the world itself (the port
  writes the camera, the hunter and the target into the block each frame) and the port only draws them.

The lock-on's camera against walls.

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
