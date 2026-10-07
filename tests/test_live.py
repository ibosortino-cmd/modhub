import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import live
import overlay
import settings

SPEC = json.loads((Path(__file__).resolve().parent.parent / "emu_settings" / "pcsx2.json").read_text("utf-8"))["live"]
VK_TO_HOTKEY = {live.vk_for(key): name for name, key in SPEC["keys"].items()}


class FakePcsx2:
    """Behaves like PCSX2 as seen in a real session: a posted key changes a setting and the answer appears in the log,
    in the user's language (Italian here) and with PCSX2's own message keys."""

    def __init__(self, log, language="it"):
        self.log, self.language = log, language
        self.multiplier, self.blending, self.volume, self.mipmap = 1, 0, 100, True
        self.names = ["Minimum", "Basic", "Medium", "High", "Full", "Maximum"]
        self.max_multiplier = 12
        self.silent = False

    def say(self, key, text):
        if not self.silent:
            with open(self.log, "a", encoding="utf-8") as f:
                f.write(f"[    9,1234] OSD [{key}]: {text}\n")

    def t(self, it, en):
        return it if self.language == "it" else en

    def key(self, vk):
        hotkey = VK_TO_HOTKEY[vk]
        if hotkey == "IncreaseUpscaleMultiplier":
            if self.multiplier < self.max_multiplier:
                self.multiplier += 1
                self.say("UpscaleMultiplierChanged",
                         self.t(f"Moltiplicatore upscale aumentato a {self.multiplier}x. ({self.multiplier * 512} x {self.multiplier * 448})",
                                f"Upscale multiplier increased to {self.multiplier}x. ({self.multiplier * 640} x {self.multiplier * 512})"))
            else:
                self.say("UpscaleMultiplierChanged", self.t(f"Moltiplicatore upscale massimizzato a {self.multiplier}x. (6144 x 5376)",
                                                            f"Upscale multiplier maximized to {self.multiplier}x."))
        elif hotkey == "DecreaseUpscaleMultiplier" and self.multiplier > 1:
            self.multiplier -= 1
            if self.multiplier == 1:  # PCSX2 words 1x differently, without any number
                self.say("UpscaleMultiplierChanged", self.t("Moltiplicatore upscale impostato sulla risoluzione nativa. (512 x 448)",
                                                            "Upscale multiplier set to native resolution. (640 x 512)"))
            else:
                self.say("UpscaleMultiplierChanged", self.t(f"Moltiplicatore upscale diminuito a {self.multiplier}x. (1024 x 896)",
                                                            f"Upscale multiplier decreased to {self.multiplier}x. (...)"))
        elif hotkey == "CycleBlendingAccuracy":
            self.blending = (self.blending + 1) % 6
            self.say("CycleBlendingAccuracy", self.t(f"Precisione del Blending impostata a {self.names[self.blending]}.",
                                                     f"Blending Accuracy set to {self.names[self.blending]}."))
        elif hotkey == "IncreaseVolume":
            self.volume += 5
            self.say("VolumeChanged", self.t(f"Volume: Aumentato a {self.volume}%", f"Volume: Increased to {self.volume}%"))
        elif hotkey == "CycleAspectRatio":
            self.say("CycleAspectRatio", self.t("Rapporto d'aspetto impostato su '16:9'.", "Aspect ratio set to '16:9'."))
        elif hotkey == "ToggleMipmapMode":
            self.mipmap = not self.mipmap
            self.say("ToggleMipmapMode", self.t("Il mipmapping hardware è ora " + ("abilitato." if self.mipmap else "disabilitato."),
                                                "Hardware mipmapping is now " + ("enabled." if self.mipmap else "disabled.")))


class LiveTests(unittest.TestCase):
    language = "it"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = self.tmp / "pcsx2.log"
        self.log.write_text("[    0,1] OSD [unsafe_settings_warning]: L'API Grafica non è impostata su Automatico.\n", "utf-8")
        self.emu = FakePcsx2(self.log, self.language)
        self.posted = []
        real_windows, real_settle = overlay.windows_of_pid, live.SETTLE
        overlay.user32 = type("U", (), {"PostMessageW": lambda s, hwnd, msg, wp, lp: self._post(hwnd, msg, wp),
                                        "MapVirtualKeyW": lambda s, vk, kind: 0})()
        overlay.windows_of_pid = lambda pid, min_size=200: [555] if pid == 99 else []
        live.SETTLE = 0.6
        self.addCleanup(setattr, overlay, "user32", __import__("ctypes").windll.user32)
        self.addCleanup(setattr, overlay, "windows_of_pid", real_windows)
        self.addCleanup(setattr, live, "SETTLE", real_settle)
        self.remote = live.Live(SPEC, 99, self.log, {"EmuCore/GS.upscale_multiplier": 1, "EmuCore/GS.accurate_blending_unit": 0,
                                                     "SPU2/Output.StandardVolume": 100, "EmuCore/GS.hw_mipmap": True})

    def _post(self, hwnd, msg, vk):
        self.assertEqual(hwnd, 555)
        if msg == live.WM_KEYDOWN:
            self.posted.append(vk)
            threading.Thread(target=lambda: (time.sleep(0.05), self.emu.key(vk)), daemon=True).start()

    def test_keys_f13_to_f24_map_to_virtual_keys(self):
        self.assertEqual([live.vk_for(f"F{n}") for n in (1, 12, 13, 24)], [0x70, 0x7B, 0x7C, 0x87])
        with self.assertRaises(core.ModHubError):
            live.vk_for("Escape")
        self.assertEqual(len(set(SPEC["keys"].values())), len(SPEC["keys"]), "two hotkeys share a key")

    def test_pressing_a_hotkey_returns_what_the_emulator_answered(self):
        result = self.remote.do("resolution", "up")
        self.assertEqual((result["value"], result["answered"], result["changed"]), (2, True, True))
        self.assertIn("2x", result["message"])
        self.assertEqual(self.posted, [live.vk_for("F13")])
        self.assertEqual(self.remote.do("resolution", "down")["value"], 1)

    def test_old_log_lines_and_unrelated_messages_are_not_taken_for_an_answer(self):
        with open(self.log, "a", encoding="utf-8") as f:
            f.write("[    1,0] OSD [UpscaleMultiplierChanged]: Moltiplicatore upscale aumentato a 7x.\n")
        self.assertEqual(self.remote.do("resolution", "up")["value"], 2)  # not 7
        self.emu.silent = True
        result = self.remote.do("resolution", "up")  # the game stays silent: no answer, value unchanged
        self.assertEqual((result["answered"], result["value"]), (False, 2))

    def test_set_presses_until_the_value_is_reached_in_both_directions(self):
        self.assertEqual(self.remote.set("resolution", 4)["reached"], True)
        self.assertEqual(self.emu.multiplier, 4)
        self.assertEqual(self.remote.set("resolution", 2)["reached"], True)
        self.assertEqual(self.emu.multiplier, 2)
        before = len(self.posted)
        self.assertTrue(self.remote.set("resolution", 2)["reached"])
        self.assertEqual(len(self.posted), before, "already there: nothing to press")

    def test_going_down_to_native_resolution_is_understood_although_the_message_has_no_number(self):
        """Regression: at 1x PCSX2 says 'set to native resolution', and ModHub used to stop at 2x."""
        self.remote.set("resolution", 4)
        result = self.remote.set("resolution", 1)
        self.assertEqual((result["reached"], self.emu.multiplier, self.remote.values["resolution"]), (True, 1, 1))
        self.assertEqual(len(self.posted), 3 + 3)

    def test_it_never_presses_more_than_needed(self):
        """Regression: the first real session pressed 'up' 200 times (the answer was in Italian and was not understood)."""
        self.remote.set("resolution", 3)
        self.assertEqual(len(self.posted), 2)  # 1x -> 2x -> 3x
        self.remote.set("resolution", 1)
        self.assertEqual(len(self.posted), 4)

    def test_cycle_controls_go_around_to_the_wanted_value(self):
        self.assertTrue(self.remote.set("blending", 2)["reached"])  # Minimum -> Basic -> Medium
        self.assertEqual((self.emu.blending, len(self.posted)), (2, 2))
        self.assertTrue(self.remote.set("blending", 0)["reached"])  # wraps around: Maximum -> Minimum
        self.assertEqual(self.emu.blending, 0)
        self.assertEqual(len(self.posted), 2 + 4)

    def test_a_silent_emulator_stops_the_loop_instead_of_pressing_forever(self):
        self.emu.silent = True  # the game does not answer
        result = self.remote.set("resolution", 5)
        self.assertEqual((result["reached"], result["answered"]), (False, False))
        self.assertEqual(len(self.posted), 1)

    def test_the_emulators_limit_stops_the_loop(self):
        self.emu.max_multiplier = 3
        self.remote.values["resolution"] = 3
        self.emu.multiplier = 3
        result = self.remote.set("resolution", 8)
        self.assertFalse(result["reached"])
        self.assertEqual(len(self.posted), 1)  # one press told us it is at its maximum

    def test_unknown_starting_value_means_no_blind_presses(self):
        other = live.Live({**SPEC, "controls": [{**c, "default": None} for c in SPEC["controls"]]}, 99, self.log)
        self.assertFalse(other.set("resolution", 3)["reached"])
        self.assertEqual(self.posted, [])

    def test_a_message_we_cannot_read_does_not_cause_more_presses(self):
        self.remote.controls["resolution"] = {**self.remote.controls["resolution"], "read": r"WILL NOT MATCH (\d+)"}
        result = self.remote.set("resolution", 6)
        self.assertEqual((result["reached"], len(self.posted)), (False, 1))

    def test_toggle_and_text_controls_read_back(self):
        self.assertIs(self.remote.do("mipmap", "press")["value"], False)  # the wording is localized: the state is flipped
        self.assertIs(self.remote.do("mipmap", "press")["value"], True)
        self.assertEqual(self.remote.do("aspect", "press")["value"], "16:9")
        shown = {c["id"]: c["display"] for c in self.remote.snapshot()["controls"]}
        self.assertEqual((shown["mipmap"], shown["aspect"], shown["resolution"]), ("Sì", "16:9", "1x"))
        self.assertEqual(shown["blending"], "Minima")  # the starting value comes from the settings file

    def test_volume_can_go_above_100_like_in_the_emulator(self):
        self.assertEqual(self.remote.do("volume", "up")["value"], 105)
        self.assertEqual({c["id"]: c["display"] for c in self.remote.snapshot()["controls"]}["volume"], "105%")

    def test_missing_game_window_is_a_clear_error(self):
        other = live.Live(SPEC, 12345, self.log)
        with self.assertRaises(core.ModHubError):
            other.do("resolution", "up")

    def test_unknown_control_or_action_is_refused(self):
        for control, action in (("nope", "up"), ("mipmap", "up"), ("resolution", "press")):
            with self.assertRaises(core.ModHubError):
                self.remote.do(control, action)

    def test_which_controls_map_to_optimizer_settings(self):
        self.assertEqual(self.remote.control_for_setting("EmuCore/GS.upscale_multiplier")["id"], "resolution")
        self.assertEqual(self.remote.control_for_setting("EmuCore/GS.accurate_blending_unit")["id"], "blending")
        self.assertIsNone(self.remote.control_for_setting("EmuCore/GS.MaxAnisotropy"))  # no live hotkey for it
        self.assertIsNone(self.remote.control_for_setting("EmuCore/GS.AspectRatio"))  # text, not settable by count
        self.assertIsNone(self.remote.control_for_setting("SPU2/Output.StandardVolume"))  # volume is not part of the levels


class LiveTestsEnglish(LiveTests):
    """The same behaviour with PCSX2 running in English."""
    language = "en"


class BindingTests(unittest.TestCase):
    INI = "[UI]\nStartFullscreen = false\n\n[Hotkeys]\nCycleAspectRatio = Keyboard/F6\nZoomIn = Keyboard/Control & Keyboard/Plus\n\n[Pad]\nType = DualShock2\n"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ini = self.tmp / "PCSX2.ini"
        self.ini.write_text(self.INI, "utf-8")

    def test_missing_bindings_are_added_to_the_hotkeys_section_only(self):
        self.assertTrue(live.prepare(self.ini, SPEC))
        text = self.ini.read_text("utf-8")
        parsed = settings.parse(text, "ini")
        self.assertEqual(parsed["Hotkeys.IncreaseUpscaleMultiplier"], "Keyboard/F13")
        self.assertIn("CycleAspectRatio = Keyboard/F6", text.splitlines())  # the user's own binding is untouched...
        self.assertIn("CycleAspectRatio = Keyboard/F18", text.splitlines())  # ...ours is added next to it
        self.assertEqual(parsed["Pad.Type"], "DualShock2")
        self.assertTrue(text.index("IncreaseUpscaleMultiplier") < text.index("[Pad]"))  # inside [Hotkeys], not after it
        for original in self.INI.splitlines():
            if original:
                self.assertIn(original, text.splitlines())
        self.assertEqual(self.ini.with_name("PCSX2.ini.modhub-bak").read_text("utf-8"), self.INI)

    def test_existing_binding_for_the_same_hotkey_is_kept_and_ours_is_added_beside_it(self):
        self.ini.write_text(self.INI.replace("ZoomIn", "IncreaseUpscaleMultiplier = Keyboard/PageUp\nZoomIn"), "utf-8")
        live.prepare(self.ini, SPEC)
        lines = [l for l in self.ini.read_text("utf-8").splitlines() if l.startswith("IncreaseUpscaleMultiplier")]
        self.assertEqual(lines, ["IncreaseUpscaleMultiplier = Keyboard/PageUp", "IncreaseUpscaleMultiplier = Keyboard/F13"])

    def test_running_it_twice_changes_nothing_the_second_time(self):
        live.prepare(self.ini, SPEC)
        once = self.ini.read_bytes()
        self.assertFalse(live.prepare(self.ini, SPEC))
        self.assertEqual(self.ini.read_bytes(), once)

    def test_section_is_created_when_the_file_has_none(self):
        self.ini.write_text("[UI]\nStartFullscreen = false\n", "utf-8")
        live.prepare(self.ini, SPEC)
        parsed = settings.parse(self.ini.read_text("utf-8"), "ini")
        self.assertEqual(parsed["Hotkeys.DecreaseUpscaleMultiplier"], "Keyboard/F14")
        self.assertEqual(parsed["UI.StartFullscreen"], False)

    def test_crlf_and_bom_are_preserved(self):
        self.ini.write_bytes(b"\xef\xbb\xbf" + self.INI.replace("\n", "\r\n").encode("utf-8"))
        live.prepare(self.ini, SPEC)
        raw = self.ini.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))


class RealSessionRegressionTests(unittest.TestCase):
    """The messages PCSX2 really wrote during the first real session (Italian)."""

    LOG = """[  107,8148] OSD [UpscaleMultiplierChanged]: Moltiplicatore upscale aumentato a 3x. (1536 x 1344)
[  111,1792] OSD [UpscaleMultiplierChanged]: Moltiplicatore upscale diminuito a 2x. (1024 x 896)
[  170,2286] OSD [UpscaleMultiplierChanged]: Moltiplicatore upscale massimizzato a 12x. (6144 x 5376)
[  150,0512] OSD [CycleAspectRatio]: Rapporto d'aspetto impostato su '10:7'.
[  171,4602] OSD [CycleBlendingAccuracy]: Precisione del Blending impostata a Medium.
[  172,9340] OSD [CycleBlendingAccuracy]: Precisione del Blending impostata a Minimum.
[    5,1000] OSD [VolumeChanged]: Volume: Aumentato a 105%
"""

    def test_every_real_message_is_understood(self):
        remote = live.Live(SPEC, 1, Path("unused.log"))
        read = lambda cid, text: remote._parse(remote.controls[cid], text)
        expected = [("resolution", 3), ("resolution", 2), ("resolution", 12), ("aspect", "10:7"), ("blending", 2), ("blending", 0), ("volume", 105)]
        lines = [live.OSD_LINE.search(l) for l in self.LOG.splitlines()]
        for (cid, want), m in zip(expected, lines):
            self.assertEqual(read(cid, m["text"]), want, m["text"])
            self.assertEqual(remote.controls[cid]["osd"], m["key"])


if __name__ == "__main__":
    unittest.main()
