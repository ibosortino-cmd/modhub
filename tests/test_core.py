import functools
import http.server
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import publish


def make_mod(root, version, files, **extra):
    root.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(root / "files", ignore_errors=True)
    for rel, text in files.items():
        p = root / "files" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, "utf-8")
    meta = {"id": "m", "name": "Mod", "author": "me", "game": "g", "version": version,
            "launch_profiles": [{"name": "P", "env": {"A": "1"}}], **extra}
    (root / "modhub.json").write_text(json.dumps(meta), "utf-8")


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.catalog, self.game, self.src = self.tmp / "cat", self.tmp / "game", self.tmp / "src"
        self.game.mkdir()
        self.state = core.State(self.tmp / "home")
        self.source = core.Source(str(self.catalog))

    def release(self, version, files, **extra):
        make_mod(self.src, version, files, **extra)
        publish.publish(self.src, self.catalog)

    def manifest(self):
        return core.load_manifest(self.source, {"manifest": "projects/m/manifest.json"})

    def test_update_downloads_only_changed_files_and_removes_stale(self):
        self.release("1.0.0", {"mods/a.txt": "a1", "mods/keep.txt": "keep", "mods/old.txt": "old"})
        self.release("1.1.0", {"mods/a.txt": "a2", "mods/keep.txt": "keep", "mods/new.txt": "new"})
        m = self.manifest()
        self.assertEqual(core.install(self.source, m, "1.0.0", self.game, self.state), 3)
        self.assertEqual((self.game / "mods/a.txt").read_text(), "a1")
        self.assertEqual(core.install(self.source, m, "1.1.0", self.game, self.state), 2)  # a.txt + new.txt
        self.assertEqual((self.game / "mods/a.txt").read_text(), "a2")
        self.assertTrue((self.game / "mods/new.txt").exists())
        self.assertFalse((self.game / "mods/old.txt").exists())
        self.assertEqual(self.state.installed["m"]["version"], "1.1.0")
        self.assertEqual(core.install(self.source, m, "1.1.0", self.game, self.state), 0)  # up to date
        self.assertFalse((self.game / core.STAGING_DIR).exists())
        # state survives a restart
        self.assertEqual(core.State(self.tmp / "home").installed["m"]["version"], "1.1.0")

    def test_rollback_to_older_version(self):
        self.release("1.0.0", {"mods/a.txt": "a1"})
        self.release("1.1.0", {"mods/a.txt": "a2"})
        m = self.manifest()
        core.install(self.source, m, "1.1.0", self.game, self.state)
        core.install(self.source, m, "1.0.0", self.game, self.state)
        self.assertEqual((self.game / "mods/a.txt").read_text(), "a1")

    def test_tampered_blob_changes_nothing(self):
        self.release("1.0.0", {"mods/a.txt": "good", "mods/b.txt": "also good"})
        m = self.manifest()
        victim = m["versions"]["1.0.0"]["files"][1]["sha256"]
        (self.catalog / "blobs" / victim).write_text("evil", "utf-8")
        with self.assertRaises(core.ModHubError):
            core.install(self.source, m, "1.0.0", self.game, self.state)
        self.assertFalse((self.game / "mods").exists())
        self.assertNotIn("m", self.state.installed)
        self.assertFalse((self.game / core.STAGING_DIR).exists())

    def test_path_traversal_is_rejected(self):
        for bad in ("../evil.txt", "/abs.txt", "C:/win.txt", "mods/../../x.txt"):
            with self.assertRaises(core.ModHubError, msg=bad):
                core.safe_dest(self.game, bad)

    def test_uninstall_restores_original_file(self):
        (self.game / "mods").mkdir()
        (self.game / "mods/a.txt").write_text("original", "utf-8")
        self.release("1.0.0", {"mods/a.txt": "modded", "deep/dir/b.txt": "b"})
        m = self.manifest()
        core.install(self.source, m, "1.0.0", self.game, self.state)
        self.assertEqual((self.game / "mods/a.txt").read_text(), "modded")
        core.uninstall("m", self.game, self.state)
        self.assertEqual((self.game / "mods/a.txt").read_text(), "original")
        self.assertFalse((self.game / "deep").exists())
        self.assertNotIn("m", self.state.installed)

    def test_two_projects_cannot_claim_same_file(self):
        self.release("1.0.0", {"mods/a.txt": "a"})
        core.install(self.source, self.manifest(), "1.0.0", self.game, self.state)
        other = dict(self.manifest(), id="other", name="Other")
        with self.assertRaises(core.ModHubError):
            core.install(self.source, other, "1.0.0", self.game, self.state)

    def test_executable_warning_and_profiles(self):
        self.release("1.0.0", {"mods/x.dll": "bin", "mods/n.txt": "t"})
        m = self.manifest()
        self.assertEqual(core.new_executables(m, "1.0.0", None), ["mods/x.dll"])
        core.install(self.source, m, "1.0.0", self.game, self.state)
        self.assertEqual(core.new_executables(m, "1.0.0", self.state.installed["m"]), [])
        self.assertEqual(self.state.installed["m"]["launch_profiles"][0]["env"], {"A": "1"})

    def test_republish_same_version_with_other_files_is_refused(self):
        self.release("1.0.0", {"a.txt": "1"})
        with self.assertRaises(core.ModHubError):
            self.release("1.0.0", {"a.txt": "2"})

    def test_version_ordering(self):
        self.assertTrue(core.vkey("1.10.0") > core.vkey("1.9.0"))
        self.assertEqual(core.project_status("1.1.0", {"version": "1.0.0"}), "update")
        self.assertEqual(core.project_status("1.1.0", {"version": "1.1.0"}), "installed")
        self.assertEqual(core.project_status("1.1.0", None), "not_installed")

    def test_install_over_http(self):
        self.release("1.0.0", {"mods/a.txt": "over http"})
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(self.catalog))
        handler.log_message = lambda *a, **k: None
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        source = core.Source(f"http://127.0.0.1:{server.server_port}")
        cat = core.load_catalog(source)
        m = core.load_manifest(source, cat["projects"][0])
        core.install(source, m, "1.0.0", self.game, self.state)
        self.assertEqual((self.game / "mods/a.txt").read_text(), "over http")


if __name__ == "__main__":
    unittest.main()
