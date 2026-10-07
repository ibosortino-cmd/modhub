"""ModHub.exe's entry point: the app, or one of its helper processes (see selfrun.py).

    ModHub.exe                         the app (local server + window)
    ModHub.exe --overlay ARGS...       the in-game panel / reminder (overlay_ui.py)
    ModHub.exe --run-python OUT CODE   a small native dialog; what it prints goes to the file OUT
"""
import io
import sys


def main():
    args = sys.argv[1:]
    if args[:1] == ["--overlay"]:
        sys.argv = [sys.argv[0], *args[1:]]
        import overlay_ui
        return overlay_ui.main()
    if args[:1] == ["--run-python"] and len(args) >= 3:
        out, code = args[1], args[2]
        sys.argv = ["-c", *args[3:]]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exec(compile(code, "<modhub>", "exec"), {"__name__": "__main__"})
        finally:
            sys.stdout = sys.__stdout__
            with open(out, "w", encoding="utf-8") as f:
                f.write(buffer.getvalue())
        return None
    import server
    return server.main()


class _Discard(io.TextIOBase):
    """Where print() and logging go in the windowed exe, which has no console (they would fail on None)."""

    def write(self, text):
        return len(text)


if __name__ == "__main__":
    if sys.stdout is None:
        sys.stdout = _Discard()
    if sys.stderr is None:
        sys.stderr = _Discard()
    try:
        main()
    except Exception:  # the exe has no console: keep the reason where a user (and a bug report) can find it
        import traceback
        import core
        log = core.home_dir() / "error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), "utf-8")
        raise
