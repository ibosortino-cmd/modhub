import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import emuinstall
import emulators

TAR = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class FakeSite:
    """A tiny web server standing in for GitHub / a project's download page."""

    def __init__(self):
        self.routes = {}
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                route = site.routes.get(self.path)
                if route is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                if isinstance(route, tuple) and route[0] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", route[1])
                    self.end_headers()
                    return
                body = route if isinstance(route, bytes) else json.dumps(route).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def publish_github(self, assets, tag="v1.2.3", repo="o/r"):
        """assets: {name: bytes}; returns nothing, registers API + download routes."""
        listing = []
        for name, data in assets.items():
            self.routes[f"/dl/{name}"] = data
            listing.append({"name": name, "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                            "browser_download_url": f"{self.base}/dl/{name}", "updated_at": "2026-01-01T00:00:00Z"})
        self.routes[f"/repos/{repo}/releases/latest"] = {"tag_name": tag, "assets": listing}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["MODHUB_HOME"] = str(self.tmp / "home")
        emuinstall.ALLOW_LOCAL = True
        self.addCleanup(setattr, emuinstall, "ALLOW_LOCAL", False)
        self.site = FakeSite()
        self.addCleanup(self.site.close)
        self.state = core.State(self.tmp / "home")
        self.spec = {"type": "github", "repo": "o/r", "api": self.site.base, "asset": r"-win\.zip$",
                     "portable_marker": "portable.txt"}
        self.emu = {"id": "fake", "name": "Fake", "exe_names": ["Fake.exe"], "install": self.spec}

    def release(self, ver, files, name=None):
        name = name or f"fake-{ver}-win.zip"
        self.site.publish_github({f"fake-{ver}-linux.tar": b"nope", name: make_zip(files),
                                  f"fake-{ver}-win-symbols.7z": b"nope"}, tag=f"v{ver}")
        return emuinstall.resolve(self.spec)

    def test_resolve_picks_the_windows_asset_with_its_hash(self):
        rel = self.release("1.2.3", {"Fake-1.2.3/Fake.exe": b"exe"})
        self.assertEqual((rel["name"], rel["version"]), ("fake-1.2.3-win.zip", "1.2.3"))
        self.assertEqual(len(rel["sha256"]), 64)

    def test_install_flattens_registers_and_adds_portable_marker(self):
        rel = self.release("1.2.3", {"Fake-1.2.3/Fake.exe": b"exe1", "Fake-1.2.3/data/a.txt": b"a"})
        stages, pct = [], []
        emuinstall.install(self.emu, rel, self.state, lambda d, t: pct.append((d, t)), stages.append)
        exe = Path(self.state.emulators["fake"])
        self.assertEqual(exe, emuinstall.installs_dir() / "fake" / "Fake.exe")
        self.assertEqual((exe.read_bytes(), (exe.parent / "data/a.txt").read_bytes()), (b"exe1", b"a"))
        self.assertTrue((exe.parent / "portable.txt").exists())
        self.assertEqual(stages, ["Download", "Estrazione", "Installazione"])
        self.assertEqual(pct[-1][0], pct[-1][1])
        rec = core.State(self.tmp / "home").emulator_installs["fake"]
        self.assertEqual((rec["version"], rec["verified"]), ("1.2.3", True))  # persisted
        self.assertFalse(list(emuinstall.installs_dir().glob(".*tmp")))  # staging cleaned

    def test_portable_marker_is_empty_because_pcsx2_reads_its_text_as_a_folder_name(self):
        emuinstall.install(self.emu, self.release("1.2.3", {"Fake/Fake.exe": b"x"}), self.state)
        marker = Path(self.state.emulators["fake"]).parent / "portable.txt"
        self.assertEqual(marker.read_bytes(), b"")

    def test_old_modhub_marker_text_is_cleaned_up_but_users_own_marker_is_not(self):
        legacy = "Created by ModHub: keeps this emulator's settings and saves in this folder.\n"
        emuinstall.install(self.emu, self.release("1.2.3", {"Fake/Fake.exe": b"x"}), self.state)
        folder = Path(self.state.emulators["fake"]).parent
        marker = folder / "portable.txt"
        marker.write_text(legacy)
        emuinstall.fix_legacy_markers(self.state)  # what ModHub does at startup
        self.assertFalse(marker.exists())
        marker.write_text(legacy)
        emuinstall.install(self.emu, self.release("1.3.0", {"Fake/Fake.exe": b"y"}), self.state)  # and on update
        self.assertEqual(marker.read_bytes(), b"")  # old text gone, empty marker recreated from the spec
        marker.write_text("D:\\my\\own\\data")  # a user's own portable.txt must never be touched
        emuinstall.fix_legacy_markers(self.state)
        self.assertEqual(marker.read_text(), "D:\\my\\own\\data")

    def test_bundled_emulators_do_not_force_portable_mode(self):
        self.assertEqual([e["id"] for e in emulators.load() if "portable_marker" in e.get("install", {})], [])

    def test_update_keeps_user_files_and_replaces_the_program(self):
        emuinstall.install(self.emu, self.release("1.2.3", {"Fake-1.2.3/Fake.exe": b"old"}), self.state)
        folder = Path(self.state.emulators["fake"]).parent
        (folder / "User").mkdir()
        (folder / "User" / "save.sav").write_bytes(b"my progress")
        (folder / "portable.txt").write_text("mine")  # not ModHub's text, so an update must leave it alone
        first_build = self.state.emulator_installs["fake"]["build"]
        new = self.release("1.3.0", {"Fake-1.3.0/Fake.exe": b"new"})  # different top folder name
        self.assertNotEqual(new["build"], first_build)
        emuinstall.install(self.emu, new, self.state)
        self.assertEqual(Path(self.state.emulators["fake"]), folder / "Fake.exe")
        self.assertEqual(((folder / "Fake.exe").read_bytes(), (folder / "User/save.sav").read_bytes(), (folder / "portable.txt").read_text()),
                         (b"new", b"my progress", "mine"))
        self.assertEqual(self.state.emulator_installs["fake"]["version"], "1.3.0")

    def test_wrong_hash_installs_nothing(self):
        rel = self.release("1.2.3", {"Fake-1.2.3/Fake.exe": b"exe"})
        rel["sha256"] = "0" * 64
        with self.assertRaises(core.ModHubError):
            emuinstall.install(self.emu, rel, self.state)
        self.assertNotIn("fake", self.state.emulators)
        self.assertFalse((emuinstall.installs_dir() / "fake").exists())
        self.assertFalse(list(emuinstall.installs_dir().glob(".*tmp")))

    def test_archive_with_path_traversal_is_rejected(self):
        rel = self.release("1.2.3", {"Fake/Fake.exe": b"x", "../evil.txt": b"pwned"})
        with self.assertRaises(core.ModHubError):
            emuinstall.install(self.emu, rel, self.state)
        self.assertFalse((emuinstall.installs_dir() / "evil.txt").exists())
        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertNotIn("fake", self.state.emulators)

    def test_archive_without_the_program_is_rejected(self):
        rel = self.release("1.2.3", {"Fake/readme.txt": b"no exe here"})
        with self.assertRaises(core.ModHubError):
            emuinstall.install(self.emu, rel, self.state)
        self.assertFalse((emuinstall.installs_dir() / "fake").exists())

    @unittest.skipUnless(TAR.is_file(), "needs Windows tar")
    def test_7z_archives_are_unpacked(self):
        src = self.tmp / "pack" / "Fake-9"
        src.mkdir(parents=True)
        (src / "Fake.exe").write_bytes(b"from 7z")
        archive = self.tmp / "fake-9-win.7z"
        subprocess.run([str(TAR), "--format", "7zip", "-cf", str(archive), "-C", str(self.tmp / "pack"), "Fake-9"], check=True)
        self.spec["asset"] = r"-win\.7z$"
        self.site.publish_github({"fake-9-win.7z": archive.read_bytes()}, tag="v9.0")
        emuinstall.install(self.emu, emuinstall.resolve(self.spec), self.state)
        self.assertEqual(Path(self.state.emulators["fake"]).read_bytes(), b"from 7z")

    def test_uninstall_removes_only_modhubs_own_copy(self):
        emuinstall.install(self.emu, self.release("1.2.3", {"Fake/Fake.exe": b"x"}), self.state)
        other = self.tmp / "my own emulator"
        other.mkdir()
        self.state.emulators["external"] = str(other / "x.exe")
        emuinstall.uninstall("fake", self.state)
        self.assertFalse((emuinstall.installs_dir() / "fake").exists())
        self.assertNotIn("fake", self.state.emulators)
        self.assertTrue(other.exists())
        with self.assertRaises(core.ModHubError):  # not installed by ModHub -> refuses
            emuinstall.uninstall("external", self.state)

    def test_only_allowed_hosts_and_https_are_contacted(self):
        emuinstall.ALLOW_LOCAL = False
        for url in ("https://evil.example/x", "http://github.com/x", "ftp://github.com/x", "https://github.com.evil.example/x"):
            with self.assertRaises(core.ModHubError, msg=url):
                emuinstall._check(url, emuinstall.GITHUB_HOSTS)
        emuinstall._check("https://api.github.com/repos/a/b", emuinstall.GITHUB_HOSTS)

    def test_redirect_to_another_host_is_refused(self):
        emuinstall.ALLOW_LOCAL = True
        self.site.routes["/dl/x.zip"] = ("redirect", "https://evil.example/x.zip")
        with self.assertRaises(core.ModHubError):
            emuinstall.download({"url": f"{self.site.base}/dl/x.zip", "hosts": set()}, self.tmp / "x.zip")

    def test_json_and_page_sources(self):
        self.site.routes["/beta"] = {"shortrev": "2609", "artifacts": [
            {"system": "Linux", "url": "https://dl.example/linux.flatpak"},
            {"system": "Windows x64", "url": "https://dl.dolphin-emu.org/releases/2609/dolphin-2609-x64.7z"}]}
        rel = emuinstall.resolve({"type": "json", "url": f"{self.site.base}/beta", "system": "Windows x64", "hosts": []})
        self.assertEqual((rel["version"], rel["name"]), ("2609", "dolphin-2609-x64.7z"))
        bundled = {e["id"]: e["install"] for e in emulators.load() if "install" in e}
        self.site.routes["/dl"] = (b'<a href="/download/redream.x86_64-windows-v1.5.0-1240-gc41f7f2.zip">dev</a>'
                                   b'<a href="/download/redream.x86_64-windows-v1.5.0.zip">stable</a>')
        rel = emuinstall.resolve({**bundled["redream"], "page": f"{self.site.base}/dl"})
        self.assertEqual((rel["version"], rel["name"]), ("1.5.0", "redream.x86_64-windows-v1.5.0.zip"))
        self.assertTrue(rel["url"].startswith(self.site.base))
        self.site.routes["/pp"] = b'<a href="https://www.ppsspp.org/files/1_20_4/ppsspp_win.zip">ZIP</a>'
        rel = emuinstall.resolve({**bundled["ppsspp"], "page": f"{self.site.base}/pp"})
        self.assertEqual((rel["version"], rel["url"]), ("1.20.4", "https://www.ppsspp.org/files/1_20_4/ppsspp_win.zip"))


class BundledSourcesTests(unittest.TestCase):
    """The patterns in emulators.json must pick the right Windows file out of the real release listings."""

    LISTINGS = {
        "pcsx2": (["pcsx2-v2.8.2-linux-appimage-x64-Qt.AppImage", "pcsx2-v2.8.2-macos-Qt.tar.xz", "pcsx2-v2.8.2-windows-x64-installer.exe",
                   "pcsx2-v2.8.2-windows-x64-Qt-symbols.7z", "pcsx2-v2.8.2-windows-x64-Qt.7z"], "pcsx2-v2.8.2-windows-x64-Qt.7z"),
        "duckstation": (["DuckStation-x64.AppImage", "duckstation-windows-arm64-release.zip", "duckstation-windows-x64-installer.exe",
                         "duckstation-windows-x64-release-symbols.7z", "duckstation-windows-x64-release.zip",
                         "duckstation-windows-x64-sse2-release.zip"], "duckstation-windows-x64-release.zip"),
        "rpcs3": (["rpcs3-v0.0.43-20237-55a3aff3_win64_msvc.7z.sha256", "rpcs3-v0.0.43-20237-55a3aff3_win64_msvc.7z"],
                  "rpcs3-v0.0.43-20237-55a3aff3_win64_msvc.7z"),
        "cemu": (["cemu-2.6-macos-12-x64.dmg", "cemu-2.6-ubuntu-22.04-x64.zip", "cemu-2.6-windows-x64.zip"], "cemu-2.6-windows-x64.zip"),
        "melonds": (["melonDS-1.1-windows-aarch64.zip", "melonDS-1.1-windows-x86_64.zip", "melonDS-1.1-ubuntu-x86_64.zip"],
                    "melonDS-1.1-windows-x86_64.zip"),
        "mgba": (["mGBA-0.10.5-win32.7z", "mGBA-0.10.5-win64-installer.exe", "mGBA-0.10.5-win64.7z", "mGBA-0.10.5-wii.7z"],
                 "mGBA-0.10.5-win64.7z"),
        "snes9x": (["snes9x-1.63-libretro-x64.zip", "snes9x-1.63-win32.zip", "snes9x-1.63-win32-x64.zip"], "snes9x-1.63-win32-x64.zip"),
        "mesen": (["Mesen_2.1.1_Linux_x64.zip", "Mesen_2.1.1_macOS_x64_Intel.zip", "Mesen_2.1.1_Windows.zip"], "Mesen_2.1.1_Windows.zip"),
        "flycast": (["flycast-2.7.apk", "flycast-macOS-2.7.zip", "flycast-win64-2.7.zip"], "flycast-win64-2.7.zip"),
        "xenia": (["xenia_canary_linux.tar_.xz", "xenia_canary_windows_.zip"], "xenia_canary_windows_.zip"),
        "xemu": (["xemu-0.8.136-windows-x86_64.zip", "xemu-win-aarch64-release.zip", "xemu-win-x86_64-release.zip"], "xemu-win-x86_64-release.zip"),
        "stella": (["Stella-7.0-macos.dmg", "Stella-7.0c-windows.zip", "Stella-7.0c-x64.exe"], "Stella-7.0c-windows.zip"),
    }

    def test_patterns_select_exactly_the_right_file(self):
        bundled = {e["id"]: e["install"] for e in emulators.load() if "install" in e}
        for eid, (names, expected) in self.LISTINGS.items():
            pattern = re.compile(bundled[eid]["asset"])
            self.assertEqual([n for n in names if pattern.search(n)], [expected], eid)

    def test_every_install_source_is_well_formed_and_https(self):
        for e in emulators.load():
            spec = e.get("install")
            if not spec:
                continue
            self.assertIn(spec["type"], ("github", "json", "page"), e["id"])
            if spec["type"] == "github":
                self.assertRegex(spec["repo"], r"^[\w.-]+/[\w.-]+$")
                re.compile(spec["asset"])
            else:
                self.assertTrue((spec.get("url") or spec["page"]).startswith("https://"), e["id"])
                self.assertTrue(spec["hosts"], e["id"])
            self.assertTrue(set(e["exe_names"]), e["id"])


if __name__ == "__main__":
    unittest.main()
