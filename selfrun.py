"""How ModHub starts its other parts (the in-game panel, the reminder, a file dialog) as separate processes.

Run from the sources they are Python scripts; in the single ModHub.exe there is no Python next to it, so the exe
starts itself again with a mode switch (see modhub_main.py).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _python(windowless=True):
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe" if windowless else "python.exe")
    return str(candidate if candidate.is_file() else exe)


def panel_command(*args):
    """The in-game panel / start-of-game reminder (overlay_ui.py) with these arguments."""
    if FROZEN:
        return [sys.executable, "--overlay", *args]
    return [_python(), str(APP_DIR / "overlay_ui.py"), *args]


def run_snippet(code, *args):
    """Run a few lines of Python in a separate process (a native dialog) and return what they print."""
    if not FROZEN:
        proc = subprocess.run([_python(windowless=False), "-c", code, *args], capture_output=True, text=True, creationflags=NO_WINDOW)
        return proc.stdout.strip()
    fd, out = tempfile.mkstemp(prefix="modhub-", suffix=".txt")
    os.close(fd)
    try:  # a windowed exe has no stdout: the snippet's output goes to a file instead
        subprocess.run([sys.executable, "--run-python", out, code, *args], creationflags=NO_WINDOW)
        return Path(out).read_text("utf-8").strip()
    finally:
        Path(out).unlink(missing_ok=True)
