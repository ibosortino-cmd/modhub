"""Change the settings of a running native port the way its own in-game menu does (Windows only).

BT3 Recompiled reads settings.toml only at startup and takes no commands from outside. Its own menu
(PS2SettingsOverlay, Shift+Tab) keeps the settings in a struct, `m_settings`, and every frame checks a flag,
`m_dirty`: when it is set, the game applies the whole struct (PS2SettingsOverlay::applySettings) and writes it to
settings.toml when it closes. So ModHub does exactly what the menu does: it finds that object in the game's memory,
writes the new value into `m_settings` and sets `m_dirty`. The game applies it on its own thread, on its next frame,
and its own menu shows the new value too.

The object is recognised, not guessed: the values the game loaded from settings.toml sit at known places in it, a
copy of them follows ("the settings at boot"), and the path of settings.toml is in it. If that is not found exactly
once, nothing is written. For the volumes, the rumble and the dead zone there is a second way (the values the audio
mixer and the pad code read every frame, found from their starting values in the program file) that is also used
while a slider is being dragged, because it does not make the game re-apply (and log) all its settings.
"""
import ctypes
import os
import re
import struct
import threading
import time
from ctypes import wintypes
from pathlib import Path

PROCESS_VM_READ, PROCESS_VM_WRITE, PROCESS_VM_OPERATION, PROCESS_QUERY_INFORMATION = 0x10, 0x20, 0x8, 0x400
MEM_COMMIT, PAGE_GUARD = 0x1000, 0x100
_WRITABLE = (0x04, 0x08, 0x40, 0x80)  # read-write, write-copy (and their executable variants)
_WIN = os.name == "nt"


class _MBI(ctypes.Structure):  # MEMORY_BASIC_INFORMATION (64-bit)
    _fields_ = [("BaseAddress", ctypes.c_ulonglong), ("AllocationBase", ctypes.c_ulonglong), ("AllocationProtect", wintypes.DWORD),
                ("_a", wintypes.DWORD), ("RegionSize", ctypes.c_ulonglong), ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD), ("_b", wintypes.DWORD)]


if _WIN:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    _k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    _k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
    _k32.VirtualQueryEx.restype = ctypes.c_size_t
    _psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]

# ---- PS2SettingsOverlay (ps2_settings_overlay.h): {bool m_visible, m_initialized, m_dirty; Settings m_settings (+8);
# Settings m_settingsAtBoot (+0xB8); std::string m_configPath (+0x168)}. Offsets inside Settings, checked against the
# game's own code (its operator== and applySettings read exactly these).
SETTINGS_AT, BOOT_AT, PATH_AT, SETTINGS_SIZE = 0x8, 0xB8, 0x168, 0xB0
FIELDS = {  # settings key -> (offset in Settings, kind)
    "netplay.overlay": (0, "b"), "achievements.enabled": (1, "b"),
    "audio.master_volume": (4, "f"), "audio.music_volume": (8, "f"), "audio.sfx_volume": (12, "f"),
    "video.glow": (32, "b"), "video.bilinear": (34, "b"), "video.halftexel": (35, "b"), "video.skippost": (36, "b"),
    "video.skip_stale_vram": (37, "b"),
    "controllers.deadzone": (44, "f"), "controllers.rumble": (48, "b"), "controllers.rumble_strength": (52, "i"),
    "video.outline": (58, "b"), "video.texture_pack": (59, "b"), "video.fps60": (68, "b"), "video.show_perf": (69, "b"),
    "video.ink_strength": (72, "i"), "video.ink_width": (76, "i"), "video.ink_color": (80, "color"),
    "video.shadows": (84, "b"), "video.dof_blur": (85, "b"), "video.dof_zfar": (88, "i"),
    "video.force_bilinear": (100, "b"),
    "video.hud.layout": (104, "i"), "video.hud.offset_left": (108, "i"), "video.hud.offset_center": (112, "i"),
    "video.hud.offset_right": (116, "i"),
}
# What the menu itself only applies at the next start (renderer, render scale, glow fix, window, intro video, button
# style) is not here; neither is controllers.overlay_enabled: switched off, the menu stops looking at m_dirty at all.
ANCHOR = ("video.dof_zfar", "video.window_w", "video.window_h")  # three ints in a row (offsets 88, 92, 96): the needle

# ---- the values read every frame by the audio mixer and the pad code
VOLUME_KEYS = ("audio.master_volume", None, "audio.music_volume", "audio.sfx_volume")  # None = the transition mute
RUMBLE_KEYS = ("controllers.rumble", "controllers.rumble_strength")
DEADZONE_KEY = "controllers.deadzone"
DIRECT_KEYS = (*[k for k in VOLUME_KEYS if k], *RUMBLE_KEYS, DEADZONE_KEY)
PLAYERS, ACTIONS, BIND_SIZE, PLAYER_SIZE = 2, 24, 16, 8 + 24 * 16  # ps2_stubs::PadBind / PadPlayerConfig
KEYS = tuple(FIELDS)
MENU_RETRY = 10.0  # seconds before searching again for the menu object (it only exists once the game has started)
_SIGN = re.compile(re.escape(struct.pack('<f', 1.0)) + b'|' + re.escape(struct.pack('<f', -1.0)))


# ------------------------------------------------------------------ the program file: where the values start out

def sections(blob):
    """{name: (virtual address, virtual size, file offset, file size)} of a PE file."""
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    if blob[pe:pe + 4] != b"PE\0\0":
        raise ValueError("non è un programma Windows")
    count = struct.unpack_from("<H", blob, pe + 6)[0]
    first = pe + 24 + struct.unpack_from("<H", blob, pe + 20)[0]
    out = {}
    for i in range(count):
        name, vsize, va, rsize, rptr = struct.unpack_from("<8sIIII", blob, first + 40 * i)
        out[name.rstrip(b"\0").decode("ascii", "replace")] = (va, vsize, rptr, rsize)
    return out


def _unique(blob, pattern, step=4):
    hits, i = [], blob.find(pattern)
    while i != -1:
        if i % step == 0:
            hits.append(i)
        i = blob.find(pattern, i + 1)
    return hits[0] if len(hits) == 1 else None


def locate(exe_path):
    """Addresses (relative to the program's load address) of the volumes and the rumble settings, from their
    initial values in the program file: 1, 1, 1, 0.4 and true, 100. None where there is not exactly one match."""
    blob = Path(exe_path).read_bytes()
    try:
        va, vsize, raw, rsize = sections(blob)[".data"]
    except (struct.error, KeyError) as e:
        raise ValueError(f"programma non riconosciuto: {e}") from e
    data = blob[raw:raw + rsize]
    volumes = _unique(data, struct.pack("<4f", 1.0, 1.0, 1.0, 0.4))
    rumble = None
    hits = [o for o in range(0, len(data) - 8, 4) if data[o] == 1 and data[o + 4:o + 8] == b"\x64\0\0\0"]
    if len(hits) == 1:
        rumble = hits[0]
    return {"volumes": None if volumes is None else va + volumes, "rumble": None if rumble is None else va + rumble,
            "data": (va, vsize)}


# ------------------------------------------------------------------ the running game

class Process:
    """Read / write another process's memory (the game ModHub started)."""

    def __init__(self, pid):
        access = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION
        self.handle = _k32.OpenProcess(access, False, pid) if _WIN else None
        if not self.handle:
            raise OSError("impossibile accedere al gioco")

    def base(self):
        mods, needed = (ctypes.c_void_p * 1)(), wintypes.DWORD()
        if not _psapi.EnumProcessModulesEx(self.handle, mods, ctypes.sizeof(mods), ctypes.byref(needed), 0x3):
            raise OSError("impossibile leggere il gioco")
        return mods[0]

    def regions(self):
        """(start, size) of every committed, writable block of the game's memory."""
        address, info = 0, _MBI()
        while _k32.VirtualQueryEx(self.handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)):
            if info.State == MEM_COMMIT and info.Protect in _WRITABLE and not info.Protect & PAGE_GUARD:
                yield info.BaseAddress, info.RegionSize
            address = info.BaseAddress + info.RegionSize
            if address >= 1 << 47:
                break

    def read(self, address, size):
        buf, done = ctypes.create_string_buffer(size), ctypes.c_size_t()
        if not _k32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(done)) or done.value != size:
            raise OSError("lettura non riuscita")
        return buf.raw

    def write(self, address, data):
        done = ctypes.c_size_t()
        if not _k32.WriteProcessMemory(self.handle, ctypes.c_void_p(address), data, len(data), ctypes.byref(done)) or done.value != len(data):
            raise OSError("scrittura non riuscita")

    def close(self):
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None


def _config_path(read, obj):
    """m_configPath of the menu object at `obj` (MSVC std::string: 16 bytes inline or a pointer, then size, capacity)."""
    raw = read(obj + PATH_AT, 32)
    size, cap = struct.unpack_from("<QQ", raw, 16)
    if size > 1024 or cap < size:
        return None
    text = raw[:size] if cap < 16 else read(struct.unpack_from("<Q", raw, 0)[0], size)
    try:
        return text.decode("utf-8")
    except UnicodeDecodeError:
        return None


def is_menu_object(read, obj, needle):
    """Does `obj` look exactly like the game's PS2SettingsOverlay?"""
    try:
        head = read(obj, BOOT_AT + SETTINGS_SIZE)
        if head[0] > 1 or head[1] != 1 or head[2] > 1:  # m_visible, m_initialized (true once running), m_dirty
            return False
        if head[BOOT_AT + 88:BOOT_AT + 100] != needle:  # the boot copy holds the same window size / blur reach
            return False
        path = _config_path(read, obj)
        return bool(path) and path.replace("\\", "/").lower().endswith("/settings.toml")
    except (OSError, struct.error):
        return False


def find_menu_object(proc, needle, chunk=8 << 20):
    """Search the game's writable memory for the menu object; its address if found exactly once."""
    found = set()
    for start, size in proc.regions():
        at = start
        while at < start + size:
            length = min(chunk + 16, start + size - at)
            try:
                blob = proc.read(at, length)
            except OSError:
                break
            i = blob.find(needle)
            while i != -1:
                obj = at + i - SETTINGS_AT - 88
                if obj % 8 == 0 and is_menu_object(proc.read, obj, needle):
                    found.add(obj)
                i = blob.find(needle, i + 1)
            at += chunk
    return found.pop() if len(found) == 1 else None


def find_binds(read, start, size, chunk=1 << 20):
    """Address of PadConfig::m_players: two blocks of 24 bindings whose dead zones (and only those) look right.

    `read(address, size)` returns bytes or raises OSError. Each PadBind is {u8 kind, i32 value, f32 sign, f32 dead
    zone}; every binding of every player has the same dead zone (the game copies the settings value into all of them)."""
    found = []
    for at in range(start, start + size, chunk):
        try:
            blob = read(at, min(chunk + PLAYER_SIZE * PLAYERS, start + size - at))
        except OSError:
            continue
        limit = min(chunk, len(blob) - PLAYER_SIZE * PLAYERS)
        # the first binding's sign (+1 or -1) sits 16 bytes in: only look where there is one
        for m in _SIGN.finditer(blob):
            off = m.start() - 16
            if 0 <= off < limit and off % 4 == 0 and _players_at(blob, off) is not None:
                found.append(at + off)
    return found[0] if len(set(found)) == 1 else None


def _players_at(blob, off):
    dz = None
    for p in range(PLAYERS):
        device = off + p * PLAYER_SIZE
        if blob[device] > 2:  # PadDeviceKind
            return None
        for a in range(ACTIONS):
            b = device + 8 + a * BIND_SIZE
            kind = blob[b]
            sign, dead = struct.unpack_from("<ff", blob, b + 8)
            if kind > 3 or sign not in (1.0, -1.0) or not 0.0 <= dead <= 0.5:
                return None
            if dz is None:
                dz = dead
            elif dead != dz:
                return None
    return dz


def pack(kind, value):
    if kind == "b":
        return bytes([1 if value else 0])
    if kind == "f":
        return struct.pack("<f", float(value))
    if kind == "color":
        text = str(value).lstrip("#")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", text):
            raise ValueError("colore non valido")
        return struct.pack("<I", int(text, 16))
    if isinstance(value, bool):
        raise ValueError("numero atteso")
    return struct.pack("<i", int(value))


class GameMemory:
    """Live settings of one running BT3. `set(key, value)` returns True if the game now uses the value.

    `file_values()` gives the values in settings.toml (what the game loaded): they are how its menu object is found."""

    def __init__(self, pid, exe_path, locations=None, file_values=None):
        self.pid, self.exe = pid, Path(exe_path)
        self._loc, self._proc, self._base, self._binds = locations, None, None, None
        self._file_values = file_values or (lambda: {})
        self._menu, self._menu_next = None, 0.0
        self.lock = threading.Lock()

    @staticmethod
    def handles(key):
        return key in FIELDS

    def _open(self):
        if self._proc is None:
            self._loc = self._loc or locate(self.exe)
            self._proc = Process(self.pid)
            self._base = self._proc.base()

    def set(self, key, value, preview=False):
        """Apply `value`. With `preview` (a slider being dragged) the volumes / rumble / dead zone go straight to the
        values the game reads every frame, without making it re-apply all its settings."""
        if key not in FIELDS:
            return False
        with self.lock:
            try:
                self._open()
                if preview and key in DIRECT_KEYS and self._set_direct(key, value):
                    return True
                if self._set_in_menu(key, value):
                    return True
                return key in DIRECT_KEYS and self._set_direct(key, value)
            except (OSError, ValueError, KeyError, TypeError, struct.error):
                return False

    # ---- like the game's own menu

    def _menu_object(self):
        if self._menu is not None:
            needle = self._proc.read(self._menu + BOOT_AT + 88, 12)
            if is_menu_object(self._proc.read, self._menu, needle):
                return self._menu
            self._menu = None  # gone or reshaped: look again
        if time.monotonic() < self._menu_next:
            return None  # searched a moment ago: the game is probably still on its start menu
        self._menu_next = time.monotonic() + MENU_RETRY
        values = self._file_values()
        try:
            needle = struct.pack("<3i", *(int(values[k]) for k in ANCHOR))
        except (KeyError, TypeError, ValueError, struct.error):
            return None
        self._menu = find_menu_object(self._proc, needle)
        return self._menu

    def prepare(self):
        """Find the game's settings ahead of time (the search takes a couple of seconds the first time)."""
        with self.lock:
            try:
                self._open()
                self._menu_object()
            except (OSError, ValueError, KeyError, TypeError, struct.error):
                pass

    def _set_in_menu(self, key, value):
        obj = self._menu_object()
        if obj is None:
            return False
        offset, kind = FIELDS[key]
        self._proc.write(obj + SETTINGS_AT + offset, pack(kind, value))
        self._proc.write(obj + 2, b"\x01")  # m_dirty: the game applies its settings on its next frame
        return True

    # ---- straight to what the audio mixer / pad code read

    def _set_direct(self, key, value):
        if key == DEADZONE_KEY:
            return self._set_deadzone(float(value))
        if key in RUMBLE_KEYS:
            return self._set_rumble(key, value)
        if key in VOLUME_KEYS:
            return self._set_volume(key, float(value))
        return False

    def _set_volume(self, key, value):
        if self._loc.get("volumes") is None or not 0.0 <= value <= 1.0:
            return False
        address = self._base + self._loc["volumes"]
        master, mute, music, sfx = struct.unpack("<4f", self._proc.read(address, 16))
        if mute not in (0.0, 1.0) or not all(0.0 <= v <= 1.0 for v in (master, music, sfx)):
            return False  # not what ModHub expects to find there: leave the game alone
        self._proc.write(address + 4 * VOLUME_KEYS.index(key), struct.pack("<f", value))
        return True

    def _set_rumble(self, key, value):
        if self._loc.get("rumble") is None:
            return False
        address = self._base + self._loc["rumble"]
        on, strength = struct.unpack("<B3xi", self._proc.read(address, 8))
        if on > 1 or not 0 <= strength <= 200:
            return False
        if key == "controllers.rumble":
            self._proc.write(address, bytes([1 if value else 0]))
        else:
            if isinstance(value, bool) or not 0 <= int(value) <= 200:
                return False
            self._proc.write(address + 4, struct.pack("<i", int(value)))
        return True

    def _set_deadzone(self, value):
        if not 0.0 <= value <= 0.5:
            return False
        if self._binds is None:
            va, vsize = self._loc["data"]
            self._binds = find_binds(self._proc.read, self._base + va, vsize)
            if self._binds is None:
                return False
        blob = self._proc.read(self._binds, PLAYER_SIZE * PLAYERS)
        if _players_at(blob, 0) is None:
            self._binds = None  # moved or changed shape: look again next time
            return False
        packed = struct.pack("<f", value)
        for p in range(PLAYERS):
            for a in range(ACTIONS):
                self._proc.write(self._binds + p * PLAYER_SIZE + 8 + a * BIND_SIZE + 12, packed)
        return True

    def close(self):
        with self.lock:
            if self._proc:
                self._proc.close()
                self._proc = None
