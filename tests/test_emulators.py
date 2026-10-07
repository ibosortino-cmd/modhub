import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import emulators


class BundledListTests(unittest.TestCase):
    def test_every_emulator_entry_is_usable(self):
        emus = emulators.load()
        self.assertGreaterEqual(len(emus), 10)
        ids = [e["id"] for e in emus]
        self.assertEqual(len(ids), len(set(ids)))
        for e in emus:
            self.assertTrue(e["name"] and e["systems"] and e["exe_names"], e["id"])
            self.assertTrue(all(n.lower().endswith(".exe") for n in e["exe_names"]), e["id"])
            self.assertIn("{rom}", e["args"], e["id"])
            if "args_fullscreen" in e:
                self.assertIn("{rom}", e["args_fullscreen"], e["id"])
            self.assertTrue(e["site"].startswith("https://"), e["id"])

    def test_the_famous_ones_are_there(self):
        systems = {s for e in emulators.load() for s in e["systems"]}
        for wanted in ("PlayStation 2", "PlayStation 3", "Wii", "GameCube", "Wii U", "Nintendo DS", "Xbox 360", "Dreamcast"):
            self.assertIn(wanted, systems)


class DetectAndArgsTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.dolphin = {"id": "dolphin", "exe_names": ["Dolphin.exe"]}

    def test_finds_the_program_in_a_nested_download_folder(self):
        exe = self.base / "dolphin-2412-x64" / "Dolphin-x64" / "Dolphin.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"x")
        self.assertEqual(emulators.detect(self.dolphin, [self.base]), exe)

    def test_ignores_unrelated_folders_and_missing_program(self):
        (self.base / "other-stuff").mkdir()
        (self.base / "other-stuff" / "Dolphin.exe").write_bytes(b"x")
        (self.base / "dolphin-empty").mkdir()
        self.assertIsNone(emulators.detect(self.dolphin, [self.base]))

    def test_rom_with_spaces_and_dashes_is_one_argument(self):
        args = emulators.build_args(["-b", "-e", "{rom}"], r"C:\Roms\-my game (EU).iso")
        self.assertEqual(args, ["-b", "-e", r"C:\Roms\-my game (EU).iso"])

    def test_args_template_parsing(self):
        self.assertEqual(emulators.parse_args_template("-f {rom}"), ["-f", "{rom}"])
        self.assertEqual(emulators.parse_args_template(""), ["{rom}"])
        self.assertEqual(emulators.parse_args_template('--cfg "a b" {rom}'), ["--cfg", "a b", "{rom}"])
        for bad in ("--fast", '--x "unclosed {rom}', " ".join(["{rom}"] * 25)):
            with self.assertRaises(core.ModHubError, msg=bad):
                emulators.parse_args_template(bad)


if __name__ == "__main__":
    unittest.main()
