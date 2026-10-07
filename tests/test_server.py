import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import publish
import overlay
import server
from test_core import make_mod


def fake_process(pid=4321):
    def wait(p, timeout=None):
        end = time.time() + (timeout or 0)
        while p.alive:
            if time.time() > end:
                import subprocess
                raise subprocess.TimeoutExpired("game", timeout)
            time.sleep(0.01)
        return 0
    return type("P", (), {"alive": True, "started": False, "pid": pid, "poll": lambda p: None if p.alive else 0, "wait": wait})()


class ServerTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        catalog, self.game = tmp / "cat", tmp / "game"
        self.game.mkdir()
        (self.game / "game.exe").write_text("x")
        (catalog).mkdir()
        (catalog / "catalog.json").write_text(json.dumps(
            {"name": "T", "games": {"g": {"name": "Game", "exe": "game.exe", "settings": "s.json"}}, "projects": []}))
        (catalog / "s.json").write_text(json.dumps({"file": "savedata/settings.toml", "groups": [{"id": "a", "title": "A", "sections": [
            {"title": "S", "fields": [{"key": "audio.master_volume", "label": "V", "type": "slider", "min": 0, "max": 1}]}]}]}))
        (self.game / "savedata").mkdir()
        (self.game / "savedata/settings.toml").write_text("[audio]\nmaster_volume = 1  # loud\n")
        make_mod(tmp / "src", "1.0.0", {"mods/a.txt": "a", "mods/x.dll": "bin"})
        publish.publish(tmp / "src", catalog)

        import os
        os.environ["MODHUB_HOME"] = str(tmp / "home")
        (tmp / "home").mkdir()
        (tmp / "home" / "state.json").write_text(json.dumps({"game_paths": {"g": str(self.game)}}))
        self.hub = server.Hub(str(catalog))
        self.hub.hotkeys_enabled = False  # never grab the real Shift+Tab during tests (the overlay tests opt in with a fake)
        self.hub.game_windows, self.hub.spawn_toast = (lambda pid: []), (lambda hotkey: None)  # no real reminder windows
        for _ in range(100):
            if not self.hub.loading:
                break
            time.sleep(0.02)
        self.token = "tok"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.hub, self.token, server.Watchdog(False)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_port}"

    def call(self, path, body=None, token=None, host=None):
        url = f"{self.base}/api/{path}{'&' if '?' in path else '?'}t={self.token if token is None else token}"
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def wait_job(self):
        for _ in range(200):
            job = self.call("state")[1]["job"]
            if job and job["state"] != "running":
                return job
            time.sleep(0.02)
        self.fail("job did not finish")

    def test_the_public_catalog_is_the_default_and_its_shipped_copy_works_without_internet(self):
        self.addCleanup(setattr, server, "DEFAULT_CATALOG", server.DEFAULT_CATALOG)
        self.addCleanup(setattr, server, "OFFLINE_CATALOG", server.OFFLINE_CATALOG)
        server.DEFAULT_CATALOG = "http://127.0.0.1:9/nothing-here/"  # the internet is down
        server.OFFLINE_CATALOG = self.hub.db.source                  # the copy that came with ModHub
        self.call("source", {"value": ""})  # empty = back to the default
        for _ in range(100):
            state = self.call("state")[1]
            if not state["loading"]:
                break
            time.sleep(0.05)
        self.assertEqual((state["sourceIsDefault"], state["offline"], state["error"]), (True, True, None))
        self.assertEqual(state["source"], server.DEFAULT_CATALOG)
        self.assertTrue(state["games"][0]["hasSettings"])  # games and their settings still work offline
        self.assertIsNone(core.State(core.home_dir()).source)  # remembered as "the default", not as an address
        self.call("source", {"value": "https://example.com/my-catalog/"})
        self.assertEqual(core.State(core.home_dir()).source, "https://example.com/my-catalog/")

    def test_requires_token_and_valid_host(self):
        self.assertEqual(self.call("state", token="wrong")[0], 403)
        self.assertEqual(self.call("state", host="evil.example")[0], 403)
        self.assertEqual(self.call("state")[0], 200)

    def test_executable_install_needs_confirmation(self):
        status, body = self.call("install", {"id": "m", "version": "1.0.0"})
        self.assertEqual(status, 428)
        self.assertFalse((self.game / "mods/x.dll").exists())
        self.assertEqual(self.call("install", {"id": "m", "version": "1.0.0", "confirmed": True})[0], 200)
        self.assertEqual(self.wait_job()["state"], "done")
        self.assertTrue((self.game / "mods/x.dll").exists())
        state = self.call("state")[1]
        self.assertEqual(state["projects"][0]["installed"], "1.0.0")
        self.assertEqual(state["projects"][0]["status"], "installed")

    def test_uninstall_and_profiles(self):
        self.call("install", {"id": "m", "version": "1.0.0", "confirmed": True})
        self.wait_job()
        names = [p["name"] for p in self.call("state")[1]["games"][0]["profiles"]]
        self.assertEqual(names, ["Standard", "P"])
        self.call("uninstall", {"id": "m"})
        self.assertEqual(self.wait_job()["state"], "done")
        self.assertFalse((self.game / "mods").exists())

    def test_unknown_project_and_version(self):
        self.assertEqual(self.call("install", {"id": "nope", "version": "1", "confirmed": True})[0], 404)
        self.assertEqual(self.call("install", {"id": "m", "version": "9.9.9", "confirmed": True})[0], 404)

    def test_game_settings_read_save_restore(self):
        self.assertTrue(self.call("state")[1]["games"][0]["hasSettings"])
        status, body = self.call("settings?game=g")
        self.assertEqual((status, body["exists"], body["values"]["audio.master_volume"], body["running"]), (200, True, 1, False))
        self.assertEqual(self.call("settings-save", {"game": "g", "changes": {"audio.master_volume": 0.25}})[1], {"saved": ["audio.master_volume"]})
        self.assertEqual((self.game / "savedata/settings.toml").read_text(), "[audio]\nmaster_volume = 0.25  # loud\n")
        self.assertEqual(self.call("settings-save", {"game": "g", "changes": {"audio.nope": 1}})[0], 400)
        self.assertEqual(self.call("settings-save", {"game": "g", "changes": {}})[0], 400)
        self.assertEqual(self.call("settings-restore", {"game": "g"})[0], 200)
        self.assertEqual((self.game / "savedata/settings.toml").read_text(), "[audio]\nmaster_volume = 1  # loud\n")
        self.assertEqual(self.call("settings?game=nope")[0], 404)

    def test_custom_games_are_added_listed_persisted_and_removed(self):
        exe = self.game.parent / "Cool_Game" / "bin" / "cg.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"x")
        self.assertEqual(server.Hub._suggest_name(exe), "Cool Game")  # generic 'bin' folder is skipped
        self.assertEqual(self.call("add-game", {"name": "Cool"})[0], 400)  # nothing picked yet
        self.hub.pending = {"kind": "exe", "path": exe}
        self.assertEqual(self.call("add-game", {"name": "  "})[0], 400)
        gid = self.call("add-game", {"name": "Cool Game"})[1]["id"]
        self.assertEqual(gid, "custom-cool-game")
        games = {g["id"]: g for g in self.call("state")[1]["games"]}
        self.assertEqual((games[gid]["custom"], games[gid]["found"], games[gid]["hasSettings"]), (True, True, False))
        self.assertIn(gid, core.State(core.home_dir()).custom_games)  # survives a restart
        self.assertEqual(self.call("settings?game=" + gid)[0], 404)
        self.assertEqual(self.call("remove-game", {"game": "g"})[0], 404)  # catalog games can't be removed
        exe.unlink()
        self.assertFalse({g["id"]: g for g in self.call("state")[1]["games"]}[gid]["found"])
        self.assertEqual(self.call("remove-game", {"game": gid})[0], 200)
        self.assertNotIn(gid, {g["id"] for g in self.call("state")[1]["games"]})
        self.assertTrue(exe.parent.exists())  # files are never touched

    def test_emulator_games_are_launched_with_the_emulators_arguments(self):
        emu = self.game.parent / "emus" / "Dolphin.exe"
        emu.parent.mkdir()
        emu.write_bytes(b"x")
        rom = self.game.parent / "roms" / "My Game (USA).iso"
        rom.parent.mkdir()
        rom.write_bytes(b"x")
        state = self.call("state")[1]
        dolphin = next(e for e in state["emulators"] if e["id"] == "dolphin")
        self.assertFalse(dolphin["configured"])
        self.assertEqual(self.call("play", {"game": "x"})[0], 400)  # unknown game
        self.hub._set_emulator_path("dolphin", emu)
        self.hub.pending = {"kind": "rom", "path": rom, "emulator": "dolphin"}
        gid = self.call("add-game", {"name": "My Game"})[1]["id"]
        game = next(g for g in self.call("state")[1]["games"] if g["id"] == gid)
        self.assertEqual((game["found"], game["emulator"], game["missing"]), (True, "dolphin", None))
        self.assertEqual([p["name"] for p in game["profiles"]], ["Standard", "Schermo intero"])

        launched = []
        real = core.launch_command
        core.launch_command = lambda exe, args, cwd: launched.append((str(exe), args, str(cwd)))
        self.addCleanup(setattr, core, "launch_command", real)
        self.assertEqual(self.call("play", {"game": gid, "project": None, "name": "Standard"})[0], 200)
        self.assertEqual(launched[-1], (str(emu), ["-b", "-e", str(rom)], str(emu.parent)))  # path with spaces stays one argument
        self.call("play", {"game": gid, "project": None, "name": "Schermo intero"})
        self.assertIn("Dolphin.Display.Fullscreen=True", launched[-1][1])

        emu.unlink()  # emulator disappears -> clear message, nothing launched
        n = len(launched)
        status, body = self.call("play", {"game": gid, "project": None, "name": "Standard"})
        self.assertEqual((status, len(launched)), (400, n))
        self.assertIn("Emulatori", body["error"])
        self.assertEqual(next(g for g in self.call("state")[1]["games"] if g["id"] == gid)["missing"], "emulator")

    def test_custom_emulator_can_be_added_used_and_forgotten(self):
        program = self.game.parent / "mystery" / "retro.exe"
        program.parent.mkdir()
        program.write_bytes(b"x")
        rom = self.game.parent / "cart.bin"
        rom.write_bytes(b"x")
        self.assertEqual(self.call("emu-custom-add", {"name": "Retro", "args": "{rom}"})[0], 400)  # nothing picked
        self.hub.pending = {"kind": "emu", "path": program}
        self.assertEqual(self.call("emu-custom-add", {"name": "Retro", "args": "--fast"})[0], 400)  # no {rom}
        eid = self.call("emu-custom-add", {"name": "Retro", "args": "--fast {rom}"})[1]["id"]
        emu = next(e for e in self.call("state")[1]["emulators"] if e["id"] == eid)
        self.assertEqual((emu["custom"], emu["configured"]), (True, True))
        self.hub.pending = {"kind": "rom", "path": rom, "emulator": eid}
        gid = self.call("add-game", {"name": "Cart"})[1]["id"]
        launched = []
        real = core.launch_command
        core.launch_command = lambda exe, args, cwd: launched.append(args)
        self.addCleanup(setattr, core, "launch_command", real)
        self.call("play", {"game": gid, "project": None, "name": "Standard"})
        self.assertEqual(launched, [["--fast", str(rom)]])
        self.assertEqual(self.call("emu-forget", {"emulator": eid})[0], 400)  # still has a game
        self.call("remove-game", {"game": gid})
        self.assertEqual(self.call("emu-forget", {"emulator": eid})[0], 200)
        self.assertNotIn(eid, [e["id"] for e in self.call("state")[1]["emulators"]])

    def test_emulator_download_plan_install_update_check_and_uninstall(self):
        import emuinstall
        from test_emuinstall import FakeSite, make_zip
        emuinstall.ALLOW_LOCAL = True
        self.addCleanup(setattr, emuinstall, "ALLOW_LOCAL", False)
        site = FakeSite()
        self.addCleanup(site.close)
        for emu in self.hub.emulators:  # point the bundled Dolphin entry at the fake site
            if emu["id"] == "dolphin":
                emu["install"] = {"type": "github", "repo": "o/r", "api": site.base, "asset": r"-win\.zip$", "portable_marker": "portable.txt"}
        site.publish_github({"dolphin-1.0-win.zip": make_zip({"Dolphin-x64/Dolphin.exe": b"v1"})}, tag="v1.0")

        plan = self.call("emu-plan", {"emulator": "dolphin"})[1]
        self.assertEqual((plan["name"], plan["version"], plan["verified"], plan["update"]), ("dolphin-1.0-win.zip", "1.0", True, False))
        self.assertEqual(self.call("emu-plan", {"emulator": "project64"})[0], 404)  # no automatic download for it
        self.assertEqual(self.call("emu-install", {"emulator": "dolphin", "asset": "something-else.zip"})[0], 409)
        self.assertEqual(self.call("emu-install", {"emulator": "dolphin", "asset": plan["name"]})[0], 200)
        job = self.wait_job()
        self.assertEqual((job["state"], job["kind"], job["pid"]), ("done", "emu-install", "dolphin"))
        self.assertIn("Dolphin 1.0 installato", job["message"])
        dolphin = next(e for e in self.call("state")[1]["emulators"] if e["id"] == "dolphin")
        self.assertEqual((dolphin["configured"], dolphin["managed"], dolphin["version"], dolphin["canInstall"]), (True, True, "1.0", True))
        self.assertEqual(self.call("emu-forget", {"emulator": "dolphin"})[0], 400)  # use uninstall instead

        self.assertFalse(self.call("emu-check", {})[1]["updates"]["dolphin"]["available"])
        site.publish_github({"dolphin-1.1-win.zip": make_zip({"Dolphin-x64/Dolphin.exe": b"v2"})}, tag="v1.1")
        check = self.call("emu-check", {})[1]["updates"]["dolphin"]
        self.assertEqual((check["available"], check["version"]), (True, "1.1"))
        self.call("emu-install", {"emulator": "dolphin"})
        self.assertIn("aggiornato", self.wait_job()["message"])
        exe = Path(self.hub.db.emulators["dolphin"])
        self.assertEqual(exe.read_bytes(), b"v2")

        self.assertEqual(self.call("emu-uninstall", {"emulator": "dolphin"})[0], 200)
        self.assertFalse(exe.exists())
        dolphin = next(e for e in self.call("state")[1]["emulators"] if e["id"] == "dolphin")
        self.assertEqual((dolphin["configured"], dolphin["managed"]), (False, False))
        self.assertEqual(self.call("emu-uninstall", {"emulator": "dolphin"})[0], 400)  # not installed anymore

    # ---- emulator settings, queued settings and the in-game panel

    INI = "[EmuCore/GS]\nupscale_multiplier = 1\nAspectRatio = 16:9\nRenderer = 3\n\n[SPU2/Output]\nStandardVolume = 100\n"

    def pcsx2_setup(self):
        """A fake PCSX2 (program + Documents data folder) and a PS2 game that uses it. Returns (game id, ini path)."""
        import settings as settings_module
        docs = self.game.parent / "Documents"
        ini = docs / "PCSX2" / "inis" / "PCSX2.ini"
        ini.parent.mkdir(parents=True, exist_ok=True)
        ini.write_text(self.INI)
        real = settings_module._documents
        settings_module._documents = lambda: docs
        self.addCleanup(setattr, settings_module, "_documents", real)
        exe = self.game.parent / "Program Files" / "PCSX2" / "pcsx2-qt.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"x")
        rom = self.game.parent / "roms" / "Game.iso"
        rom.parent.mkdir(exist_ok=True)
        rom.write_bytes(b"x")
        self.hub._set_emulator_path("pcsx2", exe)
        self.hub.pending = {"kind": "rom", "path": rom, "emulator": "pcsx2"}
        return self.call("add-game", {"name": "PS2 Game"})[1]["id"], ini

    def test_emulator_settings_can_be_read_and_saved_by_emulator_or_through_its_game(self):
        gid, ini = self.pcsx2_setup()
        state = self.call("state")[1]
        self.assertTrue(next(e for e in state["emulators"] if e["id"] == "pcsx2")["hasSettings"])
        self.assertFalse(next(e for e in state["emulators"] if e["id"] == "dolphin")["hasSettings"])
        self.assertTrue(next(g for g in state["games"] if g["id"] == gid)["hasSettings"])
        for target in ("emu:pcsx2", gid):  # the same page, reached either way
            status, body = self.call(f"settings?game={target}")
            self.assertEqual((status, body["target"], body["values"]["EmuCore/GS.upscale_multiplier"], body["values"]["EmuCore/GS.AspectRatio"]),
                             (200, "emu:pcsx2", 1, "16:9"))
        self.assertIn("PCSX2", self.call("settings?game=emu:pcsx2")[1]["title"])
        self.assertEqual(self.call("settings-save", {"game": gid, "changes": {"EmuCore/GS.upscale_multiplier": 3, "SPU2/Output.StandardVolume": 70}})[0], 200)
        self.assertEqual(ini.read_text(), self.INI.replace("upscale_multiplier = 1", "upscale_multiplier = 3").replace("StandardVolume = 100", "StandardVolume = 70"))
        self.assertEqual(self.call("settings-save", {"game": "emu:pcsx2", "changes": {"EmuCore/GS.nope": 1}})[0], 400)
        self.assertEqual(self.call("settings?game=emu:dolphin")[0], 404)
        self.assertEqual(self.call("settings-restore", {"game": "emu:pcsx2"})[0], 200)
        self.assertEqual(ini.read_text(), self.INI)

    def test_a_games_own_settings_file_beats_the_global_one_so_it_is_shown_and_kept_in_step(self):
        gid, ini = self.pcsx2_setup()
        profile = ini.parent.parent / "gamesettings" / "SLUS-1_ABCD.ini"
        profile.parent.mkdir()
        profile.write_text("[EmuCore/GS]\nfilter = 1\nAspectRatio = 4:3\n")
        log = core.home_dir() / "live" / "pcsx2.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"[0,1] Loading game settings from '{profile}'...\n")
        self.hub._learn_profile("pcsx2", log)
        self.assertEqual(self.call(f"settings?game={gid}")[1]["values"]["EmuCore/GS.AspectRatio"], "4:3")  # what really applies
        self.assertEqual(self.call("settings-save", {"game": gid, "changes": {"EmuCore/GS.AspectRatio": "16:9", "EmuCore/GS.upscale_multiplier": 3}})[0], 200)
        self.assertIn("AspectRatio = 16:9", ini.read_text())
        self.assertEqual(profile.read_text(), "[EmuCore/GS]\nfilter = 1\nAspectRatio = 16:9\n")  # only keys it already had
        self.assertEqual(core.State(core.home_dir()).prefs["profiles"], {"emu:pcsx2": str(profile)})

    def test_settings_chosen_while_the_game_runs_are_queued_and_applied_when_it_closes(self):
        import settings as settings_module
        gid, ini = self.pcsx2_setup()
        running = {"now": True}
        real = settings_module.file_locked
        settings_module.file_locked = lambda path: running["now"]
        self.addCleanup(setattr, settings_module, "file_locked", real)
        change = {"EmuCore/GS.upscale_multiplier": 4}
        self.assertEqual(self.call("settings-save", {"game": gid, "changes": change})[0], 409)  # normal save refuses
        self.assertEqual(self.call("settings-save", {"game": gid, "changes": {"EmuCore/GS.upscale_multiplier": 99}, "queue": True})[0], 400)  # bad values refused now
        self.assertEqual(self.call("settings-save", {"game": gid, "changes": change, "queue": True})[1], {"queued": ["EmuCore/GS.upscale_multiplier"]})
        self.assertEqual(ini.read_text(), self.INI)  # nothing written while running
        self.assertEqual(self.call(f"settings?game={gid}")[1]["pending"], change)
        self.assertEqual(core.State(core.home_dir()).pending_settings, {"emu:pcsx2": change})  # survives a restart
        self.assertEqual(self.call("settings-save", {"game": gid, "changes": {}, "queue": True})[1], {"queued": []})  # "Annulla" clears it
        self.assertEqual(self.call(f"settings?game={gid}")[1]["pending"], {})
        self.call("settings-save", {"game": gid, "changes": change, "queue": True})
        running["now"] = False  # the game closes
        self.hub.apply_pending()
        self.assertIn("upscale_multiplier = 4", ini.read_text())
        self.assertEqual(self.hub.db.pending_settings, {})
        self.assertIn("PCSX2", self.call("state")[1]["notice"]["text"])
        # a queued save with the game already closed is simply written
        self.assertEqual(self.call("settings-save", {"game": gid, "changes": {"SPU2/Output.StandardVolume": 50}, "queue": True})[1], {"saved": ["SPU2/Output.StandardVolume"]})

    def run_session(self, fake_hotkey_ok=True, own_overlay=None, game="ps2"):
        """Start a (fake) game through play(); returns (proc, hotkey events)."""
        import settings as settings_module
        events = []
        proc = fake_process()
        self.launched = []

        self.hotkey_labels = []
        self.busy_labels = getattr(self, "busy_labels", set())

        class FakeHotkey:
            def __init__(inner, on_press, label=None):
                self.hotkey_labels.append(label)
                inner.on_press, inner.label = on_press, label

            def start(inner):
                events.append("start")
                return fake_hotkey_ok and inner.label not in self.busy_labels

            def stop(self):
                events.append("stop")

        real_launch, real_cmd = core.launch, core.launch_command

        def start(exe, args, cwd=None):
            proc.started = True
            self.launched.append((str(exe), list(args)))
            return proc
        core.launch = lambda root, exe, env: start(Path(root) / exe, [])
        core.launch_command = start
        self.addCleanup(setattr, core, "launch", real_launch)
        self.addCleanup(setattr, core, "launch_command", real_cmd)
        real_locked = settings_module.file_locked
        settings_module.file_locked = lambda path: proc.started and proc.alive  # locked only while the game really runs
        self.addCleanup(setattr, settings_module, "file_locked", real_locked)
        server.SESSION_POLL = 0.05
        self.addCleanup(setattr, server, "SESSION_POLL", 1.0)
        self.hub.hotkeys_enabled, self.hub.hotkey_factory = True, FakeHotkey
        gid = self.pcsx2_setup()[0] if game == "ps2" else "g"
        status, body = self.call("play", {"game": gid, "project": None, "name": "Standard"})
        self.assertEqual(status, 200, body)
        return proc, events, gid

    def wait_session_end(self):
        """Until the session is over and ModHub has finished tidying up after it (settings written, panel gone)."""
        for _ in range(100):
            if self.call("state")[1]["session"] is None and not self.hub._ending:
                return
            time.sleep(0.05)
        self.fail("session did not end")

    def test_session_registers_the_hotkey_while_playing_and_releases_it_and_applies_queued_settings_after(self):
        proc, events, gid = self.run_session()
        session = self.call("state")[1]["session"]
        self.assertEqual((session["gid"], session["target"], session["hotkey"], session["own"], session["hotkeyBusy"]),
                         (gid, "emu:pcsx2", "Ctrl+Shift+Tab", None, False))  # not Shift+Tab: PCSX2 and BT3 use that themselves
        self.assertEqual((events, self.hotkey_labels), (["start"], ["Ctrl+Shift+Tab"]))
        self.call("settings-save", {"game": gid, "changes": {"EmuCore/GS.upscale_multiplier": 2}, "queue": True})
        proc.alive = False
        self.wait_session_end()
        self.assertEqual(events, ["start", "stop"])  # Shift+Tab is released the moment the game is gone
        self.assertEqual(self.hub.db.pending_settings, {})
        self.assertIn("applicate", self.call("state")[1]["notice"]["text"])

    def test_launcher_that_hands_over_is_followed_by_the_program_lock_but_a_long_run_is_not(self):
        import settings as settings_module
        proc, events, gid = self.run_session()
        locked = {"now": True}
        settings_module.file_locked = lambda path: locked["now"]  # the real game keeps its program locked
        proc.alive = False  # the process we launched exits at once (a launcher stub)...
        time.sleep(0.4)
        self.assertIsNotNone(self.call("state")[1]["session"], "...but the game itself is still running")
        locked["now"] = False  # ...and now the game closes
        self.wait_session_end()
        self.assertEqual(events, ["start", "stop"])

        # a game that ran for a long time and exited ends the session even if another copy of the program is open
        proc2, events2, _ = self.run_session()
        self.addCleanup(setattr, server, "HANDOFF_WINDOW", server.HANDOFF_WINDOW)
        server.HANDOFF_WINDOW = 0
        settings_module.file_locked = lambda path: True  # another instance of the same program is running
        proc2.alive = False
        self.wait_session_end()
        self.assertEqual(events2, ["start", "stop"])

    def bt3_like_game(self, restart=True):
        """A game with its own in-game menu (Shift+Tab) that ModHub can restart straight into play."""
        schema = Path(self.hub.source().location) / "s.json"
        data = json.loads(schema.read_text())
        data["ownOverlay"] = "Shift+Tab"
        if restart:
            data["restartEnv"] = {"PS2X_FE_AUTOPLAY": "1"}
        schema.write_text(json.dumps(data))

    def test_settings_a_native_game_reads_from_memory_change_at_once_and_are_still_saved(self):
        schema = Path(self.hub.source().location) / "s.json"
        data = json.loads(schema.read_text())
        data["memoryLive"] = "ps2x"
        data["groups"][0]["sections"][0]["fields"].append({"key": "audio.music_volume", "label": "M", "type": "slider", "min": 0, "max": 1})
        schema.write_text(json.dumps(data))
        written, closed = [], []

        class FakeGameMemory:
            def __init__(inner, pid, exe, locations, file_values):
                inner.pid = pid
                assert file_values()["audio.master_volume"] == 1  # what the game loaded: how its settings are found

            @staticmethod
            def handles(key):
                return key.startswith("audio.")

            def set(inner, key, value, preview=False):
                written.append((inner.pid, key, value))
                return True

            def prepare(inner):
                pass

            def close(inner):
                closed.append(True)
        self.hub.memory_factory = FakeGameMemory
        proc, _, gid = self.run_session(game="g")
        self.assertEqual(self.call("state")[1]["session"]["liveKeys"], ["audio.master_volume", "audio.music_volume"])
        status, body = self.call("settings-save", {"game": "g", "changes": {"audio.music_volume": 0.3}, "queue": True})
        self.assertEqual(body, {"queued": ["audio.music_volume"], "live": ["audio.music_volume"]})
        self.assertEqual(written, [(4321, "audio.music_volume", 0.3)])
        self.assertEqual(self.call("live-set", {"key": "audio.master_volume", "value": 0.7}), (200, {"live": True}))
        self.assertEqual(self.call("live-set", {"key": "audio.master_volume", "value": 7})[0], 400)  # checked like any value
        self.assertEqual(written[-1], (4321, "audio.master_volume", 0.7))
        proc.alive = False
        self.wait_session_end()
        self.assertEqual(closed, [True])
        self.assertIn("music_volume = 0.3", (self.game / "savedata/settings.toml").read_text())  # kept for next time
        self.assertEqual(self.call("live-set", {"key": "audio.master_volume", "value": 0.7})[0], 409)  # game gone

    def test_when_the_game_is_on_screen_a_reminder_shows_which_keys_open_the_panel(self):
        self.addCleanup(setattr, server, "TOAST_SETTLE", server.TOAST_SETTLE)
        server.TOAST_SETTLE = 0
        real_rect = overlay.window_rect
        sizes = {1: (0, 0, 1024, 600), 2: (0, 0, 1920, 1080)}  # the start menu, then the game itself
        overlay.window_rect = lambda hwnd: sizes[hwnd]
        self.addCleanup(setattr, overlay, "window_rect", real_rect)
        real_screen = overlay.screen_size
        overlay.screen_size = lambda: (1920, 1080)
        self.addCleanup(setattr, overlay, "screen_size", real_screen)
        shown, windows = [], {"now": []}
        toast = type("T", (), {"alive": True, "poll": lambda t: None if t.alive else 0, "terminate": lambda t: setattr(t, "alive", False)})()
        self.hub.spawn_toast = lambda hotkey: (shown.append(hotkey), toast)[1]
        self.hub.game_windows = lambda pid: windows["now"]
        proc, _, gid = self.run_session(game="g")
        windows["now"] = [1]
        time.sleep(0.8)
        self.assertEqual(shown, [])  # only the launcher's small window so far
        windows["now"] = [2]
        for _ in range(100):
            if shown:
                break
            time.sleep(0.02)
        self.assertEqual(shown, ["Ctrl+Shift+Tab"])
        proc.alive = False
        self.wait_session_end()
        self.assertFalse(toast.alive)  # gone with the game

    def test_games_without_live_control_keep_the_panels_changes_for_later_instead_of_failing(self):
        self.bt3_like_game()
        proc, _, gid = self.run_session(game="g")
        status, body = self.call("live-apply", {"changes": {"audio.master_volume": 0.5}})
        self.assertEqual((status, body), (200, {"live": [], "waiting": ["audio.master_volume"]}))
        self.assertEqual(self.hub.db.pending_settings, {"g": {"audio.master_volume": 0.5}})
        proc.alive = False
        self.wait_session_end()

    def test_restart_writes_the_waiting_settings_and_starts_the_game_again_straight_into_play(self):
        self.bt3_like_game()
        proc, _, gid = self.run_session(game="g")
        self.assertTrue(self.call("state")[1]["session"]["canRestart"])
        self.call("settings-save", {"game": "g", "changes": {"audio.master_volume": 0.5}, "queue": True})
        closed = []
        self.hub.close_game = lambda p: (closed.append(p.pid), setattr(p, "alive", False))
        second, relaunched = fake_process(999), []

        def relaunch(root, exe, env):
            relaunched.append((env, (self.game / "savedata/settings.toml").read_text()))  # settings already written
            second.started = True
            return second
        core.launch = relaunch
        self.assertEqual(self.call("game-restart", {}), (200, {"restarting": True}))
        for _ in range(200):
            if relaunched:
                break
            time.sleep(0.02)
        self.assertEqual(closed, [4321])  # asked to close, not killed
        env, text = relaunched[0]
        self.assertEqual(env, {"PS2X_FE_AUTOPLAY": "1"})  # straight into the game, past its start menu
        self.assertIn("master_volume = 0.5", text)
        self.assertEqual(self.hub.db.pending_settings, {})
        session = self.call("state")[1]["session"]
        self.assertEqual((session["gid"], session["restarting"]), ("g", False))
        second.alive = False
        self.wait_session_end()

    def test_a_game_that_does_not_close_is_left_alone(self):
        self.bt3_like_game()
        proc, _, gid = self.run_session(game="g")
        self.addCleanup(setattr, server, "RESTART_WAIT", server.RESTART_WAIT)
        server.RESTART_WAIT = 0.2
        self.hub.close_game = lambda p: None  # the game ignores the request
        self.call("game-restart", {})
        for _ in range(100):
            if (self.call("state")[1]["notice"] or {}).get("error"):
                break
            time.sleep(0.02)
        self.assertIn("non si è chiuso", self.call("state")[1]["notice"]["text"])
        self.assertTrue(proc.alive)
        proc.alive = False
        self.wait_session_end()

    def test_restart_is_only_offered_where_the_game_supports_it(self):
        self.bt3_like_game(restart=False)
        proc, _, gid = self.run_session(game="g")
        self.assertFalse(self.call("state")[1]["session"]["canRestart"])
        self.assertEqual(self.call("game-restart", {})[0], 409)
        proc.alive = False
        self.wait_session_end()

    def test_the_games_own_menu_is_opened_by_pressing_its_keys_in_the_game(self):
        self.bt3_like_game()
        proc, _, gid = self.run_session(game="g")
        real = overlay.windows_of_pid
        overlay.windows_of_pid = lambda pid, min_size=200: [77] if pid == 4321 else []
        self.addCleanup(setattr, overlay, "windows_of_pid", real)
        pressed, front = [], {"hwnd": None}
        self.hub.press_keys = pressed.append
        self.hub.focus_window = lambda hwnd: front.update(hwnd=hwnd)
        self.hub.foreground_window = lambda: front["hwnd"]
        self.assertEqual(self.call("game-menu", {}), (200, {"opened": True}))
        self.assertEqual(pressed, ["Shift+Tab"])
        front["hwnd"] = 5  # the player clicked elsewhere...
        self.hub.focus_window = lambda hwnd: None  # ...and Windows refuses to switch: say so, press nothing
        status, body = self.call("game-menu", {})
        self.assertEqual((status, pressed), (409, ["Shift+Tab"]))
        self.assertIn("Shift+Tab", body["error"])
        proc.alive = False
        self.wait_session_end()

    def test_a_value_changed_again_in_the_games_own_menu_is_not_overwritten_when_the_game_closes(self):
        self.bt3_like_game()
        proc, _, gid = self.run_session(game="g")
        self.call("settings-save", {"game": "g", "changes": {"audio.master_volume": 0.5}, "queue": True})
        (self.game / "savedata/settings.toml").write_text("[audio]\nmaster_volume = 0.8\n")  # the game's menu, later
        proc.alive = False
        self.wait_session_end()
        self.assertIn("master_volume = 0.8", (self.game / "savedata/settings.toml").read_text())
        self.assertIn("menu del gioco", self.call("state")[1]["notice"]["text"])
        self.assertEqual(self.hub.db.pending_settings, {})

    def own_overlay_game(self):
        schema = Path(self.hub.source().location) / "s.json"
        data = json.loads(schema.read_text())
        data["ownOverlay"] = "Shift+Tab"
        schema.write_text(json.dumps(data))

    def test_a_game_with_its_own_menu_still_gets_the_modhub_panel_on_another_combination(self):
        self.own_overlay_game()  # like BT3: its own menu opens with Shift+Tab
        proc, events, _ = self.run_session(game="g")
        session = self.call("state")[1]["session"]
        self.assertEqual((events, session["hotkey"], session["own"], session["hotkeyClash"]), (["start"], "Ctrl+Shift+Tab", "Shift+Tab", False))
        proc.alive = False
        self.wait_session_end()

    def test_the_combination_is_not_registered_when_the_game_itself_uses_it(self):
        self.own_overlay_game()
        self.call("prefs", {"overlayHotkey": "shift+tab"})  # the user picks the very combination the game uses
        proc, events, _ = self.run_session(game="g")
        session = self.call("state")[1]["session"]
        self.assertEqual((events, session["hotkey"], session["hotkeyClash"], session["hotkeyBusy"]), ([], None, True, False))
        proc.alive = False
        self.wait_session_end()

    def test_choosing_the_panel_key_is_validated_saved_and_applied_to_the_running_game(self):
        self.assertEqual(self.call("state")[1]["prefs"]["overlayHotkey"], "Ctrl+Shift+Tab")
        self.assertIn("Ctrl+Shift+Tab", self.call("state")[1]["prefs"]["hotkeyPresets"])
        for bad in ("", "M", "Ctrl+Plus", "Hyper+X"):
            self.assertEqual(self.call("prefs", {"overlayHotkey": bad})[0], 400, bad)
        proc, events, _ = self.run_session()
        self.assertEqual(self.call("prefs", {"overlayHotkey": "ctrl+alt+o"})[1]["overlayHotkey"], "Ctrl+Alt+O")
        self.assertEqual((events, self.hotkey_labels), (["start", "stop", "start"], ["Ctrl+Shift+Tab", "Ctrl+Alt+O"]))  # moved at once
        self.assertEqual(self.call("state")[1]["session"]["hotkey"], "Ctrl+Alt+O")
        self.assertEqual(core.State(core.home_dir()).prefs["overlay_hotkey"], "Ctrl+Alt+O")  # remembered
        proc.alive = False
        self.wait_session_end()
        self.assertEqual(events, ["start", "stop", "start", "stop"])

    def test_if_another_program_owns_the_default_combination_the_next_free_one_is_used_and_remembered(self):
        self.busy_labels = {"Ctrl+Shift+Tab"}
        proc, events, _ = self.run_session()
        session = self.call("state")[1]["session"]
        self.assertEqual((session["hotkey"], session["hotkeyBusy"]), ("Ctrl+Alt+O", False))
        self.assertEqual(self.hotkey_labels, ["Ctrl+Shift+Tab", "Ctrl+Alt+O"])
        self.assertEqual(self.call("state")[1]["prefs"]["overlayHotkey"], "Ctrl+Alt+O")  # the menu in the window shows the one in use
        proc.alive = False
        self.wait_session_end()

    def test_a_combination_chosen_by_the_user_is_never_swapped_for_another(self):
        self.call("prefs", {"overlayHotkey": "Ctrl+Shift+Tab"})
        self.busy_labels = {"Ctrl+Shift+Tab"}
        proc, events, _ = self.run_session()
        session = self.call("state")[1]["session"]
        self.assertEqual((session["hotkey"], session["hotkeyBusy"]), (None, True))
        self.assertEqual(self.hotkey_labels, ["Ctrl+Shift+Tab"])
        proc.alive = False
        self.wait_session_end()

    def test_busy_hotkey_is_reported_not_hidden(self):
        proc, events, _ = self.run_session(fake_hotkey_ok=False)
        session = self.call("state")[1]["session"]
        self.assertEqual((session["hotkey"], session["hotkeyBusy"]), (None, True))
        proc.alive = False
        self.wait_session_end()

    def fake_panel(self):
        """Stand-in for the native panel process and the Windows calls around it."""
        panel = type("Panel", (), {"alive": True, "poll": lambda s: None if s.alive else 0,
                                   "wait": lambda s, t=None: setattr(s, "alive", False), "kill": lambda s: setattr(s, "alive", False)})()
        spawned, closed, focused = [], [], []
        self.hub.spawn_overlay = lambda: spawned.append(1) or panel
        for name, fn in {"find_window": lambda title: None, "foreground": lambda: 4242, "close_window": closed.append,
                         "focus": focused.append}.items():
            real = getattr(overlay, name)
            setattr(overlay, name, fn)
            self.addCleanup(setattr, overlay, name, real)
        self.hub.base_url = "http://127.0.0.1:1/?t=tok"
        return panel, spawned, closed, focused

    def test_overlay_is_a_native_panel_that_gives_the_keyboard_back_to_the_game_when_closed(self):
        proc, events, gid = self.run_session()
        panel, spawned, closed, focused = self.fake_panel()
        self.call("overlay-toggle", {})  # first press: the panel shows...
        self.assertEqual((spawned, self.call("overlay-wanted")[1]), ([1], {"wanted": True, "session": True}))
        self.call("overlay-toggle", {})  # ...second press hides it and hands the keyboard back
        self.assertEqual((self.call("overlay-wanted")[1]["wanted"], focused), (False, [4242]))
        self.call("overlay-toggle", {})  # the same program shows it again: nothing to start, so it is instant
        self.assertEqual((spawned, self.call("overlay-wanted")[1]["wanted"]), ([1], True))
        self.assertTrue(panel.alive)
        proc.alive = False
        self.wait_session_end()
        self.assertFalse(panel.alive)  # it goes away with the game
        self.assertEqual(self.call("overlay-wanted")[1], {"wanted": False, "session": False})

    def test_overlay_does_nothing_without_a_running_game(self):
        panel, spawned, closed, focused = self.fake_panel()
        self.assertEqual(self.call("overlay-toggle", {})[0], 200)
        self.assertEqual(spawned, [])

    def test_hotkeys_and_log_are_prepared_before_the_emulator_starts_and_live_control_is_offered(self):
        import settings as settings_module
        proc, events, gid = self.run_session()
        exe, args = self.launched[-1]
        self.assertEqual(args[0], "-logfile")  # PCSX2 writes what it says into a file ModHub can read
        self.assertTrue(args[1].endswith("pcsx2.log"))
        ini = (self.game.parent / "Documents" / "PCSX2" / "inis" / "PCSX2.ini").read_text()
        self.assertIn("[Hotkeys]", ini)
        for line in ("IncreaseUpscaleMultiplier = Keyboard/F13", "CycleBlendingAccuracy = Keyboard/F15"):
            self.assertIn(line, ini)
        self.assertTrue((self.game.parent / "Documents" / "PCSX2" / "inis" / "PCSX2.ini.modhub-bak").exists())
        self.assertTrue(self.call("state")[1]["session"]["live"])
        state = self.call("live")[1]
        self.assertTrue(state["available"])
        self.assertEqual({c["id"]: c["value"] for c in state["controls"]}["resolution"], 1)  # starting value read from the ini
        proc.alive = False
        self.wait_session_end()
        self.assertFalse(self.call("live")[1]["available"])

    def test_live_changes_reach_the_game_and_everything_is_also_queued_for_the_next_launch(self):
        proc, events, gid = self.run_session()
        pressed = []

        class FakeEmulator:  # answers the way PCSX2 does
            multiplier, blending = 1, 0
            names = ["Minimum", "Basic", "Medium", "High", "Full", "Maximum"]

        emu = FakeEmulator()

        def press(hotkey, expect_key=None):  # answers like PCSX2 in Italian, with its internal message keys
            pressed.append(hotkey)
            if hotkey == "IncreaseUpscaleMultiplier":
                emu.multiplier += 1
                return [("UpscaleMultiplierChanged", f"Moltiplicatore upscale aumentato a {emu.multiplier}x. ({emu.multiplier * 512} x 896)")]
            if hotkey == "DecreaseUpscaleMultiplier":
                emu.multiplier -= 1
                return [("UpscaleMultiplierChanged", f"Moltiplicatore upscale diminuito a {emu.multiplier}x. (1024 x 896)")]
            if hotkey == "CycleBlendingAccuracy":
                emu.blending = (emu.blending + 1) % 6
                return [("CycleBlendingAccuracy", f"Precisione del Blending impostata a {emu.names[emu.blending]}.")]
            return []
        self.hub._live.press = press
        status, body = self.call("live-do", {"control": "resolution", "action": "up"})
        self.assertEqual((status, body["value"], body["message"][:12]), (200, 2, "Moltiplicato"))
        # "Qualità" level: 3x, anisotropic 8x, blending Medium: resolution and blending go live, the rest waits
        changes = {"EmuCore/GS.upscale_multiplier": 3, "EmuCore/GS.accurate_blending_unit": 2, "EmuCore/GS.MaxAnisotropy": 8}
        status, body = self.call("live-apply", {"changes": changes})
        self.assertEqual(status, 200, body)
        self.assertEqual({r["key"]: (r["reached"], r["value"]) for r in body["live"]},
                         {"EmuCore/GS.upscale_multiplier": (True, 3), "EmuCore/GS.accurate_blending_unit": (True, 2)})
        self.assertEqual(body["waiting"], ["EmuCore/GS.MaxAnisotropy"])
        self.assertEqual((emu.multiplier, emu.blending), (3, 2))
        self.assertEqual(pressed.count("CycleBlendingAccuracy"), 2)  # Minimum -> Basic -> Medium
        # applying the same level again presses nothing: it is already there
        before = len(pressed)
        self.call("live-apply", {"changes": changes})
        self.assertEqual(len(pressed), before)
        # saved too, so the next launch starts the same way
        self.assertEqual(self.hub.db.pending_settings["emu:pcsx2"], changes)
        self.assertEqual(self.call("live-do", {"control": "nope", "action": "up"})[0], 400)
        proc.alive = False
        self.wait_session_end()
        self.assertIn("upscale_multiplier = 3", (self.game.parent / "Documents" / "PCSX2" / "inis" / "PCSX2.ini").read_text())

    def test_live_control_needs_a_running_game(self):
        self.assertEqual(self.call("live-do", {"control": "resolution", "action": "up"})[0], 409)
        self.assertEqual(self.call("live-apply", {"changes": {"EmuCore/GS.upscale_multiplier": 2}})[0], 409)

    def test_scan_reports_emulators_already_on_the_pc_without_changing_anything(self):
        import emulators
        pcsx2 = self.game.parent / "Program Files" / "PCSX2" / "pcsx2-qt.exe"
        pcsx2.parent.mkdir(parents=True)
        pcsx2.write_bytes(b"x")
        real = emulators.detect
        emulators.detect = lambda emu, bases=None: pcsx2 if emu["id"] == "pcsx2" else None
        self.addCleanup(setattr, emulators, "detect", real)
        self.assertEqual(self.call("emu-scan", {})[1], {"found": {"pcsx2": str(pcsx2)}})
        self.assertNotIn("pcsx2", self.hub.db.emulators)  # only reported, not adopted
        self.assertEqual(self.call("emu-detect", {"emulator": "pcsx2"})[1]["found"], True)  # "use it" adopts it
        self.assertEqual(self.call("emu-scan", {})[1], {"found": {}})  # already configured -> not reported again

    def test_hardware_endpoint_is_cached_and_recommends_a_level(self):
        import hardware
        calls = []
        real = hardware.detect
        hardware.detect = lambda: calls.append(1) or {"gpu": "Fake GPU", "tier": 4, "tierName": "Alta", "ramGb": 16.0}
        self.addCleanup(setattr, hardware, "detect", real)
        # the test catalog's schema has no optimizer: hardware is reported, no recommendation
        status, hw = self.call("hardware?game=g")
        self.assertEqual((status, hw["gpu"], "recommended" in hw), (200, "Fake GPU", False))
        self.call("hardware?game=g")
        self.assertEqual(len(calls), 1)  # cached
        self.call("hardware?game=g&refresh=1")
        self.assertEqual(len(calls), 2)
        schema_path = Path(self.hub.source().location) / "s.json"
        schema = json.loads(schema_path.read_text())
        schema["optimizer"] = {"byTier": [1, 1, 2, 3, 3], "levels": [{}, {}, {}]}
        schema_path.write_text(json.dumps(schema))
        self.assertEqual(self.call("hardware?game=g")[1]["recommended"], 3)
        self.assertEqual(self.call("hardware?game=nope")[0], 404)

    def test_static_ui_is_served(self):
        with urllib.request.urlopen(f"{self.base}/") as r:
            self.assertIn(b"ModHub", r.read())
        with urllib.request.urlopen(f"{self.base}/app.js") as r:
            self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
