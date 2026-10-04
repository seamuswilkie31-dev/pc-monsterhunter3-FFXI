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
#include <cstring>
#include <mutex>
#include <thread>

namespace {

constexpr uint32_t kMagic = 0x4D483346;            // 'MH3F'
constexpr uint32_t kVersion = 5;
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
    uint32_t reserved[2];
    uint32_t events[32];                // port: the buttons newly pressed, a ring
    // Your nameplate, FFXI's name above your head: the hunter's head projected through the game's
    // 3D camera, in the picture's normalised coordinates (-1..1, y up).
    float name_x, name_y;               // port
    uint32_t name_ok;                   // port: 1 when the head is in front of the camera
    float name_height;                  // layer: the head's height above the hunter's feet (world units)
    uint32_t camera;                    // port: the 3D camera's CameraData, 0 until the game sets one
    uint32_t reserved2;
};
static_assert(sizeof(Block) == 936, "ffxi/port_bridge.py mirrors this layout");

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
void hook_pl_hp(PPCContext& c) {
    uint32_t obj = c.r[3];
    orig_pl_hp(c);
    g_player.store(obj, std::memory_order_relaxed);
}

// Pl_master_ck(PLW*): true for the local player's hunter.
void hook_master(PPCContext& c) {
    uint32_t obj = c.r[3];
    orig_master(c);
    if (c.r[3] & 0xFF) g_player.store(obj, std::memory_order_relaxed);
}

// The Classic's buttons (KPAD_CL_*, as video.cpp's).
enum : uint32_t { kClUp = 0x0001, kClLeft = 0x0002, kClX = 0x0008, kClA = 0x0010, kClY = 0x0020,
                  kClB = 0x0040, kClDown = 0x4000, kClRight = 0x8000 };
constexpr uint32_t kMenuButtons = kClUp | kClDown | kClLeft | kClRight | kClA | kClB | kClX | kClY;

Block* g_block = nullptr;
std::atomic<bool> g_layer_up{false};    // the layer bumped the overlay's alive within 3 s

// Channel 1's Classic, before the game reads it. Y is shared with the game, which sheathes the
// weapon and uses items with it: a tap reaches the game (at its release), held it opens the menu.
constexpr auto kHoldForMenu = std::chrono::milliseconds(350);

void classic_filter(int chan, ClassicState& s) {
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

uint32_t object(uint32_t base) {
    if (base == kBasePlayer) {
        uint32_t o = g_player.load(std::memory_order_relaxed);
        if (hunter_ok(o)) return o;
        g_player.compare_exchange_strong(o, 0);
    } else if (base == kBaseMonster) {
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

// The hunter's head (its position, +0x3C of its _PLW, raised) through the camera: view, then projection.
void project_name(Block* b) {
    uint32_t cam = g_camera.load(std::memory_order_relaxed), pl = object(kBasePlayer);
    b->camera = cam;
    if (!cam || !pl || !in_ram(cam, 0x70) || !in_ram(pl + 0x3C, 12)) { b->name_ok = 0; return; }
    float h = b->name_height > 0 && b->name_height < 10000 ? b->name_height : 180.0f;
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

void serve(Block* b, OverlayBlock* o) {
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
        b->player_obj = g_player.load(std::memory_order_relaxed);
        b->monster_obj = g_monster.load(std::memory_order_relaxed);
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
    std::thread(serve, b, o).detach();
}

RtGameLayer layer("FFXI bridge", install);

}  // namespace
#endif
