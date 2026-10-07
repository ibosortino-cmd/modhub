"""Change an emulator's settings while the game runs, using the emulator's own hotkeys (PCSX2).

PCSX2 cannot be handed new settings from outside, but it exposes hotkeys that change resolution, colour
accuracy, aspect ratio and more on the fly. ModHub binds those hotkeys to F13-F24 (keys that do not exist on a
keyboard, so they never clash with anything), starts PCSX2 with `-logfile`, posts the key to the game window and
reads the result back from the log, where PCSX2 writes every on-screen message:

    OSD [UpscaleMultiplierChanged]: Moltiplicatore upscale aumentato a 3x. (1536 x 1344)

The text is in the user's language, so values are read by the message's internal key (`UpscaleMultiplierChanged`,
the same in every language) and from the parts of the text that do not change between languages (numbers, quoted
names, English enum names). The description of the controls lives in the emulator's settings file
(emu_settings/*.json, section "live").
"""
import re
import time
from pathlib import Path

import core
import overlay
import settings

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
SETTLE = 1.6  # how long to wait for the emulator's answer in its log
OSD_LINE = re.compile(r"OSD \[(?P<key>[^\]]+)\]:\s*(?P<text>.*)$")


def vk_for(name):
    """'F13' -> virtual-key code (F1 = 0x70 ... F24 = 0x87)."""
    m = re.fullmatch(r"F(\d{1,2})", name)
    if not m or not 1 <= int(m.group(1)) <= 24:
        raise core.ModHubError(f"Tasto non supportato: {name}")
    return 0x70 + int(m.group(1)) - 1


def binding_pairs(live_spec):
    return [(hotkey, f"Keyboard/{key}") for hotkey, key in live_spec["keys"].items()]


def prepare(ini_path, live_spec):
    """Before the emulator starts: add ModHub's key bindings to its settings. Returns True if the file changed."""
    return settings.ensure_lines(ini_path, live_spec.get("section", "Hotkeys"), binding_pairs(live_spec))


class Live:
    """Remote control of one running emulator."""

    def __init__(self, live_spec, pid, log_path, initial=None):
        self.spec, self.pid, self.log = live_spec, pid, Path(log_path)
        self.controls = {c["id"]: c for c in live_spec["controls"]}
        self.values = {}  # control id -> last known value
        self.messages = []  # recent emulator messages, newest last
        for control in live_spec["controls"]:
            raw = (initial or {}).get(control.get("key"), control.get("default"))
            if raw is not None:
                self.values[control["id"]] = raw

    # ---- reading what the emulator says

    def _size(self):
        try:
            return self.log.stat().st_size
        except OSError:
            return 0

    def _osd_since(self, offset):
        """[(key, text)] the emulator printed after `offset` bytes of its log."""
        try:
            with open(self.log, "rb") as f:
                f.seek(offset)
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        found = (OSD_LINE.search(line) for line in text.splitlines())
        return [(m["key"], m["text"].strip()) for m in found if m]

    def _parse(self, control, text):
        """The control's new value from one message (None if the message does not contain it)."""
        kind = control["type"]
        m = re.search(control["read"], text) if control.get("read") else None
        if kind in ("stepper",):
            return int(m.group(1)) if m else None
        if kind == "cycle" and "values" in control:
            return control["values"].index(m.group(1)) if m and m.group(1) in control["values"] else None
        if kind == "cycle":
            return m.group(1) if m else text  # no fixed list (aspect ratio...): show what the emulator said
        return None  # toggles are handled by the caller: their text is in the user's language

    # ---- sending keys

    def window(self):
        windows = overlay.windows_of_pid(self.pid)
        if not windows:
            raise core.ModHubError("Non trovo la finestra del gioco: è ancora in avvio?")
        return windows[0]

    def press(self, hotkey, expect_key=None):
        """Press an emulator hotkey. Returns [(key, text)] the emulator printed in answer (waits for `expect_key`)."""
        vk = vk_for(self.spec["keys"][hotkey])
        hwnd = self.window()
        offset = self._size()
        scan = overlay.user32.MapVirtualKeyW(vk, 0)
        overlay.user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 1 | (scan << 16))
        time.sleep(0.05)
        overlay.user32.PostMessageW(hwnd, WM_KEYUP, vk, 1 | (scan << 16) | (1 << 30) | (1 << 31))
        deadline = time.time() + SETTLE
        messages = []
        while time.time() < deadline:
            time.sleep(0.1)
            messages = self._osd_since(offset)
            if any(expect_key is None or key == expect_key for key, _ in messages):
                time.sleep(0.12)  # let a possible second line arrive
                messages = self._osd_since(offset)
                break
        self.messages = (self.messages + [text for _, text in messages])[-12:]
        return messages

    # ---- controls

    def do(self, control_id, action):
        """One step of a control: action is 'up', 'down' or 'press'. `answered` is True only if the emulator confirmed it."""
        control = self.controls.get(control_id)
        if not control:
            raise core.ModHubError("Controllo sconosciuto.")
        hotkey = control.get(action)
        if not hotkey:
            raise core.ModHubError("Azione non disponibile per questo controllo.")
        key = control.get("osd")
        messages = [(k, t) for k, t in self.press(hotkey, key) if key is None or k == key]
        text = messages[-1][1] if messages else ""
        value = None
        if messages:
            if control["type"] == "toggle":
                previous = self.values.get(control_id)
                value = (not previous) if isinstance(previous, bool) else None  # the wording is localized: flip what we knew
            else:
                value = self._parse(control, text)
                if value is None and "fallback" in control:
                    value = control["fallback"]  # e.g. PCSX2 says "set to native resolution" (no number) for 1x
            if value is not None:
                self.values[control_id] = value
        return {"control": control_id, "value": self.values.get(control_id), "message": text,
                "answered": bool(messages), "changed": value is not None}

    def set(self, control_id, target):
        """Bring a control to `target` with as few presses as possible, checking the emulator's answer after every one.

        It never guesses: with an unknown starting value, or an answer that does not move toward the target, it stops.
        """
        control = self.controls[control_id]
        result = {"control": control_id, "value": self.values.get(control_id), "message": "", "answered": False, "reached": False}
        current = self.values.get(control_id)
        if current == target:
            result["reached"] = True
            return result
        if not isinstance(current, int) or isinstance(current, bool):
            return result
        stepper = control["type"] == "stepper"
        # a stepper needs |difference| presses (+1 to notice a limit); a cycle goes round at most once, because it
        # always moves: if our idea of where it stood was wrong, the first answer corrects it
        budget = abs(target - current) + 1 if stepper else len(control["values"])
        for _ in range(budget):
            action = ("up" if self.values[control_id] < target else "down") if stepper else "press"
            before = self.values[control_id]
            result = {**self.do(control_id, action), "reached": False}
            now = self.values.get(control_id)
            if not result["changed"]:
                break  # no answer, or an answer we cannot read: do not keep pressing blindly
            if now == target:
                break
            if stepper and (now == before or (now > target) == (before < target)):
                break  # at the emulator's limit, or stepped over the target
        result["reached"] = self.values.get(control_id) == target
        return result

    def control_for_setting(self, key):
        """The control that can bring a setting to an exact value (counting presses), if there is one."""
        return next((c for c in self.spec["controls"] if c.get("key") == key and not c.get("noOptimizer")
                     and (c["type"] == "stepper" or (c["type"] == "cycle" and "values" in c))), None)

    def snapshot(self):
        out = []
        for control in self.spec["controls"]:
            value = self.values.get(control["id"])
            display = value
            if control["type"] == "cycle" and "labels" in control and isinstance(value, int) and 0 <= value < len(control["labels"]):
                display = control["labels"][value]
            elif control["type"] == "toggle" and isinstance(value, bool):
                display = "Sì" if value else "No"
            elif control["type"] == "stepper" and value is not None and control.get("unit"):
                display = f"{value}{control['unit']}"
            out.append({"id": control["id"], "label": control["label"], "type": control["type"], "value": value,
                        "display": display, "hint": control.get("hint", ""), "key": control.get("key"), "extra": bool(control.get("extra")),
                        "min": control.get("min"), "max": control.get("max")})
        return {"controls": out, "messages": self.messages[-3:]}
