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
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <thread>

namespace {

constexpr uint32_t kMagic = 0x4D483346;            // 'MH3F'
constexpr uint32_t kVersion = 3;
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
};
static_assert(sizeof(Block) == 752, "ffxi/port_bridge.py mirrors this layout");

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
PPCFunc orig_em_hp, orig_pl_hp, orig_master;
// MH3_HUD_TRACE=1: who draws text (font_print_ex, font_print) and item names (ItemName): each new
// caller once, with its call chain and its text, to find the code that draws the HUD.
PPCFunc orig_font_ex, orig_font, orig_item_name;
PPCFunc orig_cockpit;

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

bool resolve(const Watch& w, uint32_t& out) {
    uint32_t a = w.addr;
    if (w.base != kBaseAddr) {
        uint32_t obj = w.base == kBasePlayer ? g_player.load(std::memory_order_relaxed)
                     : w.base == kBaseMonster ? g_monster.load(std::memory_order_relaxed) : 0;
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
    static auto last_alive = std::chrono::steady_clock::now();
    static bool up = false;
    auto now = std::chrono::steady_clock::now();
    if (o->alive != alive) { alive = o->alive; last_alive = now; }
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
    if (const char* h = std::getenv("MH3_HIDE_HUD"); h && !std::strcmp(h, "1")) {
        orig_cockpit = ppc_hook("mh3_CockpitDraw", hook_cockpit);
        rt_log("ffxi: Monster Hunter's HUD hidden (MH3_HIDE_HUD=1)");
    }
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
