"""FFXI's rules for the layer: jobs, levels, weapon skills, spells, job abilities, traits, skillchains.

Plain data, edited freely. It follows FFXI's classic era (level cap 75) where that makes sense: TP from
weapon delay, the 1000/2000/3000 TP tiers, the skillchain table and magic bursts, spells and abilities
learnt by level, a subjob at half the main job's level, an SP ability ("two-hour") per job. Damage is
scaled to Monster Hunter: weapon skills, nukes and pets multiply your own recent average hit, so they
stay in proportion whatever MH weapon or monster you fight; heals are in MH's HP (100-150).

Learn levels per job, MP costs, cast and recast times follow FFXI as LandSandBoat's database records them
(github.com/LandSandBoat/server, sql/spell_list.sql and abilities.sql, checked 2026-10-03), except the SP
abilities' recasts, shortened for Monster Hunter's quests. Powers, durations and the rest are from memory
of FFXI (bg-wiki.com, ffxiclopedia) and tuned for Monster Hunter: check any you care about. Effects work through what the layer can change, the hunter's
and the monster's HP, the layer's own MP and TP:
    atk         extra damage on each of your hits (fraction of the hit)
    defn        a fraction of the damage you take refunded (negative: more damage taken)
    haste       more TP per hit (FFXI's Haste makes more hits a minute; MH's hits are your own)
    store_tp    more TP per hit
    regen / refresh / regain     HP / MP / TP every tick (3 s)
    phalanx     damage taken lowered by a flat amount
    stoneskin   damage taken absorbed until the pool is spent
    shadows     hits taken absorbed, one shadow each
    enspell     elemental damage added to each of your hits (x your average hit)
    drain_hit / aspir_hit        a fraction of each hit's damage back as HP / MP
    ws_bonus / nuke_bonus / heal_bonus     weapon skill / nuke / cure power
    fast_cast / conserve_mp      cast time / MP cost lowered
    aquaveil    casts not interrupted by damage (charges)
    tp_cost_free                 job abilities that cost TP are free (Trance)
    exp_bonus   more EXP
  on the monster:
    dot         damage every tick (x your average hit)
    def_down    extra damage on every hit it takes
    slow / weight                its hits on you weakened (a fraction refunded): stand-ins for its speed
    paralyze / blind             a chance its hit on you is cancelled: stand-ins for its actions
    sleep       its hits cancelled until it takes damage
Stand-ins are marked; the real ones need its speed, accuracy and actions found in memory.
"""

# ================================================================================================
# TP
# ================================================================================================
TP_MAX = 3000
TP_WS_MIN = 1000
TP_WHEN_HIT = 40          # TP gained when a monster hits you


def tp_per_hit(delay: int) -> int:
    """FFXI's TP per hit from weapon delay (the post-2014 formula)."""
    d = delay
    if d <= 180:
        tp = 61 + (d - 180) * 63 / 360
    elif d <= 540:
        tp = 61 + (d - 180) * 88 / 360
    elif d <= 630:
        tp = 149 + (d - 540) * 20 / 360
    elif d <= 720:
        tp = 154 + (d - 630) * 28 / 360
    elif d <= 900:
        tp = 161 + (d - 720) * 24 / 360
    else:
        tp = 173 + (d - 900) * 28 / 360
    return int(tp)


# ================================================================================================
# Levels, EXP, /check
# ================================================================================================
LEVEL_CAP = 75
SUBJOB_DIVISOR = 2         # the subjob acts at half the main job's level (and no higher than its own)

# EXP to the next level, classic era (approximate): TNL[n] takes level n to n+1.
TNL = [0, 500, 750, 1000, 1250, 1500, 1750, 2000, 2200, 2400, 2600, 2800, 3000, 3200, 3400, 3600, 3800,
       4000, 4200, 4400, 4600, 4800, 5000, 5100, 5200, 5300, 5400, 5500, 5600, 5700, 5800, 5900, 6000,
       6100, 6200, 6300, 6400, 6500, 6600, 6700, 6800, 6900, 7000, 7100, 7200, 7300, 7400, 7500, 7600,
       7700, 7800, 8000, 9200, 10400, 11600, 12800, 14000, 15200, 16400, 17600, 18800, 20000, 21500,
       23000, 24500, 26000, 27500, 29000, 30500, 32000, 34000, 36000, 38000, 40000, 42000]
assert len(TNL) == LEVEL_CAP

EXP_RATE = 3.0             # Monster Hunter has fewer, longer fights than FFXI: EXP x this
CHAIN_WINDOW = 60.0        # a kill within this many seconds of the last one continues an EXP chain
CHAIN_BONUS = [1.0, 1.2, 1.25, 1.3, 1.4, 1.5]   # chain #0, #1, ... (the last one repeats)
DEATH_EXP_LOSS = 0.1       # a faint above level 5 costs this fraction of the level's TNL


def monster_level(max_hp: int) -> int:
    """A level for a Monster Hunter monster, from its HP: a Jaggi (80) about 9, a Great Jaggi (~2000)
    about 50, a Rathalos (~5000) about 62."""
    import math
    if not max_hp or max_hp <= 0:
        return 1
    return max(1, min(99, round(9 * math.log2(max(1.0, max_hp / 40)))))


# (lowest level difference, label, colour): monster level minus yours
CHECK = [(5, "Incredibly Tough", (255, 90, 90)), (3, "Very Tough", (255, 150, 90)), (1, "Tough", (255, 220, 120)),
         (0, "Even Match", (255, 255, 255)), (-3, "Decent Challenge", (150, 220, 255)),
         (-7, "Easy Prey", (140, 230, 140)), (-99, "Too Weak", (170, 170, 170))]


# what /check says, as FFXI words it
CHECK_PHRASE = {"Incredibly Tough": "seems incredibly tough", "Very Tough": "seems very tough",
                "Tough": "seems tough", "Even Match": "seems like an even match",
                "Decent Challenge": "seems like a decent challenge", "Easy Prey": "seems like easy prey",
                "Too Weak": "seems too weak to be worthwhile"}


def check(mob_level: int, level: int) -> tuple[str, tuple]:
    d = mob_level - level
    for lo, label, col in CHECK:
        if d >= lo:
            return label, col
    return CHECK[-1][1], CHECK[-1][2]


def kill_exp(mob_level: int, level: int, max_hp: int) -> int:
    """Base EXP for a kill (before chain and rate): more for tougher monsters and bigger ones."""
    label, _ = check(mob_level, level)
    if label == "Too Weak":
        return 0
    d = mob_level - level
    base = max(10, min(400, 100 + 20 * d))
    size = max(1.0, (max_hp / 200) ** 0.5)           # a large monster is worth a pack of small ones
    return int(base * size)


# ================================================================================================
# Weapon skills: per MH weapon, FFXI's skills of the matching weapon type, learnt by level
# ================================================================================================
# ftp: damage multiplier at 1000 / 2000 / 3000 TP. props: skillchain properties. hits: extra hits add
# 0.8 each. fx: what else it does (as a spell's fx, below).
WEAPON_SKILLS = {
    # Great Sword (FFXI Great Sword)
    "Hard Slash":        {"props": ["Scission"], "ftp": (1.5, 1.75, 2.0)},
    "Power Slash":       {"props": ["Transfixion"], "ftp": (1.0, 1.25, 1.5)},
    "Frostbite":         {"props": ["Induration"], "ftp": (1.0, 2.0, 2.5), "element": "ice"},
    "Freezebite":        {"props": ["Induration", "Detonation"], "ftp": (1.5, 2.5, 3.5), "element": "ice"},
    "Shockwave":         {"props": ["Reverberation"], "ftp": (1.0, 1.0, 1.0),
                          "fx": [("debuff", "sleep", 1, 20)]},
    "Crescent Moon":     {"props": ["Scission"], "ftp": (1.5, 1.75, 2.75)},
    "Sickle Moon":       {"props": ["Scission", "Impaction"], "ftp": (1.5, 2.0, 2.75), "hits": 2},
    "Spinning Slash":    {"props": ["Fragmentation"], "ftp": (2.5, 3.0, 3.5)},
    "Ground Strike":     {"props": ["Fragmentation", "Distortion"], "ftp": (1.5, 1.75, 3.0)},
    "Resolution":        {"props": ["Fragmentation", "Scission"], "ftp": (1.0, 1.5, 2.0), "hits": 5},
    # Long Sword (FFXI Great Katana)
    "Tachi: Enpi":       {"props": ["Transfixion", "Scission"], "ftp": (1.0, 1.5, 2.0), "hits": 2},
    "Tachi: Hobaku":     {"props": ["Induration"], "ftp": (1.0, 1.0, 1.0), "fx": [("debuff", "sleep", 1, 8)]},
    "Tachi: Goten":      {"props": ["Transfixion", "Impaction"], "ftp": (0.5, 1.5, 2.0), "element": "thunder"},
    "Tachi: Kagero":     {"props": ["Liquefaction"], "ftp": (0.5, 1.5, 2.0), "element": "fire"},
    "Tachi: Jinpu":      {"props": ["Scission", "Detonation"], "ftp": (0.5, 0.75, 1.0), "hits": 2, "element": "wind"},
    "Tachi: Koki":       {"props": ["Reverberation", "Impaction"], "ftp": (0.5, 1.5, 2.0), "element": "light"},
    "Tachi: Yukikaze":   {"props": ["Induration", "Detonation"], "ftp": (1.56, 1.88, 2.5),
                          "fx": [("debuff", "blind", 0.25, 60)]},
    "Tachi: Gekko":      {"props": ["Distortion", "Reverberation"], "ftp": (1.56, 1.88, 2.5),
                          "fx": [("debuff", "slow", 0.15, 60)]},
    "Tachi: Kasha":      {"props": ["Fusion", "Compression"], "ftp": (1.56, 2.12, 2.5),
                          "fx": [("debuff", "paralyze", 0.2, 60)]},
    "Tachi: Fudo":       {"props": ["Light", "Distortion"], "ftp": (3.75, 5.0, 6.0)},
    # Sword and Shield (FFXI Sword)
    "Fast Blade":        {"props": ["Scission"], "ftp": (1.0, 1.5, 2.0), "hits": 2},
    "Burning Blade":     {"props": ["Liquefaction"], "ftp": (1.0, 2.1, 3.4), "element": "fire"},
    "Red Lotus Blade":   {"props": ["Liquefaction", "Detonation"], "ftp": (1.0, 2.38, 3.75), "element": "fire"},
    "Flat Blade":        {"props": ["Impaction"], "ftp": (1.0, 1.0, 1.0), "fx": [("debuff", "sleep", 1, 6)]},
    "Shining Blade":     {"props": ["Scission"], "ftp": (1.13, 2.25, 3.38), "element": "light"},
    "Seraph Blade":      {"props": ["Scission"], "ftp": (1.13, 2.63, 3.5), "element": "light"},
    "Circle Blade":      {"props": ["Reverberation", "Impaction"], "ftp": (1.0, 1.25, 1.5)},
    "Spirits Within":    {"props": [], "ftp": (1.5, 2.5, 4.0)},
    "Vorpal Blade":      {"props": ["Scission", "Impaction"], "ftp": (1.38, 1.38, 1.38), "hits": 4},
    "Swift Blade":       {"props": ["Gravitation"], "ftp": (1.5, 1.5, 1.5), "hits": 3},
    "Savage Blade":      {"props": ["Fragmentation", "Scission"], "ftp": (3.0, 4.5, 6.0)},
    "Requiescat":        {"props": ["Gravitation", "Scission"], "ftp": (1.0, 1.0, 1.0), "hits": 5},
    # Hammer (FFXI Club)
    "Shining Strike":    {"props": ["Impaction"], "ftp": (1.63, 2.38, 3.13), "element": "light"},
    "Seraph Strike":     {"props": ["Impaction"], "ftp": (2.13, 3.63, 5.13), "element": "light"},
    "Brainshaker":       {"props": ["Reverberation"], "ftp": (1.0, 1.0, 1.0), "fx": [("debuff", "sleep", 1, 6)]},
    "Starlight":         {"props": [], "ftp": (0.0, 0.0, 0.0), "fx": [("mp", 30)]},
    "Moonlight":         {"props": [], "ftp": (0.0, 0.0, 0.0), "fx": [("mp", 50)]},
    "Skullbreaker":      {"props": ["Induration", "Reverberation"], "ftp": (1.0, 1.0, 1.0),
                          "fx": [("debuff", "def_down", 0.1, 60)]},
    "True Strike":       {"props": ["Detonation", "Impaction"], "ftp": (1.0, 1.0, 1.0)},
    "Judgment":          {"props": ["Impaction"], "ftp": (3.0, 4.0, 5.0)},
    "Hexa Strike":       {"props": ["Fusion"], "ftp": (1.13, 1.13, 1.13), "hits": 6},
    "Black Halo":        {"props": ["Fragmentation", "Compression"], "ftp": (3.0, 4.5, 6.0)},
    # Lance (FFXI Polearm)
    "Double Thrust":     {"props": ["Transfixion"], "ftp": (1.0, 1.5, 2.0), "hits": 2},
    "Thunder Thrust":    {"props": ["Transfixion", "Impaction"], "ftp": (1.5, 2.0, 2.5), "element": "thunder"},
    "Raiden Thrust":     {"props": ["Transfixion", "Impaction"], "ftp": (1.0, 2.0, 3.0), "element": "thunder"},
    "Leg Sweep":         {"props": ["Impaction"], "ftp": (1.0, 1.0, 1.0), "fx": [("debuff", "sleep", 1, 6)]},
    "Penta Thrust":      {"props": ["Compression"], "ftp": (1.0, 1.0, 1.0), "hits": 5},
    "Vorpal Thrust":     {"props": ["Reverberation", "Transfixion"], "ftp": (1.0, 1.0, 1.0), "hits": 2},
    "Skewer":            {"props": ["Transfixion", "Impaction"], "ftp": (1.0, 1.0, 1.0), "hits": 3},
    "Wheeling Thrust":   {"props": ["Fusion"], "ftp": (1.75, 2.25, 3.0), "fx": [("debuff", "def_down", 0.15, 30)]},
    "Impulse Drive":     {"props": ["Gravitation", "Induration"], "ftp": (1.0, 3.0, 5.0), "hits": 2},
    "Stardiver":         {"props": ["Gravitation", "Transfixion"], "ftp": (0.75, 1.25, 1.75), "hits": 4},
    # Switch Axe (FFXI Great Axe)
    "Shield Break":      {"props": ["Impaction"], "ftp": (1.0, 1.0, 1.0), "fx": [("debuff", "blind", 0.15, 60)]},
    "Iron Tempest":      {"props": ["Scission"], "ftp": (1.0, 1.5, 2.0)},
    "Sturmwind":         {"props": ["Reverberation", "Scission"], "ftp": (1.0, 1.0, 1.0), "hits": 2},
    "Armor Break":       {"props": ["Impaction"], "ftp": (1.0, 1.0, 1.0), "fx": [("debuff", "def_down", 0.25, 90)]},
    "Keen Edge":         {"props": ["Compression"], "ftp": (1.0, 1.0, 1.0)},
    "Weapon Break":      {"props": ["Impaction"], "ftp": (1.0, 1.0, 1.0), "fx": [("debuff", "weight", 0.2, 90)]},
    "Raging Rush":       {"props": ["Induration", "Reverberation"], "ftp": (1.0, 1.0, 1.0), "hits": 3},
    "Full Break":        {"props": ["Distortion"], "ftp": (1.0, 1.0, 1.0),
                          "fx": [("debuff", "def_down", 0.15, 60), ("debuff", "slow", 0.15, 60)]},
    "Steel Cyclone":     {"props": ["Distortion", "Detonation"], "ftp": (1.5, 2.5, 4.0)},
    "Upheaval":          {"props": ["Fusion", "Compression"], "ftp": (1.0, 3.5, 6.0), "hits": 4},
    # Bowguns (FFXI Marksmanship)
    "Hot Shot":          {"props": ["Liquefaction", "Transfixion"], "ftp": (0.5, 1.55, 2.1), "element": "fire"},
    "Split Shot":        {"props": ["Reverberation", "Transfixion"], "ftp": (1.0, 1.0, 1.0),
                          "fx": [("debuff", "def_down", 0.1, 60)]},
    "Sniper Shot":       {"props": ["Liquefaction", "Transfixion"], "ftp": (1.0, 1.0, 1.0)},
    "Slug Shot":         {"props": ["Reverberation", "Transfixion"], "ftp": (4.0, 4.5, 5.0)},
    "Blast Shot":        {"props": ["Induration", "Transfixion"], "ftp": (2.0, 2.0, 2.0)},
    "Heavy Shot":        {"props": ["Fusion"], "ftp": (3.0, 3.0, 3.0)},
    "Detonator":         {"props": ["Fusion", "Transfixion"], "ftp": (1.5, 2.5, 4.0)},
    "Last Stand":        {"props": ["Fusion", "Reverberation"], "ftp": (2.0, 2.25, 2.5), "hits": 2},
    "Coronach":          {"props": ["Darkness", "Fragmentation"], "ftp": (3.0, 3.0, 3.0)},
}

# MH Tri weapon: FFXI delay (TP per hit) and its weapon skills by the level they are learnt at.
WEAPONS = {
    "great_sword": {"delay": 480, "ws": [(1, "Hard Slash"), (9, "Power Slash"), (18, "Frostbite"), (27, "Freezebite"),
                                         (33, "Shockwave"), (41, "Crescent Moon"), (49, "Sickle Moon"),
                                         (56, "Spinning Slash"), (65, "Ground Strike"), (72, "Resolution")]},
    "long_sword": {"delay": 420, "ws": [(1, "Tachi: Enpi"), (9, "Tachi: Hobaku"), (18, "Tachi: Goten"),
                                        (26, "Tachi: Kagero"), (33, "Tachi: Jinpu"), (41, "Tachi: Koki"),
                                        (49, "Tachi: Yukikaze"), (56, "Tachi: Gekko"), (65, "Tachi: Kasha"),
                                        (72, "Tachi: Fudo")]},
    "sword_and_shield": {"delay": 220, "ws": [(1, "Fast Blade"), (9, "Burning Blade"), (18, "Red Lotus Blade"),
                                              (24, "Flat Blade"), (30, "Shining Blade"), (36, "Seraph Blade"),
                                              (42, "Circle Blade"), (48, "Spirits Within"), (54, "Vorpal Blade"),
                                              (62, "Swift Blade"), (68, "Savage Blade"), (75, "Requiescat")]},
    "hammer": {"delay": 400, "ws": [(1, "Shining Strike"), (9, "Seraph Strike"), (18, "Brainshaker"),
                                    (26, "Starlight"), (33, "Skullbreaker"), (41, "True Strike"),
                                    (49, "Judgment"), (56, "Hexa Strike"), (65, "Moonlight"), (72, "Black Halo")]},
    "lance": {"delay": 360, "ws": [(1, "Double Thrust"), (9, "Thunder Thrust"), (18, "Raiden Thrust"),
                                   (26, "Leg Sweep"), (33, "Penta Thrust"), (41, "Vorpal Thrust"), (49, "Skewer"),
                                   (56, "Wheeling Thrust"), (65, "Impulse Drive"), (72, "Stardiver")]},
    "switch_axe": {"delay": 450, "ws": [(1, "Shield Break"), (9, "Iron Tempest"), (18, "Sturmwind"),
                                        (26, "Armor Break"), (33, "Keen Edge"), (41, "Weapon Break"),
                                        (49, "Raging Rush"), (56, "Full Break"), (65, "Steel Cyclone"),
                                        (72, "Upheaval")]},
    "light_bowgun": {"delay": 240, "ws": [(1, "Hot Shot"), (9, "Split Shot"), (18, "Sniper Shot"),
                                          (30, "Slug Shot"), (41, "Blast Shot"), (49, "Heavy Shot"),
                                          (60, "Detonator"), (70, "Last Stand"), (75, "Coronach")]},
    "heavy_bowgun": {"delay": 400, "ws": [(1, "Hot Shot"), (9, "Split Shot"), (18, "Sniper Shot"),
                                          (30, "Slug Shot"), (41, "Blast Shot"), (49, "Heavy Shot"),
                                          (60, "Detonator"), (70, "Last Stand"), (75, "Coronach")]},
}


# ================================================================================================
# Skillchains and magic bursts
# ================================================================================================
SC_LEVEL = {
    "Liquefaction": 1, "Scission": 1, "Reverberation": 1, "Detonation": 1,
    "Induration": 1, "Impaction": 1, "Transfixion": 1, "Compression": 1,
    "Fusion": 2, "Fragmentation": 2, "Gravitation": 2, "Distortion": 2,
    "Light": 3, "Darkness": 3,
}

# (opener property, closer property) -> resulting skillchain
SKILLCHAINS = {
    ("Transfixion", "Compression"): "Compression",
    ("Transfixion", "Scission"): "Distortion",
    ("Transfixion", "Reverberation"): "Reverberation",
    ("Compression", "Transfixion"): "Transfixion",
    ("Compression", "Detonation"): "Detonation",
    ("Liquefaction", "Scission"): "Scission",
    ("Liquefaction", "Impaction"): "Fusion",
    ("Scission", "Liquefaction"): "Liquefaction",
    ("Scission", "Reverberation"): "Reverberation",
    ("Scission", "Detonation"): "Detonation",
    ("Reverberation", "Induration"): "Induration",
    ("Reverberation", "Impaction"): "Impaction",
    ("Detonation", "Compression"): "Gravitation",
    ("Detonation", "Scission"): "Scission",
    ("Induration", "Compression"): "Compression",
    ("Induration", "Reverberation"): "Fragmentation",
    ("Induration", "Impaction"): "Impaction",
    ("Impaction", "Liquefaction"): "Liquefaction",
    ("Impaction", "Detonation"): "Detonation",
    ("Fusion", "Gravitation"): "Gravitation",
    ("Fusion", "Fragmentation"): "Light",
    ("Fragmentation", "Fusion"): "Light",
    ("Fragmentation", "Distortion"): "Distortion",
    ("Gravitation", "Distortion"): "Darkness",
    ("Gravitation", "Fragmentation"): "Fragmentation",
    ("Distortion", "Gravitation"): "Darkness",
    ("Distortion", "Fusion"): "Fusion",
    ("Light", "Light"): "Light",
    ("Darkness", "Darkness"): "Darkness",
}

SC_BONUS = {1: 0.5, 2: 0.6, 3: 1.0}     # the skillchain's damage: this fraction of the closing WS

SC_ELEMENTS = {
    "Liquefaction": {"fire"}, "Scission": {"earth"}, "Reverberation": {"water"},
    "Detonation": {"wind"}, "Induration": {"ice"}, "Impaction": {"thunder"},
    "Transfixion": {"light"}, "Compression": {"dark"},
    "Fusion": {"fire", "light"}, "Fragmentation": {"wind", "thunder"},
    "Gravitation": {"earth", "dark"}, "Distortion": {"water", "ice"},
    "Light": {"fire", "wind", "thunder", "light"},
    "Darkness": {"earth", "water", "ice", "dark"},
}

# Each weapon skill's effect in FFXI's own files: its animation number (LandSandBoat's weapon_skills, a
# reference); a race's file for it is that race's first weapon skill file + the number - 1 (ffxi_fx.py).
WS_ANIMATION = {
    "Hard Slash": 106, "Power Slash": 107, "Frostbite": 108, "Freezebite": 109, "Shockwave": 110,
    "Crescent Moon": 111, "Sickle Moon": 112, "Spinning Slash": 113, "Ground Strike": 114, "Resolution": 118,
    "Tachi: Enpi": 166, "Tachi: Hobaku": 167, "Tachi: Goten": 168, "Tachi: Kagero": 169, "Tachi: Jinpu": 170,
    "Tachi: Koki": 171, "Tachi: Yukikaze": 172, "Tachi: Gekko": 173, "Tachi: Kasha": 174, "Tachi: Fudo": 178,
    "Fast Blade": 1, "Burning Blade": 2, "Red Lotus Blade": 3, "Flat Blade": 6, "Shining Blade": 4,
    "Seraph Blade": 5, "Circle Blade": 7, "Spirits Within": 8, "Vorpal Blade": 9, "Swift Blade": 10,
    "Savage Blade": 11, "Requiescat": 237, "Shining Strike": 76, "Seraph Strike": 77, "Brainshaker": 78,
    "Starlight": 79, "Moonlight": 80, "Skullbreaker": 81, "True Strike": 82, "Judgment": 83, "Hexa Strike": 84,
    "Black Halo": 85, "Double Thrust": 121, "Thunder Thrust": 122, "Raiden Thrust": 123, "Leg Sweep": 124,
    "Penta Thrust": 125, "Vorpal Thrust": 126, "Skewer": 127, "Wheeling Thrust": 128, "Impulse Drive": 129,
    "Stardiver": 134, "Shield Break": 91, "Iron Tempest": 92, "Sturmwind": 93, "Armor Break": 94,
    "Keen Edge": 95, "Weapon Break": 96, "Raging Rush": 97, "Full Break": 98, "Steel Cyclone": 99,
    "Upheaval": 104, "Hot Shot": 196, "Split Shot": 197, "Sniper Shot": 198, "Slug Shot": 200,
    "Blast Shot": 222, "Heavy Shot": 223, "Detonator": 224, "Last Stand": 241, "Coronach": 226
}

SC_WINDOW_OPEN = 2.0      # seconds after a WS before the next one can close a chain
SC_WINDOW_CLOSE = 8.0     # seconds after a WS when the chain window shuts
MB_WINDOW = 8.0           # seconds after a skillchain to land a magic burst
MB_BONUS = 1.35


# ================================================================================================
# Spells
# ================================================================================================
# school: white, black, song, ninjutsu, summoning, blue, geomancy (the Magic menu's lists)
# mp, cast (s), recast (s); fx: what it does, in order:
#   ("damage", power, element)          nuke: power x your average hit (magic burst if it fits)
#   ("drain", power, element, back)     a nuke healing you back x its damage
#   ("aspir", mp)                       MP back
#   ("heal", hp)                        HP back (x your healing bonus)
#   ("buff", effect, power, seconds)    on you; for shadows/stoneskin/aquaveil power is the count/pool
#   ("debuff", effect, power, seconds)  on the monster
#   ("pet", name, power, seconds, upkeep)   a pet fighting at your side: power x avg hit every tick,
#                                           upkeep MP every tick (an avatar's perpetuation)
#   ("mp", amount) ("tp", amount)       MP / TP back
# needs_pet: only with a pet out.

def _nuke(school, mp, cast, recast, power, element):
    return {"school": school, "mp": mp, "cast": cast, "recast": recast, "fx": [("damage", power, element)]}


def _buff(school, mp, cast, recast, effect, power, seconds, *more):
    return {"school": school, "mp": mp, "cast": cast, "recast": recast,
            "fx": [("buff", effect, power, seconds), *more]}


def _debuff(school, mp, cast, recast, effect, power, seconds, *more):
    return {"school": school, "mp": mp, "cast": cast, "recast": recast,
            "fx": [("debuff", effect, power, seconds), *more]}


def _heal(school, mp, cast, recast, hp):
    return {"school": school, "mp": mp, "cast": cast, "recast": recast, "fx": [("heal", hp)]}


SPELLS = {
    # --- white magic ---
    "Cure":         _heal("white", 8, 2, 5, 20),
    "Cure II":      _heal("white", 24, 2.25, 5.5, 40),
    "Cure III":     _heal("white", 46, 2.5, 6, 65),
    "Cure IV":      _heal("white", 88, 2.5, 8, 95),
    "Cure V":       _heal("white", 135, 2.5, 10, 125),
    "Regen":        _buff("white", 15, 1.5, 12, "regen", 2, 75),
    "Regen II":     _buff("white", 36, 1.75, 16, "regen", 4, 60),
    "Regen III":    _buff("white", 64, 2, 20, "regen", 6, 60),
    "Protect":      _buff("white", 9, 1, 5, "defn", 0.08, 1800),
    "Protect II":   _buff("white", 28, 1.25, 5.25, "defn", 0.12, 1800),
    "Protect III":  _buff("white", 46, 1.5, 5.5, "defn", 0.16, 1800),
    "Protect IV":   _buff("white", 65, 1.75, 5.75, "defn", 0.2, 1800),
    "Shell":        _buff("white", 18, 1, 5, "defn", 0.04, 1800),       # MH has no magic: a little defense
    "Shell II":     _buff("white", 37, 1.25, 5.25, "defn", 0.06, 1800),
    "Haste":        _buff("white", 40, 3, 20, "haste", 0.15, 180),
    "Blink":        _buff("white", 20, 6, 10, "shadows", 2, 300),
    "Stoneskin":    _buff("white", 29, 7, 30, "stoneskin", 40, 300),
    "Aquaveil":     _buff("white", 12, 5, 10, "aquaveil", 2, 600),
    "Auspice":      _buff("white", 48, 3, 10, "enspell", 0.15, 180),
    "Dia":          _debuff("white", 7, 1, 5, "dot", 0.05, 60, ("debuff", "def_down", 0.05, 60)),
    "Dia II":       _debuff("white", 30, 1.5, 6, "dot", 0.1, 120, ("debuff", "def_down", 0.1, 120)),
    "Paralyze":     _debuff("white", 6, 3, 10, "paralyze", 0.15, 120),
    "Slow":         _debuff("white", 15, 2, 20, "slow", 0.12, 120),
    "Flash":        _debuff("white", 25, 0.5, 45, "blind", 0.5, 12),
    "Repose":       _debuff("white", 26, 3, 30, "sleep", 1, 60),
    "Banish":       _nuke("white", 15, 2, 15, 1.5, "light"),
    "Banish II":    _nuke("white", 57, 2.5, 30, 3.0, "light"),
    "Holy":         _nuke("white", 100, 0.75, 60, 5.0, "light"),
    "Phalanx":      _buff("white", 21, 3, 10, "phalanx", 3, 180),
    "Refresh":      _buff("white", 40, 5, 18, "refresh", 3, 150),
    "Enfire":       _buff("white", 12, 3, 10, "enspell", 0.2, 180),
    "Enblizzard":   _buff("white", 12, 3, 10, "enspell", 0.2, 180),
    "Enthunder":    _buff("white", 12, 3, 10, "enspell", 0.2, 180),
    "Reprisal":     _buff("white", 24, 1, 180, "defn", 0.2, 60),
    # --- black magic ---
    "Stone":        _nuke("black", 4, 0.5, 2, 1.0, "earth"),
    "Water":        _nuke("black", 5, 0.5, 2, 1.1, "water"),
    "Aero":         _nuke("black", 6, 0.5, 2, 1.2, "wind"),
    "Fire":         _nuke("black", 7, 0.5, 2, 1.3, "fire"),
    "Blizzard":     _nuke("black", 8, 0.5, 2, 1.4, "ice"),
    "Thunder":      _nuke("black", 9, 0.5, 2, 1.5, "thunder"),
    "Stone II":     _nuke("black", 16, 1.5, 6, 2.2, "earth"),
    "Water II":     _nuke("black", 19, 1.5, 6, 2.4, "water"),
    "Aero II":      _nuke("black", 22, 1.5, 6, 2.6, "wind"),
    "Fire II":      _nuke("black", 26, 1.5, 6, 2.8, "fire"),
    "Blizzard II":  _nuke("black", 31, 1.5, 6, 3.0, "ice"),
    "Thunder II":   _nuke("black", 37, 1.5, 6, 3.2, "thunder"),
    "Stone III":    _nuke("black", 40, 3, 15, 3.8, "earth"),
    "Water III":    _nuke("black", 46, 3, 15, 4.0, "water"),
    "Aero III":     _nuke("black", 54, 3, 15, 4.2, "wind"),
    "Fire III":     _nuke("black", 63, 3, 15, 4.4, "fire"),
    "Blizzard III": _nuke("black", 75, 3, 15, 4.6, "ice"),
    "Thunder III":  _nuke("black", 91, 3, 15, 4.8, "thunder"),
    "Fire IV":      _nuke("black", 135, 5, 30, 6.0, "fire"),
    "Blizzard IV":  _nuke("black", 162, 5, 30, 6.2, "ice"),
    "Thunder IV":   _nuke("black", 195, 5, 30, 6.4, "thunder"),
    "Flare":        _nuke("black", 315, 8.5, 60, 9.0, "fire"),
    "Freeze":       _nuke("black", 315, 8.5, 60, 9.0, "ice"),
    "Burst":        _nuke("black", 315, 8.5, 60, 9.0, "thunder"),
    "Quake":        _nuke("black", 315, 8.5, 60, 9.0, "earth"),
    "Tornado":      _nuke("black", 315, 8.5, 60, 9.0, "wind"),
    "Flood":        _nuke("black", 315, 8.5, 60, 9.0, "water"),
    "Drain":        {"school": "black", "mp": 21, "cast": 3, "recast": 60, "fx": [("drain", 1.5, "dark", 1.0)]},
    "Drain II":     {"school": "black", "mp": 37, "cast": 3, "recast": 180, "fx": [("drain", 2.5, "dark", 1.0)]},
    "Aspir":        {"school": "black", "mp": 10, "cast": 3, "recast": 60, "fx": [("aspir", 30)]},
    "Poison":       _debuff("black", 5, 1, 5, "dot", 0.04, 90),
    "Poison II":    _debuff("black", 38, 1, 5, "dot", 0.09, 120),
    "Bio":          _debuff("black", 15, 1.5, 5, "dot", 0.07, 60, ("debuff", "def_down", 0.05, 60)),
    "Bio II":       _debuff("black", 36, 1.5, 5, "dot", 0.12, 120, ("debuff", "def_down", 0.1, 120)),
    "Burn":         _debuff("black", 25, 2.5, 10, "dot", 0.1, 90),
    "Frost":        _debuff("black", 25, 2.5, 10, "dot", 0.1, 90),
    "Shock":        _debuff("black", 25, 2.5, 10, "dot", 0.1, 90),
    "Drown":        _debuff("black", 25, 2.5, 10, "dot", 0.1, 90),
    "Blind":        _debuff("black", 5, 2, 10, "blind", 0.2, 120),
    "Bind":         _debuff("black", 8, 2, 40, "weight", 0.3, 30),            # stand-in: weaker hits
    "Gravity":      _debuff("black", 24, 1.5, 60, "weight", 0.2, 90),
    "Sleep":        _debuff("black", 19, 2.5, 30, "sleep", 1, 60),
    "Sleep II":     _debuff("black", 29, 3, 30, "sleep", 1, 90),
    "Stun":         _debuff("black", 25, 0.5, 45, "sleep", 1, 5),               # stand-in: a short sleep
    "Absorb-TP":    {"school": "black", "mp": 33, "cast": 0.5, "recast": 60, "fx": [("tp", 300)]},
    "Dread Spikes": _buff("black", 78, 3, 180, "stoneskin", 50, 180, ("buff", "drain_hit", 0.1, 180)),
    "Pyrohelix":    _debuff("black", 26, 5, 45, "dot", 0.12, 60, ("damage", 0.8, "fire")),
    "Cryohelix":    _debuff("black", 26, 5, 45, "dot", 0.12, 60, ("damage", 0.8, "ice")),
    "Ionohelix":    _debuff("black", 26, 5, 45, "dot", 0.12, 60, ("damage", 0.8, "thunder")),
    "Noctohelix":   _debuff("black", 26, 5, 45, "dot", 0.12, 60, ("damage", 0.8, "dark")),
    "Luminohelix":  _debuff("black", 26, 5, 45, "dot", 0.12, 60, ("damage", 0.8, "light")),
    # --- songs (Bard) ---
    "Valor Minuet":       _buff("song", 0, 8, 24, "atk", 0.08, 120),
    "Valor Minuet II":    _buff("song", 0, 8, 24, "atk", 0.12, 120),
    "Valor Minuet III":   _buff("song", 0, 8, 24, "atk", 0.16, 120),
    "Valor Minuet IV":    _buff("song", 0, 8, 24, "atk", 0.2, 120),
    "Sword Madrigal":     _buff("song", 0, 8, 24, "store_tp", 0.1, 120),
    "Blade Madrigal":     _buff("song", 0, 8, 24, "store_tp", 0.15, 120),
    "Advancing March":    _buff("song", 0, 8, 24, "haste", 0.1, 120),
    "Victory March":      _buff("song", 0, 8, 24, "haste", 0.16, 120),
    "Mage's Ballad":      _buff("song", 0, 8, 24, "refresh", 1, 120),
    "Mage's Ballad II":   _buff("song", 0, 8, 24, "refresh", 2, 120),
    "Army's Paeon":       _buff("song", 0, 8, 24, "regen", 1, 120),
    "Army's Paeon II":    _buff("song", 0, 8, 24, "regen", 2, 120),
    "Army's Paeon III":   _buff("song", 0, 8, 24, "regen", 3, 120),
    "Knight's Minne":     _buff("song", 0, 8, 24, "defn", 0.06, 120),
    "Knight's Minne II":  _buff("song", 0, 8, 24, "defn", 0.1, 120),
    "Foe Requiem":        _debuff("song", 0, 2, 24, "dot", 0.05, 60),
    "Foe Requiem II":     _debuff("song", 0, 2, 24, "dot", 0.08, 90),
    "Foe Lullaby":        _debuff("song", 0, 2, 24, "sleep", 1, 30),
    "Battlefield Elegy":  _debuff("song", 0, 2, 24, "slow", 0.2, 120),
    "Carnage Elegy":      _debuff("song", 0, 2, 24, "slow", 0.3, 180),
    # --- ninjutsu (Ninja) ---
    "Utsusemi: Ichi": _buff("ninjutsu", 0, 4, 30, "shadows", 3, 900),
    "Utsusemi: Ni":   _buff("ninjutsu", 0, 1.5, 45, "shadows", 3, 900),
    "Katon: Ichi":    _nuke("ninjutsu", 0, 4, 30, 1.5, "fire"),
    "Hyoton: Ichi":   _nuke("ninjutsu", 0, 4, 30, 1.5, "ice"),
    "Raiton: Ichi":   _nuke("ninjutsu", 0, 4, 30, 1.5, "thunder"),
    "Doton: Ichi":    _nuke("ninjutsu", 0, 4, 30, 1.5, "earth"),
    "Huton: Ichi":    _nuke("ninjutsu", 0, 4, 30, 1.5, "wind"),
    "Suiton: Ichi":   _nuke("ninjutsu", 0, 4, 30, 1.5, "water"),
    "Katon: Ni":      _nuke("ninjutsu", 0, 1.5, 45, 3.0, "fire"),
    "Hyoton: Ni":     _nuke("ninjutsu", 0, 1.5, 45, 3.0, "ice"),
    "Raiton: Ni":     _nuke("ninjutsu", 0, 1.5, 45, 3.0, "thunder"),
    "Doton: Ni":      _nuke("ninjutsu", 0, 1.5, 45, 3.0, "earth"),
    "Huton: Ni":      _nuke("ninjutsu", 0, 1.5, 45, 3.0, "wind"),
    "Suiton: Ni":     _nuke("ninjutsu", 0, 1.5, 45, 3.0, "water"),
    "Kurayami: Ichi": _debuff("ninjutsu", 0, 4, 30, "blind", 0.2, 180),
    "Hojo: Ichi":     _debuff("ninjutsu", 0, 4, 30, "slow", 0.1, 180),
    "Jubaku: Ichi":   _debuff("ninjutsu", 0, 4, 30, "paralyze", 0.15, 180),
    "Dokumori: Ichi": _debuff("ninjutsu", 0, 4, 30, "dot", 0.05, 60),
    # --- summoning (Summoner): an avatar is a pet; its blood pacts are job abilities ---
    "Carbuncle": {"school": "summoning", "mp": 5, "cast": 5, "recast": 5, "fx": [("pet", "Carbuncle", 0.25, 900, 1)]},
    "Ifrit":     {"school": "summoning", "mp": 7, "cast": 5, "recast": 5, "fx": [("pet", "Ifrit", 0.4, 900, 2)]},
    "Shiva":     {"school": "summoning", "mp": 7, "cast": 5, "recast": 5, "fx": [("pet", "Shiva", 0.35, 900, 2)]},
    "Garuda":    {"school": "summoning", "mp": 7, "cast": 5, "recast": 5, "fx": [("pet", "Garuda", 0.35, 900, 2)]},
    "Titan":     {"school": "summoning", "mp": 7, "cast": 5, "recast": 5, "fx": [("pet", "Titan", 0.35, 900, 2)]},
    "Ramuh":     {"school": "summoning", "mp": 7, "cast": 5, "recast": 5, "fx": [("pet", "Ramuh", 0.35, 900, 2)]},
    "Leviathan": {"school": "summoning", "mp": 7, "cast": 5, "recast": 5, "fx": [("pet", "Leviathan", 0.35, 900, 2)]},
    # --- blue magic (Blue Mage): monsters' moves ---
    "Foot Kick":       {"school": "blue", "mp": 5, "cast": 0.5, "recast": 6.5, "fx": [("damage", 1.2, "physical")], "props": ["Detonation"]},
    "Sprout Smack":    {"school": "blue", "mp": 6, "cast": 0.5, "recast": 7.25, "fx": [("damage", 1.0, "physical"), ("debuff", "slow", 0.1, 60)], "props": ["Reverberation"]},
    "Power Attack":    {"school": "blue", "mp": 5, "cast": 0.5, "recast": 7.25, "fx": [("damage", 1.4, "physical")], "props": ["Reverberation"]},
    "Head Butt":       {"school": "blue", "mp": 12, "cast": 0.5, "recast": 10, "fx": [("damage", 1.3, "physical"), ("debuff", "sleep", 1, 5)], "props": ["Impaction"]},
    "Bludgeon":        {"school": "blue", "mp": 16, "cast": 0.5, "recast": 11.75, "fx": [("damage", 2.4, "physical")], "props": ["Liquefaction"]},
    "Death Scissors":  {"school": "blue", "mp": 51, "cast": 0.5, "recast": 24.5, "fx": [("damage", 4.0, "physical")], "props": ["Compression", "Reverberation"]},
    "Frenetic Rip":    {"school": "blue", "mp": 61, "cast": 0.5, "recast": 28.5, "fx": [("damage", 4.2, "physical")], "props": ["Induration"]},
    "Vertical Cleave": {"school": "blue", "mp": 86, "cast": 0.5, "recast": 37.25, "fx": [("damage", 5.0, "physical")], "props": ["Gravitation"]},
    "Firespit":        _nuke("blue", 121, 6.5, 42.75, 4.0, "fire"),
    "Ice Break":       _nuke("blue", 142, 5.25, 33.75, 2.0, "ice"),
    "Maelstrom":       _nuke("blue", 162, 6, 39, 4.5, "water"),
    "Pollen":          _heal("blue", 8, 2, 5, 20),
    "Wild Carrot":     _heal("blue", 37, 2.5, 6, 50),
    "Magic Fruit":     _heal("blue", 72, 2.5, 6, 85),
    "Cocoon":          _buff("blue", 10, 1.75, 60, "defn", 0.25, 90),
    "Refueling":       _buff("blue", 29, 1.5, 30, "haste", 0.1, 300),
    "Metallic Body":   _buff("blue", 19, 3, 60, "stoneskin", 30, 300),
    "Diamondhide":     _buff("blue", 99, 7, 26, "stoneskin", 60, 300),
    "Feather Barrier": _buff("blue", 29, 2, 56, "shadows", 1, 30),
    "Sheep Song":      _debuff("blue", 22, 3, 60, "sleep", 1, 60),
    "Blank Gaze":      _debuff("blue", 25, 3, 10, "def_down", 0.1, 60),
    # --- geomancy (Geomancer): Indi- spells, a bubble around you ---
    "Indi-Regen":   _buff("geomancy", 37, 2, 12, "regen", 3, 180),
    "Indi-Refresh": _buff("geomancy", 63, 2, 12, "refresh", 2, 180),
    "Indi-Fury":    _buff("geomancy", 70, 2, 12, "atk", 0.15, 180),
    "Indi-Barrier": _buff("geomancy", 59, 2, 12, "defn", 0.15, 180),
    "Indi-Acumen":  _buff("geomancy", 91, 2, 12, "nuke_bonus", 0.2, 180),
    "Indi-Haste":   _buff("geomancy", 100, 2, 12, "haste", 0.2, 180),
    "Indi-Frailty": _debuff("geomancy", 147, 2, 12, "def_down", 0.15, 180),
    "Indi-Poison":  _debuff("geomancy", 12, 2, 12, "dot", 0.06, 180),
    "Indi-Slow":    _debuff("geomancy", 94, 2, 12, "slow", 0.15, 180),
}

# Each spell's effect in FFXI's own files: its animation number (LandSandBoat's spell_list, a reference),
# the effect being file 2800 + it (ffxi_sound.py). Mostly the spell's number; not for every spell.
SPELL_ANIMATION = {
    "Cure": 1, "Cure II": 2, "Cure III": 3, "Cure IV": 4, "Cure V": 5, "Regen": 108, "Regen II": 139,
    "Regen III": 140, "Protect": 43, "Protect II": 44, "Protect III": 45, "Protect IV": 46, "Shell": 48,
    "Shell II": 49, "Haste": 57, "Blink": 53, "Stoneskin": 54, "Aquaveil": 55, "Auspice": 142, "Dia": 23,
    "Dia II": 24, "Paralyze": 58, "Slow": 56, "Flash": 486, "Repose": 482, "Banish": 28, "Banish II": 29,
    "Holy": 21, "Phalanx": 106, "Refresh": 109, "Enfire": 100, "Enblizzard": 101, "Enthunder": 104,
    "Reprisal": 143, "Stone": 159, "Water": 169, "Aero": 154, "Fire": 144, "Blizzard": 149, "Thunder": 164,
    "Stone II": 160, "Water II": 170, "Aero II": 155, "Fire II": 145, "Blizzard II": 150, "Thunder II": 165,
    "Stone III": 161, "Water III": 171, "Aero III": 156, "Fire III": 146, "Blizzard III": 151,
    "Thunder III": 166, "Fire IV": 147, "Blizzard IV": 152, "Thunder IV": 167, "Flare": 204, "Freeze": 206,
    "Burst": 212, "Quake": 210, "Tornado": 208, "Flood": 214, "Drain": 245, "Drain II": 246, "Aspir": 247,
    "Poison": 220, "Poison II": 221, "Bio": 230, "Bio II": 231, "Burn": 235, "Frost": 236, "Shock": 239,
    "Drown": 240, "Blind": 254, "Bind": 258, "Gravity": 216, "Sleep": 253, "Sleep II": 265, "Stun": 252,
    "Absorb-TP": 125, "Dread Spikes": 484, "Pyrohelix": 781, "Cryohelix": 786, "Ionohelix": 783,
    "Noctohelix": 788, "Luminohelix": 787, "Valor Minuet": 393, "Valor Minuet II": 394,
    "Valor Minuet III": 395, "Valor Minuet IV": 396, "Sword Madrigal": 398, "Blade Madrigal": 399,
    "Advancing March": 434, "Victory March": 435, "Mage's Ballad": 385, "Mage's Ballad II": 386,
    "Army's Paeon": 378, "Army's Paeon II": 379, "Army's Paeon III": 380, "Knight's Minne": 388,
    "Knight's Minne II": 389, "Foe Requiem": 368, "Foe Requiem II": 369, "Foe Lullaby": 376,
    "Battlefield Elegy": 436, "Carnage Elegy": 437, "Utsusemi: Ichi": 338, "Utsusemi: Ni": 339,
    "Katon: Ichi": 320, "Hyoton: Ichi": 323, "Raiton: Ichi": 332, "Doton: Ichi": 329, "Huton: Ichi": 326,
    "Suiton: Ichi": 335, "Katon: Ni": 321, "Hyoton: Ni": 324, "Raiton: Ni": 333, "Doton: Ni": 330,
    "Huton: Ni": 327, "Suiton: Ni": 336, "Kurayami: Ichi": 347, "Hojo: Ichi": 344, "Jubaku: Ichi": 341,
    "Dokumori: Ichi": 350, "Carbuncle": 288, "Ifrit": 288, "Shiva": 288, "Garuda": 288, "Titan": 288,
    "Ramuh": 288, "Leviathan": 288, "Foot Kick": 639, "Sprout Smack": 653, "Power Attack": 675,
    "Head Butt": 665, "Bludgeon": 643, "Death Scissors": 681, "Frenetic Rip": 748, "Vertical Cleave": 739,
    "Firespit": 758, "Ice Break": 721, "Maelstrom": 690, "Pollen": 650, "Wild Carrot": 640,
    "Magic Fruit": 708, "Cocoon": 654, "Refueling": 718, "Metallic Body": 638, "Diamondhide": 753,
    "Feather Barrier": 731, "Sheep Song": 672, "Blank Gaze": 707, "Indi-Regen": 861, "Indi-Refresh": 863,
    "Indi-Fury": 872, "Indi-Barrier": 873, "Indi-Acumen": 874, "Indi-Haste": 864, "Indi-Frailty": 881,
    "Indi-Poison": 862, "Indi-Slow": 888,
}

# ================================================================================================
# Job abilities
# ================================================================================================
# recast (s); tp: TP spent; needs_pet; fx as spells', plus:
#   ("hit", power)            a physical hit: power x your average hit, x weapon-skill bonuses
#   ("next_ws", bonus)        your next weapon skill does +bonus (Sneak Attack, Climactic Flourish)
#   ("full_heal",) ("mp_full",)   all HP / all MP back
#   ("roll", effect, low, high, seconds)   Corsair's dice: 1-11; power from low to high; Double-Up adds
#   ("double_up",)            another die on your last roll; over 11 it busts
#   ("release",)              your pet leaves
#   ("hp_to", n)              your HP set to n (Mijin Gakure)
#   ("random_ws",)            Meikyo Shisui and Wild Card: TP to 3000
# sp: an SP ability ("two-hour"): FFXI's hour scaled to Monster Hunter's quests.
SP_RECAST = 600

ABILITIES = {
    # Warrior
    "Berserk":       {"recast": 300, "fx": [("buff", "atk", 0.25, 180), ("buff", "defn", -0.15, 180)]},
    "Warcry":        {"recast": 300, "fx": [("buff", "atk", 0.15, 30)]},
    "Defender":      {"recast": 180, "fx": [("buff", "defn", 0.25, 180), ("buff", "atk", -0.15, 180)]},
    "Aggressor":     {"recast": 300, "fx": [("buff", "store_tp", 0.15, 180)]},
    "Mighty Strikes": {"recast": SP_RECAST, "sp": True, "fx": [("buff", "ws_bonus", 0.5, 45)]},
    # Monk
    "Boost":         {"recast": 15, "fx": [("buff", "atk", 0.3, 15)]},
    "Focus":         {"recast": 120, "fx": [("buff", "store_tp", 0.2, 120)]},
    "Dodge":         {"recast": 120, "fx": [("buff", "shadows", 2, 120)]},
    "Chakra":        {"recast": 180, "fx": [("heal", 60)]},
    "Chi Blast":     {"recast": 180, "fx": [("hit", 2.5)]},
    "Counterstance": {"recast": 300, "fx": [("buff", "atk", 0.15, 300), ("buff", "defn", -0.1, 300)]},
    "Hundred Fists": {"recast": SP_RECAST, "sp": True, "fx": [("buff", "haste", 1.0, 45), ("buff", "atk", 0.25, 45)]},
    # White Mage
    "Divine Seal":   {"recast": 600, "fx": [("buff", "heal_bonus", 1.0, 60)]},
    "Afflatus Solace": {"recast": 60, "fx": [("buff", "heal_bonus", 0.15, 7200)]},
    "Benediction":   {"recast": SP_RECAST, "sp": True, "fx": [("full_heal",)]},
    # Black Mage
    "Elemental Seal": {"recast": 600, "fx": [("buff", "nuke_bonus", 0.5, 60)]},
    "Manafont":      {"recast": SP_RECAST, "sp": True, "fx": [("buff", "conserve_mp", 1.0, 60)]},
    # Red Mage
    "Convert":       {"recast": 600, "fx": [("mp_full",)]},
    "Composure":     {"recast": 300, "fx": [("buff", "enspell", 0.1, 7200)]},
    "Chainspell":    {"recast": SP_RECAST, "sp": True, "fx": [("buff", "fast_cast", 1.0, 60)]},
    # Thief
    "Sneak Attack":  {"recast": 60, "fx": [("next_ws", 0.5)]},
    "Trick Attack":  {"recast": 60, "fx": [("next_ws", 0.3)]},
    "Mug":           {"recast": 300, "fx": [("hit", 1.5), ("heal", 15)]},
    "Perfect Dodge": {"recast": SP_RECAST, "sp": True, "fx": [("buff", "shadows", 99, 30)]},
    # Paladin
    "Shield Bash":   {"recast": 60, "fx": [("hit", 1.0), ("debuff", "sleep", 1, 4)]},
    "Holy Circle":   {"recast": 300, "fx": [("buff", "defn", 0.1, 180)]},
    "Sentinel":      {"recast": 300, "fx": [("buff", "defn", 0.5, 30)]},
    "Rampart":       {"recast": 300, "fx": [("buff", "stoneskin", 60, 30)]},
    "Invincible":    {"recast": SP_RECAST, "sp": True, "fx": [("buff", "defn", 1.0, 30)]},
    # Dark Knight
    "Weapon Bash":   {"recast": 180, "fx": [("hit", 1.0), ("debuff", "sleep", 1, 4)]},
    "Last Resort":   {"recast": 300, "fx": [("buff", "atk", 0.25, 180), ("buff", "defn", -0.25, 180)]},
    "Souleater":     {"recast": 360, "fx": [("buff", "atk", 0.3, 60)]},
    "Arcane Circle": {"recast": 300, "fx": [("buff", "atk", 0.05, 180)]},
    "Blood Weapon":  {"recast": SP_RECAST, "sp": True, "fx": [("buff", "drain_hit", 1.0, 30)]},
    # Beastmaster: a jug pet
    "Call Beast":    {"recast": 300, "fx": [("pet", "Beast", 0.35, 1200, 0)]},
    "Sic":           {"recast": 90, "needs_pet": True, "fx": [("hit", 2.0)]},
    "Reward":        {"recast": 90, "needs_pet": True, "fx": [("heal", 10)]},
    "Familiar":      {"recast": SP_RECAST, "sp": True, "needs_pet": True, "fx": [("pet", "Beast", 0.7, 1800, 0)]},
    # Bard
    "Soul Voice":    {"recast": SP_RECAST, "sp": True, "fx": [("buff", "atk", 0.2, 180), ("buff", "regen", 3, 180)]},
    # Ranger
    "Sharpshot":     {"recast": 300, "fx": [("buff", "atk", 0.1, 60)]},
    "Barrage":       {"recast": 300, "fx": [("hit", 3.0)]},
    "Shadowbind":    {"recast": 300, "fx": [("debuff", "sleep", 1, 15)]},
    "Velocity Shot": {"recast": 60, "fx": [("buff", "atk", 0.15, 7200)]},
    "Eagle Eye Shot": {"recast": SP_RECAST, "sp": True, "fx": [("hit", 10.0)]},
    # Samurai
    "Meditate":      {"recast": 180, "fx": [("buff", "regain", 100, 15)]},
    "Third Eye":     {"recast": 60, "fx": [("buff", "shadows", 1, 30)]},
    "Hasso":         {"recast": 60, "fx": [("buff", "haste", 0.1, 300), ("buff", "atk", 0.05, 300)]},
    "Seigan":        {"recast": 60, "fx": [("buff", "defn", 0.1, 300)]},
    "Sekkanoki":     {"recast": 300, "fx": [("buff", "store_tp", 0.5, 60)]},
    "Meikyo Shisui": {"recast": SP_RECAST, "sp": True, "fx": [("random_ws",)]},
    # Ninja
    "Yonin":         {"recast": 180, "fx": [("buff", "defn", 0.1, 300)]},
    "Innin":         {"recast": 180, "fx": [("buff", "atk", 0.1, 300)]},
    "Mijin Gakure":  {"recast": SP_RECAST, "sp": True, "fx": [("hit", 12.0), ("hp_to", 1)]},
    # Dragoon: the wyvern is a pet
    "Call Wyvern":   {"recast": 1200, "fx": [("pet", "Wyvern", 0.3, 1800, 0)]},
    "Jump":          {"recast": 60, "fx": [("hit", 1.5), ("tp", 100)]},
    "High Jump":     {"recast": 120, "fx": [("hit", 2.5)]},
    "Ancient Circle": {"recast": 300, "fx": [("buff", "defn", 0.08, 180)]},
    "Spirit Link":   {"recast": 90, "needs_pet": True, "fx": [("heal", 30)]},
    "Angon":         {"recast": 180, "fx": [("debuff", "def_down", 0.2, 90)]},
    "Spirit Surge":  {"recast": SP_RECAST, "sp": True, "needs_pet": True,
                      "fx": [("buff", "atk", 0.3, 60), ("buff", "haste", 0.25, 60), ("release",)]},
    # Summoner: blood pacts need an avatar out
    "Release":       {"recast": 5, "needs_pet": True, "fx": [("release",)]},
    "Healing Ruby":  {"recast": 60, "needs_pet": True, "fx": [("heal", 40)]},
    "Shining Ruby":  {"recast": 60, "needs_pet": True, "fx": [("buff", "defn", 0.12, 180)]},
    "Poison Nails":  {"recast": 60, "needs_pet": True, "fx": [("damage", 1.5, "light"), ("debuff", "dot", 0.04, 60)]},
    "Punch":         {"recast": 60, "needs_pet": True, "fx": [("damage", 2.0, "fire")]},
    "Burning Strike": {"recast": 60, "needs_pet": True, "fx": [("damage", 3.5, "fire")]},
    "Flaming Crush": {"recast": 60, "needs_pet": True, "fx": [("damage", 5.0, "fire")]},
    "Aerial Blast":  {"recast": 60, "needs_pet": True, "fx": [("damage", 5.0, "wind")]},
    "Diamond Dust":  {"recast": 60, "needs_pet": True, "fx": [("damage", 5.0, "ice")]},
    "Judgment Bolt": {"recast": 60, "needs_pet": True, "fx": [("damage", 5.0, "thunder")]},
    "Earthen Fury":  {"recast": 60, "needs_pet": True, "fx": [("damage", 5.0, "earth")]},
    "Tidal Wave":    {"recast": 60, "needs_pet": True, "fx": [("damage", 5.0, "water")]},
    "Astral Flow":   {"recast": SP_RECAST, "sp": True, "needs_pet": True, "fx": [("damage", 12.0, "light")]},
    # Blue Mage
    "Chain Affinity": {"recast": 120, "fx": [("next_ws", 0.25)]},
    "Burst Affinity": {"recast": 120, "fx": [("buff", "nuke_bonus", 0.3, 30)]},
    "Azure Lore":    {"recast": SP_RECAST, "sp": True, "fx": [("buff", "atk", 0.3, 30), ("buff", "nuke_bonus", 0.5, 30)]},
    # Corsair
    "Fighter's Roll":  {"recast": 60, "fx": [("roll", "atk", 0.02, 0.15, 300)]},
    "Chaos Roll":      {"recast": 60, "fx": [("roll", "atk", 0.03, 0.2, 300)]},
    "Samurai Roll":    {"recast": 60, "fx": [("roll", "store_tp", 0.03, 0.25, 300)]},
    "Wizard's Roll":   {"recast": 60, "fx": [("roll", "nuke_bonus", 0.03, 0.25, 300)]},
    "Evoker's Roll":   {"recast": 60, "fx": [("roll", "refresh", 1, 4, 300)]},
    "Healer's Roll":   {"recast": 60, "fx": [("roll", "heal_bonus", 0.03, 0.25, 300)]},
    "Gallant's Roll":  {"recast": 60, "fx": [("roll", "defn", 0.02, 0.15, 300)]},
    "Corsair's Roll":  {"recast": 60, "fx": [("roll", "exp_bonus", 0.05, 0.3, 300)]},
    "Double-Up":       {"recast": 5, "fx": [("double_up",)]},
    "Fire Shot":       {"recast": 1, "fx": [("damage", 2.0, "fire")]},
    "Ice Shot":        {"recast": 1, "fx": [("damage", 2.0, "ice")]},
    "Thunder Shot":    {"recast": 1, "fx": [("damage", 2.0, "thunder")]},
    "Light Shot":      {"recast": 1, "fx": [("debuff", "sleep", 1, 10)]},
    "Dark Shot":       {"recast": 1, "fx": [("debuff", "def_down", 0.1, 60)]},
    "Wild Card":       {"recast": SP_RECAST, "sp": True, "fx": [("random_ws",), ("mp_full",)]},
    # Puppetmaster: the automaton is a pet
    "Activate":      {"recast": 1200, "fx": [("pet", "Automaton", 0.35, 1800, 0)]},
    "Deploy":        {"recast": 10, "needs_pet": True, "fx": [("hit", 1.5)]},
    "Fire Maneuver": {"recast": 10, "needs_pet": True, "fx": [("buff", "atk", 0.05, 60)]},
    "Light Maneuver": {"recast": 10, "needs_pet": True, "fx": [("buff", "regen", 1, 60)]},
    "Repair":        {"recast": 90, "needs_pet": True, "fx": [("heal", 20)]},
    "Overdrive":     {"recast": SP_RECAST, "sp": True, "needs_pet": True, "fx": [("pet", "Automaton", 1.0, 60, 0)]},
    # Dancer: waltzes cost TP
    "Curing Waltz":     {"recast": 6, "tp": 200, "fx": [("heal", 25)]},
    "Curing Waltz II":  {"recast": 8, "tp": 350, "fx": [("heal", 45)]},
    "Curing Waltz III": {"recast": 10, "tp": 500, "fx": [("heal", 70)]},
    "Curing Waltz IV":  {"recast": 12, "tp": 650, "fx": [("heal", 100)]},
    "Drain Samba":      {"recast": 60, "tp": 100, "fx": [("buff", "drain_hit", 0.1, 120)]},
    "Aspir Samba":      {"recast": 60, "tp": 100, "fx": [("buff", "aspir_hit", 0.05, 120)]},
    "Haste Samba":      {"recast": 60, "tp": 350, "fx": [("buff", "haste", 0.1, 120)]},
    "Quickstep":        {"recast": 5, "tp": 100, "fx": [("debuff", "def_down", 0.1, 60)]},
    "Box Step":         {"recast": 5, "tp": 100, "fx": [("debuff", "def_down", 0.08, 60)]},
    "Violent Flourish": {"recast": 20, "fx": [("hit", 1.0), ("debuff", "sleep", 1, 3)]},
    "Climactic Flourish": {"recast": 90, "fx": [("next_ws", 0.4)]},
    "Trance":           {"recast": SP_RECAST, "sp": True, "fx": [("buff", "tp_cost_free", 1, 60)]},
    # Scholar
    "Light Arts":    {"recast": 60, "fx": [("buff", "heal_bonus", 0.1, 7200), ("buff", "fast_cast", 0.1, 7200)]},
    "Dark Arts":     {"recast": 60, "fx": [("buff", "nuke_bonus", 0.1, 7200), ("buff", "fast_cast", 0.1, 7200)]},
    "Sublimation":   {"recast": 30, "fx": [("buff", "refresh", 2, 120)]},
    "Tabula Rasa":   {"recast": SP_RECAST, "sp": True, "fx": [("buff", "fast_cast", 0.5, 180), ("buff", "conserve_mp", 0.5, 180)]},
    # Geomancer
    "Full Circle":   {"recast": 10, "fx": [("mp", 30)]},
    "Bolster":       {"recast": SP_RECAST, "sp": True, "fx": [("buff", "atk", 0.15, 180), ("buff", "defn", 0.15, 180)]},
    # Rune Fencer
    "Vallation":     {"recast": 180, "fx": [("buff", "defn", 0.15, 120)]},
    "Swordplay":     {"recast": 300, "fx": [("buff", "store_tp", 0.1, 120)]},
    "Lunge":         {"recast": 180, "fx": [("damage", 3.0, "fire")]},
    "Valiance":      {"recast": 300, "fx": [("buff", "defn", 0.2, 180)]},
    "Elemental Sforzo": {"recast": SP_RECAST, "sp": True, "fx": [("buff", "shadows", 99, 30)]},
}


# ================================================================================================
# Jobs
# ================================================================================================
# mp: max MP at level 75, FFXI's scale (spells cost FFXI's MP). tp_mult, ws_mult, nuke_mult, heal_mult: job multipliers.
# spells / abilities: (level, name). traits: (level, effect, power), permanent while it is your job
# (or subjob, at its level). sp: the SP ability, learnt at level 1 as in FFXI.
JOBS = {
    "WAR": {"name": "Warrior", "mp": 0, "tp_mult": 1.15, "ws_mult": 1.2, "nuke_mult": 0.5, "heal_mult": 0.5,
            "spells": [],
            "abilities": [(1, "Mighty Strikes"), (15, "Berserk"), (25, "Defender"), (35, "Warcry"), (45, "Aggressor")],
            "traits": [(10, "defn", 0.03), (25, "atk", 0.05), (30, "store_tp", 0.05), (50, "atk", 0.05)]},
    "MNK": {"name": "Monk", "mp": 0, "tp_mult": 1.2, "ws_mult": 1.15, "nuke_mult": 0.5, "heal_mult": 0.5,
            "spells": [],
            "abilities": [(1, "Hundred Fists"), (5, "Boost"), (15, "Dodge"), (25, "Focus"), (35, "Chakra"),
                       (41, "Chi Blast"), (45, "Counterstance")],
            "traits": [(1, "haste", 0.1), (10, "regen", 1), (25, "atk", 0.05), (50, "store_tp", 0.05)]},
    "WHM": {"name": "White Mage", "mp": 800, "tp_mult": 0.9, "ws_mult": 0.85, "nuke_mult": 0.8, "heal_mult": 1.25,
            "spells": [(1, "Cure"), (3, "Dia"), (4, "Paralyze"), (5, "Banish"), (7, "Protect"), (10, "Aquaveil"),
                       (11, "Cure II"), (13, "Slow"), (17, "Shell"), (19, "Blink"), (21, "Cure III"), (21, "Regen"),
                       (27, "Protect II"), (28, "Stoneskin"), (30, "Banish II"), (36, "Dia II"), (37, "Shell II"),
                       (40, "Haste"), (41, "Cure IV"), (44, "Regen II"), (45, "Flash"), (47, "Protect III"),
                       (48, "Repose"), (50, "Holy"), (55, "Auspice"), (61, "Cure V"), (63, "Protect IV"),
                       (66, "Regen III")],
            "abilities": [(1, "Benediction"), (15, "Divine Seal"), (40, "Afflatus Solace")],
            "traits": [(25, "refresh", 1), (35, "heal_bonus", 0.1), (50, "conserve_mp", 0.1)]},
    "BLM": {"name": "Black Mage", "mp": 900, "tp_mult": 0.9, "ws_mult": 0.8, "nuke_mult": 1.35, "heal_mult": 0.7,
            "spells": [(1, "Stone"), (3, "Poison"), (4, "Blind"), (5, "Water"), (7, "Bind"), (9, "Aero"), (10, "Bio"),
                       (12, "Drain"), (13, "Fire"), (16, "Shock"), (17, "Blizzard"), (20, "Sleep"), (21, "Thunder"),
                       (22, "Frost"), (24, "Burn"), (25, "Aspir"), (26, "Stone II"), (27, "Drown"), (30, "Water II"),
                       (34, "Aero II"), (35, "Bio II"), (38, "Fire II"), (41, "Sleep II"), (42, "Blizzard II"),
                       (43, "Poison II"), (45, "Stun"), (46, "Thunder II"), (50, "Freeze"), (51, "Stone III"),
                       (52, "Tornado"), (54, "Quake"), (55, "Water III"), (56, "Burst"), (58, "Flood"),
                       (59, "Aero III"), (60, "Flare"), (62, "Fire III"), (64, "Blizzard III"), (66, "Thunder III"),
                       (73, "Fire IV"), (74, "Blizzard IV"), (75, "Thunder IV")],
            "abilities": [(1, "Manafont"), (15, "Elemental Seal")],
            "traits": [(10, "conserve_mp", 0.1), (15, "nuke_bonus", 0.1), (30, "nuke_bonus", 0.1), (35, "refresh", 1)]},
    "RDM": {"name": "Red Mage", "mp": 700, "tp_mult": 1.0, "ws_mult": 1.0, "nuke_mult": 1.0, "heal_mult": 1.0,
            "spells": [(1, "Dia"), (3, "Cure"), (4, "Stone"), (5, "Poison"), (6, "Paralyze"), (7, "Protect"),
                       (8, "Blind"), (9, "Water"), (10, "Bio"), (11, "Bind"), (12, "Aquaveil"), (13, "Slow"),
                       (14, "Aero"), (14, "Cure II"), (16, "Enthunder"), (17, "Shell"), (19, "Fire"), (21, "Gravity"),
                       (21, "Regen"), (22, "Enblizzard"), (23, "Blink"), (24, "Blizzard"), (24, "Enfire"),
                       (25, "Sleep"), (26, "Cure III"), (27, "Protect II"), (29, "Thunder"), (31, "Dia II"),
                       (33, "Phalanx"), (34, "Stoneskin"), (35, "Stone II"), (36, "Bio II"), (37, "Shell II"),
                       (40, "Water II"), (41, "Refresh"), (45, "Aero II"), (46, "Poison II"), (46, "Sleep II"),
                       (47, "Protect III"), (48, "Cure IV"), (48, "Haste"), (50, "Fire II"), (55, "Blizzard II"),
                       (60, "Thunder II"), (63, "Protect IV"), (65, "Stone III"), (67, "Water III"), (69, "Aero III"),
                       (71, "Fire III"), (73, "Blizzard III"), (75, "Thunder III"), (76, "Regen II"), (86, "Fire IV"),
                       (89, "Blizzard IV"), (92, "Thunder IV")],
            "abilities": [(1, "Chainspell"), (40, "Convert"), (50, "Composure")],
            "traits": [(20, "fast_cast", 0.1), (35, "fast_cast", 0.1), (41, "refresh", 1), (55, "fast_cast", 0.05)]},
    "THF": {"name": "Thief", "mp": 0, "tp_mult": 1.15, "ws_mult": 1.1, "nuke_mult": 0.5, "heal_mult": 0.5,
            "spells": [],
            "abilities": [(1, "Perfect Dodge"), (15, "Sneak Attack"), (30, "Trick Attack"), (35, "Mug")],
            "traits": [(10, "haste", 0.05), (25, "store_tp", 0.05), (55, "atk", 0.1)]},
    "PLD": {"name": "Paladin", "mp": 400, "tp_mult": 1.0, "ws_mult": 0.95, "nuke_mult": 0.8, "heal_mult": 1.0,
            "spells": [(5, "Cure"), (7, "Banish"), (10, "Protect"), (17, "Cure II"), (20, "Shell"), (30, "Cure III"),
                       (30, "Protect II"), (34, "Banish II"), (37, "Flash"), (40, "Shell II"), (50, "Protect III"),
                       (55, "Cure IV"), (55, "Holy"), (61, "Reprisal"), (70, "Protect IV"), (77, "Phalanx")],
            "abilities": [(1, "Invincible"), (5, "Holy Circle"), (15, "Shield Bash"), (30, "Sentinel"), (62, "Rampart")],
            "traits": [(10, "defn", 0.05), (25, "defn", 0.05), (35, "regen", 1), (50, "defn", 0.05)]},
    "DRK": {"name": "Dark Knight", "mp": 350, "tp_mult": 1.0, "ws_mult": 1.25, "nuke_mult": 0.9, "heal_mult": 0.5,
            "spells": [(5, "Stone"), (6, "Poison"), (10, "Drain"), (11, "Water"), (15, "Bio"), (17, "Aero"),
                       (20, "Aspir"), (20, "Bind"), (23, "Fire"), (29, "Blizzard"), (30, "Sleep"), (35, "Thunder"),
                       (37, "Stun"), (40, "Bio II"), (42, "Stone II"), (45, "Absorb-TP"), (46, "Poison II"),
                       (48, "Water II"), (54, "Aero II"), (56, "Sleep II"), (60, "Fire II"), (62, "Drain II"),
                       (66, "Blizzard II"), (71, "Dread Spikes"), (72, "Thunder II"), (76, "Stone III"),
                       (80, "Water III"), (84, "Aero III"), (88, "Fire III"), (92, "Blizzard III"), (96, "Thunder III")],
            "abilities": [(1, "Blood Weapon"), (5, "Arcane Circle"), (15, "Last Resort"), (20, "Weapon Bash"),
                       (30, "Souleater")],
            "traits": [(10, "atk", 0.05), (30, "atk", 0.05), (50, "atk", 0.05)]},
    "BST": {"name": "Beastmaster", "mp": 0, "tp_mult": 1.0, "ws_mult": 1.05, "nuke_mult": 0.5, "heal_mult": 0.5,
            "spells": [],
            "abilities": [(1, "Familiar"), (12, "Reward"), (23, "Call Beast"), (25, "Sic")],
            "traits": [(10, "defn", 0.03), (30, "atk", 0.05)]},
    "BRD": {"name": "Bard", "mp": 500, "tp_mult": 0.95, "ws_mult": 0.9, "nuke_mult": 0.8, "heal_mult": 0.8,
            "spells": [(1, "Knight's Minne"), (3, "Valor Minuet"), (5, "Army's Paeon"), (7, "Foe Requiem"),
                       (11, "Sword Madrigal"), (15, "Army's Paeon II"), (16, "Foe Lullaby"), (17, "Foe Requiem II"),
                       (21, "Knight's Minne II"), (23, "Valor Minuet II"), (25, "Mage's Ballad"),
                       (29, "Advancing March"), (35, "Army's Paeon III"), (39, "Battlefield Elegy"),
                       (43, "Valor Minuet III"), (51, "Blade Madrigal"), (55, "Mage's Ballad II"),
                       (59, "Carnage Elegy"), (60, "Victory March"), (63, "Valor Minuet IV")],
            "abilities": [(1, "Soul Voice")],
            "traits": [(25, "refresh", 1)]},
    "RNG": {"name": "Ranger", "mp": 0, "tp_mult": 1.05, "ws_mult": 1.2, "nuke_mult": 0.5, "heal_mult": 0.5,
            "spells": [],
            "abilities": [(1, "Eagle Eye Shot"), (1, "Sharpshot"), (30, "Barrage"), (40, "Shadowbind"),
                       (45, "Velocity Shot")],
            "traits": [(10, "atk", 0.05), (30, "atk", 0.05), (50, "store_tp", 0.05)]},
    "SAM": {"name": "Samurai", "mp": 0, "tp_mult": 1.1, "ws_mult": 1.2, "nuke_mult": 0.5, "heal_mult": 0.5,
            "spells": [],
            "abilities": [(1, "Meikyo Shisui"), (15, "Third Eye"), (25, "Hasso"), (30, "Meditate"), (35, "Seigan"),
                       (40, "Sekkanoki")],
            "traits": [(10, "store_tp", 0.1), (30, "store_tp", 0.05), (50, "store_tp", 0.05), (70, "store_tp", 0.05)]},
    "NIN": {"name": "Ninja", "mp": 0, "tp_mult": 1.15, "ws_mult": 1.1, "nuke_mult": 0.9, "heal_mult": 0.5,
            "spells": [(12, "Utsusemi: Ichi"), (15, "Doton: Ichi"), (15, "Huton: Ichi"), (15, "Hyoton: Ichi"),
                       (15, "Katon: Ichi"), (15, "Raiton: Ichi"), (15, "Suiton: Ichi"), (19, "Kurayami: Ichi"),
                       (23, "Hojo: Ichi"), (27, "Dokumori: Ichi"), (30, "Jubaku: Ichi"), (37, "Utsusemi: Ni"),
                       (40, "Doton: Ni"), (40, "Huton: Ni"), (40, "Hyoton: Ni"), (40, "Katon: Ni"), (40, "Raiton: Ni"),
                       (40, "Suiton: Ni")],
            "abilities": [(1, "Mijin Gakure"), (40, "Innin"), (40, "Yonin")],
            "traits": [(10, "haste", 0.1), (25, "haste", 0.05), (45, "haste", 0.05)]},
    "DRG": {"name": "Dragoon", "mp": 0, "tp_mult": 1.1, "ws_mult": 1.2, "nuke_mult": 0.5, "heal_mult": 0.5,
            "spells": [],
            "abilities": [(1, "Call Wyvern"), (1, "Spirit Surge"), (5, "Ancient Circle"), (10, "Jump"),
                       (25, "Spirit Link"), (35, "High Jump"), (75, "Angon")],
            "traits": [(10, "atk", 0.05), (30, "atk", 0.05), (50, "atk", 0.05)]},
    "SMN": {"name": "Summoner", "mp": 950, "tp_mult": 0.85, "ws_mult": 0.75, "nuke_mult": 1.1, "heal_mult": 0.9,
            "spells": [(1, "Carbuncle"), (1, "Garuda"), (1, "Ifrit"), (1, "Leviathan"), (1, "Ramuh"), (1, "Shiva"),
                       (1, "Titan")],
            "abilities": [(1, "Aerial Blast"), (1, "Astral Flow"), (1, "Diamond Dust"), (1, "Earthen Fury"),
                       (1, "Healing Ruby"), (1, "Judgment Bolt"), (1, "Punch"), (1, "Release"), (1, "Tidal Wave"),
                       (5, "Poison Nails"), (23, "Burning Strike"), (24, "Shining Ruby"), (70, "Flaming Crush")],
            "traits": [(1, "refresh", 1), (25, "refresh", 1), (50, "conserve_mp", 0.1)]},
    "BLU": {"name": "Blue Mage", "mp": 600, "tp_mult": 1.0, "ws_mult": 1.05, "nuke_mult": 1.0, "heal_mult": 0.9,
            "spells": [(1, "Foot Kick"), (1, "Pollen"), (4, "Power Attack"), (4, "Sprout Smack"), (8, "Cocoon"),
                       (8, "Metallic Body"), (12, "Head Butt"), (16, "Sheep Song"), (18, "Bludgeon"),
                       (30, "Wild Carrot"), (38, "Blank Gaze"), (48, "Refueling"), (50, "Ice Break"),
                       (56, "Feather Barrier"), (58, "Magic Fruit"), (60, "Death Scissors"), (61, "Maelstrom"),
                       (63, "Frenetic Rip"), (67, "Diamondhide"), (68, "Firespit"), (75, "Vertical Cleave")],
            "abilities": [(1, "Azure Lore"), (25, "Burst Affinity"), (40, "Chain Affinity")],
            "traits": [(10, "atk", 0.05), (30, "refresh", 1), (50, "store_tp", 0.05)]},
    "COR": {"name": "Corsair", "mp": 0, "tp_mult": 1.05, "ws_mult": 1.1, "nuke_mult": 0.8, "heal_mult": 0.5,
            "spells": [],
            "abilities": [(1, "Wild Card"), (5, "Corsair's Roll"), (5, "Double-Up"), (14, "Chaos Roll"),
                       (20, "Healer's Roll"), (34, "Samurai Roll"), (40, "Dark Shot"), (40, "Evoker's Roll"),
                       (40, "Fire Shot"), (40, "Ice Shot"), (40, "Light Shot"), (40, "Thunder Shot"),
                       (49, "Fighter's Roll"), (55, "Gallant's Roll"), (58, "Wizard's Roll")],
            "traits": [(10, "atk", 0.05), (40, "store_tp", 0.05)]},
    "PUP": {"name": "Puppetmaster", "mp": 0, "tp_mult": 1.1, "ws_mult": 1.1, "nuke_mult": 0.5, "heal_mult": 0.5,
            "spells": [],
            "abilities": [(1, "Activate"), (1, "Deploy"), (1, "Fire Maneuver"), (1, "Light Maneuver"), (1, "Overdrive"),
                       (15, "Repair")],
            "traits": [(1, "haste", 0.1), (25, "store_tp", 0.05)]},
    "DNC": {"name": "Dancer", "mp": 0, "tp_mult": 1.15, "ws_mult": 1.1, "nuke_mult": 0.5, "heal_mult": 0.8,
            "spells": [],
            "abilities": [(1, "Trance"), (5, "Drain Samba"), (15, "Curing Waltz"), (20, "Quickstep"), (25, "Aspir Samba"),
                       (30, "Box Step"), (30, "Curing Waltz II"), (45, "Curing Waltz III"), (45, "Haste Samba"),
                       (45, "Violent Flourish"), (70, "Curing Waltz IV"), (80, "Climactic Flourish")],
            "traits": [(10, "haste", 0.05), (25, "store_tp", 0.05), (45, "atk", 0.05)]},
    "SCH": {"name": "Scholar", "mp": 750, "tp_mult": 0.9, "ws_mult": 0.85, "nuke_mult": 1.15, "heal_mult": 1.1,
            "spells": [(4, "Stone"), (5, "Cure"), (8, "Water"), (10, "Protect"), (12, "Aero"), (13, "Aquaveil"),
                       (16, "Fire"), (17, "Cure II"), (18, "Regen"), (20, "Blizzard"), (20, "Shell"), (21, "Drain"),
                       (24, "Pyrohelix"), (24, "Thunder"), (26, "Cryohelix"), (28, "Ionohelix"), (29, "Blink"),
                       (30, "Cure III"), (30, "Noctohelix"), (30, "Protect II"), (30, "Sleep"), (30, "Stone II"),
                       (32, "Luminohelix"), (34, "Water II"), (36, "Aspir"), (37, "Regen II"), (38, "Aero II"),
                       (40, "Shell II"), (42, "Fire II"), (44, "Stoneskin"), (46, "Blizzard II"), (50, "Protect III"),
                       (51, "Thunder II"), (54, "Stone III"), (55, "Cure IV"), (57, "Water III"), (59, "Regen III"),
                       (60, "Aero III"), (63, "Fire III"), (65, "Sleep II"), (66, "Blizzard III"), (66, "Protect IV"),
                       (69, "Thunder III"), (73, "Fire IV"), (74, "Blizzard IV"), (75, "Thunder IV")],
            "abilities": [(1, "Tabula Rasa"), (10, "Dark Arts"), (10, "Light Arts"), (35, "Sublimation")],
            "traits": [(20, "conserve_mp", 0.1), (30, "refresh", 1), (50, "fast_cast", 0.1)]},
    "GEO": {"name": "Geomancer", "mp": 750, "tp_mult": 0.9, "ws_mult": 0.85, "nuke_mult": 1.15, "heal_mult": 0.9,
            "spells": [(1, "Indi-Poison"), (4, "Stone"), (9, "Water"), (14, "Aero"), (15, "Drain"), (15, "Indi-Regen"),
                       (19, "Fire"), (24, "Blizzard"), (28, "Indi-Barrier"), (29, "Thunder"), (30, "Aspir"),
                       (30, "Indi-Refresh"), (34, "Indi-Fury"), (34, "Stone II"), (35, "Sleep"), (38, "Water II"),
                       (42, "Aero II"), (46, "Fire II"), (46, "Indi-Acumen"), (48, "Indi-Slow"), (50, "Blizzard II"),
                       (54, "Thunder II"), (58, "Stone III"), (61, "Water III"), (64, "Aero III"), (67, "Fire III"),
                       (70, "Blizzard III"), (70, "Sleep II"), (73, "Thunder III"), (76, "Indi-Frailty"),
                       (85, "Fire IV"), (88, "Blizzard IV"), (91, "Thunder IV"), (93, "Indi-Haste")],
            "abilities": [(1, "Bolster"), (5, "Full Circle")],
            "traits": [(20, "conserve_mp", 0.1), (40, "nuke_bonus", 0.1)]},
    "RUN": {"name": "Rune Fencer", "mp": 350, "tp_mult": 1.05, "ws_mult": 1.1, "nuke_mult": 0.8, "heal_mult": 0.8,
            "spells": [(10, "Shell"), (15, "Aquaveil"), (20, "Protect"), (23, "Regen"), (30, "Shell II"), (35, "Blink"),
                       (40, "Protect II"), (45, "Flash"), (48, "Regen II"), (55, "Stoneskin"), (60, "Protect III"),
                       (62, "Refresh"), (68, "Phalanx"), (70, "Regen III"), (80, "Protect IV")],
            "abilities": [(1, "Elemental Sforzo"), (10, "Vallation"), (20, "Swordplay"), (25, "Lunge"), (50, "Valiance")],
            "traits": [(10, "defn", 0.05), (30, "defn", 0.05), (50, "store_tp", 0.05)]},
}


def mp_at(job: str, level: int) -> int:
    """A job's own max MP at a level: a fifth of its level-75 MP at level 1."""
    base = JOBS[job]["mp"]
    return round(base * (0.2 + 0.8 * min(level, LEVEL_CAP) / LEVEL_CAP)) if base else 0


def level_factor(level: int) -> float:
    """Damage and power with level: half at level 1, whole at 75."""
    return 0.5 + 0.5 * min(level, LEVEL_CAP) / LEVEL_CAP
