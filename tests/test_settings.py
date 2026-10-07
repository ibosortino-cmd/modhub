import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import settings

SAMPLE = """# Game - Recompiled
# User settings - written by the launcher.

[audio]
master_volume = 1
music_volume = 0.0360465   # quiet
sfx_volume = 0

[video]
renderer = "native"
ink_color = "#000000"
fullscreen = false
window_mode = 0
glow = true

[video.hud]
offset_left = 79
offset_right = -79

[controllers.hotkey]
pad_btns = [13, 15]

[mods]
enabled = true
disabled = ""
"""

SCHEMA = {
    "file": "savedata/settings.toml",
    "groups": [{"sections": [{"fields": [
        {"key": "audio.music_volume", "type": "slider", "min": 0, "max": 1},
        {"key": "audio.sfx_volume", "type": "slider", "min": 0, "max": 1},
        {"key": "video.glow", "type": "toggle"},
        {"key": "video.window_mode", "type": "select", "options": [{"value": 0}, {"value": 1}, {"value": 2}],
         "sync": [{"key": "video.fullscreen", "equals": 2}]},
        {"key": "video.hud.offset_left", "type": "slider", "int": True, "min": -300, "max": 300},
        {"key": "video.hud.offset_center", "type": "slider", "int": True, "min": -300, "max": 300},
        {"key": "mods.disabled", "type": "text"},
        {"key": "logging.log_level", "type": "select", "options": [{"value": 0}, {"value": 1}]},
        {"key": "video.renderer", "type": "info"},
    ]}]}],
}


class ApplyChangesTests(unittest.TestCase):
    def changed_lines(self, before, after):
        a, b = before.splitlines(), after.splitlines()
        return [(x, y) for x, y in zip(a, b) if x != y]

    def test_only_the_changed_line_differs_and_comments_survive(self):
        out = settings.apply_changes(SAMPLE, {"audio.music_volume": 0.5})
        self.assertEqual(self.changed_lines(SAMPLE, out),
                         [("music_volume = 0.0360465   # quiet", "music_volume = 0.5   # quiet")])
        self.assertEqual(out.splitlines()[:2], SAMPLE.splitlines()[:2])

    def test_values_are_typed_and_formatted_like_the_game(self):
        out = settings.apply_changes(SAMPLE, {"audio.master_volume": 1.0, "video.glow": False,
                                              "mods.disabled": "a, b", "video.hud.offset_left": -12})
        parsed = tomllib.loads(out)
        self.assertEqual(parsed["audio"]["master_volume"], 1)
        self.assertIs(parsed["video"]["glow"], False)
        self.assertEqual(parsed["mods"]["disabled"], "a, b")
        self.assertEqual(parsed["video"]["hud"]["offset_left"], -12)
        self.assertEqual(parsed["video"]["ink_color"], "#000000")
        self.assertEqual(parsed["controllers"]["hotkey"]["pad_btns"], [13, 15])

    def test_missing_key_is_added_to_its_table_and_missing_table_is_created(self):
        out = settings.apply_changes(SAMPLE, {"video.hud.offset_center": 5, "logging.log_level": 2})
        parsed = tomllib.loads(out)
        self.assertEqual(parsed["video"]["hud"]["offset_center"], 5)
        self.assertEqual(parsed["logging"]["log_level"], 2)
        self.assertEqual(parsed["video"]["hud"]["offset_left"], 79)
        lines = out.splitlines()
        self.assertEqual(lines[lines.index("offset_right = -79") + 1], "offset_center = 5")  # end of its table

    def test_crlf_and_trailing_newline_are_preserved(self):
        text = SAMPLE.replace("\n", "\r\n")
        out = settings.apply_changes(text, {"video.glow": False})
        self.assertNotIn("\r\r", out)
        self.assertEqual(out.count("\n"), out.count("\r\n"))
        self.assertTrue(out.endswith("\r\n"))
        self.assertFalse(settings.apply_changes(SAMPLE.rstrip("\n"), {"video.glow": False}).endswith("\n"))


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "savedata").mkdir()
        self.path = self.root / "savedata/settings.toml"
        self.path.write_text(SAMPLE, "utf-8")

    def test_save_syncs_fullscreen_and_makes_one_backup(self):
        written = settings.save(self.root, SCHEMA, {"video.window_mode": 2})
        self.assertEqual(written, ["video.fullscreen", "video.window_mode"])
        parsed = tomllib.loads(self.path.read_text("utf-8"))
        self.assertEqual(parsed["video"]["window_mode"], 2)
        self.assertIs(parsed["video"]["fullscreen"], True)
        backup = self.path.with_name("settings.toml.modhub-bak")
        self.assertEqual(backup.read_text("utf-8"), SAMPLE)
        settings.save(self.root, SCHEMA, {"video.glow": False})
        self.assertEqual(backup.read_text("utf-8"), SAMPLE)  # still the original
        settings.restore_backup(self.root, SCHEMA)
        self.assertEqual(self.path.read_text("utf-8"), SAMPLE)

    def test_invalid_input_is_rejected_and_file_untouched(self):
        bad = [{"video.unknown": 1}, {"video.glow": "yes"}, {"audio.sfx_volume": float("nan")},
               {"audio.sfx_volume": -1}, {"video.window_mode": 7}, {"mods.disabled": "a\nb"},
               {"video.renderer": "evil"}, {"audio.sfx_volume": True}]
        for changes in bad:
            with self.assertRaises(core.ModHubError, msg=str(changes)):
                settings.save(self.root, SCHEMA, changes)
        self.assertEqual(self.path.read_text("utf-8"), SAMPLE)
        self.assertFalse(self.path.with_name("settings.toml.modhub-bak").exists())

    def test_current_value_outside_the_options_is_still_accepted(self):
        self.path.write_text(SAMPLE.replace("window_mode = 0", "window_mode = 9"), "utf-8")
        settings.save(self.root, SCHEMA, {"video.window_mode": 9})

    def test_describe_and_missing_file(self):
        info = settings.describe(self.root, SCHEMA)
        self.assertTrue(info["exists"])
        self.assertEqual(info["values"]["video.hud.offset_right"], -79)
        self.path.unlink()
        self.assertFalse(settings.describe(self.root, SCHEMA)["exists"])
        with self.assertRaises(core.ModHubError):
            settings.save(self.root, SCHEMA, {"video.glow": True})

    def test_schema_path_cannot_escape_game_folder(self):
        with self.assertRaises(core.ModHubError):
            settings.describe(self.root, {"file": "../outside.toml", "groups": []})

    def test_running_detection_does_not_lock_a_stopped_game(self):
        exe = self.root / "game.exe"
        exe.write_bytes(b"x")
        self.assertFalse(settings.game_running(self.root, "game.exe"))
        self.assertFalse(settings.game_running(self.root, "missing.exe"))


class BundledSchemaTests(unittest.TestCase):
    def test_bundled_bt3_schema_is_consistent(self):
        path = Path(__file__).resolve().parent.parent / "catalog/games/bt3-recomp/settings.json"
        schema = json.loads(path.read_text("utf-8"))
        keys = []
        for group in schema["groups"]:
            for preset in group.get("presets", []):
                self.assertTrue(preset["values"])
            for section in group["sections"]:
                for f in section["fields"]:
                    keys.append(f["key"])
                    self.assertIn(f["type"], ("toggle", "slider", "number", "select", "text", "info"))
                    if f["type"] == "select":
                        self.assertTrue(f["options"])
                    if f["type"] == "slider":
                        self.assertLess(f["min"], f["max"])
        self.assertEqual(len(keys), len(set(keys)), "duplicate keys in schema")
        for group in schema["groups"]:
            for preset in group.get("presets", []):
                for key in preset["values"]:
                    self.assertIn(key, keys)

    def test_every_optimizer_level_only_sets_valid_values_for_known_options(self):
        path = Path(__file__).resolve().parent.parent / "catalog/games/bt3-recomp/settings.json"
        schema = json.loads(path.read_text("utf-8"))
        fields = settings._fields(schema)
        opt = schema["optimizer"]
        self.assertEqual(len(opt["levels"]), 5)
        self.assertEqual(len(opt["byTier"]), 5)
        self.assertTrue(all(1 <= n <= 5 for n in opt["byTier"]))
        keysets = [set(level["values"]) for level in opt["levels"]]
        self.assertTrue(all(k == keysets[0] for k in keysets), "levels must set the same options so the slider is comparable")
        for level in opt["levels"]:
            self.assertTrue(level["name"] and level["desc"])
            for key, value in level["values"].items():
                self.assertIn(key, fields, key)
                settings._coerce(fields[key], value, None)  # raises if the value is not valid for that option
        # the slider must be monotonic in the main quality knob
        scales = [level["values"]["video.render_scale"] for level in opt["levels"]]
        self.assertEqual(scales, sorted(scales))


if __name__ == "__main__":
    unittest.main()
