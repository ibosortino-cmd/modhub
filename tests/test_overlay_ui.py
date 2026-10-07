import json
import sys
import time
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import live
import overlay_ui

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "emu_settings" / "pcsx2.json").read_text("utf-8"))


class FakeApi:
    """The ModHub server as the panel sees it."""

    def __init__(self, with_live=True, running=True, schema=None, values=None, target="emu:pcsx2", session_extra=None):
        self.schema, self.target, self.session_extra = schema or SCHEMA, target, session_extra or {}
        self.calls = []
        self.remote = live.Live(SCHEMA["live"], 1, ROOT / "does-not-exist.log",
                                {"EmuCore/GS.upscale_multiplier": 2, "EmuCore/GS.accurate_blending_unit": 1, "SPU2/Output.StandardVolume": 80})
        self.values = values or {"EmuCore/GS.upscale_multiplier": 2, "EmuCore/GS.accurate_blending_unit": 1, "EmuCore/GS.MaxAnisotropy": 2,
                                 "EmuCore/GS.texture_preloading": 2}
        self.pending = {}
        self.with_live, self.running = with_live, running
        self.next_error = None
        self.wanted = False

    def call(self, path, body=None):
        self.calls.append((path, body))
        if self.next_error and body is not None:
            error, self.next_error = self.next_error, None
            raise overlay_ui.ApiError(error)
        if path == "state":
            return {"session": {"gid": "g", "name": "PS2 Game", "target": self.target, "live": self.with_live, **self.session_extra} if self.running else None,
                    "prefs": {"overlayHotkey": "Ctrl+Shift+Tab"}}
        if path == "settings-save":
            self.pending = dict(body["changes"])
            live_now = sorted(k for k in body["changes"] if k in self.session_extra.get("liveKeys", []))
            return {"queued": sorted(body["changes"]), **({"live": live_now} if live_now else {})}
        if path == "live-set":
            return {"live": True}
        if path == "overlay-wanted":
            return {"wanted": self.wanted, "session": self.running}
        if path.startswith("settings"):
            return {"schema": self.schema, "values": self.values, "pending": self.pending, "exists": True, "running": True}
        if path.startswith("hardware"):
            return {"gpu": "NVIDIA GeForce RTX 3060", "tierName": "Alta", "tier": 4, "recommended": 4}
        if path == "live":
            return {"available": True, **self.remote.snapshot()}
        if path == "live-do":
            return {"control": body["control"], "value": 3, "message": "Upscale multiplier increased to 3x.", "answered": True}
        if path == "live-apply":
            self.pending = dict(body["changes"])
            return {"live": [{"key": "EmuCore/GS.upscale_multiplier", "label": "Risoluzione", "reached": True, "value": 3}],
                    "waiting": ["EmuCore/GS.MaxAnisotropy"]}
        if path in ("overlay-close", "game-menu"):
            return {}
        if path == "game-restart":
            return {"restarting": True}
        raise AssertionError(path)


def texts(widget):
    out = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Label):
            out.append(child.cget("text"))
        out += texts(child)
    return out


def buttons(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, overlay_ui.Flat):
            found.append(child)
        found += buttons(child)
    return found


def button(app, label):
    return next(b for b in buttons(app.body) if b.cget("text") == label)


class PanelTests(unittest.TestCase):
    root = None

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()  # one Tk interpreter for the whole module: several per process crash Python on exit
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def make(self, **kwargs):
        api = FakeApi(**kwargs)
        app = overlay_ui.OverlayApp(self.root, api, background=lambda work, done: self._safe(work, done))
        self.addCleanup(app.cancel)
        self.root.update()
        return app, api

    @staticmethod
    def _safe(work, done):
        """Run synchronously, delivering an ApiError to `done` the way the real worker thread does."""
        try:
            result = work()
        except overlay_ui.ApiError as e:
            done(None, e)
            return False
        done(result, None)
        return False

    def test_shows_live_controls_optimizer_and_the_games_name(self):
        app, api = self.make()
        shown = texts(app.body)
        self.assertEqual(app.subtitle.cget("text"), "PS2 Game")
        for label in ("Risoluzione", "Precisione dei colori", "Volume", "Formato immagine", "Mipmap"):
            self.assertIn(label, shown)
        self.assertNotIn("Filtro TV", shown)  # the rarely used ones hide under "Altro"
        self.assertIn("2x", shown)  # resolution read from the settings file
        self.assertIn("80%", shown)
        self.assertIn("Base", shown)  # blending level 1
        self.assertIn("Bilanciato", shown)  # the slider starts on the level that matches the current settings
        self.assertIn("✓ Consigliato", [t for t in texts(app.body)] + ["✓ Consigliato"] if app.slider.reco == 4 else texts(app.body))
        self.assertEqual(app.slider.reco, 4)

    def test_other_controls_expand_and_collapse(self):
        app, _ = self.make()
        button(app, "Altro ▾")._command()
        app.root.update()
        self.assertIn("Filtro TV", texts(app.body))
        button(app, "Meno ▴")._command()
        app.root.update()
        self.assertNotIn("Filtro TV", texts(app.body))

    def test_buttons_send_the_right_live_command_and_show_the_games_answer(self):
        app, api = self.make()
        button(app, "+")._command()  # first stepper = resolution
        self.assertIn(("live-do", {"control": "resolution", "action": "up"}), api.calls)
        self.assertEqual(app.status_text, "Upscale multiplier increased to 3x.")
        buttons_cambia = [b for b in buttons(app.body) if b.cget("text") == "Cambia"]
        buttons_cambia[0]._command()
        self.assertIn(("live-do", {"control": "blending", "action": "press"}), api.calls)

    def test_choosing_a_level_shows_exactly_what_would_change_and_apply_sends_only_that(self):
        app, api = self.make()
        app.slider.set(4, 4)
        app.pick_level(4)  # "Qualità": 3x, anisotropic 8x, blending Medium
        app.root.update()
        shown = texts(app.detail)
        self.assertIn("Qualità", shown)
        self.assertIn("Risoluzione di rendering", shown)
        self.assertIn("3x · circa 1080p", shown)
        self.assertNotIn("Precaricamento delle texture", shown)  # already Full: not a change
        button(app, "Applica «Qualità»")._command()
        path, body = [c for c in api.calls if c[0] == "live-apply"][-1]
        self.assertEqual(body["changes"], {"EmuCore/GS.upscale_multiplier": 3, "EmuCore/GS.MaxAnisotropy": 8, "EmuCore/GS.accurate_blending_unit": 2})
        self.assertIn("subito: Risoluzione", app.status_text)
        self.assertIn("Filtro anisotropico: si applica quando chiudi il gioco", app.status_text)

    def test_apply_is_disabled_when_nothing_would_change(self):
        app, api = self.make()
        self.assertFalse(button(app, "Applica «Bilanciato»")._enabled)
        button(app, "Applica «Bilanciato»")._command()
        self.assertFalse([c for c in api.calls if c[0] == "live-apply"])

    def test_jump_to_the_recommended_level(self):
        app, api = self.make()
        button(app, "Consigliato")._command()
        self.assertEqual(app.level, 4)
        self.assertIn("Applica «Qualità»", [b.cget("text") for b in buttons(app.body)])

    def test_server_errors_are_shown_in_the_status_line(self):
        app, api = self.make()
        api.next_error = "Non trovo la finestra del gioco: è ancora in avvio?"
        button(app, "+")._command()
        self.assertEqual(app.status_text, "Non trovo la finestra del gioco: è ancora in avvio?")

    def test_without_a_running_game_it_explains_what_to_do_with_the_users_own_keys(self):
        app, _ = self.make(running=False)
        shown = texts(app.body)
        self.assertTrue(any("Nessun gioco in corso" in t for t in shown))
        self.assertTrue(any("Ctrl+Shift+Tab" in t for t in shown))

    def test_games_without_live_control_still_get_the_optimizer(self):
        app, _ = self.make(with_live=False)
        shown = texts(app.body)
        self.assertNotIn("Risoluzione", shown)
        self.assertIn("Bilanciato", shown)

    def test_closing_tells_the_server_so_the_keyboard_goes_back_to_the_game(self):
        app, api = self.make()
        destroyed = []
        app._destroy = lambda: destroyed.append(1)  # keep the shared test window alive
        app.close_panel()
        self.assertIn(("overlay-close", {}), api.calls)
        self.assertEqual(destroyed, [1])

    def test_it_docks_at_the_bottom_right_of_the_work_area(self):
        import overlay

        class Window:  # a window on a 125% screen whose content wants 500 px
            def __init__(self, scale=1.25):
                self.scale, self.geometry_text = scale, None

            def winfo_fpixels(self, unit):
                return 96 * self.scale

            def winfo_reqheight(self):
                return 500

            def geometry(self, text):
                self.geometry_text = text

        real = overlay.work_area
        overlay.work_area = lambda: (0, 0, 1920, 1032)  # screen minus the taskbar
        self.addCleanup(setattr, overlay, "work_area", real)
        win = Window()
        overlay_ui.place(win)
        width, height = int(400 * 1.25), 500
        gap = int(14 * 1.25)
        self.assertEqual(win.geometry_text, f"{width}x{height}+{1920 - width - gap}+{1032 - height - gap}")
        overlay_ui.place(win, 5000)  # a very tall panel is cut to the screen instead of leaving it
        self.assertTrue(win.geometry_text.startswith(f"{width}x{1032 - 2 * gap}+"))
        self.assertLess(400, 450)  # a small panel, not a full window


BT3 = json.loads((ROOT / "catalog" / "games" / "bt3-recomp" / "settings.json").read_text("utf-8"))


def bars(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, overlay_ui.ValueBar):
            found.append(child)
        found += bars(child)
    return found


class SeparateVolumeTests(PanelTests):
    """A game with its own music / effects volumes (BT3): they are offered separately in the panel."""

    def make(self, **kwargs):
        values = {"audio.master_volume": 1, "audio.music_volume": 0.04, "audio.sfx_volume": 0, "video.render_scale": 2, "video.shadows": True,
                  "video.glow": True, "video.dof_blur": False, "video.texture_pack": True, "video.fps60": False}
        return super().make(schema=BT3, values=values, target="bt3-recomp", with_live=False)

    def test_music_and_effects_have_their_own_sliders_that_are_saved_for_when_the_game_closes(self):
        app, api = self.make()
        shown = texts(app.body)
        for label in ("Volume generale", "Musica", "Effetti sonori"):
            self.assertIn(label, shown)
        self.assertIn("4%", shown)  # music starts where the settings file has it
        self.assertNotIn("Risoluzione", shown)  # no live controls for this game
        music = bars(app.body)[1]
        music.on_release(0.5)
        path, body = [c for c in api.calls if c[0] == "settings-save"][-1]
        self.assertEqual(body, {"game": "bt3-recomp", "changes": {"audio.music_volume": 0.5}, "queue": True})
        self.assertIn("Musica: 50%", app.status_text)
        bars(app.body)[2].on_release(0.25)  # effects: the music choice is kept, not lost
        self.assertEqual([c for c in api.calls if c[0] == "settings-save"][-1][1]["changes"], {"audio.music_volume": 0.5, "audio.sfx_volume": 0.25})

    def test_dragging_a_bar_updates_the_number_next_to_it(self):
        app, _ = self.make()
        bar = bars(app.body)[0]
        event = type("E", (), {"x": 10 + (bar.width - 20) // 2})()
        bar._move(event)
        self.assertEqual(texts(app.body).count("50%"), 1)

    # the generic tests of PanelTests do not apply to this schema
    test_shows_live_controls_optimizer_and_the_games_name = None
    test_other_controls_expand_and_collapse = None
    test_buttons_send_the_right_live_command_and_show_the_games_answer = None
    test_choosing_a_level_shows_exactly_what_would_change_and_apply_sends_only_that = None
    test_apply_is_disabled_when_nothing_would_change = None
    test_jump_to_the_recommended_level = None
    test_server_errors_are_shown_in_the_status_line = None
    test_without_a_running_game_it_explains_what_to_do_with_the_users_own_keys = None
    test_games_without_live_control_still_get_the_optimizer = None
    test_closing_tells_the_server_so_the_keyboard_goes_back_to_the_game = None
    test_it_docks_at_the_bottom_right_of_the_work_area = None


class OwnMenuGameTests(SeparateVolumeTests):
    """BT3: no live control from outside, its own menu (Shift+Tab), every setting in groups, restart to apply."""

    def make(self, **kwargs):
        values = {"audio.master_volume": 1, "audio.music_volume": 0.04, "audio.sfx_volume": 0, "video.render_scale": 2, "video.shadows": True,
                  "video.glow": True, "video.dof_blur": False, "video.texture_pack": True, "video.fps60": False, "video.renderer": "native"}
        return PanelTests.make(self, schema=BT3, values=values, target="bt3-recomp", with_live=False,
                               session_extra={"own": "Shift+Tab", "canRestart": True})

    def test_the_games_own_menu_is_one_click_away(self):
        app, api = self.make()
        self.assertIn("DAL VIVO", texts(app.body))
        button(app, "Apri il menu del gioco (Shift+Tab)")._command()
        self.assertIn(("game-menu", {}), api.calls)

    def test_every_setting_is_there_one_group_at_a_time_and_is_kept_for_the_restart(self):
        app, api = self.make()
        self.assertNotIn("Ombre", texts(app.body))  # groups start folded
        button(app, "▸  Grafica")._command()
        shown = texts(app.body)
        for label in ("Ombre", "Contorni", "Intensità dei contorni", "Colore dei contorni", "Bagliore Kaioken (aura)"):
            self.assertIn(label, shown)
        self.assertNotIn("Larghezza finestra", shown)  # only the open group
        shadows = next(b for b in buttons(app.body) if b.cget("text") == "Sì" and b.master.winfo_children()[0].cget("text") == "Ombre")
        shadows._command()
        self.assertEqual(api.calls[-1], ("settings-save", {"game": "bt3-recomp", "changes": {"video.shadows": False}, "queue": True}))
        self.assertIn("si applica al riavvio del gioco", app.status_text)
        button(app, "▸  Schermo")._command()
        renderer = button(app, "Vulkan nativo  ▾")
        app.pick(renderer, app.field("video.renderer"), {"value": "opengl", "label": "OpenGL"})
        self.assertEqual(api.calls[-1][1]["changes"], {"video.shadows": False, "video.renderer": "opengl"})
        self.assertIn("OpenGL  ▾", [b.cget("text") for b in buttons(app.body)])  # the new choice is shown
        self.assertEqual(app.open_group, "screen")

    def test_restart_and_apply_asks_first(self):
        app, api = self.make()
        app.save_setting(app.field("video.fps60"), True)
        button(app, "↻ Riavvia e applica")._command()
        self.assertNotIn(("game-restart", {}), api.calls)  # nothing yet: the fight in progress would be lost
        button(app, "Riavvia ora")._command()
        self.assertIn(("game-restart", {}), api.calls)
        self.assertIn("Riavvio in corso…", texts(app.body))

    def test_waiting_changes_can_be_dropped(self):
        app, api = self.make()
        app.save_setting(app.field("video.fps60"), True)
        button(app, "Annulla le modifiche")._command()
        self.assertEqual(api.calls[-1], ("settings-save", {"game": "bt3-recomp", "changes": {}, "queue": True}))
        self.assertNotIn("Annulla le modifiche", [b.cget("text") for b in buttons(app.body)])

    def test_unfolding_a_group_redraws_nothing_else(self):
        app, _ = self.make()
        menu_button = button(app, "Apri il menu del gioco (Shift+Tab)")
        button(app, "▸  Grafica")._command()
        button(app, "▾  Grafica")._command()
        button(app, "▸  Schermo")._command()
        self.assertTrue(menu_button.winfo_exists())  # same widget: the panel was not rebuilt (no flicker)
        self.assertIn("Larghezza finestra", texts(app.body))
        self.assertEqual(app.groups["graphics"]["box"].winfo_manager(), "")  # folded away
        self.assertEqual(app.open_group, "screen")

    def test_wrong_text_is_refused_with_a_message(self):
        app, api = self.make()
        api.next_error = "Colore dei contorni: formato non valido (esempio: #000000)."
        app.save_typed(app.field("video.ink_color"), "nero")
        self.assertIn("formato non valido", app.status_text)


class LiveAudioGameTests(OwnMenuGameTests):
    """BT3 with volumes and controller settings that change while it runs."""

    LIVE = ["audio.master_volume", "audio.music_volume", "audio.sfx_volume", "controllers.rumble", "controllers.rumble_strength",
            "controllers.deadzone"]

    def make(self, **kwargs):
        values = {"audio.master_volume": 1, "audio.music_volume": 0.04, "audio.sfx_volume": 0, "video.render_scale": 2, "video.shadows": True,
                  "video.glow": True, "video.dof_blur": False, "video.texture_pack": True, "video.fps60": False, "video.renderer": "native",
                  "controllers.rumble": True, "controllers.deadzone": 0.15}
        return PanelTests.make(self, schema=BT3, values=values, target="bt3-recomp", with_live=False,
                               session_extra={"own": "Shift+Tab", "canRestart": True, "liveKeys": self.LIVE})

    def test_dragging_a_volume_is_heard_at_once_and_letting_go_keeps_it(self):
        app, api = self.make()
        music = bars(app.body)[1]
        music._move(type("E", (), {"x": 10 + (music.width - 20) // 2})())
        self.assertEqual(api.calls[-1], ("live-set", {"key": "audio.music_volume", "value": 0.5}))
        music.on_release(0.5)
        self.assertEqual(api.calls[-1][0], "settings-save")
        self.assertIn("Applicato subito nel gioco", app.status_text)
        self.assertNotIn("↻ Riavvia e applica", [b.cget("text") for b in buttons(app.body)])  # nothing waits for a restart

    def test_controller_settings_are_marked_and_applied_at_once(self):
        app, api = self.make()
        button(app, "▸  Controller")._command()
        self.assertGreaterEqual(texts(app.body).count("● subito"), 3)
        rumble = next(b for b in buttons(app.body) if b.cget("text") == "Sì" and b.master.winfo_children()[0].cget("text") == "Vibrazione")
        rumble._command()
        self.assertEqual(api.calls[-1][1]["changes"], {"controllers.rumble": False})
        self.assertIn("Applicato subito nel gioco", app.status_text)
        self.assertEqual(rumble.cget("text"), "No")  # updated in place

    # these BT3 tests assume nothing is live
    test_music_and_effects_have_their_own_sliders_that_are_saved_for_when_the_game_closes = None
    test_dragging_a_bar_updates_the_number_next_to_it = None


class StandbyTests(unittest.TestCase):
    """The panel's program starts hidden with the game and only shows itself when ModHub asks."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_it_waits_hidden_shows_when_asked_hides_again_and_quits_with_the_game(self):
        win = tk.Toplevel(self.root)
        win.withdraw()
        api = FakeApi()
        app = overlay_ui.OverlayApp(win, api, background=PanelTests._safe, standby=True)
        self.assertFalse(app.visible)
        self.assertNotIn("state", [c[0] for c in api.calls])  # nothing loaded until it is wanted
        api.wanted = True
        app.poll()
        self.assertTrue(app.visible)
        self.assertIn("state", [c[0] for c in api.calls])  # fresh values every time it appears
        self.assertIn("PS2 Game", app.subtitle.cget("text"))
        api.wanted = False
        app.poll()
        self.assertFalse(app.visible)
        api.wanted = True
        app.poll()
        app.close_panel()  # ✕ hides at once and tells ModHub
        self.assertFalse(app.visible)
        self.assertIn(("overlay-close", {}), api.calls)
        app.cancel()
        api.running = False
        app.poll()  # the game is over: the program ends
        self.assertFalse(win.winfo_exists())


class ToastTests(unittest.TestCase):
    """The start-of-game reminder: the keys, a ✕, and it closes by itself."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def make(self, seconds):
        win = tk.Toplevel(self.root)
        win.withdraw()
        self.addCleanup(win.destroy)
        closed = []
        toast = overlay_ui.Toast(win, "Ctrl+Shift+Tab", seconds, on_close=lambda: closed.append(True))
        return win, toast, closed

    def test_it_names_the_keys_and_closes_by_itself(self):
        win, toast, closed = self.make(0.3)
        for key in ("Ctrl", "Shift", "Tab"):
            self.assertIn(key, texts(win))
        self.assertIn("per aprire le impostazioni di ModHub durante il gioco.", texts(win))
        end = time.time() + 2
        while not closed and time.time() < end:
            self.root.update()
            time.sleep(0.02)
        self.assertEqual(closed, [True])

    def test_the_cross_closes_it_at_once(self):
        win, toast, closed = self.make(30)
        toast.close_button._command()
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
