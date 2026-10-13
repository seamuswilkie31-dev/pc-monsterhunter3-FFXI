# FFXI layer — next session

**Swim strokes and swings in the water (2026-10-13)**: the strokes seen in play ("works but a little goofy and
cartoony... fit the FFXI style"), so remade: a breaststroke for the windmill crawl, unhurried (2.2 s a
stroke): each hand and foot along a smooth closed path (periodic Catmull-Rom through a few places), its
shoulder and elbow, hip and knee turned to reach it (two-bone IK from the neutral pose, the elbows out, the
knees out and down): the hands glide ahead together, sweep out, draw under the chest, shoot ahead; the
heels draw up, kick out and round, snap together as the hands go ahead; the body rising a little to
breathe. Treading water: the hands sculling in small loops at the waist, the legs pedalling slowly, little.
Seen in play ("looks better... a little more ergonomic", and a video of Monster Hunter 3 Ultimate's underwater
fighting: "like this"), so remade again in Monster Hunter's manner: upright in the water, never flat. Swimming
(2 s a stroke) leans 35 degrees into it, the hands reaching ahead, sweeping round and drawing in close to the
chest, the legs a steady scissor kick, the knees soft, the head up (_neck: the neck and head share a nod);
treading water upright, a slow small scissor kick, the hands sculling at the waist; the swings in the water
upright too (10 degrees), sunk as the tread. Then the game's own hunter watched swimming side on (the layer's
window captured from the screen): flat out at speed (a smooth crawl, an arm far ahead, the other back over
the back with the elbow high, a flutter kick with the heels coming up), inclined about 45 degrees slower,
upright stopped. So two swims by speed (the port blends the set's moving clips by speed): "swim" (300 a
second) flat (84 degrees), that crawl along smooth paths, rolling a little, the head up; "swsl" (120) the
leaning stroke at 45 degrees. NOT YET seen in play. Before: The swim set was the run
laid flat ("we still need the swimming animations"). FFXI has no swimming, so the strokes are made on each
skeleton's own limbs (ffxi_model._limbs: the feet its points 8/9, the wrists 10/11, else the hands 126/127,
else found by shape: the pelvis the deepest joint with two long chains to the ground, the wrists by their
fingers; the shoulder under the collarbone under the chest where the arms meet; the elbow and knee halfway
along the bones), first set hanging straight (_neutral: a stance such as Ayame's or Trion's raised arm made
one relaxed pose): the crawl (each arm a whole turn about its shoulder half a stroke apart, the elbow high
coming back, a six-beat flutter kick, a little roll, laid 80 degrees forward) and treading water (arms out
sculling, legs pedalling, upright, sunk to the shoulders). All eight races and 102 of 109 Trust models get
them (Sakura, Chacharoon, Shantotto II, the Moogles, Ullegore, King of Hearts keep the run laid flat).
Swings in the water: at0-at2 each get a twin ~at0-~at2 (the upper body's swing leaning 30 degrees forward,
the legs left to the stroke beneath, not driven), which the port's Animator plays in their place while
the hunter swims (in_water, by the tag "~" + the first three letters): yours and the Trusts'.

**Swimming (2026-10-12)**: checked in play ("the swimming works"). FFXI's people never swim, so the character ran
upright through the sea. A swim recorded (the hunter's motion and world matrix through the port's blocks):
co_01xx in the water (co_0101 swimming, co_0107 dropping in, co_0109/0110 setting off and stopping,
co_0125 turning), co_0360 afloat, co_0303 and the other co_0... on land; the hunter's world matrix
pitches as it dives and climbs (up to 76 degrees), level swimming 6-14 (the swim's own lie is in its
motion). So: ffxi_model.swim_clips adds a third set, SWIM: the stroke, its run at 0.6 of its rate laid
80 degrees forward about its hips at its feet's height (ground speed 250); treading water, its idle
sunk to its shoulders and bobbing. The port's Animator picks the swim set while the hunter is in the
water (motion_water, from its motion; g_swimming; wiiboot.log says "the hunter in/out of the water"),
its speed then counting up and down too; the game's tilt comes on top as before. The Trusts get the
same clips (build_model) and swim while you do.

**Trusts' own lasting effects (2026-10-12)**: Shantotto II's orbs checked in play (circling, gliding, the size
as a video of FFXI: "leave it as its canon size"); the others built and run offline, NOT YET seen in play.
- FFXI runs a model's init as it appears; for these it comes to effects that last while the Trust is out.
  Of 109 Trust models, seven: Shantotto II (efon: six orbs and their glass, g1?1/t1?1), Teodor (efon: a
  crimson aura), Arciela and Arciela II (init: crystals at her hands), August (init: a flourish, then eff1
  looping, a wing of light), Mumor II and Morimar (efon: lop0 looping, an aura). The layer plays each one's
  efon, else its init, as it comes out and stops it as it goes (App._fx_lasting). (Shantotto II's init took
  the branch that puts them out: efon first.)
- A generator's joint number is one of the skeleton's points (its references after the joints, 26 bytes:
  u16 joint, six floats, the last three an offset): Shantotto II's points 28-33 are joints 102-107, which
  her idl1 turns about her (her joints 28-33 are her head's). ffxi_model.skeleton_points; the port writes
  each joint's place into a companion's model block (ModelBlock joints_out, version 8); Actor.points.
- A particle kept its generator's place from its birth: an orb (a particle every 20 ticks living 40) jumped
  round, flashing. A generator on a moving point of the caster (attach 1/3/5/9, a joint) that follows now
  carries its particles along; those at the caster's feet stay as the spells were checked.
- Looping routines (0x73: Morimar's and Mumor II's lop0, August's eff1): ffxi_fx.Loop, played round after
  round until the play is stopped. (0x5F, stopping one, and a routine's 0x2D are still no-ops.)
- Sizes: Shantotto II's orbs come out a 0.2 m glass ball by her file's numbers, FFXI's twice that beside
  her; nothing in her skeleton, her orb joints (scale 0.8) or her info chunk accounts for it, so
  trust_effect_size = { "Shantotto II" = 2.0 } (Play.size, by eye); the rest as their files make them.
- All 160 spells' effects still play (4 s each, offline), none failing.
- Which last from the summoning and which are last stands (River's notes, from bg-wiki and ffxiclopedia): only
  Shantotto II's orbs and Arciela's (and Arciela II's) crystals last from the coming. The rest are an ability
  used when the Trust's HP falls low (ffxi_trust.SIGNATURE; here your HP's, the monsters striking only you), at
  most every 3 minutes: Teodor's Start from Scratch (under 50%; ends as he uses Hemocladis, at 2000 TP), August's
  Daybreak (under 66%; 90 s, or ends with No Quarter; physical damage taken -50% on you meanwhile), Mumor II's
  Firesday Night Fever (under 50%; 4 minutes, or ends with Final Eternal Heart), Morimar's Vehement Resolution
  (under 50%; ends with his next weapon skill, 12 Blades of Remorse). Its look (August's eff1, the others' efon)
  plays as it's used and stops as it ends, the file's efof after it (App._fx_signature). Run offline through a
  400 s fight: each began, ended as it should, and began again 3 minutes on. Not yet: Arciela's crystals shift
  colour with her element in FFXI (her 1sta/2sta, 1ef0/2ef0 routines); here they keep the one init chose.
- August's wing of light stood at his hips: its generators (attach 9) are on point 7 (joint 50, his upper back)
  and set their base, 0.3 along Y, along that joint's own axes, behind his spine; along FFXI's world Y (down)
  it fell to his hips. The port now writes each joint's whole 3x4 (ModelBlock joints_out, version 9),
  App._trust_points turns each point's offset by its joint's axes and gives its turn (Actor.point_rots, the
  model's Z-flip taken across as a proper rotation), and a generator with attach 9 is set out along it.
  Attach 1 (every other Trust's: Teodor's aura, with its base 1.0 along Y, rises from below his chest) stays
  turned as the actor faces. Offline: the wing's base 25 behind him at 120 of his 157.

**Targets, damage, defense, Trusts (2026-10-11/12)**: built; the lock-on, the damage and the defense checked in
play ("it locks onto Barioth", "it hits harder", "the hits feel better"); the Trusts' fixes NOT YET.
- Every monster a target: each frame from the game's own table (ENEMY_WORK, 0xB18 each, at
  ld32(0x8065AA60 + 0xA4) +0x1C, its count at +6: +0 in use, +0x28 its place, +0x13C its model or what holds it
  at +0x118), drawn or not, on the screen or off it (table_enemies; a switched-out model still skipped). LB
  locking on takes the monster nearest the middle of the screen within reach (none in sight: the nearest),
  not a far one the d-pad chose earlier (a Baggi 3000 off had stayed the target, the Barioth beside the hunter
  not), unless the d-pad's choice is itself within reach.
- FFXI's hit (Engine.avg_hit: auto-attack, and the measure of weapon skills, magic, pets and Trusts) is now
  your Monster Hunter weapon's damage a second, not a blow's: learnt from your own blows with each kind of
  weapon (Engine.mh_rate, kept with the progress; the layer's _weapon_watch tells the engine the kind in
  hand), half of it over your FFXI weapon's delay; 16 a second until learnt, never under 8 (ffxi_data
  MH_RATE...). Learnt a blow at a time from every drop not FFXI's own, and begun at 30 each run, it had sunk
  to 1-4 a hit on a 2800-HP Barioth. Nothing learnt while FFXI's Attack fights for you; a drop beyond a
  quarter of the monster's HP isn't a blow. Teach it a weapon in Monster Hunter's combat (/combat mh).
- Your FFXI gear's DEF is the hunter's defense in FFXI's combat (ffxi_defense): a monster's blow is taken as
  damage x 80 / (80 + defense) (fn_8029B05C), the defense worked out by fn_8027078C (the armor's sum and 1,
  with skills) into PLW +0x3BA and, a float, +0x3C0. The layer sends 1 + DEF + half of VIT (Engine.game_defense;
  Block defense, a spare word, the layout unchanged); the bridge sets it each frame and after the game works
  its own out (fn_8027078C hooked: tools/mh3-hooks.txt), the armor's back when it's off or the layer is gone.
  The gear's DEF isn't cut from the blows again in the layer (Engine.def_in_game). The HACKER save's hunter
  wore no armor (defense 1); under the Judge's set and a Darksteel Shield it is 297.
- Trusts' legs: a walk's or run's pace is measured from the sole sliding under it (ffxi_model.ground_speed);
  Iroha's robe hem, at the ground and still with the body, was taken for the sole while her feet were up (her
  run a fifth of its pace, her legs whirling). Only what swings to and fro with the stride counts now. A pace
  still beyond any FFXI's (11 of 109 Trusts: Moogles hovering, Rosulatia, Shantotto II...) takes the usual,
  run 380 and walk 130 (ffxi_trust.RUN_PACE, WALK_PACE); the others measure as before.
- Trusts fighting stood at the monster's height and rose with it when it flew: their height is now the ground
  the hunter walked nearest that spot (Party._ground_y, its trail), else the hunter's own.
- The repository: a public README (credits: Seamus, River, Samuele Voltan; Claude Opus and other AI assistants
  thanked; a disclaimer: not fully tested, glitches as issues), Setup.bat (tools/setup.ps1: wiikit fetched when
  GitHub's Download ZIP leaves it out, FFXI looked for, uv and MSYS2 installed, the game built, the launcher
  started), and build-game.ps1 fetching Dolphin's fonts and function database from its GitHub when Dolphin
  isn't installed.

**The level-up banner (2026-10-09)**: checked in play ("good enough"), after these:
- The effects' textures went to the port as 64 x 64 (port_fx TEX): the banner's 256 x 64 letters squeezed to
  64 across, blurred and striped. Now 256 square (FxBlock version 2, 38 MB shared; kFxTex in the bridge).
- The letters' own alpha is noise (0-128 pixel by pixel: "TV static"): the banner's drawn solid, its white
  box and whitish rim cut, a clear border all round (the texture wraps: slivers of the far edge), sized to the
  port's 256 here without ringing and its alpha left solid or clear (ffxi_fx.load_texture, CUT_BOX_FILES).
- The XYZ billboard turned FFXI's X against the view's right: the echoes read mirrored over the XZ letters.
- Effects stood at a Hume's height for every race (fx "me" now the character's own top).
- character.py added missing keys after the file's last table: character_gear landed in [music_tracks] (the
  music thread failed on it, the creator's gear never worn); added among the top-level keys now.
Earlier notes of it:
- The client's level effects (ROM/13/35 level up, 13/34 down: the files whose sounds already played) now play
  about the character (App._fx_level): "Level Up !!" in gold (lvu1) at the chest, sparks crossing about it,
  its echoes drifting below, gone in about 2.5 s; one banner for several levels at once.
- Child generators in ffxi_fx: a particle's 0x3C (a generator emitting once where it's born: each spark's
  crossing twin, g001's g002, turned 90 degrees) and 0x44 (one following it while it lives: the banner's echo,
  g000's g004). 0x53 and 0x6A still not run.
- The banner's letters stand on a box of white at alpha 75/128, which the client cuts away: ffxi_fx.CUT_BOX_FILES
  cuts alpha < 100 for those two files only.
- A GM's !levelup [n]: n levels, the EXP given (for trying it).

**Fixes (2026-10-09)**: built, NOT YET checked in play (the GM aura checked: it looks right).
- Voices: se201-se328 are FFXI's NPCs' voice sets, not one per race and face (the Trusts' models name a few:
  Gessho, a Galka, se214; Babban, a Mithra, se220; Fablinix, a goblin, se211; King of Hearts se268); FFXI's
  player characters have none. A Tarutaru male's face 3 got se267, the deepest of all (about 64 Hz: "an
  ogre"). Each race now takes from its own list (ffxi_sound.VOICE_CHOICES, by pitch measured with YIN on the
  hurt and fall sounds: Tarutaru the highest, about 530-700 Hz; Galka Gessho's then the deepest); /voice
  next/prev/<n>/auto/off/on tries others in the game (its attack grunt played), kept with the progress.
- The command menu's confirm leaking into the game: picking an action closes the menu with the button still
  down, and the game took it as its own press (the pad's A is the Classic's B: a roll). The buttons held as
  the menu closes are now kept from the game until let go (classic_filter's swallow).
- Monsters react to FFXI's blows as to a sword's: with the damage, hook_em_damage marks the monster hit this
  frame (+0x833, as fn_8029B97C does first) with an ordinary blow's reaction (+0x828/+0x82A: kind 1, the
  priority table at 0x805CE448 giving 1-3 and 6-9 priority 1; or what the hunter's own blows have been,
  learnt) and where it came from (+0x816: the way from the hunter, half round); a blow of the hunter's in
  the same frame is left as it is.
- FFXI's command menu on X and Y in FFXI's combat (River): the pad's X held opens it, its Y confirms, its X goes
  back (closing it at the first level); A and B stay the game's even while it's open (Block menu_xy, the
  layer's _menu_step). Monster Hunter's combat keeps A confirm, B back, Y tapped closing it.
  The pad's A (the Classic's B, the roll) then does nothing in FFXI's combat but in the game's own menus,
  where it is their back (checked in play: no roll).
- The GM's legs: the Judge's Cuisses mark a band of alpha 0x55 across the thighs, the Greaves 0x44-0x55 all
  over; on the usual alpha scale (not doubled as PS2's), the renderer's cut-out at half took them away.
  ffxi_model.ps2_alpha: such a texture keeps only its lowest steps (< 0x20) as cut-outs.

**More FFXI (2026-10-08, Seamus's list 2-8)**: built, NOT YET checked in play. Block version 12 (2124 bytes).
- Pad macro bars (FFXI's combat, pad_macros): LT (the Classic's ZL) or RT (ZR) held shows that bar in place of
  the keys' (UiState.palette_title); the d-pad and the face buttons then fire its eight (up right down left,
  then the face's top right bottom left: Xbox Y B A X); a trigger tapped alone still reaches the game.
  classic_filter's block, before the Y menu's; Block macro_held, macro_events. Bars: pad_macros_lt/_rt, else
  the weapon skills and newest spells and abilities.
- "Obtains" for the hunter's own finds: the pouch (European Gecko codes: 48 slots of u16 item, u16 count from
  0x900E0610) watched each frame in the field (pouch_frame: not in the village, not 2 s after a map loads,
  not a bulk change of more than 3 items); each gain with the game's name (ItemName's table at 0x806AED38 + 4)
  to the layer (got_items, got_names). wiiboot.log's "the pouch holds: ..." once: check it is the pouch.
- Quests' ends: nwAddResource always hooked; 04/co_clear, co_failed, co_retire.brres registering (the banner
  as it shows) -> "has completed the quest!" (FFXI's level-up fanfare), "The quest has failed.", "has
  abandoned the quest." quest_init__FUc can't mark a start (it clears the quest work from 7 places, at boot
  too). An area's name across the top as you arrive (AREA_NAMES from data00_eng.bin's short names' order;
  map07 on unchecked).
- Treasure pool: a kill's drops wait 5 min (10 at most): the menu's Treasure Pool (Lot, Pass), /lot, /pass;
  unpassed when the time's up, yours; passed, lost. treasure_pool = false: straight to you as before.
- Vana'diel's clock (vana_time: 25x real from 898 at 2002-01-01 00:00 UTC; the day's element), top left; the
  day's element's magic x1.1, the element it beats x0.9 (Engine.day_element, day_bonus).
- Auto-target (FFXI's combat, auto_target): 2.5 s after a kill, the port's next target engaged if within
  auto_target_range (1500).
- Pets seen: the engine's pet (an avatar, the wyvern) built as a Trust from FFXI's monster model (pet_list's
  pool's model; file = model + 1300: Carbuncle 16 ... Diabolos 25, Wyvern 24), following and swinging (its
  damage the engine's pet tick's); released when the pet goes. A slot of the five.
- The crash entering a quest area a second time: resolved (Seamus, 2026-10-08).

**Fights more like FFXI, and polish (2026-10-08, Seamus's ask)**: built, NOT YET checked in play.
- Weapon skills in motion: each skill of the hunter's weapon has the race's own file (ffxi_fx.WS_BASE + its
  animation - 1) holding its motions; its `main` routine plays them on the caster (op 0x05, the name's three
  letters and ?, a duration; delays trail their command) and shows the target hurt at its `mdam` call
  (ffxi_fx.motion_schedule). ffxi_model.ws_clips adds them to the character as battle actions (ws<n><motion>;
  the same motion in several skills one clip: a character holds 96, a sword's skills bring 89), the layer
  plays them in turn (_motions) and the engine lands the blow at mdam's time (Engine.ws_land, landing).
- Trusts: their own spells (LandSandBoat's mob_spell_lists by mob_pools' spellList; -ra forms as the single)
  and weapon skills (mob_skill_lists by skill_list_id, named by the rows' comments); buffs on you (ON_YOU:
  Protect, Shell, Haste, Refresh, Regen, songs, Indi-) and debuffs on the foe (ON_FOE), each family's
  strongest and again once worn off (Engine.cast_by, family_active, spell_family: a stronger one replaces
  a weaker); their weapon skills through Engine.ws_by, opening and closing skillchains with yours; their
  WS's look from them. trusts.json rebuilt with "spells" and "skills".
- FFXI's item icons (ffxi_items.icon: the item block's 0x280, a 32x32 8-bit bitmap, its palette's alpha 128
  opaque; kept as PNGs in the asset folder's icons) in the Items and Equipment menus; FFXI's 4x4
  Equipment window (ffxi_ui._equipment) while Equipment is open, the menu's slot ringed.
- /heal: resting, HP and MP back each 10 s (the first after 20 s), more each tick; moving, hurt, fighting or
  casting stands you up (App.rest, _rest_step).
- FFXI's colours on Monster Hunter's own frames (brres_tex.ffxi_look: waku_*.brres, palettes and RGBA8
  recoloured navy-to-silver by brightness; warm colours and greys only), the launcher's checkbox (on),
  applied before the game starts, the originals kept in build/extract-orig and put back when off.
- NOT done: FFXI's blows on the part faced (the parts' tables, ENEMY_WORK +0x83A flinch HP per part and the
  break groups f_801266F4, give no positions; blows on a part a monster lacks could misbehave: needs the
  hitzones' joints, found and checked in play); the lock-on camera avoiding walls (needs the game's camera
  collision, or a ray through the scenery's draws).
- Shield clipping: fixed (Seamus, 2026-10-08).

**River's fourth round (2026-10-08)**: checked in play (Moga Woods, Kelbi): FFXI's blows took its HP to 0
(wiiboot.log: "took FFXI's 17 as a blow: HP 35 -> 18"...), it fell, the hunter walked over and carved it (Raw
Meat), "defeats the Kelbi" and EXP; the d-pad's targets and F9 work. NOT YET seen: the GM's aura and red
name, the linkpearl's sprite in the game, the launcher's name, a large monster's flinches and carves.
- FFXI's damage reaching monsters, for real this time. The third round's +0x928 is the monster's POISON
  slot: its ailments (fn_801327CC) clear it each frame just before the damage routine reads it, so what the
  port wrote between frames never landed (auto-attack, magic, Trusts: all "doing nothing"). Now FFXI's
  damage waits in the bridge (game_damage) for that monster's damage routine, fn_8011F6B8, newly hooked
  (tools/mh3-hooks.txt: the code translated again), and goes in as a blow on its first part (ENEMY_WORK
  +0x7CA, the s16 per part the hunter's blows add to through fn_8011EF88, cleared at each frame's start by
  fn_8029A200): the routine takes it off the HP with the hunter's, the part's flinch counter (+0x83A) runs
  down (flinches), and at none left its fall. hook_em_hp keeps FFXI's part out of the hunter's blows;
  HP not moving (a state it can't be hurt in) puts it back to wait, dropped after 2 s; the engine lets its
  own count of it go after 3 s. To check: wiiboot.log "took FFXI's ... as a blow: HP a -> b", the monster
  flinching, falling, carved.
- Combat modes (the menu's new Config, /combat ffxi|mh, /mhcombat; saved; config combat_mode = "ffxi"):
  FFXI's: Y and B dropped while Attack fights, the d-pad always the target's (dpad_target "auto"), a fallen
  target carved (auto_carve). Monster Hunter's: the game's buttons and d-pad. The d-pad: when nothing lies
  that way on screen it cycles by distance (up/left nearer, down/right further).
- The d-pad given back to the game's menus (checked in play): while a menu of its draws its cursor
  (put_menu_cursor, hooked: 57 callers; the HUD's own, inside hook_cockpit, not counted) in the last 250 ms,
  and while no monster is near enough to target (the village). The carving waits out a menu too.
- Talking to someone (checked in play): the game draws through another camera of its scene root's (closer, its
  own field of view) while the one following the hunter stays put; the character, drawn through that one, came
  out shrunk. SetCurrentCamera hooked: the roots remembered; render_camera is the current one of the root
  holding follow_camera's (+0xF4 its index, cameras from +0xF8, 0x10C apart), for the character, the Trusts,
  the effects and the nameplate; the lock-on keeps the following one.
- Fallen monsters aren't targets (kept 1.5 s first, so the layer sees the fall: EXP, drops, disengage); the
  lock lets go.
- Auto-carve (carve_frame, Block auto_carve/carve_seq): the last frame's target's HP to none; after 2 s the
  hunter is walked to it (the left stick, by the camera's view matrix rows) until within 110 + 0.35 x its
  height or no nearer for 40 frames, then the Classic's A every half second, up to 14 s or the carcass
  gone; the pad's own stick or a button stops it. Assumes A is MH3's carve button on the Classic.
- F9 (hotkeys ui_swap; /ui; Config, Interface): Monster Hunter's HUD (hook_cockpit, Block mh_hud) in place
  of FFXI's interface (ui_state empty but the menu and the chat line).
- The launcher's name (FFXI_NAME, 15 at most; empty: config's name).
- Linkpearl: FFXI's own, "ustats"' third row's first (white, tinted x2 by the colour), its alpha doubled
  (FFXI's 128 is opaque; the GM icon's too: it was half see-through).
- GM: the name red (GM_NAME), and an aura (ModelBlock flags 4, wiikit video_model_aura: six faint copies
  either side swaying across the view, just behind the model, blended; gm_aura = false for none).

**River's third round (2026-10-06)**: built; the damage did NOT work (see the fourth round).
- FFXI's damage dealt as the game's own: added to the monster's pending damage (ENEMY_WORK +0x928, s16),
  which its damage routine (fn_8011F6B8, each frame) takes off its HP, running its death when none is
  left (the HP written directly never died, nor flinched). The layer counts what it has dealt
  (Block damage_total), the port hands the new part over each frame (game_damage), and neither counts
  the game taking it off as the hunter's blow (engine ffxi_owed, the bridge's g_ffxi_owed). To check:
  that fn_8011F6B8 runs each frame (wiiboot.log: "FFXI's damage ... handed"), the monster flinching, its
  fall, its carving. config.toml damage_through_game = false goes back to writing the HP.
- /mhcombat (and Equipment's Button Combat): the game's own swings on the pad's Y and B (the Classic's X
  and A) dropped while FFXI's Attack fights (Block no_mh_swings), the buttons the game's elsewhere.
- Dual wield, and any weapon: the worn main hand's own style of battle motions (look_for_weapon no longer
  swaps a club for a sword); each weapon's grip (its info chunk's sixth byte, a skeleton reference) put in
  its hand in the battle motions (ffxi_model.grip_pose: 113 joint 4 to 127's joint 68, 121's to 126's),
  sheathed at rest as the motions put it; a shield, no grip, stays on its mount.
- Trusts stood by their origin, as FFXI stands an actor (pack origin_ground): the rest pose's soles lifted
  every one 5-25 units, AATT 75; those that hover in FFXI (AATT, a Moogle) hover by their own motions.

**River's second list (2026-10-06)**: built, NOT YET checked in play unless said.
1. Headgear clipping: done. FFXI's occlusion (ffxi_model `HIDDEN_BY`, `shown`): a mesh header's fourth byte
   is what it hides of the other pieces, each triangle group's display type (the 0x8010 instruction's
   fourteenth byte) what it is (1-3 hair, 4 face, 5 wrists, 6 trousers, 7 shins); a hat hides the outer
   hair, a helmet all of it. Checked in renders (face 1A, 4A, Sallet).
2. Shield clipping: fixed (2026-10-08). The shield hangs off its mount (joint 87, a child of the forearm), keyed by
   every motion as the client does; its info chunk's sixth byte is 1 for a shield (no grip joint, byte 7
   0xFF) but what it changes isn't known. In renders the peace run swings it into the body. Asked for a
   screenshot of where it clips.
3. Lockstyle: done (/lockstyle on|off, Equipment's Lockstyle; saved with the progress).
4/5. The creator's equipment by name, with stats: done (character.py: each slot's items the race wears, typed
   to narrow; Save writes character_gear and a serial; the layer hands them over and wears them once).
5b. GM: done (the launcher's GM: FFXI's GM icon, ROM/0/1.DAT's "ustats" third row second, cut at runtime; a
   GM wears anything; !additem <name> [count]).
6. Kraken Club: in the creator's main hand list (Rare, so never a drop); a GM's !additem Kraken Club.
7. Damage over time: done (the fractions kept, a share of the monster's HP too, said each tick).
8. FFXI damage finishing monsters: config allow_kills = true (was the game's last blow only: everything
   stopped at 1 HP). Needs checking in play: does a monster at 0 HP die without the game's own hit?
9. The d-pad changes the target (dpad_target: "engaged" while attacking or locked on, "always", "off"): the
   nearest monster that way on the screen; the lock goes with it.

**River's list (2026-10-05)**: where each stands (built and committed; "in play" once checked there).
1. Tarutaru's run too fast: done, Taru only (`_send_character` slows the run clips' rate).
2. FFXI's Attack: done. The menu's Attack (or /attack, /a) engages: locked on, the weapon drawn, a swing
   each `swing_delay` (the weapon's delay / 60 s) within reach (`attack_reach` + 0.6 x the target's height);
   magic and abilities go on meanwhile; off again with Attack, or 2 s with no target. Block `engage`
   (MENU_AT+24) tells the bridge (`target_frame`'s lock, the battle stance).
3. Jobs: done. Job (main and support, swapped anywhere) and All Jobs (every job's spells and abilities,
   the highest MP; `engine.all_jobs`, saved) in the menu's Job; Magic and Abilities their own; the
   command menu bottom left above the chat log, rising with it (`ffxi_ui._menu`, `_log_top`).
4. Round shadow: done. Monster Hunter's foot shadows are the common effects' model e_qc_000 (efm_com.bin,
   texture e_qc_000_BM), one per foot: the bridge hides the hunter's (`foot_shadow`, by the model's name,
   within 120 units) while the character is drawn. FFXI's round shadow (its fade measured from system
   kage_tex, ROM/27/81) is drawn by wiikit (`video_model_shadow`, `draw_shadow`) on the ground a 9 x 9 grid
   of rays finds, so it lies along slopes. config.toml `character_shadow = "mh"` goes back, "none" for
   neither. The linkpearl: done (launcher: the pearl and its colour; `FFXI_LINKPEARL`; drawn by the name).
5. Nameplate: done, the current font in italics (FFXI's nameplate isn't a sprite font).
6. Trusts: called and following, checked in play (2026-10-05); fighting built, NOT YET checked in play.
   `ffxi_trust.py`: every Trust (LSB's spell_list and mob_pools, fetched once into the assets: names, jobs,
   cast/recast, melee delay and damage; 118 of 121 with their model in this install: NPC model m is file
   m + 96907, FTABLE's ROM/309/20 being Shantotto's 3000; Abenzio's 296, Monberaux's and Matsui-P's not
   found). Each is a spell of school "trust" every job knows (Magic, Trust); cast, it joins (up to 5): its
   model built as the player's (its own file's motions through character_clips, at the player's scale),
   sent to its own model block (port_model.PortModel(slot): "mh3-ffxi-model-N"), placed through the main
   block's companions (port_bridge.set_companion, block version 10). The bridge (`companions_frame`) eases
   each to where the layer puts it, animates it as the hunter's character, and wiikit draws it (model slots
   1-5), set on the ground its own grid of rays finds (planks too). Following: along the hunter's way,
   further back each, sides in turn. Fighting (`Party.fight`): engaged with you, about the target (casters
   back), melee on its delay with TP and a weapon skill, its job's spells at your level (nukes; cures on you
   by job thresholds), FFXI's words; the party list above the target window (`ffxi_ui._party`). Release:
   the menu's Party, or /refa all. Their names above their heads (the port projects each one's highest
   joint standing: Block companions' name_x/y), light blue; their casting aura about them and their spells
   from them (an effect actor each). Coming: the call's own effect where it
   appears (LSB's animation 939: ROM/318/100). To do: Trust-specific skills (LSB's spell lists), buffs.
7. Attack camera: done with 2 (the lock's camera while engaged; config.toml `attack_lock_camera = false` to go back).
8. Race voices: done (`ffxi_sound.voice_set`: se2NN by race and face; attack/hurt/faint, 0.4 s apart).
   The hunter's own voice muted (`hook_se_req`: its se_w kinds are 1 voice, 2 feet, 3 weapon; config.toml
   `mute_hunter_sounds = ["1:*"]`, through the block's `mute_se`; /hunter gives it back).
9. /sit: done (si0/si1 from the race's own motions when the emote files lack them; /heal rx0/rx1).
10. Area 10's invisible "Aptonoth": hidden monsters (flags & 0x60 == 0x60) are no longer targeted, and
    the target log (wiiboot.log) names what the arrow points at, to tell if it happens again.
Then: the launcher's prerequisite check (`prereqs.py`, SETUP-NEEDED.txt) and SETUP-INSTRUCTIONS.txt: done.


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
