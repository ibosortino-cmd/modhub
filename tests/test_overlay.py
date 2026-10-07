import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import overlay

WINDOWS = sys.platform == "win32"


@unittest.skipUnless(WINDOWS, "Windows only")
class HotkeyTests(unittest.TestCase):
    def test_hotkey_registers_fires_and_releases_the_key(self):
        pressed = threading.Event()
        hk = overlay.Hotkey(pressed.set)
        self.assertTrue(hk.start(), "Shift+Tab is already owned by another program")
        try:
            # simulate the key press by posting the same message Windows sends (no real keystroke)
            overlay.user32.PostThreadMessageW(hk._thread_id, overlay.WM_HOTKEY, 1, 0)
            self.assertTrue(pressed.wait(2))
            # while we hold it, nobody else can register the same combination
            other = overlay.Hotkey(lambda: None)
            self.assertFalse(other.start())
        finally:
            hk.stop()
        again = overlay.Hotkey(lambda: None)
        self.assertTrue(again.start(), "the key must be released after stop()")  # Shift+Tab works normally again
        again.stop()


@unittest.skipUnless(WINDOWS, "Windows only")
class WindowTests(unittest.TestCase):
    def test_find_raise_to_top_and_close_a_window(self):
        import tkinter as tk
        root = tk.Tk()
        root.title("ModHub Overlay Test 123")
        root.geometry("200x100+50+50")
        def cleanup():
            try:
                root.destroy()
            except tk.TclError:
                pass  # already closed by the test
        self.addCleanup(cleanup)
        for _ in range(10):
            root.update()
            time.sleep(0.02)
        hwnd = overlay.find_window("ModHub Overlay Test")
        self.assertTrue(hwnd)
        self.assertIsNone(overlay.find_window("Definitely not a window title"))
        self.assertFalse(overlay.is_topmost(hwnd))
        overlay.make_topmost(hwnd)
        for _ in range(5):
            root.update()
        self.assertTrue(overlay.is_topmost(hwnd))
        overlay.close_window(hwnd)
        for _ in range(20):
            try:
                root.update()
            except tk.TclError:
                break
            time.sleep(0.02)
        self.assertIsNone(overlay.find_window("ModHub Overlay Test"))


if __name__ == "__main__":
    unittest.main()
