"""The in-game panel: a small borderless native window docked at the bottom-right of the screen.

It runs as its own process (started by ModHub when Shift+Tab is pressed during a game), talks to the ModHub
server over HTTP and is built with tkinter, so it appears instantly and is not a browser window.
"""
import argparse
import ctypes
import json
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overlay

BG, CARD, CARD2, LINE = "#0e1117", "#161b26", "#212a3a", "#2c3547"
TEXT, MUTED, FAINT = "#e9edf5", "#98a3b8", "#66728a"
GREEN, GREEN_HI, GREEN_DK, AMBER, RED = "#22c55e", "#4ade80", "#16a34a", "#fbbf24", "#f87171"
FONT = "Segoe UI"
WIDTH, MARGIN, MAX_HEIGHT = 400, 14, 660
STANDBY_POLL, STANDBY_GIVE_UP = 100, 50  # ms between "show?" questions while hidden; failures before quitting
PREVIEW_EVERY = 0.1  # seconds between values sent to the game while a bar is dragged


class ApiError(Exception):
    pass


class Api:
    """Blocking client for the ModHub server (the panel calls it from worker threads)."""

    def __init__(self, base):
        parts = urllib.parse.urlparse(base)
        self.root = f"{parts.scheme}://{parts.netloc}"
        self.token = urllib.parse.parse_qs(parts.query).get("t", [""])[0]

    def call(self, path, body=None):
        url = f"{self.root}/api/{path}{'&' if '?' in path else '?'}t={self.token}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                raise ApiError(json.load(e).get("error", str(e))) from e
            except ValueError:
                raise ApiError(str(e)) from e
        except OSError as e:
            raise ApiError(f"ModHub non risponde: {e}") from e


# ------------------------------------------------------------------ small widgets

class Flat(tk.Label):
    """A flat button with hover colours."""

    KINDS = {"ghost": (CARD2, TEXT, "#2b3650"), "green": (GREEN, "#04200f", GREEN_HI), "quiet": (CARD, MUTED, CARD2),
             "danger": (CARD2, RED, "#3a2530")}

    def __init__(self, parent, text, command, kind="ghost", width=None, pad=(12, 5)):
        bg, fg, hover = self.KINDS[kind]
        super().__init__(parent, text=text, bg=bg, fg=fg, font=(FONT, 9, "bold"), cursor="hand2", padx=pad[0], pady=pad[1],
                         width=width, bd=0)
        self._colors, self._command, self._enabled = (bg, fg, hover), command, True
        self.bind("<Enter>", lambda e: self._enabled and self.config(bg=self._colors[2]))
        self.bind("<Leave>", lambda e: self.config(bg=self._colors[0]))
        self.bind("<Button-1>", lambda e: self._enabled and self._command())

    def restyle(self, text, kind):
        """New label and colours in place (no redraw of the panel)."""
        self._colors = self.KINDS[kind]
        self.config(text=text, bg=self._colors[0], fg=self._colors[1])

    def enable(self, on=True):
        self._enabled = on
        bg, fg, _ = self._colors
        self.config(fg=fg if on else FAINT, bg=bg if on else CARD, cursor="hand2" if on else "arrow")


class ValueBar(tk.Canvas):
    """A continuous green bar for a number (volume...): drag to change, `on_release` is called once you let go."""

    def __init__(self, parent, low, high, value, show, on_release, width=336):
        super().__init__(parent, width=width, height=24, bg=CARD, highlightthickness=0, cursor="hand2")
        self.low, self.high, self.value, self.show, self.on_release, self.width = low, high, value, show, on_release, width
        self.bind("<Button-1>", self._move)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: self.on_release(self.value))
        self.draw()

    def _x(self, value):
        span = max(self.high - self.low, 1e-9)
        return 10 + (min(max(value, self.low), self.high) - self.low) / span * (self.width - 20)

    def draw(self):
        self.delete("all")
        self.create_line(10, 12, self.width - 10, 12, fill=LINE, width=6, capstyle="round")
        self.create_line(10, 12, self._x(self.value), 12, fill=GREEN, width=6, capstyle="round")
        cx = self._x(self.value)
        self.create_oval(cx - 8, 4, cx + 8, 20, fill="white", outline=GREEN, width=3)

    def _move(self, event):
        ratio = min(max((event.x - 10) / (self.width - 20), 0), 1)
        self.value = self.low + ratio * (self.high - self.low)
        self.draw()
        self.show(self.value)


class LevelSlider(tk.Canvas):
    """A green line with one notch per quality level; click or drag to choose."""

    def __init__(self, parent, levels, on_change, width=352):
        super().__init__(parent, width=width, height=62, bg=CARD, highlightthickness=0, cursor="hand2")
        self.n, self.width, self.on_change = levels, width, on_change
        self.level, self.reco = 1, None
        self.bind("<Button-1>", self._pick)
        self.bind("<B1-Motion>", self._pick)

    def x(self, level):
        pad = 16
        return pad + (level - 1) * (self.width - 2 * pad) / max(1, self.n - 1)

    def set(self, level, reco=None):
        self.level, self.reco = level, reco
        self.draw()

    def draw(self):
        self.delete("all")
        y = 18
        self.create_line(self.x(1), y, self.x(self.n), y, fill=LINE, width=6, capstyle="round")
        self.create_line(self.x(1), y, self.x(self.level), y, fill=GREEN, width=6, capstyle="round")
        for i in range(1, self.n + 1):
            on = i <= self.level
            self.create_line(self.x(i), y + 12, self.x(i), y + (20 if i == self.reco else 16), fill=GREEN if (on or i == self.reco) else FAINT,
                             width=3 if i == self.reco else 2)
        if self.reco:
            self.create_text(self.x(self.reco), y + 32, text="Consigliato", fill=GREEN_HI, font=(FONT, 8, "bold"))
        cx = self.x(self.level)
        self.create_oval(cx - 13, y - 13, cx + 13, y + 13, fill="#14301f", outline="")
        self.create_oval(cx - 9, y - 9, cx + 9, y + 9, fill="white", outline=GREEN, width=4)

    def _pick(self, event):
        best = min(range(1, self.n + 1), key=lambda i: abs(self.x(i) - event.x))
        if best != self.level:
            self.level = best
            self.draw()
            self.on_change(best)


# ------------------------------------------------------------------ the panel

class OverlayApp:
    def __init__(self, root, api, background=None, standby=False):
        self.root, self.api = root, api
        self.standby, self.visible, self._fails = standby, not standby, 0
        self.background = background or self._thread
        self.session = self.settings = self.hw = self.live = None
        self.level = None
        self.hotkey = "Ctrl+Shift+Tab"
        self._fit_job = None
        self.show_extra = False
        self.open_group = None  # the group of "Tutte le impostazioni" that is unfolded
        self.confirm_restart = False
        self._scroll_top = 0.0
        self._restore_scroll = False
        self._scroll_to = None
        self.groups = {}
        self._preview = {}  # slider being dragged: key -> (time of the last value sent, job waiting to send)
        self.error = None
        self.status_text = ""
        self.shell()
        if standby:
            self.poll()  # wait hidden until ModHub says "show"
        else:
            self.refresh()

    # ---- plumbing

    def _thread(self, work, done):
        def run():
            try:
                result, error = work(), None
            except ApiError as e:
                result, error = None, e
            except Exception as e:  # never let a worker thread die silently
                result, error = None, ApiError(str(e))
            self.root.after(0, lambda: done(result, error))
        threading.Thread(target=run, daemon=True).start()

    def say(self, text, error=False):
        self.status_text = text
        self.status.config(text=text, fg=RED if error else GREEN_HI)

    def shell(self):
        root = self.root
        root.configure(bg=BG)
        outer = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=LINE)
        outer.pack(fill="both", expand=True)
        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 6))
        self.dot = tk.Label(head, text="●", fg=GREEN_HI, bg=BG, font=(FONT, 9))
        self.dot.pack(side="left")
        self.title = tk.Label(head, text="ModHub · In gioco", fg=TEXT, bg=BG, font=(FONT, 11, "bold"))
        self.title.pack(side="left", padx=(6, 0))
        self.close = Flat(head, "✕", self.close_panel, kind="quiet", pad=(8, 2))
        self.close.pack(side="right")
        self.subtitle = tk.Label(outer, text="", fg=MUTED, bg=BG, font=(FONT, 9), anchor="w", justify="left", wraplength=WIDTH - 36)
        self.subtitle.pack(fill="x", padx=16, pady=(0, 6))
        for widget in (head, self.title, self.dot, self.subtitle):  # drag the panel by its header
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
        self.scroll = tk.Canvas(outer, bg=BG, highlightthickness=0, width=WIDTH - 4)
        self.scroll.pack(fill="both", expand=True, padx=(8, 0))
        self.body = tk.Frame(self.scroll, bg=BG)
        self.scroll.create_window((0, 0), window=self.body, anchor="nw", width=WIDTH - 24)
        self.body.bind("<Configure>", lambda e: self.scroll.configure(scrollregion=self.scroll.bbox("all")))
        self.scroll.bind_all("<MouseWheel>", lambda e: self.scroll.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", padx=14, pady=(4, 10))
        self.status = tk.Label(foot, text="", fg=GREEN_HI, bg=BG, font=(FONT, 9), anchor="w", justify="left", wraplength=WIDTH - 36)
        self.status.pack(fill="x")
        root.bind("<Escape>", lambda e: self.close_panel())

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _destroy(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass  # already closed

    def close_panel(self):
        """Tell the server (it hands the keyboard back to the game), then hide (or, outside standby, go away)."""
        if self.standby:
            self.hide()
            self.background(lambda: self.api.call("overlay-close", {}), lambda _result, _error: None)
            return
        self.background(lambda: self.api.call("overlay-close", {}), lambda _result, _error: self._destroy())
        self.root.after(1500, self._destroy)  # in case the server is gone

    # ---- standby: the program starts with the game and waits hidden, so the hotkey shows it at once

    def poll(self):
        def done(result, error):
            if error:
                self._fails += 1
                if self._fails > STANDBY_GIVE_UP:
                    return self._destroy()  # ModHub is gone
            else:
                self._fails = 0
                if not result.get("session"):
                    return self._destroy()  # the game is over
                if result.get("wanted") and not self.visible:
                    self.reveal()
                elif not result.get("wanted") and self.visible:
                    self.hide()
            self.root.after(STANDBY_POLL, self.poll)
        self.background(lambda: self.api.call("overlay-wanted"), done)

    def reveal(self):
        """Fresh values first, the right size and place second, then on screen: no empty or jumping window."""
        self.visible = True
        self.refresh(then=self._appear)

    def _appear(self):
        if not self.visible:
            return  # hidden again while loading
        if self._fit_job:
            self.root.after_cancel(self._fit_job)
        self.fit()
        self.root.deiconify()
        self.root.update_idletasks()
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            overlay.round_corners(hwnd, (34, 197, 94))
            overlay.make_topmost(hwnd)
            overlay.focus(hwnd)
        except (AttributeError, OSError):
            pass
        self.root.focus_force()

    def hide(self):
        self.visible = False
        self.confirm_restart = False
        self.root.withdraw()

    # ---- loading

    def refresh(self, then=None):
        def work():
            state = self.api.call("state")
            sess = state.get("session")
            out = {"session": sess, "settings": None, "live": None, "hw": None, "hotkey": (state.get("prefs") or {}).get("overlayHotkey")}
            if sess and sess.get("target"):
                target = urllib.parse.quote(sess["target"], safe="")
                out["settings"] = self.api.call(f"settings?game={target}")
                if out["settings"]["schema"].get("optimizer"):
                    out["hw"] = self.api.call(f"hardware?game={target}")
                if sess.get("live"):
                    out["live"] = self.api.call("live")
            return out

        def done(result, error):
            if error:
                self.error, self.session = str(error), None
            else:
                self.error = None
                self.hotkey = result["hotkey"] or self.hotkey
                self.session, self.settings, self.hw, self.live = result["session"], result["settings"], result["hw"], result["live"]
                if self.settings and self.level is None and self.settings["schema"].get("optimizer"):
                    self.level = self.closest_level()
            self.render()
            if then:
                then()
        self.background(work, done)

    def refresh_live(self, then=None):
        def done(result, error):
            if not error:
                self.live = result
                self.render()
            if then:
                then()
        self.background(lambda: self.api.call("live"), done)

    # ---- values

    def field(self, key):
        for group in self.settings["schema"]["groups"]:
            for section in group["sections"]:
                for f in section["fields"]:
                    if f["key"] == key:
                        return f
        return None

    def effective(self, key):
        """Current value of a setting: what the running game was last told, else queued, else the file, else the default."""
        for c in (self.live or {}).get("controls", []):
            if c.get("key") == key and c.get("value") is not None and c["type"] in ("stepper", "cycle") and isinstance(c["value"], int):
                return c["value"]
        s = self.settings
        if key in s["pending"]:
            return s["pending"][key]
        if key in s["values"]:
            return s["values"][key]
        f = self.field(key)
        return f.get("default") if f else None

    def closest_level(self):
        levels = self.settings["schema"]["optimizer"]["levels"]
        scores = [sum(1 for k, v in L["values"].items() if self.effective(k) == v) for L in levels]
        return scores.index(max(scores)) + 1

    def shown(self, key, value):
        f = self.field(key)
        if not f:
            return str(value)
        if f["type"] == "toggle":
            return "Sì" if value else "No"
        if f["type"] == "select":
            return next((o["label"] for o in f["options"] if o["value"] == value), str(value))
        return f"{value}{(' ' + f['unit']) if f.get('unit') else ''}"

    def diff(self, level):
        L = self.settings["schema"]["optimizer"]["levels"][level - 1]
        return [(self.field(k)["label"], self.shown(k, self.effective(k)), self.shown(k, v))
                for k, v in L["values"].items() if self.field(k) and self.effective(k) != v]

    # ---- drawing

    def card(self, title=None, note=None, parent=None):
        frame = tk.Frame(parent or self.body, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        frame.pack(fill="x", padx=(8, 8), pady=(0, 10))
        inner = tk.Frame(frame, bg=CARD)
        inner.pack(fill="x", padx=12, pady=8)
        if title:
            tk.Label(inner, text=title.upper(), fg=GREEN_HI, bg=CARD, font=(FONT, 8, "bold")).pack(anchor="w")
        if note:
            tk.Label(inner, text=note, fg=MUTED, bg=CARD, font=(FONT, 9), justify="left", anchor="w", wraplength=WIDTH - 70).pack(anchor="w", pady=(2, 4))
        return inner

    def fit(self):
        """Size the window to its content (scrolling only when the screen is too small) and keep it docked."""
        self._fit_job = None
        self.root.update_idletasks()
        scale = self.root.winfo_fpixels("1i") / 96
        left, top, right, bottom = overlay.work_area()
        room = bottom - top - int(2 * MARGIN * scale)
        chrome = self.root.winfo_reqheight() - self.scroll.winfo_reqheight()
        body = self.body.winfo_reqheight()
        self.scroll.config(height=max(60, min(body, room - chrome, int(MAX_HEIGHT * scale) - chrome)))
        self.root.update_idletasks()
        place(self.root)
        self.root.update_idletasks()
        if self._scroll_to is not None:  # a group was unfolded: bring its title to the top
            try:
                y = self._scroll_to.winfo_rooty() - self.body.winfo_rooty()
                self.scroll.yview_moveto(max(0.0, (y - 6) / max(1, self.body.winfo_height())))
            except tk.TclError:
                pass
            self._scroll_to = None
        elif self._restore_scroll:
            self.scroll.yview_moveto(self._scroll_top)  # a redraw keeps the scrolling where it was
        self._restore_scroll = False

    def schedule_fit(self):
        if self._fit_job:
            self.root.after_cancel(self._fit_job)
        self._fit_job = self.root.after(20, self.fit)

    def cancel(self):
        """Stop timers and remove the panel's widgets (used when closing, and by the tests)."""
        if self._fit_job:
            try:
                self.root.after_cancel(self._fit_job)
            except tk.TclError:
                pass
            self._fit_job = None
        for child in self.root.winfo_children():
            child.destroy()

    def render(self):
        try:
            self._scroll_top = self.scroll.yview()[0]
        except tk.TclError:
            self._scroll_top = 0.0
        for child in self.body.winfo_children():
            child.destroy()
        self.groups = {}
        self._restore_scroll = True
        self.schedule_fit()
        s = self.session
        self.subtitle.config(text=s["name"] if s else "")
        if self.error or not s:
            card = self.card()
            tk.Label(card, text="Nessun gioco in corso" if not self.error else "ModHub non risponde", fg=TEXT, bg=CARD, font=(FONT, 11, "bold")).pack(anchor="w")
            tk.Label(card, text=self.error or f"Avvia un gioco da ModHub: durante la partita premi {self.hotkey} per aprire questo pannello.", fg=MUTED,
                     bg=CARD, font=(FONT, 9), wraplength=WIDTH - 70, justify="left").pack(anchor="w", pady=(4, 0))
            return
        if not self.settings:
            card = self.card()
            tk.Label(card, text="Per questo gioco non ci sono impostazioni modificabili da ModHub.", fg=MUTED, bg=CARD, font=(FONT, 9),
                     wraplength=WIDTH - 70, justify="left").pack(anchor="w")
            return
        if s.get("restarting"):
            card = self.card()
            tk.Label(card, text="Riavvio in corso…", fg=TEXT, bg=CARD, font=(FONT, 11, "bold")).pack(anchor="w")
            tk.Label(card, text="Il gioco si chiude, ModHub scrive le impostazioni e lo riapre direttamente nella partita.", fg=MUTED,
                     bg=CARD, font=(FONT, 9), wraplength=WIDTH - 70, justify="left").pack(anchor="w", pady=(4, 0))
            return
        self.pending_holder = tk.Frame(self.body, bg=BG)
        self.pending_holder.pack(fill="x")
        self.fill_pending()
        if self.live and self.live.get("available"):
            self.live_card()
        elif s.get("own"):
            self.own_menu_card()
        if self.audio_fields() and not any(c["id"] == "volume" for c in (self.live or {}).get("controls", [])):
            self.audio_card()
        if self.settings["schema"].get("optimizer"):
            self.optimizer_card()
        self.all_settings_card()

    # ---- when the change is seen

    def live_keys(self):
        """Settings this game takes while it runs, straight from the panel (BT3: volumes and controller)."""
        return set((self.session or {}).get("liveKeys") or [])

    def later(self, many=False):
        """How a saved change reaches the game, in words."""
        verb = "si applicano" if many else "si applica"
        if self.session.get("canRestart"):
            return f"{verb} al riavvio del gioco"
        return f"{verb} quando chiudi il gioco"

    def fill_pending(self):
        """The "waiting" card, redrawn on its own (only for the changes the running game cannot take yet)."""
        for child in self.pending_holder.winfo_children():
            child.destroy()
        if [k for k in self.settings["pending"] if k not in self.live_keys()]:
            self.pending_card()
        self.schedule_fit()

    def pending_card(self):
        n = len([k for k in self.settings["pending"] if k not in self.live_keys()])
        card = self.card("In attesa", parent=self.pending_holder)
        tk.Label(card, text=f"{n} {'modifica salvata' if n == 1 else 'modifiche salvate'}: {self.later(n > 1)}.", fg=TEXT, bg=CARD,
                 font=(FONT, 9), wraplength=WIDTH - 70, justify="left").pack(anchor="w")
        actions = tk.Frame(card, bg=CARD)
        actions.pack(fill="x", pady=(8, 0))
        if self.confirm_restart:
            tk.Label(card, text="La partita in corso si interrompe (i salvataggi non si toccano). Il gioco riparte da solo.", fg=AMBER,
                     bg=CARD, font=(FONT, 9), wraplength=WIDTH - 70, justify="left").pack(anchor="w", pady=(6, 0))
            Flat(actions, "Riavvia ora", self.restart, kind="green").pack(side="right")
            Flat(actions, "No", lambda: self.ask_restart(False), kind="ghost").pack(side="right", padx=(0, 6))
            return
        if self.session.get("canRestart"):
            Flat(actions, "↻ Riavvia e applica", lambda: self.ask_restart(True), kind="green").pack(side="right")
        Flat(actions, "Annulla le modifiche", self.drop_pending, kind="quiet").pack(side="left")

    def ask_restart(self, on):
        self.confirm_restart = on
        self.fill_pending()

    def restart(self):
        self.confirm_restart = False
        self.say("Riavvio…")

        def done(_result, error):
            if error:
                return self.say(str(error), error=True)
            self.session["restarting"] = True
            self.render()
        self.background(lambda: self.api.call("game-restart", {}), done)

    def drop_pending(self):
        def done(_result, error):
            if error:
                return self.say(str(error), error=True)
            self.settings["pending"] = {}
            self.say("Modifiche annullate.")
            self.render()
        self.background(lambda: self.api.call("settings-save", {"game": self.session["target"], "changes": {}, "queue": True}), done)

    def own_menu_card(self):
        own = self.session["own"]
        note = self.settings["schema"].get("ownOverlayNote") or "Nel menu del gioco le modifiche si vedono subito."
        if self.live_keys():
            text = f"Quasi tutto cambia subito anche da qui (le voci segnate «● subito»). {note}"
        else:
            text = f"Da fuori ModHub non può cambiare questo gioco mentre gira. {note}"
        card = self.card("Dal vivo", text)
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(2, 0))
        Flat(row, f"Apri il menu del gioco ({own})", self.open_game_menu, kind="green").pack(side="left")

    def open_game_menu(self):
        self.say("Apro il menu del gioco…")

        def done(_result, error):
            if error:
                return self.say(str(error), error=True)
            self.say("Menu del gioco aperto.")  # ModHub closes this panel by itself, so the game gets the keyboard
        self.background(lambda: self.api.call("game-menu", {}), done)

    # ---- every setting of the game, one group at a time

    def all_settings_card(self):
        shown_above = {f["key"] for f in self.audio_fields()} if self.audio_fields() else set()
        groups = []
        for g in self.settings["schema"]["groups"]:
            fields = [f for s in g["sections"] for f in s["fields"] if f["key"] not in shown_above]
            if fields:
                groups.append((g, fields))
        if not groups:
            return
        if self.live_keys():
            note = f"Quelle segnate «subito» cambiano mentre giochi; le altre {self.later(True)}."
        else:
            note = f"Le modifiche fatte qui {self.later(True)}."
        card = self.card("Tutte le impostazioni", note)
        for g, fields in groups:
            header = Flat(card, f"▸  {g['title']}", lambda gid=g["id"]: self.toggle_group(gid), kind="ghost", pad=(10, 4))
            header.config(anchor="w")
            header.pack(fill="x", pady=(4, 0))
            self.groups[g["id"]] = {"title": g["title"], "card": card, "header": header, "fields": fields, "box": None}
        if self.open_group in self.groups:
            self._unfold(self.open_group)

    def _unfold(self, gid):
        g = self.groups[gid]
        if g["box"] is None:
            g["box"] = tk.Frame(g["card"], bg=CARD)
            for f in g["fields"]:
                self.field_row(g["box"], f)
        g["box"].pack(fill="x", pady=(2, 4), after=g["header"])
        g["header"].config(text=f"▾  {g['title']}")

    def toggle_group(self, gid):
        """Fold / unfold one group in place: nothing else is redrawn, so the panel does not flicker or jump."""
        was = self.open_group
        if was in self.groups:
            self.groups[was]["box"].pack_forget()
            self.groups[was]["header"].config(text=f"▸  {self.groups[was]['title']}")
        self.open_group = None if was == gid else gid
        if self.open_group:
            self._unfold(gid)
            self._scroll_to = self.groups[gid]["header"]
        self.schedule_fit()

    def field_row(self, parent, f):
        value = self.effective(f["key"])
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text=f["label"], fg=TEXT, bg=CARD, font=(FONT, 9, "bold"), anchor="w", justify="left",
                 wraplength=WIDTH - 170).pack(side="left")
        if f["key"] in self.live_keys():
            tk.Label(row, text="● subito", fg=GREEN_HI, bg=CARD, font=(FONT, 7, "bold")).pack(side="left", padx=(6, 0))
        kind = f["type"]
        if kind == "info":
            tk.Label(row, text=str(value if value not in (None, "") else "—"), fg=MUTED, bg=CARD, font=(FONT, 9)).pack(side="right")
        elif kind == "toggle":
            button = Flat(row, "Sì" if value else "No", lambda: None, kind="green" if value else "ghost", width=4, pad=(6, 2))
            button._command = lambda b=button: self.flip(b, f)
            button.pack(side="right")
        elif kind == "select":
            label = next((o["label"] for o in f["options"] if o["value"] == value), str(value))
            button = Flat(row, f"{label}  ▾", lambda: None, kind="ghost", pad=(8, 2))
            button._command = lambda b=button: self.choose(b, f)
            button.pack(side="right")
        elif kind in ("slider", "number") and "min" in f and "max" in f:
            value = f["min"] if value is None else value
            shown = tk.Label(row, text=self.format_value(f, value), fg=GREEN_HI, bg=CARD, font=(FONT, 9, "bold"))
            shown.pack(side="right")
            ValueBar(parent, f["min"], f["max"], value, lambda v, f=f, label=shown: self.dragging(f, v, label),
                     lambda v, f=f: self.save_setting(f, self.clean(f, v)), width=WIDTH - 64).pack(anchor="w")
        else:  # text (or a number with no range): typed, saved with Enter
            entry = tk.Entry(row, bg=CARD2, fg=TEXT, insertbackground=TEXT, relief="flat", font=(FONT, 9), width=14)
            entry.insert(0, "" if value is None else str(value))
            entry.pack(side="right", ipady=2)
            entry.bind("<Return>", lambda e, f=f, entry=entry: self.save_typed(f, entry.get()))
        if f.get("desc"):
            tk.Label(parent, text=f["desc"], fg=FAINT, bg=CARD, font=(FONT, 8), anchor="w", justify="left",
                     wraplength=WIDTH - 70).pack(fill="x")

    def flip(self, button, f):
        new = not self.effective(f["key"])
        self.save_setting(f, new, lambda: button.restyle("Sì" if new else "No", "green" if new else "ghost"))

    def choose(self, button, f):
        current = self.effective(f["key"])
        menu = tk.Menu(self.root, tearoff=0, bg=CARD2, fg=TEXT, activebackground=GREEN_DK, activeforeground="white", font=(FONT, 9), bd=0)
        for o in f["options"]:
            menu.add_command(label=("✓ " if o["value"] == current else "   ") + o["label"],
                             command=lambda o=o: self.pick(button, f, o))
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())

    def pick(self, button, f, option):
        self.save_setting(f, option["value"], lambda: button.restyle(f"{option['label']}  ▾", "ghost"))

    def dragging(self, f, value, label):
        """A bar is being dragged: show the number and, for settings the game takes live, let it hear/feel it now."""
        value = self.clean(f, value)
        label.config(text=self.format_value(f, value))
        if f["key"] not in self.live_keys():
            return
        last, job = self._preview.get(f["key"], (0.0, None))
        if job:
            self.root.after_cancel(job)
        wait = PREVIEW_EVERY - (time.monotonic() - last)
        if wait <= 0:
            self._send_preview(f["key"], value)
        else:  # at most a few values a second; the last one always arrives
            self._preview[f["key"]] = (last, self.root.after(int(wait * 1000), lambda: self._send_preview(f["key"], value)))

    def _send_preview(self, key, value):
        self._preview[key] = (time.monotonic(), None)
        self.background(lambda: self.api.call("live-set", {"key": key, "value": value}), lambda _r, _e: None)

    def save_typed(self, f, text):
        text = text.strip()
        if f["type"] == "number":
            try:
                text = float(text) if not f.get("int") else int(text)
            except ValueError:
                return self.say(f"{f['label']}: scrivi un numero.", error=True)
        self.save_setting(f, text)

    def live_card(self):
        card = self.card("In tempo reale")
        controls = self.live["controls"]
        extras = [c for c in controls if c.get("extra")]
        for c in [c for c in controls if not c.get("extra")] + (extras if self.show_extra else []):
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=c["label"], fg=TEXT, bg=CARD, font=(FONT, 10)).pack(side="left")
            right = tk.Frame(row, bg=CARD)
            right.pack(side="right")
            value = c["display"] if c["display"] is not None else "—"
            if c["type"] == "stepper":
                Flat(right, "−", lambda cid=c["id"]: self.do(cid, "down"), width=2, pad=(6, 2)).pack(side="left")
                tk.Label(right, text=str(value), fg=GREEN_HI, bg=CARD, font=(FONT, 10, "bold"), width=6).pack(side="left")
                Flat(right, "+", lambda cid=c["id"]: self.do(cid, "up"), width=2, pad=(6, 2)).pack(side="left")
            else:
                tk.Label(right, text=str(value), fg=GREEN_HI, bg=CARD, font=(FONT, 10, "bold")).pack(side="left", padx=(0, 8))
                Flat(right, "Cambia", lambda cid=c["id"]: self.do(cid, "press"), pad=(10, 2)).pack(side="left")
        if extras:
            more = Flat(card, "Meno ▴" if self.show_extra else "Altro ▾", self.toggle_extra, kind="quiet", pad=(6, 2))
            more.pack(anchor="w", pady=(4, 0))

    def toggle_extra(self):
        self.show_extra = not self.show_extra
        self.render()

    def audio_fields(self):
        """Volume sliders the game itself offers (BT3: general, music, effects)."""
        return [f for g in self.settings["schema"]["groups"] if g["id"] == "audio" for s in g["sections"] for f in s["fields"]
                if f["type"] == "slider"]

    def audio_card(self):
        card = self.card("Audio")
        for f in self.audio_fields():
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", pady=(4, 0))
            tk.Label(row, text=f["label"], fg=TEXT, bg=CARD, font=(FONT, 10)).pack(side="left")
            value = self.effective(f["key"])
            value = f["min"] if value is None else value
            shown = tk.Label(row, text=self.format_value(f, value), fg=GREEN_HI, bg=CARD, font=(FONT, 10, "bold"))
            shown.pack(side="right")
            ValueBar(card, f["min"], f["max"], value, lambda v, f=f, label=shown: self.dragging(f, v, label),
                     lambda v, f=f: self.save_setting(f, self.clean(f, v))).pack(anchor="w")

    @staticmethod
    def clean(f, value):
        step = f.get("step")
        if step and step >= 1:
            value = round(value / step) * step
        return int(round(value)) if f.get("int") else round(value, 2)

    @staticmethod
    def format_value(f, value):
        if f.get("percent"):
            return f"{round(value * 100)}%"
        unit = f.get("unit") or ""
        return f"{value}{unit}" if unit == "%" else f"{value}{(' ' + unit) if unit else ''}"

    def save_setting(self, f, value, then=None):
        """Keep a value chosen in the panel. The game takes it at once if it can (BT3: volumes, controller), and it is
        written into the game's settings when the game restarts or closes. Only what changed is redrawn."""
        changes = {**self.settings["pending"], f["key"]: value}

        def done(result, error):
            if error:
                return self.say(str(error), error=True)
            self.settings["pending"] = changes
            shown = self.shown(f["key"], value) if f["type"] in ("toggle", "select") else self.format_value(f, value)
            if f["key"] in (result or {}).get("live", []):
                self.say(f"{f['label']}: {shown}. Applicato subito nel gioco.")
            else:
                self.say(f"{f['label']}: {shown}. Salvato: {self.later()}.")
            if then:
                then()
            self.fill_pending()
        self.background(lambda: self.api.call("settings-save", {"game": self.session["target"], "changes": changes, "queue": True}), done)

    def optimizer_card(self):
        opt = self.settings["schema"]["optimizer"]
        levels = opt["levels"]
        card = self.card("Ottimizza per il tuo PC")
        hw = self.hw
        if hw and "gpu" in hw:
            gpu = hw["gpu"].replace("NVIDIA ", "").replace("AMD ", "")
            tk.Label(card, text=f"{gpu} · fascia {hw['tierName'].lower()}", fg=MUTED, bg=CARD, font=(FONT, 9), anchor="w").pack(fill="x", pady=(2, 4))
        reco = hw.get("recommended") if hw else None
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text="Prestazioni", fg=MUTED, bg=CARD, font=(FONT, 8, "bold")).pack(side="left")
        tk.Label(row, text="Qualità", fg=MUTED, bg=CARD, font=(FONT, 8, "bold")).pack(side="right")
        self.slider = LevelSlider(card, len(levels), self.pick_level)
        self.slider.pack(pady=(0, 2))
        self.slider.set(self.level or 1, reco)
        self.detail = tk.Frame(card, bg=CARD)
        self.detail.pack(fill="x")
        self.draw_detail()

    def pick_level(self, level):
        self.level = level
        self.draw_detail()

    def draw_detail(self):
        for child in self.detail.winfo_children():
            child.destroy()
        opt = self.settings["schema"]["optimizer"]
        L = opt["levels"][self.level - 1]
        reco = self.hw.get("recommended") if self.hw else None
        name = tk.Frame(self.detail, bg=CARD)
        name.pack(fill="x")
        tk.Label(name, text=L["name"], fg=TEXT, bg=CARD, font=(FONT, 12, "bold")).pack(side="left")
        if self.level == reco:
            tk.Label(name, text="✓ Consigliato", fg=GREEN_HI, bg="#10321f", font=(FONT, 8, "bold"), padx=7, pady=1).pack(side="left", padx=8)
        tk.Label(self.detail, text=L["desc"], fg=MUTED, bg=CARD, font=(FONT, 9), justify="left", anchor="w", wraplength=WIDTH - 70).pack(fill="x", pady=(2, 6))
        changes = self.diff(self.level)
        if changes:
            for label, before, after in changes:
                line = tk.Frame(self.detail, bg=CARD2)
                line.pack(fill="x", pady=2)
                tk.Label(line, text=label, fg=MUTED, bg=CARD2, font=(FONT, 9), anchor="w").pack(side="left", padx=(8, 6), pady=3)
                tk.Label(line, text=after, fg=GREEN_HI, bg=CARD2, font=(FONT, 9, "bold")).pack(side="right", padx=(0, 8))
                tk.Label(line, text=f"{before}  →", fg=FAINT, bg=CARD2, font=(FONT, 9)).pack(side="right")
        else:
            tk.Label(self.detail, text="Le impostazioni sono già così.", fg=MUTED, bg=CARD, font=(FONT, 9), anchor="w").pack(fill="x")
        actions = tk.Frame(self.detail, bg=CARD)
        actions.pack(fill="x", pady=(8, 0))
        if reco and self.level != reco:
            Flat(actions, "Consigliato", lambda: self.pick_reco(reco), kind="ghost").pack(side="left")
        apply_button = Flat(actions, f"Applica «{L['name']}»", self.apply_level, kind="green")
        apply_button.pack(side="right")
        apply_button.enable(bool(changes))

    def pick_reco(self, reco):
        self.level = reco
        self.slider.set(reco, reco)
        self.draw_detail()

    # ---- actions

    def do(self, control, action):
        self.say("…")

        def done(result, error):
            if error:
                return self.say(str(error), error=True)
            self.say(result["message"] if result.get("answered") else "Comando inviato, ma il gioco non ha risposto.", error=not result.get("answered"))
            self.refresh_live()
        self.background(lambda: self.api.call("live-do", {"control": control, "action": action}), done)

    def apply_level(self):
        changes = {k: v for k, v in self.settings["schema"]["optimizer"]["levels"][self.level - 1]["values"].items()
                   if self.effective(k) != v}
        if not changes:
            return
        self.say("Applico…")
        L = self.settings["schema"]["optimizer"]["levels"][self.level - 1]

        def done(result, error):
            if error:
                return self.say(str(error), error=True)
            ok = [r["label"] for r in result["live"] if r["reached"]]
            late = [self.field(k)["label"] for k in result["waiting"] if self.field(k)]
            missed = [r["label"] for r in result["live"] if not r["reached"]]
            parts = [f"«{L['name']}» applicato"]
            if ok:
                parts.append("subito: " + ", ".join(ok))
            if late and not ok and not missed:
                parts = [f"«{L['name']}» salvato: {self.later(True)}"]
            elif late:
                parts.append(", ".join(late) + f": {self.later()}")
            if missed:
                parts.append("il gioco non ha confermato (salvato per la prossima volta): " + ", ".join(missed))
            self.say(" · ".join(parts))
            self.refresh()
        self.background(lambda: self.api.call("live-apply", {"changes": changes}), done)


# ------------------------------------------------------------------ startup

def place(root, height_px=None):
    """Dock the window at the bottom-right of the work area (the screen without the taskbar)."""
    left, top, right, bottom = overlay.work_area()
    scale = root.winfo_fpixels("1i") / 96
    width = int(WIDTH * scale)
    gap = int(MARGIN * scale)
    height = min(height_px or root.winfo_reqheight(), bottom - top - 2 * gap)
    root.geometry(f"{width}x{height}+{right - width - gap}+{bottom - height - gap}")


class Toast:
    """The reminder shown at the top-right when a game starts: which keys open ModHub's panel. It closes by itself
    after `seconds` (a thin bar shows the time left) or with its ✕, and never takes the keyboard from the game."""

    WIDTH = 340

    def __init__(self, root, hotkey, seconds=30, on_close=None):
        self.root, self.seconds, self.left = root, seconds, float(seconds)
        self.on_close = on_close or root.destroy
        self._job = None
        root.configure(bg=BG)
        outer = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=GREEN_DK)
        outer.pack(fill="both", expand=True)
        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", padx=(14, 8), pady=(10, 0))
        tk.Label(head, text="●", fg=GREEN_HI, bg=BG, font=(FONT, 9)).pack(side="left")
        tk.Label(head, text="ModHub", fg=TEXT, bg=BG, font=(FONT, 10, "bold")).pack(side="left", padx=(6, 0))
        self.close_button = Flat(head, "✕", self.close, kind="quiet", pad=(8, 1))
        self.close_button.pack(side="right")
        line = tk.Frame(outer, bg=BG)
        line.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(line, text="Premi", fg=MUTED, bg=BG, font=(FONT, 10)).pack(side="left")
        for i, key in enumerate(hotkey.split("+")):
            if i:
                tk.Label(line, text="+", fg=FAINT, bg=BG, font=(FONT, 9)).pack(side="left", padx=1)
            tk.Label(line, text=key, fg=TEXT, bg=CARD2, font=(FONT, 9, "bold"), padx=6, pady=1,
                     highlightthickness=1, highlightbackground=LINE).pack(side="left", padx=(4 if i == 0 else 1, 0))
        tk.Label(outer, text="per aprire le impostazioni di ModHub durante il gioco.", fg=MUTED, bg=BG, font=(FONT, 10),
                 anchor="w", justify="left", wraplength=self.WIDTH - 30).pack(fill="x", padx=14, pady=(2, 8))
        self.bar = tk.Canvas(outer, height=3, bg=BG, highlightthickness=0)
        self.bar.pack(fill="x", padx=1, pady=(0, 1))
        self.tick()

    def tick(self):
        self.bar.delete("all")
        width = max(1, self.bar.winfo_width())
        self.bar.create_rectangle(0, 0, width * self.left / self.seconds, 3, fill=GREEN, width=0)
        if self.left <= 0:
            return self.close()
        self.left -= 0.1
        self._job = self.root.after(100, self.tick)

    def close(self):
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        self.on_close()


def show_toast(hotkey, seconds):
    before = overlay.foreground()  # the game: taken before Tk creates any window of ours
    root = tk.Tk()
    root.withdraw()  # first thing: a window Tk shows on its own would take the keyboard from the game
    root.title(overlay.OVERLAY_TITLE + " · avviso")
    root.overrideredirect(True)
    Toast(root, hotkey, seconds)
    root.update_idletasks()
    scale = root.winfo_fpixels("1i") / 96
    left, top, right, bottom = overlay.work_area()
    width, gap = int(Toast.WIDTH * scale), int(MARGIN * scale)
    root.geometry(f"{width}x{root.winfo_reqheight()}+{right - width - gap}+{top + gap}")
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
    overlay.make_passive(hwnd)
    ctypes.windll.user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE: on screen without taking the keyboard
    x, y, height = right - width - gap, top + gap, root.winfo_reqheight()
    overlay.place_window(hwnd, x, y, width, height)
    root.update()
    overlay.round_corners(hwnd, (22, 163, 74))
    def give_back():  # Windows may still activate a new program's first window: hand the keyboard straight back
        if before and overlay.foreground() != before:
            overlay.focus(before)
    give_back()
    root.after(300, give_back)
    root.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="ModHub address with the access code, e.g. http://127.0.0.1:1234/?t=...")
    ap.add_argument("--toast", metavar="KEYS", help="only show the 'press KEYS for ModHub' reminder")
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--standby", action="store_true", help="start hidden and wait for ModHub to ask for the panel")
    args = ap.parse_args()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    if args.toast:
        return show_toast(args.toast, max(3, min(args.seconds, 300)))
    if not args.base:
        ap.error("--base is required")
    root = tk.Tk()
    if args.standby:
        root.withdraw()  # ready but invisible until the hotkey
    root.title(overlay.OVERLAY_TITLE)
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    place(root, 160)  # a first guess; the panel resizes itself once it has its content
    app = OverlayApp(root, Api(args.base), standby=args.standby)
    if args.standby:
        return root.mainloop()
    root.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
    overlay.round_corners(hwnd, (34, 197, 94))
    overlay.make_topmost(hwnd)
    root.focus_force()
    root.mainloop()


if __name__ == "__main__":
    main()
