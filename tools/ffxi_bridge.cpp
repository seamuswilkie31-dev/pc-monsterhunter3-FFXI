// Monster Hunter Tri — the bridge to the FFXI layer (ffxi/ffxi_mh3.py).
//
// Off unless MH3_FFXI=1. The layer runs as its own process, as it did beside
// Dolphin; the port serves it guest memory through a named shared block
// ("mh3-ffxi", Windows only). The layer says what to watch: up to 8 values,
// each an address and an optional pointer chain (Dolphin Memory Engine's
// follow_pointers: for each offset, read the word there and add the offset).
// A host thread resolves them 60 times a second, publishes each value, and
// applies the layer's writes.
//
// The game's objects move between quests; three hooks (tools/mh3-hooks.txt)
// say where they are: the monster whose HP the game last changed
// (mh3_EmHpAdd), and the local player's hunter (Pl_master_ck, mh3_PlHpAdd).
// A watch may start from either instead of a fixed address.
//
// Not synchronised with the game: a read may see a value mid-frame, and a
// write may be overwritten by the game's own read-modify-write, as with
// Dolphin Memory Engine.
#include "rt.h"
#include "video.h"

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <atomic>
#include <cmath>
#include <cstdlib>
#include <cctype>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>
#include <algorithm>
#include <unordered_map>

// the recompiled RSO modules' table (the recompiler's output; wiikit's rso.cpp fills g_rso_base)
extern const RsoModule g_rso_modules[];
extern const size_t g_rso_nmodules;
extern uint32_t g_rso_base[];

namespace {

constexpr uint32_t kMagic = 0x4D483346;            // 'MH3F'
constexpr uint32_t kVersion = 10;
constexpr int kWatches = 8, kOffsets = 4;
constexpr uint32_t kMem1 = 0x80000000u, kMem1Size = 0x01800000u;
constexpr uint32_t kMem2 = 0x90000000u, kMem2Size = 0x04000000u;

// The block's layout; ffxi/port_bridge.py mirrors it field by field.
// Host byte order; values are given as the guest holds them, made host-endian.
struct Watch {              // written by the layer
    uint32_t addr;          // 0: unused
    uint32_t size;          // 1, 2 or 4 bytes
    uint32_t n_offsets;
    uint32_t offsets[kOffsets];
    uint32_t write_seq;     // the layer bumps it after setting write_value
    uint32_t write_value;
    uint32_t base;          // kBaseAddr: addr is an address; else the object's + addr
};
enum : uint32_t { kBaseAddr = 0, kBasePlayer = 1, kBaseMonster = 2 };
struct Result {             // written by the port
    uint32_t valid;         // 1: resolved to RAM this tick
    uint32_t resolved;      // the final guest address
    uint32_t value;
    uint32_t write_ack;     // the last write_seq applied (or refused)
};
struct Block {
    uint32_t magic, version;
    uint32_t heartbeat;     // the port bumps it every tick
    uint32_t pad;
    Watch watch[kWatches];
    Result result[kWatches];
    // A snapshot of RAM, for finding addresses (ffxi/memscan.py): the layer
    // sets snap_path and bumps snap_seq; the port writes MEM1 then MEM2 there,
    // as the guest holds them, and sets snap_ack (snap_ok 0 if it failed).
    uint32_t snap_seq, snap_ack, snap_ok;
    char snap_path[260];    // UTF-8, NUL-terminated
    // From the hooks: the objects, 0 until the game has touched one.
    uint32_t player_obj, monster_obj;
    uint32_t monster_hits;  // the game's changes to a monster's HP, counted
    int32_t monster_delta;  // the last one (negative: damage)
    // The FFXI command menu (classic_filter): while the layer runs, the Classic's Y (the pad's
    // left face button) held opens it, tapped closes it (a tap with it closed is the game's); while open, the d-pad and the face buttons are its,
    // each press an event for the layer, and the game doesn't see them.
    uint32_t menu_open;                 // port
    uint32_t menu_close_seq, menu_close_ack;   // the layer closes it: bumps seq; the port acks
    uint32_t item_seq, item_ack;        // the layer asks for one Y press to reach the game (its item)
    uint32_t ev_head;                   // port: events written so far
    uint32_t engage;                    // layer: FFXI's Attack: 1 the weapon out, 2 the camera locked on the target
    uint32_t reserved;
    uint32_t events[32];                // port: the buttons newly pressed, a ring
    // Your nameplate, FFXI's name above your head: the hunter's head projected through the game's
    // 3D camera, in the picture's normalised coordinates (-1..1, y up).
    float name_x, name_y;               // port
    uint32_t name_ok;                   // port: 1 when the head is in front of the camera
    float name_height;                  // layer: the head's height above the hunter's feet (world units)
    uint32_t camera;                    // port: the 3D camera's CameraData, 0 until the game sets one
    uint32_t reserved2;
    // FFXI's target (target_frame): the monster nearest the hunter, or the one the camera is locked on
    // (the Classic's L held: lock_filter), its arrow's place above its head through the camera.
    float target_x, target_y;           // port: -1..1, y up
    uint32_t target_ok;                 // port: 1 when there's a target in front of the camera
    uint32_t target_locked;             // port: 1 while the camera is locked on it
    uint32_t target_model;              // port: its scene model (0: none)
    char target_name[16];               // port: its model's name ("em027")
    float target_dist;                  // port: from the hunter, in the game's units
    // Music: what Monster Hunter's music player was last asked to play (PlayStream's stream), for the
    // layer to play FFXI's in its place; and the layer's word that the game's own be silent (its
    // stream music's volume, set_str_bgm_main_vol's, held at 0, given back when the layer stops).
    uint32_t bgm_stream;                // port: the stream's number
    uint32_t bgm_seq;                   // port: bumped with each
    uint32_t bgm_mute;                  // layer: 1 while FFXI's music plays
    uint32_t reserved3;
    char map[16];                       // port: the map's code module linked now ("map_village", "map01"; "": none)
    // FFXI's chat line (chat_key): "/" opens it, the keyboard typing into it (wiikit's text capture), Enter
    // sends it, Esc closes it.
    uint32_t chat_open;                 // port: 1 while it's open
    uint32_t chat_seq;                  // port: bumped with each line sent
    char chat_line[128];                // port: what's typed so far (UTF-8)
    char chat_sent[128];                // port: the last line sent
    // The hunter's own sounds the layer silences (hook_se_req): each kind << 16 | code, code 0xFFFF
    // every one of that kind (kind 1, the hunter's voice: its grunts); 0 an empty place.
    uint32_t mute_se[8];                // layer
    // The layer's companions (FFXI's Trusts), each its own model block ("mh3-ffxi-model-1" to "-5", the
    // layer making it when it calls one): where each stands, as the layer moves it (companions_frame).
    // [0] unused: the player's is the hunter's place.
    struct Companion {
        uint32_t shown;                 // layer: 1 drawn
        float x, y, z;                  // layer: its feet, in the world
        float yaw;                      // layer: radians it is turned from facing +Z (as the hunter's own)
        uint32_t battle;                // layer: 1 its weapon out (its battle set)
        uint32_t dead;                  // layer: 1 fallen
        float shadow_radius, shadow_dark;   // layer: its round shadow (0: none)
        uint32_t spare[3];
    } companions[6];
};
static_assert(sizeof(Block) == 1592, "ffxi/port_bridge.py mirrors this layout");

// The FFXI interface the layer draws, laid over the game's picture by the renderer
// (video_overlay_update). Its own block ("mh3-ffxi-overlay"): two image slots; the layer
// fills the one not shown, sets slot, w and h, then bumps serial; it bumps alive as it runs.
constexpr uint32_t kOverlayMagic = 0x4D48334F;     // 'MH3O'
constexpr int kOverlayMaxW = 1920, kOverlayMaxH = 1080;
struct OverlayBlock {
    uint32_t magic, version;
    uint32_t serial, slot, w, h;
    uint32_t alive;         // the layer bumps it while it runs: 3 s without, the overlay goes
    uint32_t pad;
    uint8_t px[2][kOverlayMaxW * kOverlayMaxH * 4];   // RGBA, straight alpha, rows top first
};

// Set by the hooks, on guest threads; published by serve().
std::atomic<uint32_t> g_player{0}, g_monster{0}, g_hits{0};
std::atomic<int32_t> g_delta{0};
PPCFunc orig_em_hp, orig_pl_hp, orig_master, orig_set_perspective;
std::atomic<uint32_t> g_camera{0};       // the 3D camera's nw4r::g3d::CameraData
bool in_ram(uint32_t a, uint32_t n);      // below

float f32(uint32_t a) {
    uint32_t v = ld32(a);
    float f;
    std::memcpy(&f, &v, 4);
    return f;
}

// nw4r::g3d::Camera::SetPerspective(fovy, aspect, near, far): r3 the Camera, a handle to its
// CameraData (+0x00 the view matrix, 3x4; +0x30 the projection, 4x4; +0xA8 the projection's type,
// +0xAC fovy ... +0xB8 far: as the function writes them). A 3D camera is remembered.
void hook_set_perspective(PPCContext& c) {
    uint32_t cam = c.r[3];
    double fovy = c.f[1];
    orig_set_perspective(c);
    if (fovy < 5 || fovy > 150 || !in_ram(cam, 4)) return;
    uint32_t data = ld32(cam);
    if (in_ram(data, 0xBC) && ld32(data + 0xA8) == 0 && std::fabs(f32(data + 0xAC) - fovy) < 0.01)
        g_camera.store(data, std::memory_order_relaxed);
}
// MH3_HUD_TRACE=1: who draws text (font_print_ex, font_print) and item names (ItemName): each new
// caller once, with its call chain and its text, to find the code that draws the HUD.
PPCFunc orig_font_ex, orig_font, orig_item_name;
PPCFunc orig_cockpit;
PPCFunc orig_add_resource;
PPCFunc orig_play_stream, orig_bgm_vol;
std::atomic<uint32_t> g_bgm_stream{0}, g_bgm_seq{0}, g_sda{0};   // g_sda: the game's r13 (its small data)
std::atomic<uint32_t> g_game_bgm_vol{0x3F800000};                 // the game's own music volume (a float's bits)

// PlayStream(stream, ?): the game's music (and its other streams) started
void hook_play_stream(PPCContext& c) {
    uint32_t id = c.r[3], arg = c.r[4], lr = c.lr;
    g_sda.store(c.r[13], std::memory_order_relaxed);
    orig_play_stream(c);
    g_bgm_stream.store(id, std::memory_order_relaxed);
    g_bgm_seq.fetch_add(1, std::memory_order_relaxed);
    rt_log("ffxi: the game plays stream %u (%u), from %08X", id, arg, lr);
}

// set_str_bgm_main_vol(float): the game's stream music volume, kept at r13 - 0x4460, read as it mixes
constexpr uint32_t kBgmVolSda = 0x4460;
void hook_bgm_vol(PPCContext& c) {
    float v = (float)c.f[1];
    uint32_t bits;
    std::memcpy(&bits, &v, 4);
    g_game_bgm_vol.store(bits, std::memory_order_relaxed);
    g_sda.store(c.r[13], std::memory_order_relaxed);
    orig_bgm_vol(c);
}

// fn_800D9D2C(se_w, code, ...): a sound effect requested for an actor (the se_w's +4 the actor, +8 its
// kind: the hunter has three, 1 its voice, 2 its feet on the ground, 3 its weapon). The hunter's own
// voice gives way to FFXI's (the layer plays its race's): the sounds the layer names (Block::mute_se),
// and MH3_MUTE_SE's ("1:15,1:*": kind, then the code in hex or * for all), are dropped for the hunter;
// MH3_SE_TRACE=1 logs each of the hunter's (and each a hunter's kind of actor asks for), to find them.
PPCFunc orig_se_req;
uint32_t object(uint32_t base);
extern Block* g_block;
std::vector<uint32_t> g_mute_se;                   // MH3_MUTE_SE's, as Block::mute_se's
bool g_se_trace = false;
bool se_muted(uint32_t kind, uint32_t code) {
    auto hit = [&](uint32_t m) { return m && m >> 16 == kind && ((m & 0xFFFF) == 0xFFFF || (m & 0xFFFF) == code); };
    for (uint32_t m : g_mute_se)
        if (hit(m)) return true;
    if (g_block)
        for (uint32_t m : g_block->mute_se)
            if (hit(m)) return true;
    return false;
}
void hook_se_req(PPCContext& c) {
    uint32_t w = c.r[3], code = c.r[4] & 0xFFFF;
    uint32_t owner = in_ram(w + 4, 8) ? ld32(w + 4) : 0, kind = in_ram(w + 4, 8) ? ld32(w + 8) : 0;
    uint32_t pl = object(kBasePlayer);
    bool hunter = pl && owner == pl;
    bool mute = hunter && se_muted(kind, code);
    if (g_se_trace && (hunter || kind == 1 || kind == 3))
        rt_log("ffxi: sound %04X for %08X%s (kind %u; %08X %08X), from %08X%s", code, owner, hunter ? " the hunter" : "",
               kind, c.r[5], c.r[6], c.lr, mute ? ": muted" : "");
    if (mute) {
        c.r[3] = 0;
        return;
    }
    orig_se_req(c);
}

// The map: the game links one code module per map (wiikit's RSO table: "...\\map01.plf"); its name's last
// part, without ".plf"
void serve_map(Block* b) {
    char out[16] = {};
    for (size_t k = 0; k < g_rso_nmodules; ++k) {
        if (!g_rso_base[k]) continue;
        const char* n = g_rso_modules[k].name;
        const char* base = n;
        for (const char* q = n; *q; ++q)
            if (*q == '\\' || *q == '/') base = q + 1;
        if (std::strncmp(base, "map", 3) != 0) continue;
        size_t len = 0;
        while (base[len] && base[len] != '.' && len < sizeof out - 1) ++len;
        std::memcpy(out, base, len);
        break;
    }
    std::memcpy(b->map, out, sizeof out);
}

// the layer's music on or off: the game's held at 0 meanwhile, its own value given back after
void serve_music(Block* b, bool layer_up) {
    static bool muted = false;
    uint32_t sda = g_sda.load(std::memory_order_relaxed);
    b->bgm_stream = g_bgm_stream.load(std::memory_order_relaxed);
    b->bgm_seq = g_bgm_seq.load(std::memory_order_relaxed);
    bool mute = layer_up && b->bgm_mute;
    if (!sda || !in_ram(sda - kBgmVolSda, 4)) return;
    if (mute) {
        st32(sda - kBgmVolSda, 0);
        muted = true;
    } else if (muted) {
        st32(sda - kBgmVolSda, g_game_bgm_vol.load(std::memory_order_relaxed));
        muted = false;
    }
}

// MH3_RES_TRACE=1: every resource the game registers once a file is loaded (nwAddResource), to name
// the one that arrives damaged when NW4R panics (ResTex::ref: Object not valid).
void hook_add_resource(PPCContext& c) {
    rt_log("ffxi: resource %s at %08X", guest_cstr(c.r[3], 96).c_str(), c.r[4]);
    orig_add_resource(c);
}

// MH3_HIDE_HUD=1: Monster Hunter's hunt HUD (mh3_CockpitDraw) is not drawn; the FFXI interface replaces it.
void hook_cockpit(PPCContext&) {}

void trace(const char* fn, PPCContext& c, const char* text) {
    static std::mutex m;
    static uint32_t seen[256];
    static int n = 0;
    std::lock_guard<std::mutex> l(m);
    for (int i = 0; i < n; ++i)
        if (seen[i] == c.lr) return;
    if (n == 256) return;
    seen[n++] = c.lr;
    rt_log("ffxi: trace %s from %s: \"%s\"", fn, rt_name(c.lr).c_str(), text);
    rt_backtrace(c, stderr, 12);
}
void hook_font_ex(PPCContext& c) { trace("font_print_ex", c, guest_cstr(c.r[6], 64).c_str()); orig_font_ex(c); }
void hook_font(PPCContext& c) { trace("font_print", c, guest_cstr(c.r[3], 64).c_str()); orig_font(c); }
void hook_item_name(PPCContext& c) {
    char id[16];
    std::snprintf(id, sizeof id, "item %u", c.r[3] & 0xFFFF);
    trace("ItemName", c, id);
    orig_item_name(c);
}

// fn_8011E6BC(em*, delta, cap): the One Hit Kill code's function (NEXT.md).
void hook_em_hp(PPCContext& c) {
    uint32_t obj = c.r[3];
    int32_t delta = (int32_t)c.r[4];
    orig_em_hp(c);
    g_monster.store(obj, std::memory_order_relaxed);
    g_delta.store(delta, std::memory_order_relaxed);
    g_hits.fetch_add(1, std::memory_order_relaxed);
}

// fn_802762F0(PLW*, short delta): the Infinite Health code's function. Offline,
// only the local hunter's HP changes.
std::atomic<uint32_t> g_hit_seq{0}, g_hit_big{0};    // the hunter hit (FFXI's character flinches): how often, the last big
void hook_pl_hp(PPCContext& c) {
    uint32_t obj = c.r[3];
    int delta = (int16_t)(c.r[4] & 0xFFFF);
    int before = in_ram(obj + 0x370, 4) ? (int16_t)ld16(obj + 0x370) : 0;
    orig_pl_hp(c);
    g_player.store(obj, std::memory_order_relaxed);
    if (delta < 0 && before > 0 && in_ram(obj + 0x372, 2)) {
        int max = (int16_t)ld16(obj + 0x372);
        g_hit_big.store(max > 0 && -delta * 5 >= max ? 1 : 0, std::memory_order_relaxed);
        g_hit_seq.fetch_add(1, std::memory_order_relaxed);
    }
}

// Pl_master_ck(PLW*): true for the local player's hunter.
void hook_master(PPCContext& c) {
    uint32_t obj = c.r[3];
    orig_master(c);
    if (c.r[3] & 0xFF) g_player.store(obj, std::memory_order_relaxed);
}

// The Classic's buttons (KPAD_CL_*, as video.cpp's).
enum : uint32_t { kClUp = 0x0001, kClLeft = 0x0002, kClX = 0x0008, kClA = 0x0010, kClY = 0x0020,
                  kClB = 0x0040, kClHome = 0x0800, kClL = 0x2000, kClDown = 0x4000, kClRight = 0x8000 };
std::atomic<bool> g_lock_toggle{false};  // the Classic's L held: lock on (or off), for target_frame
constexpr uint32_t kMenuButtons = kClUp | kClDown | kClLeft | kClRight | kClA | kClB | kClX | kClY;

Block* g_block = nullptr;
std::atomic<bool> g_layer_up{false};    // the layer bumped the overlay's alive within 3 s

// Channel 1's Classic, before the game reads it. Y is shared with the game, which sheathes the
// weapon and uses items with it: a tap reaches the game (at its release), held it opens the menu.
constexpr auto kHoldForMenu = std::chrono::milliseconds(350);

void classic_filter(int chan, ClassicState& s) {
    s.buttons &= ~kClHome;                        // the Home Button menu crashes the port: never opened
    Block* b = g_block;
    if (chan != 0 || !b) return;
    using clock = std::chrono::steady_clock;
    static uint32_t prev = 0;
    static clock::time_point y_since{}, y_until{};
    static bool y_held = false;                   // Y down, the menu closed: a tap or a hold, not yet known
    auto now = clock::now();
    uint32_t pressed = s.buttons & ~prev, released = prev & ~s.buttons;
    prev = s.buttons;
    bool up = g_layer_up.load(std::memory_order_relaxed);
    if (b->menu_close_seq != b->menu_close_ack) {
        b->menu_open = 0;
        b->menu_close_ack = b->menu_close_seq;
    }
    if (!up) {
        b->menu_open = 0;
        y_held = false;
        return;                                   // no layer: the pad is the game's, as it was
    }
    if (b->menu_open) {
        if (pressed & kClY) {                     // a tap closes it
            b->menu_open = 0;
            pressed &= ~kClY;
        }
    } else if (pressed & kClY) {
        y_held = true;
        y_since = now;
    } else if (y_held && (released & kClY)) {
        y_held = false;                           // a tap: the game's, now
        y_until = now + std::chrono::milliseconds(120);
    } else if (y_held && now - y_since >= kHoldForMenu) {
        y_held = false;                           // held: the menu
        b->menu_open = 1;
    }
    if (b->menu_open) {
        if (uint32_t ev = pressed & kMenuButtons) {
            b->events[b->ev_head % 32] = ev;
            std::atomic_thread_fence(std::memory_order_release);
            b->ev_head = b->ev_head + 1;
        }
        s.buttons &= ~kMenuButtons;
    }
    if (b->item_seq != b->item_ack) {
        b->item_ack = b->item_seq;
        y_until = now + std::chrono::milliseconds(120);
    }
    if (now < y_until) s.buttons |= kClY;         // a tap, or the layer's Items: the game's own Y
    else s.buttons &= ~kClY;                      // otherwise the filter's, until it knows

    // L, the game's camera behind the hunter: a tap is the game's, as Y's is; held, FFXI's lock-on
    static bool l_held = false;
    static clock::time_point l_since{}, l_until{};
    if (pressed & kClL) {
        l_held = true;
        l_since = now;
    } else if (l_held && (released & kClL)) {
        l_held = false;
        l_until = now + std::chrono::milliseconds(120);
    } else if (l_held && now - l_since >= kHoldForMenu) {
        l_held = false;
        g_lock_toggle.store(true, std::memory_order_relaxed);
    }
    if (now < l_until) s.buttons |= kClL, s.lt = 1.0f;
    else s.buttons &= ~kClL, s.lt = 0.0f;
}

// FFXI's chat line. The keyboard's "/" (or the keypad's) opens it while the layer runs; meanwhile the
// keyboard is text for it, not the game's (wiikit's text capture): Backspace, Enter to send, Esc to close.
// SDL's scancodes:
enum : int { kScReturn = 40, kScEscape = 41, kScBackspace = 42, kScSlash = 56, kScKpDivide = 84, kScKpEnter = 88 };
std::mutex g_chat_mx;
std::string g_chat;
bool g_chat_open = false;
std::chrono::steady_clock::time_point g_chat_opened;

void chat_publish(Block* b) {                       // g_chat_mx held
    b->chat_open = g_chat_open ? 1 : 0;
    std::memset(b->chat_line, 0, sizeof b->chat_line);
    std::memcpy(b->chat_line, g_chat.data(), std::min(g_chat.size(), sizeof b->chat_line - 1));
}

bool chat_key(int scancode, const char* text) {
    Block* b = g_block;
    if (!b || !g_layer_up.load(std::memory_order_relaxed)) return false;
    std::lock_guard<std::mutex> lk(g_chat_mx);
    auto now = std::chrono::steady_clock::now();
    if (!g_chat_open) {
        if (text || (scancode != kScSlash && scancode != kScKpDivide)) return false;
        g_chat_open = true, g_chat = "/", g_chat_opened = now;
        video_text_capture(true);
        chat_publish(b);
        return true;
    }
    if (text) {                                   // the "/" that opened it comes as text too: once
        if (g_chat == "/" && std::strcmp(text, "/") == 0 && now - g_chat_opened < std::chrono::milliseconds(300))
            return true;
        if (g_chat.size() + std::strlen(text) < sizeof b->chat_line - 1) g_chat += text;
    } else if (scancode == kScBackspace) {
        while (!g_chat.empty() && (g_chat.back() & 0xC0) == 0x80) g_chat.pop_back();   // a whole UTF-8 character
        if (!g_chat.empty()) g_chat.pop_back();
    } else if (scancode == kScReturn || scancode == kScKpEnter) {
        if (!g_chat.empty()) {
            std::memset(b->chat_sent, 0, sizeof b->chat_sent);
            std::memcpy(b->chat_sent, g_chat.data(), std::min(g_chat.size(), sizeof b->chat_sent - 1));
            std::atomic_thread_fence(std::memory_order_release);
            b->chat_seq = b->chat_seq + 1;
        }
        g_chat_open = false, g_chat.clear();
        video_text_capture(false);
    } else if (scancode == kScEscape) {
        g_chat_open = false, g_chat.clear();
        video_text_capture(false);
    }
    chat_publish(b);
    return true;                                  // while it's open every key is the line's
}

// Only RAM is committed in the host's reservation: MEM1 and MEM2.
bool in_ram(uint32_t a, uint32_t n) {
    auto in = [&](uint32_t lo, uint32_t size) { return a >= lo && a - lo + n <= size; };
    return in(kMem1, kMem1Size) || in(kMem2, kMem2Size);
}

// Written beside the path, then renamed over it: the layer never sees half a file.
bool snapshot(const char* path) {
    std::string tmp = std::string(path) + ".part";
    FILE* f = std::fopen(tmp.c_str(), "wb");
    if (!f) return false;
    bool ok = std::fwrite(host(kMem1), 1, kMem1Size, f) == kMem1Size &&
              std::fwrite(host(kMem2), 1, kMem2Size, f) == kMem2Size;
    ok = (std::fclose(f) == 0) && ok;
    return ok && MoveFileExA(tmp.c_str(), path, MOVEFILE_REPLACE_EXISTING);
}

uint32_t rd(uint32_t a, uint32_t n) {
    if (n == 1) return ld8(a);
    if (n == 2) return ld16(a);
    return ld32(a);
}

void wr(uint32_t a, uint32_t n, uint32_t v) {
    if (n == 1) st8(a, (uint8_t)v);
    else if (n == 2) st16(a, (uint16_t)v);
    else st32(a, v);
}

// The objects the hooks found may be freed when the game leaves an area, and their memory reused (a
// file being loaded): one is believed only while it still looks like what it was, and is forgotten
// otherwise, so the layer never reads or writes a stranger's memory through it.
bool hunter_ok(uint32_t obj) {
    if (!obj || !in_ram(obj + 0x370, 4)) return false;
    int hp = (int16_t)ld16(obj + 0x370), max = (int16_t)ld16(obj + 0x372);
    return max >= 50 && max <= 200 && hp >= 0 && hp <= max;
}

bool monster_ok(uint32_t obj) {
    if (!obj || !in_ram(obj + 0x7A0, 8)) return false;
    int32_t hp = (int32_t)ld32(obj + 0x7A0), max = (int32_t)ld32(obj + 0x7A4);
    return max >= 10 && max <= 1000000 && hp >= 0 && hp <= max;
}

// The game's monsters (get_move_work_adrs(3), get_move_work_max(3)): a table of ENEMY_WORK, 0xB18 apart,
// each in use while its first byte is set; its HP at +0x7A0 as mh3_EmHpAdd's. A monster's scene model is
// found from it through a word in it pointing at the model, or at an MHchar holding it (+0x118): the
// word's place found once, by looking through a monster's words.
std::atomic<uint32_t> g_target_em{0};      // the target's ENEMY_WORK (0: not found)
uint32_t em_of_model(uint32_t scn) {
    static uint32_t cached_scn = 0, cached_em = 0;
    static int link = -1;
    auto links = [&](uint32_t w, int i) {
        uint32_t p = ld32(w + 4 * (uint32_t)i);
        return p == scn || (in_ram(p + 0x118, 4) && ld32(p + 0x118) == scn);
    };
    if (!scn) return 0;
    if (scn == cached_scn && cached_em && in_ram(cached_em, 0xB18) && ld8(cached_em) && link >= 0 && links(cached_em, link))
        return cached_em;
    cached_scn = scn, cached_em = 0;
    if (!in_ram(0x8065AA60 + 0xA4, 4)) return 0;
    uint32_t t = ld32(0x8065AA60 + 0xA4);
    if (!in_ram(t, 0x30)) return 0;
    uint32_t base = ld32(t + 0x10 + 3 * 4), n = ld16(t + 3 * 2);
    if (!n || n > 256 || !in_ram(base, n * 0xB18)) return 0;
    for (uint32_t k = 0; k < n; ++k) {
        uint32_t w = base + k * 0xB18;
        if (!ld8(w)) continue;
        if (link >= 0) {
            if (links(w, link)) return cached_em = w;
            continue;
        }
        for (int i = 1; i < 0xB18 / 4; ++i)
            if (links(w, i)) {
                link = i;
                rt_log("ffxi: a monster's model is linked from its ENEMY_WORK +0x%X (monster %u of %u, HP %d of %d)",
                       4 * i, k, n, (int32_t)ld32(w + 0x7A0), (int32_t)ld32(w + 0x7A4));
                return cached_em = w;
            }
    }
    return 0;
}

bool monster_ok(uint32_t obj);

uint32_t object(uint32_t base) {
    if (base == kBasePlayer) {
        uint32_t o = g_player.load(std::memory_order_relaxed);
        if (hunter_ok(o)) return o;
        g_player.compare_exchange_strong(o, 0);
    } else if (base == kBaseMonster) {
        // FFXI acts on its target: the target's own monster, else the one the game last hurt
        uint32_t t = g_target_em.load(std::memory_order_relaxed);
        if (t && monster_ok(t)) return t;
        uint32_t o = g_monster.load(std::memory_order_relaxed);
        if (monster_ok(o)) return o;
        g_monster.compare_exchange_strong(o, 0);
    }
    return 0;
}

bool resolve(const Watch& w, uint32_t& out) {
    uint32_t a = w.addr;
    if (w.base != kBaseAddr) {
        uint32_t obj = object(w.base);
        if (!obj) return false;
        a = obj + w.addr;
    }
    uint32_t n = w.n_offsets < (uint32_t)kOffsets ? w.n_offsets : (uint32_t)kOffsets;
    for (uint32_t i = 0; i < n; ++i) {
        if (!in_ram(a, 4)) return false;
        a = ld32(a) + w.offsets[i];
    }
    if (w.size != 1 && w.size != 2 && w.size != 4) return false;
    if (!in_ram(a, w.size)) return false;
    out = a;
    return true;
}

void serve_overlay(OverlayBlock* o) {
    static uint32_t shown = 0, alive = 0;
    static std::chrono::steady_clock::time_point last_alive{};   // never: no layer until it bumps alive
    static bool up = false;
    auto now = std::chrono::steady_clock::now();
    if (o->alive != alive) { alive = o->alive; last_alive = now; }
    g_layer_up.store(now - last_alive < std::chrono::seconds(3), std::memory_order_relaxed);
    if (up && now - last_alive > std::chrono::seconds(3)) {
        video_overlay_update(nullptr, 0, 0);     // the layer has gone
        up = false;
        shown = o->serial;
        return;
    }
    if (o->serial == shown) return;
    std::atomic_thread_fence(std::memory_order_acquire);
    uint32_t w = o->w, h = o->h, slot = o->slot & 1;
    shown = o->serial;
    if (w > (uint32_t)kOverlayMaxW || h > (uint32_t)kOverlayMaxH) return;
    video_overlay_update(w && h ? o->px[slot] : nullptr, (int)w, (int)h);
    up = w && h;
}

// The game keeps its cameras in an array (0x10C apart): the hook sees whichever sets a perspective,
// often a preview's. The one that follows the hunter is the camera looking at it: among the camera
// blocks around the one seen, the one whose target (+0x8C) is nearest the hunter, chosen again now
// and then.
bool camera_block(uint32_t a) {
    if (!in_ram(a, 0xBC) || ld32(a + 0xA8) != 0) return false;         // a perspective projection
    float fovy = f32(a + 0xAC), aspect = f32(a + 0xB0), n = f32(a + 0xB4), fa = f32(a + 0xB8);
    return fovy > 5 && fovy < 150 && aspect > 0.5f && aspect < 3 && n > 0 && fa > n;
}

// The array also holds cameras never set up, looking at the world's origin from their defaults: near
// the origin one of them would be nearer the hunter than the following camera's target (170 above its
// feet), so a camera looking at exactly 0, 0, 0 is never the one. (The serve thread and the frame hook
// both ask.)
uint32_t follow_camera(uint32_t pl) {
    static std::mutex mx;
    static uint32_t chosen = 0;
    static int age = 0;
    std::lock_guard<std::mutex> lk(mx);
    uint32_t seen = g_camera.load(std::memory_order_relaxed);
    if (!seen || !pl) return 0;
    if (chosen && camera_block(chosen) && ++age < 60) return chosen;
    age = 0;
    float hx = f32(pl + 0x3C), hy = f32(pl + 0x40), hz = f32(pl + 0x44);
    float best = 1e12f;
    uint32_t pick = 0;
    for (uint32_t a = seen > 0x4000 ? seen - 0x4000 : seen; a < seen + 0x4000; a += 4) {
        if (!camera_block(a)) continue;
        if (ld32(a + 0x8C) == 0 && ld32(a + 0x90) == 0 && ld32(a + 0x94) == 0) {
            a += 0x100;                           // a default camera, never aimed
            continue;
        }
        float dx = f32(a + 0x8C) - hx, dy = f32(a + 0x90) - hy, dz = f32(a + 0x94) - hz;
        float d = dx * dx + dy * dy + dz * dz;
        if (std::isfinite(d) && d < best) { best = d; pick = a; }
        a += 0x100;                               // past this block
    }
    chosen = best < 2000.0f * 2000.0f ? pick : 0;  // looking at the hunter, not somewhere else
    return chosen;
}

// The hunter's head (its position, +0x3C of its _PLW, raised) through the camera: view, then projection.
void project_name(Block* b) {
    uint32_t pl = object(kBasePlayer), cam = follow_camera(pl);
    b->camera = cam;
    if (!cam || !pl || !in_ram(cam, 0x70) || !in_ram(pl + 0x3C, 12)) { b->name_ok = 0; return; }
    float h = b->name_height > 0 && b->name_height < 10000 ? b->name_height : 200.0f;
    float w[3] = {f32(pl + 0x3C), f32(pl + 0x40) + h, f32(pl + 0x44)};
    float v[3], clip[4];
    for (int r = 0; r < 3; ++r)
        v[r] = f32(cam + 16 * r) * w[0] + f32(cam + 16 * r + 4) * w[1] + f32(cam + 16 * r + 8) * w[2] + f32(cam + 16 * r + 12);
    for (int r = 0; r < 4; ++r)
        clip[r] = f32(cam + 0x30 + 16 * r) * v[0] + f32(cam + 0x30 + 16 * r + 4) * v[1]
                + f32(cam + 0x30 + 16 * r + 8) * v[2] + f32(cam + 0x30 + 16 * r + 12);
    if (!(clip[3] > 0.001f) || !std::isfinite(clip[0]) || !std::isfinite(clip[1])) { b->name_ok = 0; return; }
    b->name_x = clip[0] / clip[3];
    b->name_y = clip[1] / clip[3];
    b->name_ok = std::fabs(b->name_x) < 1.1f && std::fabs(b->name_y) < 1.1f;
}

// ================================================================================================
// FFXI's target and lock-on. Monsters are scene models whose resource is named em<number> ("em027"):
// each one's place is noted as the scene computes it (note_enemy, from hook_scn_world, the game's
// thread). Each frame (target_frame) the target is the one locked on, else the nearest within reach;
// its arrow goes above the highest of its joints (the model's world matrices, ScnMdl +0xEC, as many as
// its +0x102 says). Locked on, the gameplay camera stands behind the hunter facing the target, at the
// game's own distance and height (lock_camera, after the game's Camera::SetPosition and SetPosture:
// NW4R keeps the eye at CameraData +0x74 and the point looked at at +0x8C, the view built from them
// later, while +0x70's bit 8 is clear); the game's idea of where the camera looks follows
// (get_camera_direction reads the camera), so the stick does too.
// ================================================================================================
struct Enemy {
    uint32_t obj;
    float x, y, z;
    uint32_t frame;         // the frame it was last placed in
    char name[16];
};
std::vector<Enemy> g_enemies;            // the game's thread only
uint32_t g_frame = 0;                    // frames finished (target_frame)
constexpr float kTargetReach = 2500.0f, kLockReach = 5000.0f;

// a monster's model: its resource (ScnMdl +0xE8, an MDL0) named em and a digit
bool enemy_model(uint32_t obj, char name[16]) {
    if (!in_ram(obj + 0xE8, 4)) return false;
    uint32_t mdl = ld32(obj + 0xE8);
    if (!in_ram(mdl, 0x50) || ld32(mdl) != 0x4D444C30) return false;              // 'MDL0'
    uint32_t sections = ld32(mdl + 8) >= 10 ? 14 : 11;
    uint32_t at = mdl + ld32(mdl + 0x10 + 4 * sections);
    if (!in_ram(at, 16) || ld8(at) != 'e' || ld8(at + 1) != 'm' || !std::isdigit(ld8(at + 2))) return false;
    for (int i = 0; i < 15; ++i) name[i] = (char)ld8(at + i);
    name[15] = 0;
    return true;
}

void note_enemy(uint32_t obj) {
    char name[16];
    if (!enemy_model(obj, name) || !in_ram(obj + 0x3C, 48)) return;
    // a model the game keeps placed but draws neither opaque nor translucent (a monster switched out,
    // away from the area) is nobody's target: the arrow once pointed at an unseen Aptonoth so
    if (in_ram(obj + 0xCC, 4) && (ld32(obj + 0xCC) & 0x60) == 0x60) return;
    float x = f32(obj + 0x48), y = f32(obj + 0x58), z = f32(obj + 0x68);
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) return;
    for (Enemy& e : g_enemies)
        if (e.obj == obj) {
            e.x = x, e.y = y, e.z = z, e.frame = g_frame;
            std::memcpy(e.name, name, sizeof name);
            return;
        }
    if (g_enemies.size() >= 64) return;
    Enemy e{obj, x, y, z, g_frame, {}};
    std::memcpy(e.name, name, sizeof name);
    g_enemies.push_back(e);
}

// the highest of a model's joints (its world matrices' translations), or its root's height and more
float enemy_top(const Enemy& e) {
    uint32_t arr = ld32(e.obj + 0xEC), n = std::min<uint32_t>(ld16(e.obj + 0x102), 512);
    if (!n || !in_ram(arr, n * 0x30)) return e.y + 150.0f;
    float top = -1e9f;
    for (uint32_t i = 0; i < n; ++i) {
        float y = f32(arr + i * 0x30 + 0x1C);
        if (std::isfinite(y) && std::fabs(y - e.y) < 5000.0f) top = std::max(top, y);
    }
    return top > -1e9f ? top : e.y + 150.0f;
}

// a point of the world through a camera (its view, then its projection): -1..1, y up; false behind it
bool project(uint32_t cam, const float w[3], float& sx, float& sy) {
    if (!in_ram(cam, 0x70)) return false;
    float v[3], clip[4];
    for (int r = 0; r < 3; ++r)
        v[r] = f32(cam + 16 * r) * w[0] + f32(cam + 16 * r + 4) * w[1] + f32(cam + 16 * r + 8) * w[2] + f32(cam + 16 * r + 12);
    for (int r = 0; r < 4; ++r)
        clip[r] = f32(cam + 0x30 + 16 * r) * v[0] + f32(cam + 0x30 + 16 * r + 4) * v[1]
                + f32(cam + 0x30 + 16 * r + 8) * v[2] + f32(cam + 0x30 + 16 * r + 12);
    if (!(clip[3] > 0.001f) || !std::isfinite(clip[0]) || !std::isfinite(clip[1])) return false;
    sx = clip[0] / clip[3], sy = clip[1] / clip[3];
    return std::fabs(sx) < 1.1f && std::fabs(sy) < 1.1f;
}

// what lock_camera needs, from target_frame (both on the game's thread)
struct Lock {
    bool on = false;
    float weight = 0;                    // 1 locked; easing in and out
    uint32_t camera = 0;                 // the gameplay camera's CameraData
    float tx = 0, ty = 0, tz = 0;        // the target: its middle
    float arrow = 0;                     // its arrow's height (above its head)
    float yaw = 0;                       // the camera's, around the hunter, eased toward the target
    bool have_game = false;
    float eye[3] = {}, look[3] = {};     // the game's own, as it last set them
} g_lock;
uint32_t g_target_obj = 0;
float g_target_place[4] = {};             // the target's feet and its height, for the effects (fx_frame)
bool g_target_ok = false;
PPCFunc orig_cam_position, orig_cam_posture;

void target_frame(uint32_t pl, uint32_t cam, float dt) {
    ++g_frame;
    Block* b = g_block;
    g_enemies.erase(std::remove_if(g_enemies.begin(), g_enemies.end(),
                                   [](const Enemy& e) { return g_frame - e.frame > 30; }), g_enemies.end());
    auto live = [&](uint32_t obj) -> Enemy* {
        for (Enemy& e : g_enemies)
            if (e.obj == obj && g_frame - e.frame <= 3) return &e;
        return nullptr;
    };
    float hx = pl ? f32(pl + 0x3C) : 0, hy = pl ? f32(pl + 0x40) : 0, hz = pl ? f32(pl + 0x44) : 0;
    auto dist = [&](const Enemy& e) { return std::hypot(e.x - hx, e.z - hz); };
    bool toggle = g_lock_toggle.exchange(false, std::memory_order_relaxed);
    Enemy* t = g_lock.on ? live(g_target_obj) : nullptr;
    if (g_lock.on && (!t || !pl || dist(*t) > kLockReach)) g_lock.on = false, t = nullptr;   // gone: let go
    if (!t && pl) {
        float best = kTargetReach;
        for (Enemy& e : g_enemies)
            if (g_frame - e.frame <= 3 && dist(e) < best) best = dist(e), t = &e;
    }
    if (toggle) {
        if (g_lock.on) g_lock.on = false;
        else if (t && cam) {
            g_lock.on = true;
            if (g_lock.weight <= 0.0f && g_lock.have_game)     // from where the game's camera is
                g_lock.yaw = std::atan2(g_lock.look[0] - g_lock.eye[0], g_lock.look[2] - g_lock.eye[2]);
        }
        rt_log("ffxi: lock-on %s %s", g_lock.on ? "on" : "off", t ? t->name : "(no target)");
    }
    // FFXI's Attack (the layer's engage): the camera locked on the target while it lasts (and let go after
    // if it was the attack that locked it)
    static bool locked_by_attack = false;
    bool attack_lock = b && (b->engage & 2);
    if (attack_lock && t && !g_lock.on && cam) {
        g_lock.on = locked_by_attack = true;
        if (g_lock.weight <= 0.0f && g_lock.have_game)
            g_lock.yaw = std::atan2(g_lock.look[0] - g_lock.eye[0], g_lock.look[2] - g_lock.eye[2]);
    } else if (!attack_lock && locked_by_attack) {
        g_lock.on = locked_by_attack = false;
    }
    g_target_em.store(t ? em_of_model(t->obj) : 0, std::memory_order_relaxed);
    static uint32_t told_target = 0;
    if (t && t->obj != told_target) {                        // each new target, for a report of a strange one
        told_target = t->obj;
        rt_log("ffxi: target %s (scene model %08X, draw flags %08X) at %.0f %.0f %.0f, %.0f from the hunter",
               t->name, t->obj, in_ram(t->obj + 0xCC, 4) ? ld32(t->obj + 0xCC) : 0, t->x, t->y, t->z, dist(*t));
    }
    g_target_obj = t ? t->obj : 0;
    g_lock.camera = cam;
    float top = t ? enemy_top(*t) : 0;
    g_target_ok = t != nullptr;
    if (t) g_target_place[0] = t->x, g_target_place[1] = t->y, g_target_place[2] = t->z, g_target_place[3] = top - t->y;
    if (t) {
        g_lock.tx = t->x, g_lock.ty = (t->y + top) * 0.5f, g_lock.tz = t->z, g_lock.arrow = top + 40.0f;
        float want = std::atan2(t->x - hx, t->z - hz), d = want - g_lock.yaw;
        d -= 6.2831853f * std::floor((d + 3.14159265f) / 6.2831853f);   // the short way round
        if (g_lock.on) g_lock.yaw += d * std::min(1.0f, dt * 5.0f);
    }
    g_lock.weight = g_lock.on ? std::min(1.0f, g_lock.weight + dt / 0.3f) : std::max(0.0f, g_lock.weight - dt / 0.3f);
    if (!b) return;
    b->target_model = t ? t->obj : 0;
    b->target_locked = g_lock.on ? 1 : 0;
    std::memset(b->target_name, 0, sizeof b->target_name);
    if (t) std::memcpy(b->target_name, t->name, sizeof b->target_name);
    b->target_dist = t ? std::hypot(dist(*t), t->y - hy) : 0;
    float w[3] = {t ? t->x : 0, top + 40.0f, t ? t->z : 0}, sx = 0, sy = 0;
    bool ok = t && cam && project(cam, w, sx, sy);
    b->target_x = sx, b->target_y = sy;
    b->target_ok = ok ? 1 : 0;
}

// The gameplay camera locked on: behind the hunter on the line from the target, at the game's own
// distance and height above the point it looks at (its zoom and its tilt kept), that point drawn toward
// the target, so both are in sight; eased in and out over 0.3 s.
void lock_camera(uint32_t data) {
    if (!(g_lock.weight > 0.0f) || data != g_lock.camera || !g_lock.have_game || !in_ram(data, 0x98)) return;
    uint32_t pl = object(kBasePlayer);
    if (!pl) return;
    float h[3] = {f32(pl + 0x3C), f32(pl + 0x40), f32(pl + 0x44)};
    const float* e0 = g_lock.eye;
    const float* l0 = g_lock.look;
    float off[3] = {l0[0] - h[0], l0[1] - h[1], l0[2] - h[2]};             // where the game looks, by the hunter
    float dx = e0[0] - l0[0], dy = e0[1] - l0[1], dz = e0[2] - l0[2];
    float flat = std::hypot(dx, dz);
    if (!(flat > 1.0f)) return;
    float c = h[0] + off[0], cy = h[1] + off[1], cz = h[2] + off[2];      // the game's point, the hunter's
    // toward the target: a third of the way across (300 units at most), a quarter of the way up or down
    // (120 at most): FFXI's lock turns the camera, it hardly tilts it
    float ax = g_lock.tx - c, az = g_lock.tz - cz, across = std::hypot(ax, az);
    float k = across > 900.0f ? 300.0f / across : 1.0f / 3.0f;
    float look[3] = {c + ax * k, cy + std::clamp((g_lock.ty - cy) * 0.25f, -120.0f, 120.0f), cz + az * k};
    float sx = std::sin(g_lock.yaw), sz = std::cos(g_lock.yaw);
    float eye[3] = {c - sx * flat, cy + dy, cz - sz * flat};
    // and tilted as far as needed, no further, to keep the target's arrow within 17 degrees of the view's
    // middle (a monster up a slope, or below): in sight
    float dl = std::hypot(look[0] - eye[0], look[2] - eye[2]), de = std::hypot(g_lock.tx - eye[0], g_lock.tz - eye[2]);
    if (dl > 1.0f && de > 1.0f) {
        float pv = std::atan2(look[1] - eye[1], dl), pt = std::atan2(g_lock.arrow - eye[1], de);
        pv = std::clamp(pv, pt - 0.30f, pt + 0.30f);
        look[1] = eye[1] + dl * std::tan(pv);
    }
    float w = g_lock.weight;
    for (int i = 0; i < 3; ++i) {
        float ev = e0[i] + (eye[i] - e0[i]) * w, lv = l0[i] + (look[i] - l0[i]) * w;
        uint32_t ui, ul;
        std::memcpy(&ui, &ev, 4), std::memcpy(&ul, &lv, 4);
        st32(data + 0x74 + 4 * i, ui);
        st32(data + 0x8C + 4 * i, ul);
    }
    st32(data + 0x70, ld32(data + 0x70) & ~8u);                            // the view built again from them
}

// nw4r::g3d::Camera::SetPosition(const VEC3&): r3 the Camera (a handle to its CameraData), r4 the eye
void hook_cam_position(PPCContext& c) {
    uint32_t cam = c.r[3], v = c.r[4];
    uint32_t data = in_ram(cam, 4) ? ld32(cam) : 0;
    if (data && data == g_lock.camera && in_ram(v, 12))                    // the game's eye, before ours
        for (int i = 0; i < 3; ++i) g_lock.eye[i] = f32(v + 4 * i);
    orig_cam_position(c);
    if (data) lock_camera(data);
}

// Camera::SetPosture(const PostureInfo&): r4 the posture (+0 its kind: 0 looking at a point, 2 aiming at one
// with a twist, as the game does; +0x10 that point)
void hook_cam_posture(PPCContext& c) {
    uint32_t cam = c.r[3], p = c.r[4];
    uint32_t data = in_ram(cam, 4) ? ld32(cam) : 0;
    if (data && data == g_lock.camera && in_ram(p, 0x1C)) {
        static bool told = false;
        if (!told) told = true, rt_log("ffxi: the gameplay camera's posture: kind %u", ld32(p));
        uint32_t kind = ld32(p);
        if (kind == 0 || kind == 2) {                        // looking at a point; aiming at one (the game's)
            for (int i = 0; i < 3; ++i) g_lock.look[i] = f32(p + 0x10 + 4 * i);
            if (in_ram(data + 0x74, 12))
                for (int i = 0; i < 3; ++i)
                    if (g_lock.weight <= 0.0f) g_lock.eye[i] = f32(data + 0x74 + 4 * i);
            g_lock.have_game = true;
        }
    }
    orig_cam_posture(c);
    if (data) lock_camera(data);
}


// ================================================================================================
// FFXI's character in your hunter's place (ffxi/ffxi_model.py, port_model.py): the layer sends a model
// on its skeleton, with its motions, through its own block; the port poses it every frame (Animator) and
// the renderer skins it and draws it into the scene (video_model_*) where the hunter stands, with the
// game's camera. Its motions: standing and moving ones, picked by the hunter's speed, from the peace
// set or, while the hunter's weapon is out, the battle set; and actions over them, on the layer's
// request (a spell's casting) or the hunter's (Monster Hunter's attacks: hunter_motion).
// ================================================================================================
constexpr uint32_t kModelMagic = 0x4D48334D, kModelVersion = 7;     // 'MH3M'
constexpr int kModelMaxVerts = 100000, kModelVertFloats = 20, kModelMaxLayers = 16, kModelTex = 256;
constexpr int kModelMaxJoints = 128, kModelMaxClips = 96, kModelPool = 1 << 22, kXf = 10;
enum : uint32_t { kStand = 0, kMove = 1, kAction = 2 };
enum : uint32_t { kPeace = 0, kBattle = 1 };
constexpr uint32_t kNone = 0xFFFFFFFF;
constexpr uint32_t fourcc(const char (&s)[5]) {
    return (uint32_t)(uint8_t)s[0] | (uint32_t)(uint8_t)s[1] << 8 | (uint32_t)(uint8_t)s[2] << 16 | (uint32_t)(uint8_t)s[3] << 24;
}
struct ModelClip {
    uint32_t frames;        // keyframes
    uint32_t first;         // its first float in the pool: frames x joints x (rotation x y z w, offset x y z, scale x y z)
    float rate;             // keyframes a game frame (30 a second)
    float speed;            // a moving one's: the ground speed it was made for, units a second
    uint32_t kind;          // kStand, kMove (picked by the hunter's speed) or kAction (on request)
    uint32_t set;           // kPeace (the weapon away) or kBattle (drawn)
    uint32_t tag;           // its name's first four letters ("at0 ", "draw", "shea", "mw0 "...)
    uint32_t reserved;
};
struct ModelBlock {
    uint32_t magic, version;
    uint32_t serial;        // the layer bumps it once everything below is written
    uint32_t n_verts, layers, tex_w, tex_h;
    uint32_t flags;         // 1: draw it; 2: hide Monster Hunter's hunter
    uint32_t alive;         // the layer bumps it while it runs
    uint32_t n_joints, n_clips;
    uint32_t act_seq;       // the layer bumps it with each request:
    uint32_t act_clip;      //   play this action once (kNone: end the one playing),
    uint32_t act_next;      //   then loop this one until the next request (kNone: back to moving)
    uint32_t battle;        // the port's: 1 while Monster Hunter's hunter has its weapon out
    float light_gain;       // the layer's: its textures' brightness under the game's light (0: unlit),
    float ground_shade;     //   and how much the ground's baked light dims it (video_model_shading)
    uint32_t swings;        // the port's: bumped with each of Monster Hunter's hunter's attacks (FFXI's voice)
    uint32_t shadow;        // the layer's: 1 Monster Hunter's foot shadows under the hunter hidden, 2 FFXI's round
                            //   one drawn under the character; bits 8-15 its darkness (0-255)
    float shadow_radius;    // the layer's: the round shadow's radius, in the game's units
    char motion[16];        // the port's: the hunter's motion now (Monster Hunter's animation's name)
    float place[12];                                      // the model's space to the hunter's, 3x4 rows
    int32_t parents[kModelMaxJoints];                     // -1: the root; a parent comes before its children
    ModelClip clips[kModelMaxClips];
    float verts[kModelMaxVerts * kModelVertFloats];       // video_model_mesh's corners
    float pool[kModelPool];
    uint8_t tex[kModelMaxLayers * kModelTex * kModelTex * 4];
};
static_assert(sizeof(ModelBlock) == 28975248, "ffxi/port_model.py mirrors this layout");

// The character's motions, copied from the block when it changes. Moving: under 15 units a second (30
// to start) the set's standing one; moving, the one made for the nearest speed, played that much faster
// or slower (a half to twice), so the feet keep pace with the ground. A change blends over a quarter of
// a second; walking and running share their place in the stride. An action plays over all of it, eased
// in and out: once, then either looped (a chant) or let go.
struct Animator {
    int nj = 0;
    std::vector<int32_t> parents;
    std::vector<ModelClip> clips;
    std::vector<float> pool;
    float place[12] = {};
    int cur = -1, prev = -1;
    float phase[kModelMaxClips] = {};
    float blend = 1.0f;     // cur's share
    float speed = 0.0f;     // the hunter's over the ground, smoothed
    bool moving = false, have_last = false, battle = false;
    float last_x = 0, last_z = 0;
    int act = -1, act_next = -1;                            // the action playing, and the one to loop after
    float act_phase = 0, act_w = 0;
    bool act_loop = false, act_out = false;
    int swing = 0;                                          // the next attack of at0, at1, at2
    std::vector<float> a, b, c, rows;

    bool load(const ModelBlock* mb) {
        nj = (int)std::min<uint32_t>(mb->n_joints, kModelMaxJoints);
        parents.assign(mb->parents, mb->parents + nj);
        std::memcpy(place, mb->place, sizeof place);
        clips.clear();
        pool.clear();
        for (uint32_t i = 0; i < std::min<uint32_t>(mb->n_clips, kModelMaxClips); ++i) {
            ModelClip k = mb->clips[i];
            size_t n = (size_t)k.frames * nj * kXf;
            if (!k.frames || k.first > (uint32_t)kModelPool || n > (size_t)kModelPool - k.first || !(k.rate > 0)) break;
            uint32_t at = (uint32_t)pool.size();
            pool.insert(pool.end(), mb->pool + k.first, mb->pool + k.first + n);
            k.first = at;
            clips.push_back(k);
        }
        cur = prev = act = act_next = -1, blend = 1.0f, act_w = 0;
        std::fill(std::begin(phase), std::end(phase), 0.0f);
        a.assign((size_t)nj * kXf, 0.0f), b = a, c = a, rows.assign((size_t)nj * 12, 0.0f);
        cur = locomotion();
        return nj > 0 && cur >= 0;
    }

    int tagged(uint32_t tag) const {
        for (int i = 0; i < (int)clips.size(); ++i)
            if (clips[i].tag == tag && clips[i].kind == kAction) return i;
        return -1;
    }

    // the set's standing or moving motion for the hunter's speed (the peace set's if the battle set has none)
    int locomotion() const {
        auto pick = [&](uint32_t set) {
            int stand = -1, best = -1;
            float bd = 1e9f;
            for (int i = 0; i < (int)clips.size(); ++i) {
                const ModelClip& k = clips[i];
                if (k.set != set) continue;
                if (k.kind == kStand && stand < 0) stand = i;
                if (k.kind == kMove && moving && k.speed > 0) {
                    float d = std::fabs(std::log(speed / k.speed));
                    if (d < bd) bd = d, best = i;
                }
            }
            return best >= 0 ? best : stand;
        };
        int w = battle ? pick(kBattle) : -1;
        return w >= 0 ? w : pick(kPeace);
    }

    // an action once, then next looped (kNone: none); clip kNone ends the one playing
    void request(uint32_t clip, uint32_t next) {
        bool ok = clip < clips.size() && clips[clip].kind == kAction;
        if (!ok) {
            if (act >= 0) act_out = true;
            act_next = -1;
            return;
        }
        act = (int)clip, act_phase = 0, act_loop = false, act_out = false;
        act_next = next < clips.size() && clips[next].kind == kAction ? (int)next : -1;
    }

    // Monster Hunter's hunter drew or put away its weapon, or attacked
    void set_battle(bool on) {
        if (on == battle) return;
        battle = on;
        int t = tagged(on ? fourcc("draw") : fourcc("shea"));
        if (t >= 0 && !dead && (act < 0 || act_out)) request((uint32_t)t, kNone);
    }
    // Monster Hunter's hunter hit: a flinch, or knocked back (big: a fifth of its health or more)
    bool dead = false;
    void hurt(bool big) {
        if (dead) return;
        const char* tag = big ? (battle ? "bkno" : "knoc") : (battle ? "bhit" : "hit ");
        int t = tagged(fourcc4(tag));
        if (t < 0 && big) t = tagged(fourcc4(battle ? "bhit" : "hit "));
        if (t >= 0) request((uint32_t)t, kNone);
    }
    // fainted: falling, then lying there until it's up again
    void set_dead(bool d) {
        if (d == dead) return;
        dead = d;
        if (d) {
            int fall = tagged(fourcc4(battle ? "bded" : "ded ")), lie = tagged(fourcc4(battle ? "bcor" : "cor "));
            if (fall >= 0) request((uint32_t)fall, lie >= 0 ? (uint32_t)lie : kNone);
        } else {
            request(kNone, kNone);
        }
    }
    static uint32_t fourcc4(const char* s) {
        return (uint32_t)(uint8_t)s[0] | (uint32_t)(uint8_t)s[1] << 8 | (uint32_t)(uint8_t)s[2] << 16 | (uint32_t)(uint8_t)s[3] << 24;
    }

    void attack() {
        if (dead) return;
        static const uint32_t tags[3] = {fourcc("at0 "), fourcc("at1 "), fourcc("at2 ")};
        for (int i = 0; i < 3; ++i) {
            int t = tagged(tags[(swing + i) % 3]);
            if (t >= 0) {
                swing = (swing + i + 1) % 3;
                request((uint32_t)t, kNone);
                return;
            }
        }
    }

    // one clip at phase 0..1: keyframes interpolated (rotations the short way, renormalised)
    void sample(int i, float ph, float* out) const {
        const ModelClip& k = clips[i];
        int last = (int)k.frames - 1;
        float pos = std::clamp(ph, 0.0f, 1.0f) * (float)last;
        int lo = std::min((int)pos, std::max(last - 1, 0)), hi = std::min(lo + 1, last);
        float f = pos - (float)lo;
        const float* p = &pool[k.first + (size_t)lo * nj * kXf];
        const float* q = &pool[k.first + (size_t)hi * nj * kXf];
        for (int j = 0; j < nj * kXf; j += kXf) mix(p + j, q + j, f, out + j);
    }

    static void mix(const float* p, const float* q, float f, float* out) {
        float d = p[0] * q[0] + p[1] * q[1] + p[2] * q[2] + p[3] * q[3], g = d < 0 ? -f : f, n = 0;
        for (int i = 0; i < 4; ++i) out[i] = p[i] * (1 - f) + q[i] * g, n += out[i] * out[i];
        n = n > 0 ? 1.0f / std::sqrt(n) : 1.0f;
        for (int i = 0; i < 4; ++i) out[i] *= n;
        for (int i = 4; i < kXf; ++i) out[i] = p[i] * (1 - f) + q[i] * f;
    }

    float advance(int i, float ph, float dt, float pace = 1.0f) const {
        const ModelClip& k = clips[i];
        return ph + dt * 30.0f * k.rate * pace / (float)std::max<uint32_t>(k.frames - 1, 1);
    }

    // dt seconds on, the hunter at x, z: each joint's 3x4 in the model's space, into rows
    void step(float dt, bool hunter, float x, float z) {
        if (hunter && have_last && dt > 0) {
            float v = std::hypot(x - last_x, z - last_z) / dt;
            if (v > 3000.0f) v = speed;                              // a jump to elsewhere (an area loaded)
            speed += (v - speed) * std::min(1.0f, dt / 0.15f);
        }
        if (!hunter) speed = 0;
        have_last = hunter, last_x = x, last_z = z;
        moving = moving ? speed > 15.0f : speed > 30.0f;
        if (moving && act >= 0 && !act_out && (act_loop || act_next >= 0) && !dead) act_out = true;   // moving ends a held one
        int want = locomotion();
        if (want >= 0 && want != cur) {
            bool stride = cur >= 0 && clips[cur].kind == kMove && clips[want].kind == kMove;
            phase[want] = stride ? phase[cur] : 0.0f;
            prev = cur, cur = want, blend = 0.0f;
        }
        for (int i : {cur, prev}) {
            if (i < 0) continue;
            float pace = clips[i].speed > 0 ? std::clamp(speed / clips[i].speed, 0.5f, 2.0f) : 1.0f;
            phase[i] = advance(i, phase[i], dt, pace);
            phase[i] -= std::floor(phase[i]);
        }
        blend = std::min(1.0f, blend + dt / 0.25f);
        if (blend >= 1.0f) prev = -1;
        if (act >= 0) {
            act_phase = advance(act, act_phase, dt);
            if (act_phase >= 1.0f) {
                if (act_loop) act_phase -= std::floor(act_phase);
                else if (act_next >= 0) act = act_next, act_next = -1, act_loop = true, act_phase = 0.0f;
                else act_phase = 1.0f, act_out = true;
            }
            act_w = act_out ? std::max(0.0f, act_w - dt / 0.2f) : std::min(1.0f, act_w + dt / 0.12f);
            if (act_out && act_w <= 0.0f) act = -1;
        }
        if (cur < 0) return;
        sample(cur, phase[cur], a.data());
        if (prev >= 0) {
            sample(prev, phase[prev], b.data());
            for (int j = 0; j < nj * kXf; j += kXf) mix(b.data() + j, a.data() + j, blend, a.data() + j);
        }
        if (act >= 0 && act_w > 0.0f) {
            sample(act, act_phase, c.data());
            for (int j = 0; j < nj * kXf; j += kXf)
                if (!std::isnan(c[j])) mix(a.data() + j, c.data() + j, act_w, a.data() + j);
        }
        chain();
    }

    // parents first: a joint's offset scaled by its parent's scale and turned by its rotation, rotations
    // chained, scales multiplied; its matrix the rotation times its scale, and its place
    void chain() {
        std::vector<float> rot((size_t)nj * 9), pos((size_t)nj * 3), scl((size_t)nj * 3);
        for (int i = 0; i < nj; ++i) {
            const float* t = &a[(size_t)i * kXf];
            float x = t[0], y = t[1], z = t[2], w = t[3];
            float r[9] = {1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
                          2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
                          2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)};
            float* R = &rot[(size_t)i * 9];
            float* P = &pos[(size_t)i * 3];
            float* S = &scl[(size_t)i * 3];
            int p = parents[i];
            if (p < 0 || p >= i) {
                std::memcpy(R, r, sizeof r);
                for (int k = 0; k < 3; ++k) P[k] = t[4 + k], S[k] = t[7 + k];
            } else {
                const float* PR = &rot[(size_t)p * 9];
                const float* PP = &pos[(size_t)p * 3];
                const float* PS = &scl[(size_t)p * 3];
                float o[3] = {PS[0] * t[4], PS[1] * t[5], PS[2] * t[6]};
                for (int k = 0; k < 3; ++k) {
                    P[k] = PP[k] + PR[k * 3] * o[0] + PR[k * 3 + 1] * o[1] + PR[k * 3 + 2] * o[2];
                    S[k] = PS[k] * t[7 + k];
                    for (int m = 0; m < 3; ++m)
                        R[k * 3 + m] = PR[k * 3] * r[m] + PR[k * 3 + 1] * r[3 + m] + PR[k * 3 + 2] * r[6 + m];
                }
            }
            float* out = &rows[(size_t)i * 12];
            for (int k = 0; k < 3; ++k) {
                for (int m = 0; m < 3; ++m) out[k * 4 + m] = R[k * 3 + m] * S[m];
                out[k * 4 + 3] = P[k];
            }
        }
    }
};
Animator g_anim;

// What Monster Hunter's hunter is doing: its body model's animation object (ScnMdl +0x124: a blend of
// up to six, +0x18 how many, +0x1C them, +0x20 their weights), the heaviest one's resource (+0x2C: a
// CHR0) and that's name: "co_0313" (common, the weapon away), "w01_0001" (sword and shield's, drawn),
// "w01_01xx" its attacks.
std::atomic<uint32_t> g_hunter_body{0};

bool hunter_motion(char out[16]) {
    uint32_t body = g_hunter_body.load(std::memory_order_relaxed);
    if (!body || !in_ram(body + 0x124, 4)) return false;
    uint32_t blend = ld32(body + 0x124);
    if (!in_ram(blend + 0x18, 12)) return false;
    uint32_t n = std::min<uint32_t>(ld32(blend + 0x18), 8), slots = ld32(blend + 0x1C), weights = ld32(blend + 0x20);
    if (!in_ram(slots, 4 * n) || !in_ram(weights, 4 * n)) return false;
    uint32_t best = 0;
    float bw = -1.0f;
    for (uint32_t i = 0; i < n; ++i) {
        uint32_t s = ld32(slots + 4 * i);
        float w = f32(weights + 4 * i);
        if (s && in_ram(s + 0x2C, 4) && w > bw) bw = w, best = s;
    }
    if (!best) return false;
    uint32_t res = ld32(best + 0x2C);
    if (!in_ram(res, 0x20) || ld32(res) != 0x43485230) return false;              // 'CHR0'
    uint32_t name = res + ld32(res + (ld32(res + 8) >= 5 ? 0x18 : 0x14));
    if (!in_ram(name, 16)) return false;
    for (int i = 0; i < 15; ++i) out[i] = (char)ld8(name + i);
    out[15] = 0;
    return out[0] != 0;
}

// "w01_0001": the weapon out; "w01_01xx": an attack
bool motion_battle(const char* m) { return m[0] == 'w' && std::isdigit((uint8_t)m[1]) && std::isdigit((uint8_t)m[2]); }
bool motion_attack(const char* m) { return motion_battle(m) && m[3] == '_' && m[4] == '0' && m[5] == '1'; }

std::mutex g_hunter_mx;
float g_hunter_mtx[12];                 // the hunter model's world matrix, rows
uint32_t g_hunter_stamp = 0;            // bumped as it is computed (once a frame while the hunter is in a scene)
std::atomic<bool> g_hide_hunter{false};
PPCFunc orig_scn_world;

// The hunter's model is a few NW4R scene models: its body and weapons standing where the hunter object
// stands, its armour's pieces (owned_by_hunter). Each computes its world matrix in ScnMdl::CalcWorldMtx (vtable slot 12: the
// parent's, its local +0xC, the extra +0xDC, into +0x3C). Right after, one at the hunter gives FFXI's
// character its place and facing, and is hidden by its draw flags (ScnObj +0xCC, mScnObjFlags:
// 0x20 no opaque draw, 0x40 no translucent draw, as ScnObj::SetScnObjOption sets them), its skeleton
// still computed: weapons and hits as they were.
constexpr uint32_t kNoDraw = 0x20 | 0x40;
std::vector<std::pair<uint32_t, uint32_t>> g_hidden;   // scene models we hid, with their vtables (guest thread only)

// x, y, z at a (each `stride` apart) within 2 units of the hunter object's position
bool at_hunter(uint32_t a, uint32_t stride, uint32_t pl) {
    if (!in_ram(a, 2 * stride + 4)) return false;
    float dx = f32(a) - f32(pl + 0x3C), dy = f32(a + stride) - f32(pl + 0x40), dz = f32(a + 2 * stride) - f32(pl + 0x44);
    return dx * dx + dy * dy + dz * dz <= 4.0f;
}

// The armour's pieces stand at the world's origin, posed through the body's skeleton by their callback
// (ScnMdl +0x11C: the game's record of the piece). That record names the model the piece belongs to
// (+8 for armour, +4 for a weapon), whose position is at its +4: a piece of the model standing where
// the hunter stands is the hunter's.
bool owned_by_hunter(uint32_t obj, uint32_t pl) {
    if (!in_ram(obj + 0x11C, 4)) return false;
    uint32_t cb = ld32(obj + 0x11C);
    if (!in_ram(cb, 12)) return false;
    for (uint32_t k : {4u, 8u})
        if (at_hunter(ld32(cb + k) + 4, 4, pl)) return true;
    return false;
}

// Monster Hunter's foot shadows: the common effects' model e_qc_000 (efm_com.bin, its texture e_qc_000_BM),
// one under each creature, a quad under each foot. The hunter's (those at its feet) go while the FFXI
// character stands in its place with its own round shadow (ModelBlock::shadow bit 1); /hunter, or
// character_shadow = "mh", gives them back. A monster's stay.
std::atomic<uint32_t> g_shadow{0};                   // ModelBlock::shadow, while the character is drawn
std::atomic<float> g_shadow_radius{0.0f};
struct HiddenShadow { uint32_t obj, vt, bits; };
std::vector<HiddenShadow> g_hidden_shadows;

bool model_named(uint32_t obj, const char* want) {
    if (!in_ram(obj + 0xE8, 4)) return false;
    uint32_t mdl = ld32(obj + 0xE8);
    if (!in_ram(mdl, 0x50) || ld32(mdl) != 0x4D444C30) return false;              // 'MDL0'
    uint32_t sections = ld32(mdl + 8) >= 10 ? 14 : 11;
    uint32_t at = mdl + ld32(mdl + 0x10 + 4 * sections);
    size_t n = std::strlen(want);
    if (!in_ram(at, (uint32_t)n + 1)) return false;
    for (size_t i = 0; i <= n; ++i)
        if (ld8(at + (uint32_t)i) != (uint8_t)want[i]) return false;
    return true;
}

void foot_shadow(uint32_t obj, uint32_t pl) {
    if (!in_ram(obj + 0x3C, 0x94)) return;
    float dx = f32(obj + 0x48) - f32(pl + 0x3C), dy = f32(obj + 0x58) - f32(pl + 0x40), dz = f32(obj + 0x68) - f32(pl + 0x44);
    bool hunters = dx * dx + dz * dz < 120.0f * 120.0f && std::fabs(dy) < 150.0f;
    static int told = 0;
    static bool seen = false;
    if (!seen) {
        seen = true;
        rt_log("ffxi: Monster Hunter's foot shadows found (model e_qc_000): the first at %.0f %.0f %.0f, the hunter at %.0f %.0f %.0f",
               f32(obj + 0x48), f32(obj + 0x58), f32(obj + 0x68), f32(pl + 0x3C), f32(pl + 0x40), f32(pl + 0x44));
    }
    if (told < 6 && hunters) {
        ++told;
        rt_log("ffxi: Monster Hunter's foot shadow under the hunter: scene model %08X, %.0f %.0f %.0f from it", obj, dx, dy, dz);
    }
    bool hide = hunters && g_hide_hunter.load(std::memory_order_relaxed) && (g_shadow.load(std::memory_order_relaxed) & 1);
    auto it = std::find_if(g_hidden_shadows.begin(), g_hidden_shadows.end(), [&](auto& h) { return h.obj == obj; });
    if (hide && it == g_hidden_shadows.end()) {
        if (g_hidden_shadows.size() >= 16) g_hidden_shadows.erase(g_hidden_shadows.begin());
        g_hidden_shadows.push_back({obj, ld32(obj), ld32(obj + 0xCC) & kNoDraw});
        st32(obj + 0xCC, ld32(obj + 0xCC) | kNoDraw);
    } else if (!hide && it != g_hidden_shadows.end()) {
        if (ld32(obj) == it->vt) st32(obj + 0xCC, (ld32(obj + 0xCC) & ~kNoDraw) | it->bits);
        g_hidden_shadows.erase(it);
    }
}

void hook_scn_world(PPCContext& c) {
    uint32_t obj = c.r[3];
    orig_scn_world(c);
    note_enemy(obj);                                         // a monster: FFXI's targets
    uint32_t pl = object(kBasePlayer);
    if (pl && model_named(obj, "e_qc_000")) {                // a foot shadow
        foot_shadow(obj, pl);
        return;
    }
    bool hide = g_hide_hunter.load(std::memory_order_relaxed);
    if (!hide && !g_hidden.empty()) {                         // shown again: our bits off
        for (auto [o, vt] : g_hidden)                        // still that object (not memory reused)
            if (in_ram(o, 0xD0) && ld32(o) == vt) st32(o + 0xCC, ld32(o + 0xCC) & ~kNoDraw);
        g_hidden.clear();
    }
    if (!pl || !in_ram(obj + 0x3C, 48)) return;
    if (at_hunter(obj + 0x48, 0x10, pl)) {                   // the body, the weapons: their place is the hunter's
        std::lock_guard<std::mutex> lk(g_hunter_mx);
        for (int i = 0; i < 12; ++i) g_hunter_mtx[i] = f32(obj + 0x3C + 4 * i);
        ++g_hunter_stamp;
        if (in_ram(obj + 0x124, 4) && in_ram(ld32(obj + 0x124), 0x24))   // the body: its animations
            g_hunter_body.store(obj, std::memory_order_relaxed);
    } else if (!owned_by_hunter(obj, pl)) {
        return;                                              // not the hunter
    }
    if (hide && in_ram(obj + 0xCC, 4) && (ld32(obj + 0xCC) & kNoDraw) != kNoDraw) {
        st32(obj + 0xCC, ld32(obj + 0xCC) | kNoDraw);
        if (std::none_of(g_hidden.begin(), g_hidden.end(), [&](auto& h) { return h.first == obj; })) {
            if (g_hidden.size() >= 64) g_hidden.erase(g_hidden.begin());
            g_hidden.emplace_back(obj, ld32(obj));
            if (g_hidden.size() <= 8) rt_log("ffxi: a piece of the hunter's model hidden: scene model %08X", obj);
        }
    }
}

// 3x4 rows, as 4x4 with a last row 0 0 0 1: out = a b
void mul34(const float* a, const float* b, float* out) {
    for (int r = 0; r < 3; ++r)
        for (int c = 0; c < 4; ++c)
            out[r * 4 + c] = a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c] + a[r * 4 + 2] * b[8 + c] + (c == 3 ? a[r * 4 + 3] : 0.0f);
}

// Each frame as the game finishes it (video_set_frame_hook, on the game's thread, before its XFB copy is
// recorded): the hunter's place and the camera are then what this frame showed, and the model is posed
// and recorded with the frame's own draws, so it keeps step with the scene however far behind the game
// the renderer runs. Shown only if the hunter's model was placed since the last frame (in a scene, not
// a loading screen or a fade between areas).
ModelBlock* g_model_block = nullptr;

// ================================================================================================
// FFXI's spell effects (ffxi/ffxi_fx.py, port_fx.py): the layer runs them (FFXI's own effect files, the
// casting and the spells landing) and sends their triangles, in the world, through their own block,
// "mh3-ffxi-fx", two slots it writes in turn; each frame the port hands the newest to the renderer with
// the frame's camera (video_fx_frame), which draws them into the scene after the character. The port
// writes, each frame, where things are: the camera, the hunter, the target.
// ================================================================================================
constexpr uint32_t kFxMagic = 0x4633484D, kFxVersion = 1;            // 'MH3F'
constexpr int kFxMaxVerts = 60000, kFxVertFloats = 10, kFxMaxLayers = 128, kFxTex = 64;
struct FxBlock {
    uint32_t magic, version;
    uint32_t alive;                 // layer: bumped while it runs (3 s without: nothing drawn)
    uint32_t tex_serial;            // layer: bumped once tex and tex_layers are written
    uint32_t tex_layers;
    uint32_t seq;                   // layer: bumped once a frame's triangles are in slot `front`
    uint32_t front;
    uint32_t counts[2][8];          // layer: each slot's corners by blending (opaque, alpha, added, subtracted, darkened)
    uint32_t scene_seq;             // port: bumped each frame
    uint32_t scene_ok;              // port: 1 when the camera and the hunter are known
    uint32_t target_ok;             // port: 1 when there's a target
    float camera[12];               // port: the world to the camera's space, 3x4 rows
    float hunter[12];               // port: the hunter's world matrix, 3x4 rows (its turn, its place)
    float target[4];                // port: the target's feet x y z, its height
    uint32_t spare[10];
    float verts[2][kFxMaxVerts * kFxVertFloats];
    uint8_t tex[kFxMaxLayers * kFxTex * kFxTex * 4];
};
static_assert(sizeof(FxBlock) == 6897408, "ffxi/port_fx.py mirrors this layout");
FxBlock* g_fx_block = nullptr;

void fx_frame(uint32_t pl, uint32_t cam) {
    FxBlock* f = g_fx_block;
    if (!f) return;
    static uint32_t alive = 0, tex_seen = 0;
    static auto last_alive = std::chrono::steady_clock::time_point{};
    auto now = std::chrono::steady_clock::now();
    if (f->alive != alive) { alive = f->alive; last_alive = now; }
    bool up = now - last_alive < std::chrono::seconds(3);
    float view[12] = {}, proj[6] = {};
    bool ok = pl && cam && in_ram(cam, 0x70);
    if (ok) {
        for (int i = 0; i < 12; ++i) view[i] = f32(cam + 4 * i);
        const int pi[6] = {0, 2, 5, 6, 10, 11};               // GX's six: P00 P02 P11 P12 P22 P23
        for (int i = 0; i < 6; ++i) proj[i] = f32(cam + 0x30 + 4 * pi[i]);
        std::memcpy(f->camera, view, sizeof view);
        std::lock_guard<std::mutex> lk(g_hunter_mx);
        std::memcpy(f->hunter, g_hunter_mtx, sizeof f->hunter);
    }
    f->target_ok = g_target_ok ? 1 : 0;
    std::memcpy(f->target, g_target_place, sizeof f->target);
    f->scene_ok = ok ? 1 : 0;
    f->scene_seq = f->scene_seq + 1;
    if (!ok) return;
    uint32_t counts[5] = {}, n = 0;
    const float* verts = nullptr;
    if (up) {
        if (f->tex_serial != tex_seen) {
            std::atomic_thread_fence(std::memory_order_acquire);
            tex_seen = f->tex_serial;
            video_fx_textures(f->tex, (int)std::min<uint32_t>(f->tex_layers, kFxMaxLayers), kFxTex, kFxTex);
        }
        uint32_t front = f->front & 1;
        std::atomic_thread_fence(std::memory_order_acquire);
        for (int i = 0; i < 5; ++i) {
            counts[i] = std::min<uint32_t>(f->counts[front][i], (uint32_t)kFxMaxVerts - n);
            n += counts[i];
        }
        verts = f->verts[front];
    }
    if (n) video_fx_frame(view, proj, verts, counts);
}

// The layer's companions (FFXI's Trusts), each frame: its model block opened once the layer has made it
// (looked for every half second while it's to be shown), its model handed to the renderer when it
// changes (slot 1 to 5), then drawn where the layer has it standing: eased there (the layer moves it a
// few times a frame or less: here it glides), its motions as the hunter's are chosen (standing, walking
// or running by its speed over the ground; the layer's actions; its battle set; fallen).
constexpr int kCompanions = 6;
struct CompanionState {
    ModelBlock* mb = nullptr;
    Animator anim;
    uint32_t shown = 0, act_seen = 0;
    bool loaded = false, placed = false;
    float x = 0, y = 0, z = 0, yaw = 0;
};
CompanionState g_companions[kCompanions];

void companions_frame(float dt, uint32_t pl, uint32_t cam, bool scene) {
    Block* b = g_block;
    if (!b) return;
    static int tick = 0;
    ++tick;
    float camera[12] = {}, proj[6] = {};
    bool ok = scene && pl && cam && in_ram(cam, 0x70);
    if (ok) {
        for (int i = 0; i < 12; ++i) camera[i] = f32(cam + 4 * i);
        const int pi[6] = {0, 2, 5, 6, 10, 11};                // GX's six: P00 P02 P11 P12 P22 P23
        for (int i = 0; i < 6; ++i) proj[i] = f32(cam + 0x30 + 4 * pi[i]);
    }
    for (int s = 1; s < kCompanions; ++s) {
        CompanionState& c = g_companions[s];
        const Block::Companion& a = b->companions[s];
        if (!c.mb) {
            if (!a.shown || tick % 30) continue;
            wchar_t tag[32];
            std::swprintf(tag, 32, L"mh3-ffxi-model-%d", s);
            HANDLE h = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, tag);
            if (!h) continue;
            c.mb = static_cast<ModelBlock*>(MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(ModelBlock)));
            if (!c.mb) continue;
        }
        ModelBlock* mb = c.mb;
        if (mb->magic != kModelMagic || mb->version != kModelVersion) continue;
        if (mb->serial != c.shown) {
            std::atomic_thread_fence(std::memory_order_acquire);
            c.shown = mb->serial;
            c.anim = Animator{};
            c.loaded = c.anim.load(mb);
            c.act_seen = mb->act_seq;
            uint32_t n = std::min<uint32_t>(mb->n_verts, kModelMaxVerts);
            if (c.loaded && mb->tex_w == (uint32_t)kModelTex && mb->tex_h == (uint32_t)kModelTex)
                video_model_mesh(mb->verts, (int)n, mb->tex, (int)std::min<uint32_t>(mb->layers, kModelMaxLayers),
                                 kModelTex, kModelTex, s);
            rt_log("ffxi: companion %d: %u corners, %d joints, %zu motions%s", s, n, c.anim.nj, c.anim.clips.size(),
                   c.loaded ? "" : " (not drawn: no joints or motions)");
        }
        if (!a.shown || !c.loaded || !ok) {
            c.placed = false;
            continue;
        }
        if (!c.placed) {                                       // where it appears: there at once
            c.x = a.x, c.y = a.y, c.z = a.z, c.yaw = a.yaw;
            c.placed = true;
        } else {
            float k = 1.0f - std::exp(-dt * 12.0f);
            float dx = a.x - c.x, dz = a.z - c.z;
            if (dx * dx + dz * dz > 600.0f * 600.0f) k = 1.0f;   // far off (it was sent there): at once
            c.x += dx * k, c.y += (a.y - c.y) * k, c.z += dz * k;
            float dyaw = std::remainder(a.yaw - c.yaw, 6.2831853f);
            c.yaw += dyaw * std::min(1.0f, k * 1.5f);
        }
        if (mb->act_seq != c.act_seen) {
            c.act_seen = mb->act_seq;
            c.anim.request(mb->act_clip, mb->act_next);
        }
        c.anim.set_battle(a.battle != 0);
        c.anim.set_dead(a.dead != 0);
        c.anim.step(dt, true, c.x, c.z);
        mb->battle = c.anim.battle ? 1 : 0;
        float cy = std::cos(c.yaw), sy = std::sin(c.yaw);
        const float w[12] = {cy, 0, sy, c.x, 0, 1, 0, c.y, -sy, 0, cy, c.z};
        float wp[12], view[12];
        mul34(w, c.anim.place, wp);                            // the model's space to its place, to the world's
        mul34(camera, wp, view);                               // then the camera's
        video_model_frame(view, proj, true, c.anim.rows.data(), c.anim.nj, camera, s);
        video_model_shadow(a.shadow_radius, a.shadow_dark, c.anim.place[7], s);
    }
}

void model_frame() {
    ModelBlock* mb = g_model_block;
    static uint32_t shown = 0, alive = 0, stamp = 0, act_seen = 0;
    static bool loaded = false;
    static char last_motion[16] = {};
    static auto last_alive = std::chrono::steady_clock::time_point{}, last_frame = std::chrono::steady_clock::now();
    auto now = std::chrono::steady_clock::now();
    // the time in whole fields of the 60 Hz picture (a frame of the game is two at 30 a second): steady,
    // where the clock's own reading wavers by a few milliseconds
    float fields = std::round(std::chrono::duration<float>(now - last_frame).count() * 60.0f);
    float dt = std::clamp(fields, 1.0f, 6.0f) / 60.0f;
    last_frame = now;
    if (mb->alive != alive) { alive = mb->alive; last_alive = now; }
    bool up = now - last_alive < std::chrono::seconds(3);
    if (mb->serial != shown) {
        std::atomic_thread_fence(std::memory_order_acquire);
        shown = mb->serial;
        uint32_t n = std::min<uint32_t>(mb->n_verts, kModelMaxVerts);
        uint32_t layers = std::min<uint32_t>(mb->layers, kModelMaxLayers);
        loaded = g_anim.load(mb);
        act_seen = mb->act_seq;                               // a request made before is not played
        if (loaded && mb->tex_w == (uint32_t)kModelTex && mb->tex_h == (uint32_t)kModelTex)
            video_model_mesh(mb->verts, (int)n, mb->tex, (int)layers, kModelTex, kModelTex);
        rt_log("ffxi: a character: %u corners, %d joints, %zu motions%s", n, g_anim.nj, g_anim.clips.size(),
               loaded ? "" : " (not drawn: no joints or motions)");
    }
    g_hide_hunter.store(up && loaded && (mb->flags & 2), std::memory_order_relaxed);
    g_shadow.store(up && loaded && (mb->flags & 1) ? mb->shadow : 0, std::memory_order_relaxed);
    g_shadow_radius.store(mb->shadow_radius, std::memory_order_relaxed);
    {
        uint32_t tp = object(kBasePlayer), tc = follow_camera(tp);
        target_frame(tp, tc, dt);                             // FFXI's target, and the lock-on
        fx_frame(tp, tc);                                     // FFXI's effects
        static uint32_t comp_stamp = 0;                       // the hunter placed since: a scene, not a loading screen
        bool placed;
        {
            std::lock_guard<std::mutex> lk(g_hunter_mx);
            placed = g_hunter_stamp != comp_stamp;
            comp_stamp = g_hunter_stamp;
        }
        companions_frame(dt, tp, tc, placed);                 // FFXI's Trusts
    }
    if (!loaded) return;
    float view[12] = {}, proj[6] = {}, h[12], camera[12] = {};
    bool fresh;
    {
        std::lock_guard<std::mutex> lk(g_hunter_mx);
        fresh = g_hunter_stamp != stamp;
        stamp = g_hunter_stamp;
        std::memcpy(h, g_hunter_mtx, sizeof h);
    }
    uint32_t pl = object(kBasePlayer), cam = follow_camera(pl);
    bool visible = up && (mb->flags & 1) && fresh && cam && pl;
    if (mb->act_seq != act_seen) {                            // the layer's request: a spell's casting
        act_seen = mb->act_seq;
        g_anim.request(mb->act_clip, mb->act_next);
    }
    static uint32_t hit_seen = 0;
    if (uint32_t hs = g_hit_seq.load(std::memory_order_relaxed); hs != hit_seen) {
        hit_seen = hs;
        if (pl) g_anim.hurt(g_hit_big.load(std::memory_order_relaxed) != 0);
    }
    if (pl) g_anim.set_dead((int16_t)ld16(pl + 0x370) <= 0);
    char motion[16] = {};
    bool attacking = g_block && (g_block->engage & 1);        // FFXI's Attack: the weapon out
    if (fresh && pl && hunter_motion(motion)) {              // what Monster Hunter's hunter is doing
        g_anim.set_battle(motion_battle(motion) || attacking);
        if (motion_attack(motion) && std::strcmp(motion, last_motion) != 0) {
            g_anim.attack();
            mb->swings = mb->swings + 1;
        }
        std::memcpy(last_motion, motion, sizeof motion);
        std::memcpy(mb->motion, motion, sizeof motion);
    }
    if (attacking) g_anim.set_battle(true);
    mb->battle = g_anim.battle ? 1 : 0;
    video_model_shading(mb->light_gain, mb->ground_shade);    // (0: unlit)
    g_anim.step(dt, pl != 0, pl ? f32(pl + 0x3C) : 0.0f, pl ? f32(pl + 0x44) : 0.0f);
    if (visible) {
        float v[12], hp[12];
        for (int i = 0; i < 12; ++i) camera[i] = v[i] = f32(cam + 4 * i);
        mul34(h, g_anim.place, hp);                           // the model's space to the hunter's, to the world's
        mul34(v, hp, view);                                   // then the camera's
        const int pi[6] = {0, 2, 5, 6, 10, 11};               // GX's six: P00 P02 P11 P12 P22 P23
        for (int i = 0; i < 6; ++i) proj[i] = f32(cam + 0x30 + 4 * pi[i]);
        static bool told = false;
        if (!told) {
            told = true;
            rt_log("ffxi: the character's origin in the camera's space: %.1f %.1f %.1f; projection %g %g %g %g %g %g",
                   view[3], view[7], view[11], proj[0], proj[1], proj[2], proj[3], proj[4], proj[5]);
        }
    }
    video_model_frame(view, proj, visible, g_anim.rows.data(), g_anim.nj, camera);   // camera: lit as the game lights
    // FFXI's round shadow (ModelBlock::shadow bit 2), lying on the ground the renderer finds under the feet
    uint32_t sh = g_shadow.load(std::memory_order_relaxed);
    video_model_shadow((sh & 2) ? g_shadow_radius.load(std::memory_order_relaxed) : 0.0f, (float)(sh >> 8 & 0xFF) / 255.0f,
                       g_anim.place[7]);
}

void serve(Block* b, OverlayBlock* o, ModelBlock* mb) {
    for (;;) {
        if (o) serve_overlay(o);
        for (int i = 0; i < kWatches; ++i) {
            Watch w;
            std::memcpy(&w, (const void*)&b->watch[i], sizeof w);
            Result& r = b->result[i];
            uint32_t a = 0;
            bool ok = w.addr && resolve(w, a);
            bool pending = w.write_seq != r.write_ack;
            if (ok && pending) wr(a, w.size, w.write_value);
            r.resolved = ok ? a : 0;
            r.value = ok ? rd(a, w.size) : 0;
            r.valid = ok;
            // the value after the write before the ack: the layer reads the ack first, then the value
            std::atomic_thread_fence(std::memory_order_release);
            if (pending) r.write_ack = w.write_seq;              // applied, or refused while unresolved
        }
        if (b->snap_seq != b->snap_ack) {
            char path[sizeof b->snap_path];
            std::memcpy(path, b->snap_path, sizeof path);
            path[sizeof path - 1] = 0;
            bool ok = snapshot(path);
            if (!ok) rt_log("ffxi: snapshot to %s failed", path);
            b->snap_ok = ok;
            std::atomic_thread_fence(std::memory_order_release);
            b->snap_ack = b->snap_seq;
        }
        project_name(b);
        serve_music(b, g_layer_up.load(std::memory_order_relaxed));
        if (!g_layer_up.load(std::memory_order_relaxed)) {
            std::lock_guard<std::mutex> lk(g_chat_mx);
            if (g_chat_open) {
                g_chat_open = false, g_chat.clear();
                video_text_capture(false);
                chat_publish(b);
            }
        }
        serve_map(b);
        b->player_obj = g_player.load(std::memory_order_relaxed);
        b->monster_obj = object(kBaseMonster);
        b->monster_delta = g_delta.load(std::memory_order_relaxed);
        b->monster_hits = g_hits.load(std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_release);
        b->heartbeat = b->heartbeat + 1;
        std::this_thread::sleep_for(std::chrono::milliseconds(16));
    }
}

void install() {
    const char* on = std::getenv("MH3_FFXI");
    if (!on || std::strcmp(on, "1")) return;
    HANDLE h = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(Block), L"mh3-ffxi");
    if (!h) { rt_log("ffxi: no shared block (error %lu)", GetLastError()); return; }
    auto* b = static_cast<Block*>(MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Block)));
    if (!b) { rt_log("ffxi: shared block not mapped (error %lu)", GetLastError()); return; }
    orig_em_hp = ppc_hook("mh3_EmHpAdd", hook_em_hp);
    orig_pl_hp = ppc_hook("mh3_PlHpAdd", hook_pl_hp);
    orig_master = ppc_hook("Pl_master_ck__FP4_PLW", hook_master);
    orig_set_perspective = ppc_hook("SetPerspective__Q34nw4r3g3d6CameraFffff", hook_set_perspective);
    if (const char* h = std::getenv("MH3_HIDE_HUD"); h && !std::strcmp(h, "1")) {
        orig_cockpit = ppc_hook("mh3_CockpitDraw", hook_cockpit);
        rt_log("ffxi: Monster Hunter's HUD hidden (MH3_HIDE_HUD=1)");
    }
    if (const char* t = std::getenv("MH3_RES_TRACE"); t && !std::strcmp(t, "1"))
        orig_add_resource = ppc_hook("nwAddResource__FPcPv", hook_add_resource);
    if (const char* t = std::getenv("MH3_HUD_TRACE"); t && !std::strcmp(t, "1")) {
        orig_font_ex = ppc_hook("font_print_ex__FsssPSce", hook_font_ex);
        orig_font = ppc_hook("font_print__FPSce", hook_font);
        orig_item_name = ppc_hook("ItemName__FUs", hook_item_name);
    }
    if (!orig_em_hp || !orig_pl_hp || !orig_master)
        rt_log("ffxi: a hook is missing (tools/mh3-hooks.txt): the player and monster objects won't be known");
    // The layer may have made the block first and written its watches: keep them.
    b->version = kVersion;
    b->magic = kMagic;
    rt_log("ffxi: serving guest memory to the FFXI layer (shared block mh3-ffxi)");
    g_block = b;
    wpad_set_classic_filter(classic_filter);
    video_set_key_hook(chat_key);
    OverlayBlock* o = nullptr;
    if (HANDLE oh = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(OverlayBlock),
                                       L"mh3-ffxi-overlay")) {
        o = static_cast<OverlayBlock*>(MapViewOfFile(oh, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(OverlayBlock)));
        if (o) {
            o->version = 1;
            o->magic = kOverlayMagic;
        }
    }
    if (!o) rt_log("ffxi: no overlay block: the FFXI interface won't show in the game");
    ModelBlock* mb = nullptr;
    if (HANDLE mh = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(ModelBlock),
                                       L"mh3-ffxi-model")) {
        mb = static_cast<ModelBlock*>(MapViewOfFile(mh, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(ModelBlock)));
        if (mb) {
            mb->version = kModelVersion;
            mb->magic = kModelMagic;
        }
    }
    orig_scn_world = ppc_hook("mh3_ScnMdlCalcWorldMtx", hook_scn_world);
    orig_play_stream = ppc_hook("PlayStream__FUlUl", hook_play_stream);
    orig_bgm_vol = ppc_hook("set_str_bgm_main_vol__Ff", hook_bgm_vol);
    orig_se_req = ppc_hook("fn_800D9D2C", hook_se_req);
    g_se_trace = std::getenv("MH3_SE_TRACE") && !std::strcmp(std::getenv("MH3_SE_TRACE"), "1");
    if (const char* m = std::getenv("MH3_MUTE_SE")) {
        for (const char* p = m; *p;) {
            char* end = nullptr;
            unsigned long kind = std::strtoul(p, &end, 10);
            if (end == p || *end != ':') { ++p; continue; }
            p = end + 1;
            unsigned long code = 0xFFFF;
            if (*p == '*') ++p;
            else {
                code = std::strtoul(p, &end, 16);
                if (end == p) continue;
                p = end;
            }
            g_mute_se.push_back((uint32_t)kind << 16 | ((uint32_t)code & 0xFFFF));
        }
        rt_log("ffxi: %zu of the hunter's sounds muted (MH3_MUTE_SE)", g_mute_se.size());
    }
    orig_cam_position = ppc_hook("SetPosition__Q34nw4r3g3d6CameraFRCQ34nw4r4math4VEC3", hook_cam_position);
    orig_cam_posture = ppc_hook("SetPosture__Q34nw4r3g3d6CameraFRCQ44nw4r3g3d6Camera11PostureInfo", hook_cam_posture);
    if (!orig_cam_position || !orig_cam_posture) rt_log("ffxi: the camera's hooks are missing (tools/mh3-hooks.txt): no lock-on");
    if (HANDLE fh = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(FxBlock),
                                       L"mh3-ffxi-fx")) {
        g_fx_block = static_cast<FxBlock*>(MapViewOfFile(fh, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(FxBlock)));
        if (g_fx_block) {
            g_fx_block->version = kFxVersion;
            g_fx_block->magic = kFxMagic;
        }
    }
    if (!g_fx_block) rt_log("ffxi: no effects block: FFXI's spell effects won't show");
    g_model_block = mb;
    if (mb) video_set_frame_hook(model_frame);
    std::thread(serve, b, o, mb).detach();
}

RtGameLayer layer("FFXI bridge", install);

}  // namespace
#endif
