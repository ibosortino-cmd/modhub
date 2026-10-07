import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import memlive

DATA_VA = 0x5000


def fake_exe(data):
    """A minimal PE file with one .data section holding `data`."""
    pe = 0x80
    head = bytearray(0x200)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe)
    head[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<H", head, pe + 6, 1)       # one section
    struct.pack_into("<H", head, pe + 20, 0xF0)   # size of the optional header
    sec = pe + 24 + 0xF0
    struct.pack_into("<8sIIII", head, sec, b".data", len(data), DATA_VA, len(data), 0x200)
    return bytes(head) + data


def bind_block(deadzone=0.15, players=2):
    out = b""
    for p in range(players):
        out += struct.pack("<B3xi", 0, -1)  # PadDevice
        for a in range(memlive.ACTIONS):
            out += struct.pack("<B3xiff", 2 if a < 16 else 3, a, -1.0 if a % 2 else 1.0, deadzone)
    return out


class FakeMemory:
    """The game's memory: the program's .data copied at `base`, readable and writable."""

    def __init__(self, base, data):
        self.base, self.mem = base, bytearray(data)

    def read(self, address, size):
        o = address - self.base - DATA_VA
        if o < 0 or o + size > len(self.mem):
            raise OSError("outside")
        return bytes(self.mem[o:o + size])

    def write(self, address, data):
        o = address - self.base - DATA_VA
        self.mem[o:o + len(data)] = data

    def close(self):
        pass


class MemLiveTests(unittest.TestCase):
    def setUp(self):
        self.data = bytearray(0x3000)
        self.data[0x100:0x110] = struct.pack("<4f", 1.0, 1.0, 1.0, 0.4)   # master, curtain mute, music, sfx
        self.data[0x200:0x208] = struct.pack("<B3xi", 1, 100)             # rumble on, strength
        self.data[0x1000:0x1000 + len(bind_block())] = bind_block()
        tmp = Path(tempfile.mkdtemp())
        self.exe = tmp / "game.exe"
        self.exe.write_bytes(fake_exe(bytes(self.data)))

    def game(self):
        memory = FakeMemory(0x140000000, self.data)
        g = memlive.GameMemory(1234, self.exe)
        g._proc, g._base, g._loc = memory, memory.base, memlive.locate(self.exe)
        return g, memory

    def test_the_settings_are_found_from_their_starting_values_in_the_program(self):
        loc = memlive.locate(self.exe)
        self.assertEqual((loc["volumes"], loc["rumble"]), (DATA_VA + 0x100, DATA_VA + 0x200))

    def test_two_matches_mean_no_live_change_at_all(self):
        self.data[0x400:0x410] = struct.pack("<4f", 1.0, 1.0, 1.0, 0.4)
        self.exe.write_bytes(fake_exe(bytes(self.data)))
        self.assertIsNone(memlive.locate(self.exe)["volumes"])

    def test_volumes_rumble_and_dead_zone_change_in_the_running_game(self):
        g, memory = self.game()
        self.assertTrue(g.set("audio.music_volume", 0.5))
        self.assertTrue(g.set("audio.sfx_volume", 0.25))
        self.assertEqual(struct.unpack("<4f", memory.read(memory.base + DATA_VA + 0x100, 16)), (1.0, 1.0, 0.5, 0.25))
        self.assertTrue(g.set("controllers.rumble", False))
        self.assertTrue(g.set("controllers.rumble_strength", 150))
        self.assertEqual(struct.unpack("<B3xi", memory.read(memory.base + DATA_VA + 0x200, 8)), (0, 150))
        self.assertTrue(g.set("controllers.deadzone", 0.3))
        blob = memory.read(memory.base + DATA_VA + 0x1000, memlive.PLAYER_SIZE * 2)
        self.assertAlmostEqual(memlive._players_at(blob, 0), 0.3, places=6)

    def test_nothing_is_written_where_the_values_do_not_look_right(self):
        g, memory = self.game()
        memory.write(memory.base + DATA_VA + 0x100, struct.pack("<4f", 7.0, 3.0, 9.0, 1.0))  # not volumes
        self.assertFalse(g.set("audio.music_volume", 0.5))
        self.assertEqual(struct.unpack("<4f", memory.read(memory.base + DATA_VA + 0x100, 16)), (7.0, 3.0, 9.0, 1.0))
        self.assertFalse(g.set("audio.music_volume", 3.0))  # out of range
        self.assertFalse(g.set("video.render_scale", 2))    # not something the game takes live

    def test_the_dead_zone_needs_one_clear_match(self):
        self.data[0x2000:0x2000 + len(bind_block())] = bind_block()  # a second copy: ambiguous
        g, _ = self.game()
        self.assertFalse(g.set("controllers.deadzone", 0.3))



HEAP = 0x7F0000000000


def menu_object(path=b"C:/Games/BT3/savedata/settings.toml", shadows=True, zfar=200000, w=1920, h=1080, at=HEAP):
    """PS2SettingsOverlay as it sits in the game's memory, with its m_configPath pointing at `path` (stored after it)."""
    obj = bytearray(0x200)
    obj[0:3] = b"\0\1\0"  # m_visible, m_initialized, m_dirty
    for base in (memlive.SETTINGS_AT, memlive.BOOT_AT):
        struct.pack_into("<3i", obj, base + 88, zfar, w, h)
        obj[base + 84] = 1 if shadows else 0
        struct.pack_into("<f", obj, base + 8, 0.25)  # music
    struct.pack_into("<Q8xQQ", obj, memlive.PATH_AT, at + 0x200, len(path), 47)  # MSVC std::string: pointer (in a 16-byte union), size, capacity
    return bytes(obj) + path


class Heap:
    """Writable blocks of the game's memory."""

    def __init__(self, blocks):
        self.blocks = {start: bytearray(data) for start, data in blocks.items()}

    def regions(self):
        return [(start, len(data)) for start, data in self.blocks.items()]

    def _where(self, address, size):
        for start, data in self.blocks.items():
            if start <= address and address + size <= start + len(data):
                return data, address - start
        raise OSError("outside")

    def read(self, address, size):
        data, o = self._where(address, size)
        return bytes(data[o:o + size])

    def write(self, address, value):
        data, o = self._where(address, len(value))
        data[o:o + len(value)] = value

    def close(self):
        pass


class MenuObjectTests(unittest.TestCase):
    FILE = {"video.dof_zfar": 200000, "video.window_w": 1920, "video.window_h": 1080}

    def game(self, heap):
        g = memlive.GameMemory(1234, "unused.exe", locations={"volumes": None, "rumble": None, "data": (0, 0)},
                               file_values=lambda: self.FILE)
        g._proc, g._base = heap, 0x140000000
        return g

    def test_graphics_change_like_in_the_games_own_menu(self):
        heap = Heap({HEAP: b"\xAA" * 64 + menu_object(at=HEAP + 64)})
        obj = HEAP + 64
        g = self.game(heap)
        self.assertTrue(g.set("video.shadows", False))
        self.assertEqual(heap.read(obj + memlive.SETTINGS_AT + 84, 1), b"\0")
        self.assertEqual(heap.read(obj + 2, 1), b"\1")  # m_dirty: the game applies it on its next frame
        self.assertEqual(heap.read(obj + memlive.BOOT_AT + 84, 1), b"\1")  # the copy from startup is not touched
        self.assertTrue(g.set("video.ink_color", "#FF8000"))
        self.assertEqual(struct.unpack("<I", heap.read(obj + memlive.SETTINGS_AT + 80, 4))[0], 0xFF8000)
        self.assertTrue(g.set("audio.music_volume", 0.5))
        self.assertEqual(struct.unpack("<f", heap.read(obj + memlive.SETTINGS_AT + 8, 4))[0], 0.5)
        self.assertFalse(g.set("video.render_scale", 3))  # only at the next start, even in the game's own menu

    def test_nothing_is_written_unless_the_object_is_certain(self):
        for blocks in ({HEAP: menu_object(path=b"C:/Games/BT3/savedata/other.ini")},       # not the settings file
                       {HEAP: menu_object(zfar=123)},                                       # not what the file says
                       {HEAP: menu_object(), HEAP + 0x10000: menu_object(at=HEAP + 0x10000)}):              # two candidates
            heap = Heap(blocks)
            before = {k: bytes(v) for k, v in heap.blocks.items()}
            self.assertFalse(self.game(heap).set("video.shadows", False))
            self.assertEqual({k: bytes(v) for k, v in heap.blocks.items()}, before)

    def test_while_dragging_volumes_skip_the_full_reapply(self):
        heap = Heap({HEAP: menu_object(), 0x140000000 + DATA_VA: struct.pack("<4f", 1.0, 1.0, 1.0, 0.4)})
        g = self.game(heap)
        g._loc = {"volumes": DATA_VA, "rumble": None, "data": (0, 0)}
        self.assertTrue(g.set("audio.music_volume", 0.7, preview=True))
        self.assertAlmostEqual(struct.unpack("<f", heap.read(0x140000000 + DATA_VA + 8, 4))[0], 0.7, places=6)
        self.assertEqual(heap.read(HEAP + 2, 1), b"\0")  # m_dirty untouched


if __name__ == "__main__":
    unittest.main()
