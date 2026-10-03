# Monster Hunter Tri's interface: where it lives

Found 2026-10-04 with the port's texture dump (`WIIKIT_TEXDUMP=dir`, `WIIKIT_TEXDUMP_MAX`, `WIIKIT_TEXDUMP_MIN`:
every texture upload as a PNG named by its address, format and size) while playing from the village into
the field, then matched by name, size and format to the textures in the disc's NW4R resources (`.brres`,
`TEX0` sections). European release; each file has `_eng`, `_fre`, `_ger`, `_ita` and `_spa` versions.

| file (`files/04/`) | texture | size, format | what |
|---|---|---|---|
| `cock_eng.brres` ("cockpit") | `cock00` | 512x256 RGBA8 | **the hunt HUD**: health and stamina bars, sharpness gauge, item selector frames, the clock's gear, bowgun reload signs |
| | `cock01` | 256x384 IA4 | the clock face, the map's compass and frame, numbers |
| | `cock02`, `cock03` | 128x128 CMPR, CI8 | |
| | `load_back01`, `nowloading` | | the loading screen |
| `cock_d_eng.brres` | | | the HUD again (`d`: likely the variant for another mode) |
| `menu_eng.brres` | `menu00` | 1024x304 CI8 | menu panels |
| | `menu01` | 256x288 RGBA8 | controller button prompts (Classic and Remote), cursors, the HR badge |
| | `menu02` | 1024x256 CI8 | **menu words as images**: "Quest Info", "Skill Tree", "Item Box is full.", ... |
| | `hunter00`, `hunter01` | | Hunter's Notes |
| | `tutorial` | 472x512 CI8 | |
| `waku_eng.brres` ("frame") | `waku00`-`waku03` | CI8, RGBA8 | window and dialog frames, borders |
| `icon_eng.brres` | `icon`, `icon01`, `icon_m` | IA4, CI8 | items, equipment, monsters |
| `result_eng.brres` | `reward00`-`02`, `hou_back*` | | the quest rewards screen |
| `co_clear_eng`, `co_failed_eng`, `co_retire_eng` | | | quest complete / failed / abandoned banners |
| `map_mNNx_eng.brres` | | | each area's map |
| `village_eng.brres`, `arena_eng.brres` | | | the village's and the arena's screens |

Elsewhere:
- `files/00/ec.tpl`: Monster Hunter's window frames (title and system screens).
- `files/00/system.tpl`: the Capcom logo, a title font.
- Dialog text: the Wii's system font (`build/fonts/font_western.bin`, the boot ROM's).
- `files/00/dataNN_lang.bin`: `OMOM` containers (a count, then offset/size pairs) of Capcom's own formats,
  likely message tables and screen layouts.

## The HUD's code

`mh3_CockpitDraw` (`802E55C0`) draws the whole hunt HUD every frame: about 20 parts (the item bar
`fn_802EDD48`, a text panel `fn_802ED4E4`, ...), called by the scene's draw (`fn_8004925C`). Found by
tracing the callers of `font_print_ex` and `ItemName` in the field (`MH3_HUD_TRACE=1`). The port skips it
with `MH3_HIDE_HUD=1`: the HUD is gone, bars, icons and labels together (checked in the field), while its
textures stay as they are.

Clearing `cock00` instead (tried first) removes only the frames: the health and stamina fills are drawn
as plain coloured quads, and the item's icon and name come from the icon sheet and the font.

## What it means for the FFXI interface

- The port reads the game's files from `build/extract`, so a texture redrawn and written back into its
  `.brres` restyles that piece everywhere, with no code: menus, frames, words, icons. It needs a TEX0
  encoder for these formats (RGBA8, RGB5A3, IA4, CI8 with its PLT0 palette; CMPR if those are touched).
- Monster Hunter's hunt HUD is hidden by code instead (`MH3_HIDE_HUD=1`, above).
- The live FFXI HUD (status bar, target window, combat log, command menu) is drawn by code: milestone 2.
- The art is ours, in FFXI's style; nothing is taken from FFXI's files.
