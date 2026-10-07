"""The in-game panel: a global hotkey and an always-on-top window (Windows only).

Unlike Steam's overlay this does not draw inside the game: ModHub opens its own small window on top of it.
The hotkey is registered only while a game started by ModHub is running, so Shift+Tab keeps working
normally in every other program the rest of the time.
"""
import ctypes
import os
import threading
from ctypes import wintypes

OVERLAY_TITLE = "ModHub Overlay"
# Shift+Tab is taken by BT3's own menu and by PCSX2's slow motion, so ModHub starts with Ctrl+Shift+Tab: Windows itself
# does not use it (it only means "previous tab" inside some programs) and the plain Tab never reaches the game
# (and if another program already owns it, ModHub tries the next one by itself).
DEFAULT_HOTKEY = "Ctrl+Shift+Tab"
HOTKEY_PRESETS = ["Ctrl+Shift+Tab", "Ctrl+Alt+O", "Ctrl+Shift+F10", "Ctrl+Alt+M", "Shift+Tab"]
_MODIFIERS = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}


def parse_hotkey(text):
    """'Ctrl+Alt+M' -> (modifier flags, virtual-key code, normalised label). Raises ValueError for anything odd."""
    parts = [p.strip() for p in str(text or "").split("+") if p.strip()]
    if len(parts) < 1:
        raise ValueError("Scegli una combinazione di tasti.")
    *mods, key = parts
    flags = 0
    for m in mods:
        if m.lower() not in _MODIFIERS:
            raise ValueError(f"Tasto modificatore sconosciuto: {m}")
        flags |= _MODIFIERS[m.lower()]
    k = key.lower()
    if k == "tab":
        vk = 0x09
    elif k == "space":
        vk = 0x20
    elif len(k) == 1 and k.isalnum() and k.isascii():
        vk = ord(k.upper())
    elif k.startswith("f") and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        vk = 0x70 + int(k[1:]) - 1
    else:
        raise ValueError(f"Tasto non supportato: {key}")
    if not flags and not k.startswith("f"):
        raise ValueError("Aggiungi almeno Ctrl, Alt o Shift, altrimenti il tasto non funzionerebbe più nei giochi.")
    names = [n for n, f in (("Ctrl", 0x2), ("Alt", 0x1), ("Shift", 0x4), ("Win", 0x8)) if flags & f]
    label = "+".join(names + [key.upper() if len(key) == 1 else key.capitalize() if k in ("tab", "space") else key.upper()])
    return flags, vk, label


def same_combo(a, b):
    try:
        return parse_hotkey(a)[:2] == parse_hotkey(b)[:2]
    except ValueError:
        return False

_WIN = os.name == "nt"
user32 = ctypes.windll.user32 if _WIN else None
kernel32 = ctypes.windll.kernel32 if _WIN else None

MOD_SHIFT, MOD_NOREPEAT, VK_TAB = 0x0004, 0x4000, 0x09
WM_QUIT, WM_CLOSE, WM_HOTKEY = 0x0012, 0x0010, 0x0312
SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040

if _WIN:
    # Without declared types ctypes passes 32-bit ints, which turns HWND_TOPMOST (-1) and 64-bit handles into garbage.
    HWND_TOPMOST = wintypes.HWND(-1)
    _sig = {
        "RegisterHotKey": ([wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        "UnregisterHotKey": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        "GetMessageW": ([ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT], ctypes.c_int),
        "PostThreadMessageW": ([wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        "PostMessageW": ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
        "GetWindowTextLengthW": ([wintypes.HWND], ctypes.c_int),
        "GetWindowTextW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        "GetForegroundWindow": ([], wintypes.HWND),
        "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
        "BringWindowToTop": ([wintypes.HWND], wintypes.BOOL),
        "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        "AttachThreadInput": ([wintypes.DWORD, wintypes.DWORD, wintypes.BOOL], wintypes.BOOL),
        "GetWindowLongW": ([wintypes.HWND, ctypes.c_int], ctypes.c_long),
        "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
        "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        "SystemParametersInfoW": ([wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT], wintypes.BOOL),
    }
    for _name, (_args, _ret) in _sig.items():
        _fn = getattr(user32, _name)
        _fn.argtypes, _fn.restype = _args, _ret
else:
    HWND_TOPMOST = -1


class Hotkey:
    """Calls `on_press` whenever Shift+Tab is pressed anywhere, until stopped."""

    def __init__(self, on_press, vk=VK_TAB, modifiers=MOD_SHIFT):
        self.on_press, self.vk, self.modifiers = on_press, vk, modifiers
        self.ok = False
        self._ready = threading.Event()
        self._thread_id = None
        self._thread = None

    @classmethod
    def from_label(cls, on_press, label):
        modifiers, vk, _ = parse_hotkey(label)
        return cls(on_press, vk=vk, modifiers=modifiers)

    def start(self, timeout=3.0):
        """Returns False if another program already owns the key combination (or this is not Windows)."""
        if not _WIN:
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.ok

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        self.ok = bool(user32.RegisterHotKey(None, 1, self.modifiers | MOD_NOREPEAT, self.vk))
        self._ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                threading.Thread(target=self.on_press, daemon=True).start()
        user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self._thread_id and self.ok:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
        self.ok = False


def _windows():
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(visit, 0)
    return found


def find_window(title_prefix):
    if not _WIN:
        return None
    return next((hwnd for hwnd, title in _windows() if title.startswith(title_prefix)), None)


def windows_of_pid(pid, min_size=200):
    """Visible top-level windows of a process, biggest first (the game window of an emulator is the big one)."""
    if not _WIN:
        return []
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if width >= min_size and height >= min_size:
                found.append((width * height, hwnd))
        return True

    user32.EnumWindows(visit, 0)
    return [hwnd for _area, hwnd in sorted(found, reverse=True)]


def work_area():
    """(left, top, right, bottom) of the screen without the taskbar."""
    if not _WIN:
        return (0, 0, 1920, 1080)
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return (rect.left, rect.top, rect.right, rect.bottom)


def round_corners(hwnd, border_rgb=None):
    """Windows 11: rounded corners and a thin coloured border for a borderless window."""
    if not _WIN:
        return
    dwm = ctypes.windll.dwmapi
    value = ctypes.c_int(2)  # DWMWCP_ROUND
    dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 33, ctypes.byref(value), 4)
    if border_rgb:
        r, g, b = border_rgb
        colour = ctypes.c_int(r | (g << 8) | (b << 16))  # COLORREF
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 34, ctypes.byref(colour), 4)


def foreground():
    return user32.GetForegroundWindow() if _WIN else None


def focus(hwnd):
    """Bring a window to the front even though we are a background process (attach to the active thread's input)."""
    if not _WIN or not hwnd:
        return
    fg = user32.GetForegroundWindow()
    mine, theirs = kernel32.GetCurrentThreadId(), user32.GetWindowThreadProcessId(fg, None)
    attached = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(mine, theirs, False)


def make_topmost(hwnd):
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    focus(hwnd)


def is_topmost(hwnd):
    return bool(user32.GetWindowLongW(hwnd, -20) & 0x00000008)  # WS_EX_TOPMOST


def close_window(hwnd):
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def window_rect(hwnd):
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("finestra non trovata")
    return rect.left, rect.top, rect.right, rect.bottom


GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE = -20, 0x80, 0x8, 0x08000000


def place_window(hwnd, x, y, width, height):
    """Move a window on top of everything without activating it (SWP_NOACTIVATE).""" 
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, 0x0010 | SWP_SHOWWINDOW)


def no_activate_startup():
    """Start a program whose first window appears without taking the keyboard (STARTF_USESHOWWINDOW + SW_SHOWNOACTIVATE).""" 
    import subprocess
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 4
    return info


def make_passive(hwnd):
    """A window that stays on top but never takes the keyboard from the game (clicks on it still work)."""
    get = user32.GetWindowLongPtrW
    get.argtypes, get.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t
    put = user32.SetWindowLongPtrW
    put.argtypes, put.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
    put(hwnd, GWL_EXSTYLE, get(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)


# ---- pressing a key combination in the window that has the focus (to open a game's own menu)

_SCAN = {0x10: 0x2A, 0x11: 0x1D, 0x12: 0x38, 0x09: 0x0F}  # left Shift / Ctrl / Alt, Tab: games read the scan code
_MOD_VK = {0x2: 0x11, 0x1: 0x12, 0x4: 0x10, 0x8: 0x5B}
INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 1, 0x0002, 0x0008


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]  # as big as the mouse variant
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _key_event(vk, up):
    scan = _SCAN.get(vk) or (user32.MapVirtualKeyW(vk, 0) if _WIN else 0)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return _INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags))


def send_combo(label, hold=0.08):
    """Press and release a combination like 'Shift+Tab' as if typed, in the window that has the focus."""
    import time
    flags, vk, _ = parse_hotkey(label) if "+" in label else (0, parse_hotkey("Shift+" + label)[1], label)
    mods = [_MOD_VK[f] for f in (0x2, 0x1, 0x4, 0x8) if flags & f]
    for down in [*mods, vk]:
        user32.SendInput(1, ctypes.byref(_key_event(down, False)), ctypes.sizeof(_INPUT))
        time.sleep(0.03)
    time.sleep(hold)  # some games only look at the keys once per frame
    for up in [vk, *reversed(mods)]:
        user32.SendInput(1, ctypes.byref(_key_event(up, True)), ctypes.sizeof(_INPUT))
        time.sleep(0.03)


def screen_size():
    return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)) if _WIN else (1920, 1080)
