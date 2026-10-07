"""Bundled emulator list (emulators.json) and helpers to find an emulator on this PC."""
import json
import os
import shlex
import string
from pathlib import Path

import core

APP_DIR = Path(__file__).resolve().parent
FULLSCREEN_PROFILE = "Schermo intero"


def load():
    return json.loads((APP_DIR / "emulators.json").read_text("utf-8-sig"))["emulators"]


def _bases():
    env = os.environ.get
    home = Path(env("USERPROFILE") or Path.home())
    candidates = [env("ProgramFiles"), env("ProgramFiles(x86)"), env("LOCALAPPDATA") and Path(env("LOCALAPPDATA")) / "Programs",
                  env("LOCALAPPDATA"), env("APPDATA"), home / "Downloads", home / "Desktop", home / "Documents", home]
    candidates += [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    seen = []
    for c in filter(None, candidates):
        p = Path(c)
        if p.is_dir() and p not in seen:
            seen.append(p)
    return seen


def _find(folder, names, depth):
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    for e in entries:
        if e.name.lower() in names and e.is_file():
            return e
    if depth > 0:
        for e in entries:
            if e.is_dir():
                hit = _find(e, names, depth - 1)
                if hit:
                    return hit
    return None


find_exe = _find


def detect(emu, bases=None):
    """Look in the usual places for a folder named after the emulator that contains its program."""
    key = emu.get("keyword", emu["id"]).lower()
    names = {n.lower() for n in emu.get("exe_names", [])}
    if not names:
        return None
    for base in bases or _bases():
        try:
            children = list(base.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and key in child.name.lower():
                hit = _find(child, names, 3)
                if hit:
                    return hit
    return None


def build_args(args, rom):
    return [a.replace("{rom}", str(rom)) for a in args]


def parse_args_template(text):
    """Turn 'user text with {rom}' into an argument list, e.g. '-f {rom}' -> ['-f', '{rom}']."""
    try:
        parts = shlex.split((text or "").strip() or "{rom}")
    except ValueError as e:
        raise core.ModHubError(f"Argomenti non validi: {e}") from e
    if "{rom}" not in parts:
        raise core.ModHubError("Negli argomenti scrivi {rom} dove va il percorso del gioco, ad esempio: -f {rom}")
    if len(parts) > 20 or any(len(p) > 200 for p in parts):
        raise core.ModHubError("Troppi argomenti o troppo lunghi.")
    return parts


def dialog_filters(emu):
    exts = " ".join(f"*.{e}" for e in emu.get("extensions", []))
    return exts
