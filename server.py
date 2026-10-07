"""ModHub: local server + app window. Run `python server.py` (or ModHub.cmd).

The UI (ui/) is a web page shown in an Edge/Chrome "app" window. This process owns all the
logic (core.py) and exposes it to the page as a small JSON API on 127.0.0.1. Every API call
needs a random per-run token, and the Host header is checked, so other web pages cannot drive it.
"""
import argparse
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import core
import emuinstall
import emulators
import hardware
import live
import memlive
import selfrun
import overlay
import settings

APP_DIR = Path(__file__).resolve().parent
UI_DIR = APP_DIR / "ui"
DEFAULT_CATALOG = "https://raw.githubusercontent.com/ibosortino-cmd/modhub-catalogo/main/"  # the public catalog
OFFLINE_CATALOG = str(APP_DIR / "public-catalog")  # its copy shipped with ModHub, used when the internet is not there
DEMO_CATALOG = str(APP_DIR / "catalog")  # the older local demo catalog (two sample mods)
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
STANDARD = "Standard"
SESSION_POLL = 1.0  # seconds between checks that the game is still running
TOAST_SECONDS = 30  # how long the "press ... for ModHub" reminder stays at the top-right when a game starts
TOAST_WAIT = 45.0
TOAST_SETTLE = 1.5  # seconds between the game window appearing and the reminder  # seconds to wait for the game's big window; after that any window of it will do
RESTART_WAIT = 20.0  # seconds a game gets to close by itself when ModHub restarts it
HANDOFF_WINDOW = 6.0  # a launcher that exits within this many seconds probably handed over to the real game process


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Hub:
    """All app state and actions; the HTTP layer only translates to and from JSON."""

    def __init__(self, catalog=None):
        self.db = core.State(core.home_dir())
        if catalog:
            self.db.source = catalog
        self.lock = threading.RLock()
        self.catalog = {"name": "", "games": {}, "projects": []}
        self.manifests = {}
        self.loading = False
        self.offline = False  # the public catalog could not be reached: the copy shipped with ModHub is in use
        self.error = None
        self.job = None
        self._seq = 0
        self.session = None  # the game ModHub started and is still running: {gid, name, target, own, hotkey, ...}
        self._session_hotkey = None
        self._live = None  # live.Live for the running game, when its emulator supports changing settings on the fly
        self._overlay_proc = None  # the panel's program: started hidden with the game, shown / hidden by the hotkey
        self._overlay_wanted = False
        self._ending = False  # a session just ended and ModHub is still writing its settings
        self.spawn_overlay = self._spawn_overlay
        self._toast_proc = None
        self.spawn_toast = self._spawn_toast
        self.game_windows = overlay.windows_of_pid  # (pid) -> its visible windows, biggest first
        self.hotkey_factory = overlay.Hotkey.from_label
        self.hotkeys_enabled = True
        self._session_proc = self._relaunch = self._restart_env = None
        self._mem = None  # memlive.GameMemory of the running native game, when its settings can change in memory
        self._mem_locations = {}  # program file -> where its settings live (found once per version of the file)
        self.memory_factory = memlive.GameMemory
        # how ModHub touches the game's window (replaced by the tests)
        self.press_keys, self.focus_window, self.foreground_window = overlay.send_combo, overlay.focus, overlay.foreground
        self.close_game = lambda proc: [overlay.close_window(h) for h in overlay.windows_of_pid(proc.pid, min_size=0)]
        self.base_url = None  # set by main(): where the overlay page lives
        self.on_session_end = None
        self._prev_window = None
        self._notice, self._notice_seq = None, 0
        self._hw = None  # hardware summary, detected on first use
        self.pending = None  # a program / ROM / emulator picked with the system dialog, waiting for a name
        self.emulators = emulators.load()
        emuinstall.fix_legacy_markers(self.db)
        self.refresh()

    # ------------------------------------------------------------------ catalog

    def source(self):
        if not self.db.source and self.offline:
            return core.Source(OFFLINE_CATALOG)
        return core.Source(self.db.source or DEFAULT_CATALOG)

    def refresh(self):
        self.loading = True
        self.offline = False
        source = self.source()

        def work():
            offline = False
            try:
                cat, err = core.load_catalog(source), None
            except core.ModHubError as e:
                cat, err = {"name": "", "games": {}, "projects": []}, str(e)
                if not self.db.source and Path(OFFLINE_CATALOG, "catalog.json").is_file():
                    try:  # no internet: the copy that came with ModHub, so games and their settings still work
                        cat, err, offline = core.load_catalog(core.Source(OFFLINE_CATALOG)), None, True
                    except core.ModHubError:
                        pass
            with self.lock:
                self.catalog, self.error, self.loading, self.offline = cat, err, False, offline
                self.manifests.clear()
            self.apply_pending()  # settings queued during an earlier run, if the game is closed now

        threading.Thread(target=work, daemon=True).start()

    def set_source(self, value):
        """A catalog address or folder; empty (or the default address) means ModHub's own public catalog."""
        value = (value or "").strip()
        self.db.source = None if value in ("", DEFAULT_CATALOG, DEFAULT_CATALOG.rstrip("/")) else value
        self.db.save()
        self.refresh()

    def entry(self, pid):
        for p in self.catalog["projects"]:
            if p["id"] == pid:
                return p
        raise ApiError("Progetto non trovato nel catalogo.", 404)

    def manifest(self, pid):
        with self.lock:
            if pid in self.manifests:
                return self.manifests[pid]
        manifest = core.load_manifest(self.source(), self.entry(pid))
        with self.lock:
            self.manifests[pid] = manifest
        return manifest

    # ------------------------------------------------------------------ games

    def game_def(self, gid):
        """Catalog definition of a game, or the entry the user added by hand."""
        return self.catalog["games"].get(gid) or self.db.custom_games.get(gid)

    # ---- emulators

    def emulator_defs(self):
        custom = [{"id": eid, "name": e["name"], "systems": [], "site": None, "exe_names": [], "extensions": [],
                   "args": e["args"], "custom": True} for eid, e in self.db.custom_emulators.items()]
        return self.emulators + custom

    def emulator_def(self, eid):
        return next((e for e in self.emulator_defs() if e["id"] == eid), None)

    def emu_exe(self, eid):
        path = self.db.emulators.get(eid)
        return Path(path) if path and Path(path).is_file() else None

    def _need_emulator(self, eid):
        emu = self.emulator_def(eid)
        if not emu:
            raise ApiError("Emulatore sconosciuto.", 404)
        return emu

    def _set_emulator_path(self, eid, path):
        path = Path(path)
        if not path.is_file() or path.suffix.lower() != ".exe":
            raise ApiError("Scegli il programma dell'emulatore (.exe).")
        self.db.emulators[eid] = str(path)
        self.db.save()

    def emu_detect(self, eid):
        emu = self._need_emulator(eid)
        hit = emulators.detect(emu)
        if hit:
            self._set_emulator_path(eid, hit)
        return {"found": bool(hit), "path": str(hit) if hit else None}

    def emu_scan(self):
        """Emulators already on this PC that ModHub is not using yet. Changes nothing."""
        found = {}
        for emu in self.emulator_defs():
            if emu.get("exe_names") and not self.emu_exe(emu["id"]):
                hit = emulators.detect(emu)
                if hit:
                    found[emu["id"]] = str(hit)
        return {"found": found}

    def emu_pick(self, eid):
        emu = self._need_emulator(eid)
        names = " ".join(emu.get("exe_names", [])) or "*.exe"
        code = ("import sys, tkinter as t, tkinter.filedialog as f; r = t.Tk(); r.withdraw(); r.attributes('-topmost', True); "
                "print(f.askopenfilename(title=sys.argv[1], filetypes=[('Emulatore', sys.argv[2]), ('Programmi', '*.exe')]))")
        chosen = self._native_dialog(code, f"Programma di {emu['name']}", names)
        if not chosen:
            return {"picked": False}
        self._set_emulator_path(eid, chosen)
        return {"picked": True}

    def _install_spec(self, eid):
        emu = self._need_emulator(eid)
        if not emu.get("install"):
            raise ApiError("Per questo emulatore non c'è il download automatico: usa il sito ufficiale.", 404)
        return emu

    def emu_plan(self, eid):
        """What would be downloaded: shown to the user for confirmation before anything is fetched."""
        emu = self._install_spec(eid)
        rel = emuinstall.resolve(emu["install"])
        return {"name": rel["name"], "version": rel["version"], "size": rel["size"], "host": urlparse(rel["url"]).hostname,
                "verified": bool(rel["sha256"]), "update": eid in self.db.emulator_installs}

    def emu_install(self, eid, asset):
        emu = self._install_spec(eid)
        rel = emuinstall.resolve(emu["install"])
        if asset and asset != rel["name"]:
            raise ApiError("Nel frattempo il file è cambiato: ricontrolla e riprova.", 409)

        def work(progress):
            def stage(name):
                self.job["stage"] = name
                self.job["pct"] = 0
            return emuinstall.install(emu, rel, self.db, progress, stage)

        verb = "aggiornato" if eid in self.db.emulator_installs else "installato"
        self._start_job("emu-install", eid, work, lambda version: f"{emu['name']} {version} {verb}")

    def emu_check(self):
        """For emulators ModHub installed: is a newer build published?"""
        result = {}
        for eid, rec in self.db.emulator_installs.items():
            emu = self.emulator_def(eid)
            if not emu or not emu.get("install"):
                continue
            try:
                rel = emuinstall.resolve(emu["install"])
                result[eid] = {"version": rel["version"], "available": rel["build"] != rec["build"]}
            except core.ModHubError as e:
                result[eid] = {"error": str(e)}
        return {"updates": result}

    def emu_uninstall(self, eid):
        emu = self._need_emulator(eid)
        emuinstall.uninstall(eid, self.db)  # games using it stay in the library until it is installed again
        return {"name": emu["name"]}

    def emu_open(self, eid):
        exe = self.emu_exe(self._need_emulator(eid)["id"])
        if not exe:
            raise ApiError("Emulatore non impostato.", 404)
        if hasattr(os, "startfile"):
            os.startfile(exe.parent)

    def emu_forget(self, eid):
        self._need_emulator(eid)
        if eid in self.db.emulator_installs:
            raise ApiError("Questo emulatore è stato installato da ModHub: usa Disinstalla.")
        if any(g.get("emulator") == eid for g in self.db.custom_games.values()) and eid in self.db.custom_emulators:
            raise ApiError("Ci sono giochi che usano questo emulatore: rimuovili prima dalla libreria.")
        self.db.emulators.pop(eid, None)
        self.db.custom_emulators.pop(eid, None)
        self.db.save()

    def emu_site(self, eid):
        site = self._need_emulator(eid).get("site")
        if not site or not site.startswith("https://"):
            raise ApiError("Questo emulatore non ha un sito indicato.", 404)
        webbrowser.open(site)

    def emu_custom_pick(self):
        code = ("import tkinter as t, tkinter.filedialog as f; r = t.Tk(); r.withdraw(); r.attributes('-topmost', True); "
                "print(f.askopenfilename(title='Scegli il programma dell\\'emulatore', filetypes=[('Programmi', '*.exe')]))")
        chosen = self._native_dialog(code)
        if not chosen:
            return {"picked": False}
        path = Path(chosen)
        if not path.is_file() or path.suffix.lower() != ".exe":
            raise ApiError("Scegli il programma dell'emulatore (.exe).")
        self.pending = {"kind": "emu", "path": path}
        return {"picked": True, "name": path.stem, "exe": str(path)}

    def emu_custom_add(self, name, args):
        pending = self.pending
        if not pending or pending["kind"] != "emu" or not pending["path"].is_file():
            raise ApiError("Scegli prima il programma dell'emulatore.")
        name = (name or "").strip()
        if not name or len(name) > 60:
            raise ApiError("Dai un nome all'emulatore (massimo 60 caratteri).")
        template = emulators.parse_args_template(args)
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "emulatore"
        eid, n = f"custom-{base}", 2
        while self.emulator_def(eid):
            eid, n = f"custom-{base}-{n}", n + 1
        self.db.custom_emulators[eid] = {"name": name, "args": template}
        self.db.emulators[eid] = str(pending["path"])
        self.db.save()
        self.pending = None
        return {"id": eid}

    def pick_rom(self, eid):
        emu = self._need_emulator(eid)
        if not self.emu_exe(eid):
            raise ApiError(f"Prima imposta dove si trova {emu['name']}.")
        code = ("import sys, tkinter as t, tkinter.filedialog as f; r = t.Tk(); r.withdraw(); r.attributes('-topmost', True); "
                "ft = [('Giochi', sys.argv[2])] if sys.argv[2] else []; "
                "print(f.askopenfilename(title=sys.argv[1], filetypes=ft + [('Tutti i file', '*.*')]))")
        chosen = self._native_dialog(code, f"Scegli il gioco per {emu['name']}", emulators.dialog_filters(emu))
        if not chosen:
            return {"picked": False}
        rom = Path(chosen)
        if not rom.is_file():
            raise ApiError("File non trovato.")
        if any(g.get("emulator") == eid and g.get("rom") == str(rom) for g in self.db.custom_games.values()):
            raise ApiError("Questo gioco è già nella libreria.")
        self.pending = {"kind": "rom", "path": rom, "emulator": eid}
        return {"picked": True, "name": re.sub(r"[_]+", " ", rom.stem).strip() or rom.stem, "exe": str(rom)}

    # ---- games

    def game_exe(self, gid):
        """The program that represents a game (its own exe, or its emulator's)."""
        custom = self.db.custom_games.get(gid)
        if custom and custom.get("emulator"):
            return self.emu_exe(custom["emulator"])
        gdef, root = self.game_def(gid), self.game_root(gid)
        return root / gdef["exe"] if gdef and root else None

    def game_root(self, gid):
        custom = self.db.custom_games.get(gid)
        if custom and custom.get("emulator"):
            rom = Path(custom["rom"])
            return rom.parent if rom.exists() and self.emu_exe(custom["emulator"]) else None
        if custom:
            return Path(custom["root"]) if (Path(custom["root"]) / custom["exe"]).is_file() else None
        gdef = self.catalog["games"].get(gid)
        if not gdef:
            return None
        root = core.find_game(gdef, self.db, gid, [APP_DIR, *APP_DIR.parents[:3]])
        if root and self.db.game_paths.get(gid) != str(root):
            self.db.game_paths[gid] = str(root)
            self.db.save()
        return root

    @staticmethod
    def _native_dialog(code, *args):
        return selfrun.run_snippet(code, *args)

    def pick_game_exe(self):
        """Ask for a game's executable with the system dialog. The path never comes from the web page."""
        code = ("import tkinter as t, tkinter.filedialog as f; r = t.Tk(); r.withdraw(); r.attributes('-topmost', True); "
                "print(f.askopenfilename(title='Scegli il programma del gioco', "
                "filetypes=[('Programmi', '*.exe *.cmd *.bat'), ('Tutti i file', '*.*')]))")
        chosen = self._native_dialog(code)
        if not chosen:
            return {"picked": False}
        path = Path(chosen)
        if not path.is_file() or path.suffix.lower() not in (".exe", ".cmd", ".bat"):
            raise ApiError("Scegli un programma (.exe, .cmd o .bat).")
        for gid, gdef in self.catalog["games"].items():  # already known by the catalog: just link the folder
            if path.name.lower() == gdef["exe"].lower():
                self.db.game_paths[gid] = str(path.parent)
                self.db.save()
                return {"picked": True, "linked": gid, "name": gdef["name"]}
        if any("exe" in c and Path(c["root"]) / c["exe"] == path for c in self.db.custom_games.values()):
            raise ApiError("Questo gioco è già nella libreria.")
        self.pending = {"kind": "exe", "path": path}
        return {"picked": True, "name": self._suggest_name(path), "exe": str(path)}

    @staticmethod
    def _suggest_name(path):
        generic = {"bin", "binaries", "release", "debug", "x64", "x86", "win64", "win32", "game", "app", "windows"}
        folder = path.parent
        if folder.name.lower() in generic and folder.parent != folder:
            folder = folder.parent
        name = re.sub(r"[_\-]+", " ", folder.name if folder.name else path.stem).strip()
        return name or path.stem

    def add_game(self, name):
        pending = self.pending
        name = (name or "").strip()
        if not pending or pending["kind"] not in ("exe", "rom") or not pending["path"].is_file():
            raise ApiError("Scegli prima il gioco da aggiungere.")
        if not name or len(name) > 80:
            raise ApiError("Dai un nome al gioco (massimo 80 caratteri).")
        path = pending["path"]
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "gioco"
        gid, n = f"custom-{base}", 2
        while gid in self.db.custom_games or gid in self.catalog["games"]:
            gid, n = f"custom-{base}-{n}", n + 1
        if pending["kind"] == "rom":
            if not self.emulator_def(pending["emulator"]):
                raise ApiError("Emulatore sconosciuto.", 404)
            entry = {"name": name, "root": str(path.parent), "emulator": pending["emulator"], "rom": str(path)}
        else:
            entry = {"name": name, "root": str(path.parent), "exe": path.name}
        self.db.custom_games[gid] = entry
        self.db.save()
        self.pending = None
        return {"id": gid}

    def remove_game(self, gid):
        if gid not in self.db.custom_games:
            raise ApiError("Solo i giochi aggiunti a mano si possono rimuovere dalla libreria.", 404)
        del self.db.custom_games[gid]
        self.db.save()

    def open_folder(self, gid):
        root = self.game_root(gid)
        if not root:
            raise ApiError("Cartella del gioco non trovata.", 404)
        if hasattr(os, "startfile"):
            os.startfile(root)

    def game_icon(self, gid):
        """PNG of the game's icon (cached), or None."""
        exe = self.game_exe(gid)
        if not exe or not hasattr(os, "startfile"):
            return None
        out =core.home_dir() / "icons" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', gid)}.png"
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            script = ("Add-Type -AssemblyName System.Drawing; "
                      "$i = [System.Drawing.Icon]::ExtractAssociatedIcon($env:MH_EXE); "
                      "$i.ToBitmap().Save($env:MH_OUT, [System.Drawing.Imaging.ImageFormat]::Png)")
            env = {**os.environ, "MH_EXE": str(exe), "MH_OUT": str(out)}
            try:
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                               capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                return None
        return out.read_bytes() if out.is_file() else None

    def pick_folder(self, gid):
        gdef = self.catalog["games"].get(gid)
        if not gdef:
            raise ApiError("Gioco sconosciuto.", 404)
        code = ("import sys, tkinter as t, tkinter.filedialog as f; r = t.Tk(); r.withdraw(); "
                "r.attributes('-topmost', True); print(f.askdirectory(title=sys.argv[1]))")
        folder = self._native_dialog(code, f"Cartella di {gdef['name']}")
        if not folder:
            return {"picked": False}
        if not (Path(folder) / gdef["exe"]).is_file():
            raise ApiError(f"In quella cartella non c'è «{gdef['exe']}».")
        self.db.game_paths[gid] = str(Path(folder))
        self.db.save()
        return {"picked": True}

    def _play_emulated(self, gid, custom, name):
        emu = self._need_emulator(custom["emulator"])
        exe = self.emu_exe(emu["id"])
        if not exe:
            raise ApiError(f"{emu['name']} non è impostato: vai nella sezione Emulatori.")
        if not Path(custom["rom"]).exists():
            raise ApiError("Il file del gioco non si trova più.")
        args = list(emu["args_fullscreen"] if name == emulators.FULLSCREEN_PROFILE and emu.get("args_fullscreen") else emu["args"])
        live_spec, log_path, initial = self._prepare_live(emu), None, None
        if live_spec:
            log_path = core.home_dir() / "live" / f"{emu['id']}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._learn_profile(emu["id"], log_path)  # the previous run's log still names this game's own settings file
            log_path.unlink(missing_ok=True)
            args = [live_spec["logArg"], str(log_path), *args]
            try:
                t = self._settings_target(f"emu:{emu['id']}")
                initial = {**self._effective(t), **self.db.pending_settings.get(t["id"], {})}
            except (ApiError, core.ModHubError):
                initial = None
        try:
            proc = core.launch_command(exe, emulators.build_args(args, custom["rom"]), exe.parent)
        except OSError as e:
            raise ApiError(f"Impossibile avviare l'emulatore: {e}")
        remote = live.Live(live_spec, proc.pid, log_path, initial) if live_spec and proc is not None else None
        return {"session": self._begin_session(gid, custom["name"], exe, proc, remote)}

    def _prepare_live(self, emu):
        """Bind ModHub's live-control keys in the emulator's settings, if it supports them. Returns the spec or None."""
        if not emu.get("settings"):
            return None
        try:
            t = self._settings_target(f"emu:{emu['id']}")
            spec = t["schema"].get("live")
            ini = settings.settings_path(t["root"], t["schema"])
            if not spec or not ini.is_file() or settings.file_locked(t["exe"]):
                return None
            live.prepare(ini, spec)
            return spec
        except (ApiError, core.ModHubError, OSError):
            return None

    def play(self, gid, project, name, extra_env=None):
        custom = self.db.custom_games.get(gid)
        if custom and custom.get("emulator"):
            return self._play_emulated(gid, custom, name)
        root = self.game_root(gid)
        if not root:
            raise ApiError("Gioco non trovato: scegli la cartella del gioco.")
        env = {}
        if project:
            rec = self.db.installed.get(project) or {}
            profile = next((p for p in rec.get("launch_profiles", []) if p["name"] == name), None)
            if not profile:
                raise ApiError("Profilo di avvio non trovato.", 404)
            env = profile.get("env", {})
        gdef = self.game_def(gid)
        try:
            proc = core.launch(root, gdef["exe"], {**env, **(extra_env or {})})
        except OSError as e:
            raise ApiError(f"Impossibile avviare il gioco: {e}")
        return {"session": self._begin_session(gid, gdef["name"], root / gdef["exe"], proc, relaunch=[gid, project, name])}

    # ------------------------------------------------------------------ game settings

    def _settings_target(self, tid):
        """What a settings page edits: a catalog game, or an emulator (as 'emu:<id>' or through a game it runs)."""
        tid = tid or ""
        custom = self.db.custom_games.get(tid)
        if tid.startswith("emu:") or (custom and custom.get("emulator")):
            eid = tid[4:] if tid.startswith("emu:") else custom["emulator"]
            emu = self.emulator_def(eid)
            if not emu or not emu.get("settings"):
                raise ApiError("Per questo emulatore non ci sono impostazioni modificabili da ModHub.", 404)
            exe = self.emu_exe(eid)
            if not exe:
                raise ApiError(f"Prima imposta dove si trova {emu['name']}.")
            schema = json.loads((APP_DIR / emu["settings"]).read_text("utf-8"))
            return {"id": f"emu:{eid}", "name": emu["name"], "exe": exe, "schema": schema,
                    "root": settings.emulator_root(schema, exe.parent)}
        gdef = self.catalog["games"].get(tid)
        if not gdef or not gdef.get("settings"):
            raise ApiError("Questo gioco non ha impostazioni modificabili da ModHub.", 404)
        root = self.game_root(tid)
        if not root:
            raise ApiError("Gioco non trovato: scegli prima la cartella del gioco.")
        return {"id": tid, "name": gdef["name"], "root": root, "exe": root / gdef["exe"],
                "schema": self.source().json(gdef["settings"])}

    # An emulator may keep per-game exceptions (PCSX2 gamesettings/<serial>_<crc>.ini) that beat the global file.
    # ModHub learns the file from the emulator's own log and keeps it in step with what the user changes.

    def _profile(self, t):
        path = self.db.prefs.get("profiles", {}).get(t["id"])
        return Path(path) if path and Path(path).is_file() else None

    def _learn_profile(self, emu_id, log_path):
        try:
            found = settings.profile_from_log(Path(log_path).read_bytes().decode("utf-8", "replace"))
        except OSError:
            return
        profiles = self.db.prefs.setdefault("profiles", {})
        if found and profiles.get(f"emu:{emu_id}") != str(found):
            profiles[f"emu:{emu_id}"] = str(found)
            self.db.save()

    def _effective(self, t):
        """Values that really apply: the global file, overlaid with the game's own exceptions."""
        values = dict(settings.describe(t["root"], t["schema"])["values"])
        profile = self._profile(t)
        if profile:
            values.update(settings.profile_values(profile, t["schema"]))
        return values

    def _write_settings(self, t, changes):
        saved = settings.save(t["root"], t["schema"], changes)
        profile = self._profile(t)
        if profile:
            try:
                settings.save_profile(profile, t["schema"], changes)
            except (core.ModHubError, OSError):
                pass  # the global file is saved; a damaged game file is left as it was
        return saved

    def settings_get(self, tid):
        t = self._settings_target(tid)
        info = settings.describe(t["root"], t["schema"])
        if info["exists"]:
            info["values"] = self._effective(t)
        return {"schema": t["schema"], **info, "target": t["id"],
                "running": settings.file_locked(t["exe"]), "path": t["schema"]["file"],
                "title": t["schema"].get("title") or t["name"], "pending": self.db.pending_settings.get(t["id"], {})}

    def hardware_info(self, tid, refresh=False):
        """This PC's summary (cached for the session) and, if the target has an optimizer, the suggested level."""
        with self.lock:
            if refresh or self._hw is None:
                self._hw = hardware.detect()
            hw = dict(self._hw)
        schema = self._settings_target(tid)["schema"]
        if schema.get("optimizer"):
            hw["recommended"] = hardware.recommended_level(hw, schema["optimizer"])
        return hw

    def settings_save(self, tid, changes, queue=False):
        """Write the settings now; with `queue`, a running game's changes wait and are applied when it closes."""
        t = self._settings_target(tid)
        if not isinstance(changes, dict):
            raise ApiError("Richiesta non valida.")
        running = settings.file_locked(t["exe"])
        if queue and not changes:
            self.db.pending_settings.pop(t["id"], None)
            self._pending_base().pop(t["id"], None)
            self.db.save()
            return {"queued": []}
        if not changes:
            raise ApiError("Nessuna modifica da salvare.")
        if running and queue:
            settings.validate(t["root"], t["schema"], changes)  # refuse bad values now, not when the game closes
            self.db.pending_settings[t["id"]] = dict(changes)
            # What the file said when each value was chosen: if the game's own menu changes it afterwards, the
            # game's later choice wins when the queue is written.
            now = settings.describe(t["root"], t["schema"])["values"]
            base = self._pending_base().setdefault(t["id"], {})
            for key in changes:
                base.setdefault(key, now.get(key))
            for key in [k for k in base if k not in changes]:
                del base[key]
            self.db.save()
            now_live = self._apply_in_memory(t, changes)
            return {"queued": sorted(changes), **({"live": now_live} if now_live else {})}
        if running:
            raise ApiError("Il gioco è aperto: chiudilo prima di salvare, altrimenti le modifiche andrebbero perse.", 409)
        saved = self._write_settings(t, changes)
        self._pending_base().pop(t["id"], None)
        if self.db.pending_settings.pop(t["id"], None) is not None:
            self.db.save()  # an explicit save replaces anything that was waiting
        return {"saved": saved}

    def settings_restore(self, tid):
        t = self._settings_target(tid)
        if settings.file_locked(t["exe"]):
            raise ApiError("Il gioco è aperto: chiudilo prima di ripristinare.", 409)
        settings.restore_backup(t["root"], t["schema"])

    # ------------------------------------------------------------------ settings waiting for a game to close

    def notice(self, text, error=False):
        with self.lock:
            self._notice_seq += 1
            self._notice = {"id": self._notice_seq, "text": text, "error": error}

    def apply_pending(self):
        """Write queued settings for every game / emulator that is no longer running."""
        applied, overridden = [], []
        for tid in list(self.db.pending_settings):
            try:
                t = self._settings_target(tid)
                if settings.file_locked(t["exe"]):
                    continue
                base = self._pending_base().pop(tid, {})
                now = settings.describe(t["root"], t["schema"])["values"]
                changes = {}
                for key, value in self.db.pending_settings[tid].items():
                    if key in base and now.get(key) != base[key] and now.get(key) != value:
                        overridden.append(key)  # changed again in the game's own menu after ModHub: that is newer
                    else:
                        changes[key] = value
                if changes:
                    self._write_settings(t, changes)
                del self.db.pending_settings[tid]
                applied.append(t["name"])
            except (ApiError, core.ModHubError) as e:
                self.notice(f"Non sono riuscito ad applicare le impostazioni in attesa: {e}", error=True)
        if applied:
            self.db.save()
            kept = f" ({len(overridden)} cambiate poi dal menu del gioco: tenute quelle)" if overridden else ""
            self.notice(f"Impostazioni applicate: {', '.join(applied)}{kept}.")

    def _locations(self, exe):
        try:
            st = Path(exe).stat()
            key = (str(exe), st.st_size, st.st_mtime_ns)
            if key not in self._mem_locations:
                self._mem_locations[key] = memlive.locate(exe)
            return self._mem_locations[key]
        except (OSError, ValueError, KeyError):
            return {"volumes": None, "rumble": None, "data": (0, 0)}

    def _apply_in_memory(self, t, changes, preview=False):
        """Settings a native game can take while it runs (BT3: almost all of them) are handed to it at once."""
        mem, sess = self._mem, self.session
        if not mem or not sess or sess.get("target") != t["id"]:
            return []
        coerced = settings.validate(t["root"], t["schema"], changes)
        return sorted(k for k, v in coerced.items() if mem.handles(k) and mem.set(k, v, preview=preview))

    def live_set(self, key, value):
        """Try one value in the running game without saving it (a slider being dragged)."""
        sess = self.session
        if not sess or not self._mem or key not in sess.get("liveKeys", []):
            raise ApiError("Questa impostazione non cambia mentre il gioco gira.", 409)
        t = self._settings_target(sess["target"])
        try:
            return {"live": bool(self._apply_in_memory(t, {key: value}, preview=True))}
        except core.ModHubError as e:
            raise ApiError(str(e))

    def _pending_base(self):
        return self.db.prefs.setdefault("pending_base", {})

    # ------------------------------------------------------------------ the running game and the in-game panel

    def _begin_session(self, gid, name, exe, proc, remote=None, relaunch=None):
        self._end_session()
        mem, live_keys = None, []
        try:
            t = self._settings_target(gid)
            target, own, restart_env = t["id"], t["schema"].get("ownOverlay"), t["schema"].get("restartEnv")
            if t["schema"].get("memoryLive") and proc is not None and remote is None:
                fields = {f["key"] for g in t["schema"]["groups"] for sec in g["sections"] for f in sec["fields"]}
                root, schema = t["root"], t["schema"]
                mem = self.memory_factory(proc.pid, exe, self._locations(exe), lambda: settings.describe(root, schema)["values"])
                live_keys = [k for k in memlive.KEYS if k in fields]
        except ApiError:
            target, own, restart_env = None, None, None
        label = self.overlay_hotkey()
        explicit = bool(self.db.prefs.get("overlay_hotkey"))  # the user picked it: never swap it behind their back
        clash = bool(own and overlay.same_combo(own, label))  # the game itself uses this very combination
        hotkey = None
        if target and not clash and self.hotkeys_enabled:
            options = [label] if explicit else [label] + [p for p in overlay.HOTKEY_PRESETS if p != label and not overlay.same_combo(p, own or "")]
            for candidate in options:  # another program may already own the combination: take the next free one
                attempt = self.hotkey_factory(self._on_hotkey, candidate)
                if attempt.start():
                    hotkey = attempt
                    if candidate != label:
                        label = candidate
                        self.db.prefs["overlay_hotkey"] = label
                        self.db.save()
                    break
        sess = {"gid": gid, "name": name, "target": target, "own": own, "live": remote is not None,
                "canRestart": bool(relaunch and restart_env is not None and proc is not None), "restarting": False,
                "liveKeys": live_keys,
                "hotkey": label if hotkey else None, "hotkeyClash": clash,
                "hotkeyBusy": bool(target and not clash and self.hotkeys_enabled and not hotkey)}
        with self.lock:
            self.session, self._session_hotkey, self._live = sess, hotkey, remote
            self._session_proc, self._relaunch, self._restart_env = proc, relaunch, restart_env
            self._mem = mem
        threading.Thread(target=self._watch_session, args=(sess, proc, exe), daemon=True).start()
        if hotkey and proc is not None:
            threading.Thread(target=self._announce, args=(sess, proc), daemon=True).start()
        if target and self.base_url:
            threading.Thread(target=self._warm_panel, args=(sess,), daemon=True).start()
        return sess

    def _warm_panel(self, sess):
        """Get the panel ready before it is needed: its program waits hidden and this PC's graphics card is already
        known, so the hotkey shows it at once instead of after a couple of seconds."""
        try:
            if self._settings_target(sess["target"])["schema"].get("optimizer"):
                self.hardware_info(sess["target"])
        except (ApiError, core.ModHubError, OSError):
            pass
        if self.session is sess and not self._panel_alive():
            self._overlay_proc = self.spawn_overlay()

    def _announce(self, sess, proc):
        """Once the game is on screen, remind the player which keys open ModHub's panel (top-right, 30 seconds).

        A launcher or start menu comes first (BT3's PLAY screen, PCSX2's game list), so wait for a window that fills
        a good part of the screen; after TOAST_WAIT seconds any window of the game will do."""
        started = time.time()
        width, height = overlay.screen_size()
        while self.session is sess and proc.poll() is None:
            wins = self.game_windows(proc.pid)
            if wins and (time.time() - started > TOAST_WAIT or self._is_big(wins[0], width * height)):
                time.sleep(TOAST_SETTLE)  # let the game settle (fullscreen switch) before drawing on top of it
                if self.session is sess and sess.get("hotkey") and not self._overlay_is_open():
                    self._toast_proc = self.spawn_toast(sess["hotkey"])
                return
            time.sleep(0.5)

    @staticmethod
    def _is_big(hwnd, screen_area):
        try:
            rect = overlay.window_rect(hwnd)
            return (rect[2] - rect[0]) * (rect[3] - rect[1]) >= 0.45 * screen_area
        except (OSError, TypeError, ValueError):
            return False

    def close_toast(self):
        proc, self._toast_proc = self._toast_proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def _watch_session(self, sess, proc, exe):
        """The session ends when the process we started ends. Some launchers hand over to another process and
        exit at once: then (and only then) we follow the program file's lock instead."""
        started, by_lock = time.time(), False
        while True:
            time.sleep(SESSION_POLL)
            if by_lock:
                if not (exe and settings.file_locked(exe)):
                    break
            elif proc is None or proc.poll() is not None:
                if time.time() - started < HANDOFF_WINDOW and exe and settings.file_locked(exe):
                    by_lock = True
                else:
                    break
        if self.session is sess:
            self._end_session()

    def _end_session(self):
        with self.lock:
            sess, hotkey, remote, mem = self.session, self._session_hotkey, self._live, self._mem
            self.session = self._session_hotkey = self._live = self._mem = None
            self._ending = bool(sess)
        try:
            self._tidy_up(sess, hotkey, remote, mem)
        finally:
            self._ending = False

    def _tidy_up(self, sess, hotkey, remote, mem):
        if mem:
            mem.close()
        if not sess:
            return
        self.close_toast()
        self._stop_panel()
        if remote and sess.get("target", "").startswith("emu:"):
            self._learn_profile(sess["target"][4:], remote.log)
        if hotkey:
            hotkey.stop()
        self.close_overlay()
        self.apply_pending()
        if self.on_session_end:
            self.on_session_end()

    def _on_hotkey(self):
        self.toggle_overlay()

    # ---- ModHub's own preferences

    def overlay_hotkey(self):
        label = self.db.prefs.get("overlay_hotkey") or overlay.DEFAULT_HOTKEY
        try:
            return overlay.parse_hotkey(label)[2]
        except ValueError:
            return overlay.DEFAULT_HOTKEY

    def prefs(self):
        return {"overlayHotkey": self.overlay_hotkey(), "hotkeyPresets": overlay.HOTKEY_PRESETS}

    def set_prefs(self, hotkey):
        try:
            label = overlay.parse_hotkey(hotkey)[2]
        except ValueError as e:
            raise ApiError(str(e))
        sess = self.session
        if sess and sess.get("target"):  # a game is running: move the panel's key to the new combination right away
            if self._session_hotkey:
                self._session_hotkey.stop()
            clash = bool(sess.get("own") and overlay.same_combo(sess["own"], label))
            fresh = None
            if not clash and self.hotkeys_enabled:
                fresh = self.hotkey_factory(self._on_hotkey, label)
                if not fresh.start():
                    fresh = None
            with self.lock:
                self._session_hotkey = fresh
                sess.update(hotkey=label if fresh else None, hotkeyClash=clash,
                            hotkeyBusy=bool(not clash and self.hotkeys_enabled and not fresh))
        self.db.prefs["overlay_hotkey"] = label
        self.db.save()
        return self.prefs()

    # ---- changing settings while the game runs

    def _need_live(self):
        if not self.session or not self._live:
            raise ApiError("Questo gioco non è in esecuzione o il suo emulatore non permette modifiche in tempo reale.", 409)
        return self._live

    def live_state(self):
        remote = self._live
        if remote and self.session and (self.session.get("target") or "").startswith("emu:"):
            self._learn_profile(self.session["target"][4:], remote.log)
        return {"available": bool(self.session and remote), **(remote.snapshot() if remote else {"controls": [], "messages": []})}

    def live_do(self, control, action):
        return self._need_live().do(control, action)

    def live_apply(self, changes):
        """Apply what can change right now to the running game, and queue everything (so it is also saved when the
        game closes). Returns what was applied live and what has to wait."""
        if not self.session or not self.session.get("target"):
            raise ApiError("Nessun gioco in esecuzione.", 409)
        remote = self._live
        if not isinstance(changes, dict) or not changes:
            raise ApiError("Nessuna modifica da applicare.")
        live_results, waiting = [], []
        target = self._settings_target(self.session["target"])
        labels = {f["key"]: f["label"] for g in target["schema"]["groups"] for sec in g["sections"] for f in sec["fields"]}
        for key, value in changes.items():
            control = remote.control_for_setting(key) if remote else None
            if not control and self._mem and self._mem.handles(key):
                ok = bool(self._apply_in_memory(target, {key: value}))
                live_results.append({"key": key, "label": labels.get(key, key), "reached": ok, "value": value})
            elif control and isinstance(value, int) and not isinstance(value, bool):
                result = remote.set(control["id"], value)
                live_results.append({"key": key, "label": control["label"], "reached": result["reached"], "value": result["value"]})
            else:
                waiting.append(key)
        target = self.session["target"]
        self.settings_save(target, {**self.db.pending_settings.get(self._settings_target(target)["id"], {}), **changes}, queue=True)
        return {"live": live_results, "waiting": waiting}

    def _game_window(self):
        proc = self._session_proc
        wins = overlay.windows_of_pid(proc.pid) if proc is not None and proc.poll() is None else []
        return wins[0] if wins else None

    def open_game_menu(self):
        """Close ModHub's panel and press the game's own menu key in the game (BT3: Shift+Tab), where every change
        is seen at once."""
        sess = self.session
        if not sess or not sess.get("own"):
            raise ApiError("Questo gioco non ha un suo menu delle impostazioni.", 409)
        hwnd = self._game_window()
        if not hwnd:
            raise ApiError(f"Non trovo la finestra del gioco: premi tu {sess['own']} nel gioco.", 409)
        self.close_overlay()
        time.sleep(0.25)
        self.focus_window(hwnd)
        for _ in range(10):
            if self.foreground_window() == hwnd:
                break
            time.sleep(0.1)
        else:
            raise ApiError(f"Il gioco non è passato in primo piano: clicca sul gioco e premi {sess['own']}.", 409)
        self.press_keys(sess["own"])
        return {"opened": True}

    def restart_game(self):
        """Close the game politely, write the waiting settings and start it again straight into the game."""
        sess = self.session
        if not sess or not sess.get("canRestart"):
            raise ApiError("Questo gioco non si può riavviare da ModHub.", 409)
        if sess.get("restarting"):
            return {"restarting": True}
        proc, relaunch, extra = self._session_proc, list(self._relaunch), dict(self._restart_env or {})
        sess["restarting"] = True
        self.close_overlay()
        threading.Thread(target=self._restart, args=(sess, proc, relaunch, extra), daemon=True).start()
        return {"restarting": True}

    def _restart(self, sess, proc, relaunch, extra):
        self.close_game(proc)
        try:
            proc.wait(RESTART_WAIT)
        except subprocess.TimeoutExpired:
            sess["restarting"] = False
            self.notice("Il gioco non si è chiuso: chiudilo tu, le impostazioni si applicheranno da sole.", error=True)
            return
        deadline = time.time() + RESTART_WAIT
        while self.session is sess and time.time() < deadline:  # the watcher ends the session and writes the settings
            time.sleep(0.1)
        if self.session is sess:
            self._end_session()
        try:
            self.play(*relaunch, extra_env=extra)
            self.notice("Impostazioni applicate: il gioco si sta riavviando.")
        except ApiError as e:
            self.notice(f"Impostazioni applicate, ma non sono riuscito a riavviare il gioco: {e}", error=True)

    def _spawn_overlay(self):
        """The in-game panel is a small native window (overlay_ui.py), not a browser window."""
        return subprocess.Popen(selfrun.panel_command("--base", self.base_url, "--standby"), cwd=str(APP_DIR))

    def _spawn_toast(self, hotkey):
        """The reminder is the same small native program as the panel, in its --toast mode."""
        return subprocess.Popen(selfrun.panel_command("--toast", hotkey, "--seconds", str(TOAST_SECONDS)),
                                cwd=str(APP_DIR), startupinfo=overlay.no_activate_startup())

    def _panel_alive(self):
        proc = self._overlay_proc
        return bool(proc and proc.poll() is None)

    def _overlay_is_open(self):
        return self._overlay_wanted and self._panel_alive()

    def toggle_overlay(self):
        if self._overlay_is_open():
            self.close_overlay()
        else:
            self.open_overlay()

    def open_overlay(self):
        if not self.session or not self.base_url:
            return
        self.close_toast()  # the reminder did its job
        if self._mem:  # find the game's settings now, while the player looks at the panel
            threading.Thread(target=self._mem.prepare, daemon=True).start()
        self._prev_window = overlay.foreground()
        if not self._panel_alive():
            self._overlay_proc = self.spawn_overlay()
        self._overlay_wanted = True  # the waiting panel sees this within a tenth of a second and shows itself

    def close_overlay(self):
        """Hide the panel (its program stays ready for next time) and give the keyboard back to the game."""
        was, self._overlay_wanted = self._overlay_wanted, False
        prev, self._prev_window = self._prev_window, None
        if was and prev:
            time.sleep(0.12)  # the panel hides itself on its next look; then the game gets the keyboard back
            overlay.focus(prev)

    def overlay_wanted(self):
        """Asked by the waiting panel ten times a second: show or hide? `session` False tells it to quit."""
        return {"wanted": bool(self._overlay_wanted and self.session), "session": self.session is not None}

    def _stop_panel(self):
        self._overlay_wanted = False
        proc, self._overlay_proc = self._overlay_proc, None
        if proc is not None:
            def reap():  # it quits by itself when it sees the game is gone; make sure
                try:
                    proc.wait(2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            threading.Thread(target=reap, daemon=True).start()

    # ------------------------------------------------------------------ snapshot

    def snapshot(self):
        with self.lock:
            cat, installed = self.catalog, self.db.installed
            games = []
            for gid, g in cat["games"].items():
                root = self.game_root(gid)
                profiles = [{"project": None, "name": STANDARD}]
                for pid, rec in installed.items():
                    if rec.get("game") == gid:
                        profiles += [{"project": pid, "name": p["name"]} for p in rec.get("launch_profiles", [])]
                games.append({"id": gid, "name": g["name"], "found": bool(root), "root": str(root) if root else None,
                              "hasSettings": bool(g.get("settings")), "custom": False,
                              "base": core.base_version(g, root), "profiles": profiles})
            for gid, g in self.db.custom_games.items():
                root = self.game_root(gid)
                profiles = [{"project": None, "name": STANDARD}]
                entry = {"id": gid, "name": g["name"], "found": bool(root), "root": g["root"], "custom": True,
                         "hasSettings": False, "base": None, "emulator": None}
                if g.get("emulator"):
                    emu = self.emulator_def(g["emulator"]) or {"name": "Emulatore rimosso", "args": []}
                    if emu.get("args_fullscreen"):
                        profiles.append({"project": None, "name": emulators.FULLSCREEN_PROFILE})
                    missing = "emulator" if not self.emu_exe(g["emulator"]) else None if Path(g["rom"]).exists() else "rom"
                    entry.update(emulator=g["emulator"], emulatorName=emu["name"], rom=g["rom"], missing=missing,
                                 hasSettings=bool(emu.get("settings")) and missing != "emulator")
                games.append({**entry, "profiles": profiles})
            emulator_list = []
            for emu in self.emulator_defs():
                path = self.emu_exe(emu["id"])
                emulator_list.append({
                    "id": emu["id"], "name": emu["name"], "systems": emu.get("systems", []), "site": bool(emu.get("site")),
                    "needsBios": bool(emu.get("needs_bios")), "custom": bool(emu.get("custom")),
                    "configured": bool(path), "path": str(path) if path else None,
                    "games": sum(1 for g in self.db.custom_games.values() if g.get("emulator") == emu["id"]),
                    "extensions": emu.get("extensions", []), "aliases": emu.get("aliases", []),
                    "canInstall": bool(emu.get("install")), "managed": emu["id"] in self.db.emulator_installs,
                    "hasSettings": bool(emu.get("settings")),
                    "version": (self.db.emulator_installs.get(emu["id"]) or {}).get("version")})
            projects = []
            for p in cat["projects"]:
                rec = installed.get(p["id"])
                projects.append({**{k: p[k] for k in ("id", "name", "author", "game", "summary", "latest")},
                                 "installed": rec["version"] if rec else None,
                                 "status": core.project_status(p["latest"], rec)})
            return {"catalogName": cat.get("name", ""), "source": self.db.source or DEFAULT_CATALOG,
                "sourceIsDefault": not self.db.source, "offline": self.offline,
                "feedback": cat.get("feedback") if str(cat.get("feedback", "")).startswith("https://") else None,
                    "loading": self.loading, "error": self.error, "games": games, "projects": projects,
                    "emulators": emulator_list, "session": self.session, "notice": self._notice, "prefs": self.prefs(),
                    "job": self.job}

    def project_detail(self, pid):
        manifest = self.manifest(pid)
        record = self.db.installed.get(pid)
        risky = {v: core.new_executables(manifest, v, record) for v in manifest["versions"]}
        return {"manifest": manifest, "risky": risky}

    # ------------------------------------------------------------------ jobs

    def _start_job(self, kind, pid, work, done_message):
        with self.lock:
            if self.job and self.job["state"] == "running":
                raise ApiError("C'è già un'operazione in corso.", 409)
            self._seq += 1
            job = self.job = {"id": self._seq, "kind": kind, "pid": pid, "state": "running", "pct": 0, "message": "", "stage": ""}

        def progress(done, total):
            job["pct"] = int(done * 100 / total) if total else 100

        def target():
            try:
                message = done_message(work(progress))
                job.update(state="done", pct=100, message=message)
            except (core.ModHubError, OSError) as e:
                job.update(state="error", message=str(e))
            except Exception as e:
                traceback.print_exc()
                job.update(state="error", message=f"Errore imprevisto: {e}")

        threading.Thread(target=target, daemon=True).start()

    def install(self, pid, version, confirmed):
        entry = self.entry(pid)
        root = self.game_root(entry["game"])
        if not root:
            raise ApiError("Gioco non trovato: scegli prima la cartella del gioco.")
        manifest = self.manifest(pid)
        if version not in manifest["versions"]:
            raise ApiError("Versione inesistente.", 404)
        record = self.db.installed.get(pid)
        if core.new_executables(manifest, version, record) and not confirmed:
            raise ApiError("Serve la conferma per installare file eseguibili.", 428)
        verb = "aggiornato" if record and core.vkey(version) > core.vkey(record["version"]) else "installato"
        self._start_job(
            "install", pid,
            lambda progress: core.install(self.source(), manifest, version, root, self.db, progress),
            lambda n: f"{manifest['name']} {version} {verb} ({n} file scaricati)")

    def uninstall(self, pid):
        record = self.db.installed.get(pid)
        if not record:
            raise ApiError("Non è installato.", 404)
        root = self.game_root(record["game"])
        if not root:
            raise ApiError("Gioco non trovato: scegli la cartella del gioco.")
        self._start_job("uninstall", pid, lambda progress: core.uninstall(pid, root, self.db),
                        lambda _: f"{record['name']} disinstallato")


class Watchdog:
    """Quits the server when the window is gone (closed page says bye, pings stop)."""

    def __init__(self, enabled, keep_alive=lambda: False):
        self.enabled = enabled
        self.keep_alive = keep_alive  # True while a game started by ModHub is running: the hotkey must stay available
        self.start = self.last_ping = time.time()
        self.pinged = False
        self.bye_at = None

    def session_ended(self):
        if time.time() - self.last_ping > 15:  # no window is open any more
            self.bye_at = time.time() + 4

    def ping(self):
        self.pinged, self.last_ping, self.bye_at = True, time.time(), None

    def bye(self):
        self.bye_at = time.time() + 4  # a reload says bye too, then pings again right away

    def run(self):
        while True:
            time.sleep(1)
            now = time.time()
            gone = (self.bye_at and now > self.bye_at) or (self.pinged and now - self.last_ping > 180) \
                or (not self.pinged and now - self.start > 90)
            if self.enabled and gone and not self.keep_alive():
                os._exit(0)


def make_handler(hub, token, watchdog):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body, ctype):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        def _routes(self, q, body):
            return {
                ("GET", "state"): lambda: hub.snapshot(),
                ("GET", "project"): lambda: hub.project_detail(q.get("id", [""])[0]),
                ("POST", "refresh"): lambda: hub.refresh() or {},
                ("POST", "source"): lambda: hub.set_source(body.get("value")) or {},
                ("POST", "install"): lambda: hub.install(body.get("id"), body.get("version"), bool(body.get("confirmed"))) or {},
                ("POST", "uninstall"): lambda: hub.uninstall(body.get("id")) or {},
                ("POST", "play"): lambda: hub.play(body.get("game"), body.get("project"), body.get("name")),
                ("POST", "prefs"): lambda: hub.set_prefs(body.get("overlayHotkey")),
                ("GET", "live"): lambda: hub.live_state(),
                ("POST", "live-do"): lambda: hub.live_do(body.get("control"), body.get("action")),
                ("POST", "live-apply"): lambda: hub.live_apply(body.get("changes")),
                ("POST", "overlay-toggle"): lambda: hub.toggle_overlay() or {},
                ("POST", "overlay-close"): lambda: hub.close_overlay() or {},
            ("POST", "game-menu"): lambda: hub.open_game_menu(),
            ("GET", "overlay-wanted"): lambda: hub.overlay_wanted(),
            ("POST", "live-set"): lambda: hub.live_set(body.get("key"), body.get("value")),
            ("POST", "game-restart"): lambda: hub.restart_game(),
                ("POST", "pick-folder"): lambda: hub.pick_folder(body.get("game")),
                ("POST", "pick-game"): lambda: hub.pick_game_exe(),
                ("POST", "pick-rom"): lambda: hub.pick_rom(body.get("emulator")),
                ("POST", "emu-plan"): lambda: hub.emu_plan(body.get("emulator")),
                ("POST", "emu-install"): lambda: hub.emu_install(body.get("emulator"), body.get("asset")) or {},
                ("POST", "emu-check"): lambda: hub.emu_check(),
                ("POST", "emu-uninstall"): lambda: hub.emu_uninstall(body.get("emulator")),
                ("POST", "emu-open"): lambda: hub.emu_open(body.get("emulator")) or {},
                ("POST", "emu-scan"): lambda: hub.emu_scan(),
                ("POST", "emu-detect"): lambda: hub.emu_detect(body.get("emulator")),
                ("POST", "emu-pick"): lambda: hub.emu_pick(body.get("emulator")),
                ("POST", "emu-forget"): lambda: hub.emu_forget(body.get("emulator")) or {},
                ("POST", "emu-site"): lambda: hub.emu_site(body.get("emulator")) or {},
                ("POST", "emu-custom-pick"): lambda: hub.emu_custom_pick(),
                ("POST", "emu-custom-add"): lambda: hub.emu_custom_add(body.get("name"), body.get("args")),
                ("POST", "add-game"): lambda: hub.add_game(body.get("name")),
                ("POST", "remove-game"): lambda: hub.remove_game(body.get("game")) or {},
                ("POST", "open-folder"): lambda: hub.open_folder(body.get("game")) or {},
                ("GET", "settings"): lambda: hub.settings_get(q.get("game", [""])[0]),
                ("GET", "hardware"): lambda: hub.hardware_info(q.get("game", [""])[0], q.get("refresh", ["0"])[0] == "1"),
                ("POST", "settings-save"): lambda: hub.settings_save(body.get("game"), body.get("changes"), bool(body.get("queue"))),
                ("POST", "settings-restore"): lambda: hub.settings_restore(body.get("game")) or {},
                ("POST", "ping"): lambda: watchdog.ping() or {},
                ("POST", "bye"): lambda: watchdog.bye() or {},
            }

        def _handle(self, method):
            port = self.server.server_port
            if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return self._json(403, {"error": "Host non consentito."})
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                item = STATIC.get(url.path)
                if method != "GET" or not item:
                    return self._send(404, b"Not found", "text/plain")
                return self._send(200, (UI_DIR / item[0]).read_bytes(), item[1])
            q = parse_qs(url.query)
            if not hmac.compare_digest(q.get("t", [""])[0], token):
                return self._json(403, {"error": "Token non valido."})
            if url.path == "/api/game-icon" and method == "GET":
                png = hub.game_icon(q.get("id", [""])[0])
                if not png:
                    return self._send(404, b"", "text/plain")
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                return self.wfile.write(png)
            try:
                body = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > 1_000_000:
                        raise ApiError("Richiesta troppo grande.", 413)
                    body = json.loads(self.rfile.read(length) or b"{}")
                handler = self._routes(q, body).get((method, url.path[5:]))
                if not handler:
                    raise ApiError("Endpoint sconosciuto.", 404)
                self._json(200, handler())
            except ApiError as e:
                self._json(e.status, {"error": str(e)})
            except core.ModHubError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                self._json(500, {"error": f"Errore interno: {e}"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return Handler


def browser_candidates():
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for root in filter(None, roots):
        yield Path(root) / "Microsoft/Edge/Application/msedge.exe"
        yield Path(root) / "Google/Chrome/Application/chrome.exe"


def open_window(url, size=(1240, 800), position=None):
    for exe in browser_candidates():
        if exe.is_file():
            args = [str(exe), f"--app={url}", f"--window-size={size[0]},{size[1]}"]
            if position:
                args.append(f"--window-position={position[0]},{position[1]}")
            subprocess.Popen(args)
            return
    webbrowser.open(url)


def main():
    ap = argparse.ArgumentParser(description="ModHub")
    ap.add_argument("--catalog", help="catalog URL or folder (default: ModHub's public catalog on GitHub)")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-window", action="store_true", help="print the URL instead of opening a window")
    args = ap.parse_args()

    hub, token = Hub(args.catalog), secrets.token_urlsafe(18)
    watchdog = Watchdog(enabled=not args.no_window, keep_alive=lambda: hub.session is not None)
    hub.on_session_end = watchdog.session_ended
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(hub, token, watchdog))
    url = f"http://127.0.0.1:{server.server_port}/?t={token}"
    hub.base_url = url
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if args.no_window:
        print(url, flush=True)
    else:
        open_window(url)
    watchdog.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log = core.home_dir() / "error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), "utf-8")
        raise
