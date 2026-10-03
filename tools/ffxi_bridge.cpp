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
// Not synchronised with the game: a read may see a value mid-frame, and a
// write may be overwritten by the game's own read-modify-write, as with
// Dolphin Memory Engine. Phase 2 moves this onto a hooked per-frame function.
#include "rt.h"

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <thread>

namespace {

constexpr uint32_t kMagic = 0x4D483346;            // 'MH3F'
constexpr uint32_t kVersion = 2;
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
};
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
};

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

void serve(Block* b) {
    for (;;) {
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
    // The layer may have made the block first and written its watches: keep them.
    b->version = kVersion;
    b->magic = kMagic;
    rt_log("ffxi: serving guest memory to the FFXI layer (shared block mh3-ffxi)");
    std::thread(serve, b).detach();
}

RtGameLayer layer("FFXI bridge", install);

}  // namespace
#endif
