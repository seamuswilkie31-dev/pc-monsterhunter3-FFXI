"""FFXI rules used by the companion script: jobs, weapon skills, spells, skillchains.

Everything here is plain data, so edit freely. Numbers follow FFXI where it makes sense
(TP per hit from weapon delay, 1000/2000/3000 TP tiers, skillchain table, magic-burst elements),
but damage is scaled to Monster Hunter: weapon skills and nukes multiply your *own recent average
hit*, so they stay balanced whichever MH weapon or monster you're fighting.

Skillchain properties are from memory of the FFXI wiki (bg-wiki.com) - double-check any you care about.
"""

# --- TP -----------------------------------------------------------------------------------------
TP_MAX = 3000
TP_WS_MIN = 1000


def tp_per_hit(delay: int) -> int:
    """FFXI's post-2014 TP-per-hit formula from weapon delay."""
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


TP_WHEN_HIT = 40  # TP gained when a monster hits you


# --- Weapon skills ------------------------------------------------------------------------------
# ftp = damage multiplier at 1000 / 2000 / 3000 TP (interpolated in between).
# props = skillchain properties, in FFXI's order.
WEAPON_SKILLS = {
    # Great Sword
    "Hard Slash":      {"props": ["Scission"], "ftp": (1.5, 1.75, 2.0)},
    "Freezebite":      {"props": ["Induration", "Detonation"], "ftp": (1.5, 2.5, 3.5)},
    "Ground Strike":   {"props": ["Fragmentation", "Distortion"], "ftp": (1.5, 1.75, 3.0)},
    "Resolution":      {"props": ["Fragmentation", "Scission"], "ftp": (1.0, 1.5, 2.0), "hits": 5},
    # Long Sword (FFXI Great Katana)
    "Tachi: Enpi":     {"props": ["Transfixion", "Scission"], "ftp": (1.0, 1.5, 2.0), "hits": 2},
    "Tachi: Jinpu":    {"props": ["Scission", "Detonation"], "ftp": (0.5, 0.75, 1.0), "hits": 2},
    "Tachi: Kasha":    {"props": ["Fusion", "Compression"], "ftp": (1.56, 2.12, 2.5)},
    "Tachi: Fudo":     {"props": ["Light", "Distortion"], "ftp": (3.75, 5.75, 8.0)},
    # Sword and Shield (FFXI Sword)
    "Fast Blade":      {"props": ["Scission"], "ftp": (1.0, 1.5, 2.0), "hits": 2},
    "Burning Blade":   {"props": ["Liquefaction"], "ftp": (1.0, 2.1, 3.4)},
    "Red Lotus Blade": {"props": ["Liquefaction", "Detonation"], "ftp": (1.0, 2.38, 3.75)},
    "Savage Blade":    {"props": ["Fragmentation", "Scission"], "ftp": (4.0, 10.25, 13.75)},
    # Hammer (FFXI Club)
    "Brainshaker":     {"props": ["Reverberation"], "ftp": (1.0, 1.0, 1.0)},
    "Skullbreaker":    {"props": ["Induration", "Reverberation"], "ftp": (1.0, 1.0, 1.0)},
    "Judgment":        {"props": ["Impaction"], "ftp": (3.5, 8.75, 12.0)},
    "Black Halo":      {"props": ["Fragmentation", "Compression"], "ftp": (3.0, 7.25, 9.75)},
    # Lance (FFXI Polearm)
    "Double Thrust":   {"props": ["Transfixion"], "ftp": (1.0, 1.5, 2.0), "hits": 2},
    "Penta Thrust":    {"props": ["Compression"], "ftp": (1.0, 1.0, 1.0), "hits": 5},
    "Impulse Drive":   {"props": ["Gravitation", "Induration"], "ftp": (1.0, 3.0, 5.5), "hits": 2},
    "Stardiver":       {"props": ["Gravitation", "Transfixion"], "ftp": (0.75, 1.25, 1.75), "hits": 4},
    # Switch Axe (FFXI Great Axe)
    "Shield Break":    {"props": ["Impaction"], "ftp": (1.0, 1.0, 1.0)},
    "Iron Tempest":    {"props": ["Scission"], "ftp": (1.0, 1.5, 2.0)},
    "Raging Rush":     {"props": ["Induration", "Reverberation"], "ftp": (1.0, 1.0, 1.0), "hits": 3},
    "Upheaval":        {"props": ["Fusion", "Compression"], "ftp": (1.0, 3.5, 6.5), "hits": 4},
    # Bowguns (FFXI Marksmanship)
    "Hot Shot":        {"props": ["Liquefaction", "Transfixion"], "ftp": (0.5, 1.55, 2.1)},
    "Split Shot":      {"props": ["Reverberation", "Transfixion"], "ftp": (1.0, 1.0, 1.0)},
    "Slug Shot":       {"props": ["Reverberation", "Transfixion"], "ftp": (4.0, 4.5, 5.0)},
    "Last Stand":      {"props": ["Fusion", "Reverberation"], "ftp": (2.0, 2.25, 2.5), "hits": 2},
}

# MH Tri weapon -> FFXI delay (for TP per hit) and its four weapon-skill slots.
WEAPONS = {
    "great_sword":     {"delay": 480, "ws": ["Hard Slash", "Freezebite", "Ground Strike", "Resolution"]},
    "long_sword":      {"delay": 420, "ws": ["Tachi: Enpi", "Tachi: Jinpu", "Tachi: Kasha", "Tachi: Fudo"]},
    "sword_and_shield": {"delay": 220, "ws": ["Fast Blade", "Burning Blade", "Red Lotus Blade", "Savage Blade"]},
    "hammer":          {"delay": 400, "ws": ["Brainshaker", "Skullbreaker", "Judgment", "Black Halo"]},
    "lance":           {"delay": 360, "ws": ["Double Thrust", "Penta Thrust", "Impulse Drive", "Stardiver"]},
    "switch_axe":      {"delay": 450, "ws": ["Shield Break", "Iron Tempest", "Raging Rush", "Upheaval"]},
    "light_bowgun":    {"delay": 240, "ws": ["Hot Shot", "Split Shot", "Slug Shot", "Last Stand"]},
    "heavy_bowgun":    {"delay": 400, "ws": ["Hot Shot", "Split Shot", "Slug Shot", "Last Stand"]},
}


# --- Skillchains --------------------------------------------------------------------------------
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

# Skillchain bonus = this fraction of the closing weapon skill's damage.
SC_BONUS = {1: 0.5, 2: 0.6, 3: 1.0}

# Elements a magic burst can use on each skillchain.
SC_ELEMENTS = {
    "Liquefaction": {"fire"}, "Scission": {"earth"}, "Reverberation": {"water"},
    "Detonation": {"wind"}, "Induration": {"ice"}, "Impaction": {"thunder"},
    "Transfixion": {"light"}, "Compression": {"dark"},
    "Fusion": {"fire", "light"}, "Fragmentation": {"wind", "thunder"},
    "Gravitation": {"earth", "dark"}, "Distortion": {"water", "ice"},
    "Light": {"fire", "wind", "thunder", "light"},
    "Darkness": {"earth", "water", "ice", "dark"},
}

SC_WINDOW_OPEN = 2.0    # seconds after a WS before the next one can close a chain
SC_WINDOW_CLOSE = 8.0   # seconds after a WS when the chain window shuts
MB_WINDOW = 8.0         # seconds after a skillchain to land a magic burst
MB_BONUS = 1.35


# --- Spells -------------------------------------------------------------------------------------
# kind: heal (power = HP healed), nuke (power = x average hit), regen (HP per tick for duration s),
#       protect (refund `power` fraction of damage taken for duration s), drain (nuke that heals you)
SPELLS = {
    "Cure":       {"kind": "heal", "mp": 8, "cast": 2.0, "power": 25},
    "Cure II":    {"kind": "heal", "mp": 24, "cast": 2.25, "power": 50},
    "Cure III":   {"kind": "heal", "mp": 46, "cast": 2.5, "power": 90},
    "Regen":      {"kind": "regen", "mp": 15, "cast": 2.0, "power": 2, "duration": 60, "tick": 3},
    "Protect":    {"kind": "protect", "mp": 9, "cast": 2.0, "power": 0.2, "duration": 180},
    "Banish":     {"kind": "nuke", "mp": 15, "cast": 2.5, "power": 1.5, "element": "light"},
    "Fire":       {"kind": "nuke", "mp": 7, "cast": 2.0, "power": 1.5, "element": "fire"},
    "Fire II":    {"kind": "nuke", "mp": 26, "cast": 3.0, "power": 3.0, "element": "fire"},
    "Blizzard":   {"kind": "nuke", "mp": 8, "cast": 2.0, "power": 1.6, "element": "ice"},
    "Blizzard II": {"kind": "nuke", "mp": 31, "cast": 3.0, "power": 3.2, "element": "ice"},
    "Aero II":    {"kind": "nuke", "mp": 22, "cast": 3.0, "power": 2.8, "element": "wind"},
    "Stone II":   {"kind": "nuke", "mp": 16, "cast": 3.0, "power": 2.6, "element": "earth"},
    "Thunder":    {"kind": "nuke", "mp": 9, "cast": 2.0, "power": 1.7, "element": "thunder"},
    "Thunder II": {"kind": "nuke", "mp": 37, "cast": 3.0, "power": 3.4, "element": "thunder"},
    "Water II":   {"kind": "nuke", "mp": 19, "cast": 3.0, "power": 2.7, "element": "water"},
    "Drain":      {"kind": "drain", "mp": 21, "cast": 3.0, "power": 1.5, "element": "dark"},
}


# --- Jobs ---------------------------------------------------------------------------------------
# tp_mult: TP gain multiplier. ws_mult / nuke_mult: damage multipliers. mp_regen: MP per 3 s.
JOBS = {
    "WAR": {"name": "Warrior", "mp": 0, "mp_regen": 0, "tp_mult": 1.15, "ws_mult": 1.2, "nuke_mult": 0.0,
            "spells": []},
    "DRK": {"name": "Dark Knight", "mp": 60, "mp_regen": 1, "tp_mult": 1.0, "ws_mult": 1.25, "nuke_mult": 0.9,
            "spells": ["Drain", "Stone II", "Blizzard", "Thunder"]},
    "PLD": {"name": "Paladin", "mp": 80, "mp_regen": 1, "tp_mult": 1.0, "ws_mult": 0.95, "nuke_mult": 0.8,
            "spells": ["Cure", "Cure II", "Cure III", "Protect", "Banish"]},
    "RDM": {"name": "Red Mage", "mp": 110, "mp_regen": 2, "tp_mult": 1.0, "ws_mult": 1.0, "nuke_mult": 1.0,
            "spells": ["Cure", "Cure II", "Regen", "Protect", "Fire", "Blizzard", "Thunder", "Stone II"]},
    "WHM": {"name": "White Mage", "mp": 140, "mp_regen": 2, "tp_mult": 0.9, "ws_mult": 0.85, "nuke_mult": 0.8,
            "spells": ["Cure", "Cure II", "Cure III", "Regen", "Protect", "Banish"]},
    "BLM": {"name": "Black Mage", "mp": 160, "mp_regen": 3, "tp_mult": 0.9, "ws_mult": 0.8, "nuke_mult": 1.35,
            "spells": ["Fire II", "Blizzard II", "Aero II", "Stone II", "Thunder II", "Water II", "Drain", "Fire"]},
}
