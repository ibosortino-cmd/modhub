"""ModHub core: catalog access, install / update / uninstall. Standard library only."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

# Files of these types run code on the user's machine (the BT3 runner loads every mods/*.dll).
EXEC_EXT = {".dll", ".exe", ".so", ".cmd", ".bat", ".ps1", ".sh", ".py", ".lnk"}
BACKUP_SUFFIX = ".modhub-bak"
STAGING_DIR = ".modhub-staging"


class ModHubError(Exception):
    pass


def home_dir():
    override = os.environ.get("MODHUB_HOME")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA") or Path.home()) / "ModHub"


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def vkey(version):
    """Sortable key for versions like '1.10.0' (numeric parts only)."""
    return tuple(int(n) for n in re.findall(r"\d+", version or "")) or (0,)


def project_status(latest, record):
    if not record:
        return "not_installed"
    return "update" if vkey(latest) > vkey(record["version"]) else "installed"


# --------------------------------------------------------------------------- state

class State:
    """What the user has: catalog source, game folders, installed projects."""

    def __init__(self, home):
        self.path = Path(home) / "state.json"
        self.source = None
        self.game_paths = {}
        self.installed = {}
        self.custom_games = {}  # games added by hand: id -> {name, root, exe} or {name, root, emulator, rom}
        self.emulators = {}  # emulator id -> path of its program on this PC
        self.custom_emulators = {}  # emulators the user defined: id -> {name, args}
        self.emulator_installs = {}  # emulators ModHub downloaded: id -> {version, build, asset, sha256, verified, date}
        self.pending_settings = {}  # settings chosen while a game was running: target id -> {key: value}, applied on exit
        self.prefs = {}  # ModHub's own preferences, e.g. {"overlay_hotkey": "Ctrl+Alt+M"}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except (OSError, ValueError):
                data = {}
            self.source = data.get("source")
            self.game_paths = data.get("game_paths", {})
            self.installed = data.get("installed", {})
            self.custom_games = data.get("custom_games", {})
            self.emulators = data.get("emulators", {})
            self.custom_emulators = data.get("custom_emulators", {})
            self.emulator_installs = data.get("emulator_installs", {})
            self.pending_settings = data.get("pending_settings", {})
            self.prefs = data.get("prefs", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"source": self.source, "game_paths": self.game_paths, "installed": self.installed,
             "custom_games": self.custom_games, "emulators": self.emulators,
             "custom_emulators": self.custom_emulators, "emulator_installs": self.emulator_installs,
             "pending_settings": self.pending_settings, "prefs": self.prefs}, indent=2), "utf-8")
        os.replace(tmp, self.path)


# --------------------------------------------------------------------------- catalog source

class Source:
    """A catalog: an http(s) base URL or a local folder with the same layout."""

    def __init__(self, location):
        self.location = location.strip()
        self.remote = self.location.lower().startswith(("http://", "https://"))
        if self.remote and not self.location.endswith("/"):
            self.location += "/"

    def _open(self, rel):
        if rel.lower().startswith(("http://", "https://")):
            url = rel
        elif self.remote:
            url = urllib.parse.urljoin(self.location, rel)
        else:
            return open(Path(self.location) / rel, "rb")
        req = urllib.request.Request(url, headers={"User-Agent": "ModHub/0.1"})
        return urllib.request.urlopen(req, timeout=20)

    def json(self, rel):
        try:
            with self._open(rel) as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            raise ModHubError(f"Impossibile leggere {rel} da {self.location}: {e}") from e

    def fetch_blob(self, entry, dest, on_bytes):
        """Download one file entry to `dest`, verifying its SHA-256."""
        h = hashlib.sha256()
        try:
            with self._open(entry.get("url") or f"blobs/{entry['sha256']}") as src, open(dest, "wb") as out:
                while block := src.read(1 << 16):
                    out.write(block)
                    h.update(block)
                    on_bytes(len(block))
        except OSError as e:
            raise ModHubError(f"Download fallito per {entry['path']}: {e}") from e
        if h.hexdigest() != entry["sha256"]:
            dest.unlink(missing_ok=True)
            raise ModHubError(f"{entry['path']}: il file scaricato è corrotto o manomesso (hash diverso).")


def load_catalog(source):
    cat = source.json("catalog.json")
    cat.setdefault("games", {})
    cat.setdefault("projects", [])
    return cat


def load_manifest(source, entry):
    return source.json(entry["manifest"])


# --------------------------------------------------------------------------- games

def find_game(gdef, state, game_id, hints=()):
    """Return the install folder of a game, or None."""
    candidates = [state.game_paths.get(game_id), *hints]
    for c in candidates:
        if c and (Path(c) / gdef["exe"]).is_file():
            return Path(c)
    return None


def base_version(gdef, root):
    spec = gdef.get("version_file")
    if not spec or not root:
        return None
    try:
        text = (Path(root) / spec["file"]).read_text("utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(spec["regex"], text)
    return m.group(1) if m else None


def launch_command(exe, args, cwd):
    """Start a program with an argument list (no shell, so game names can't inject commands)."""
    return subprocess.Popen([str(exe), *args], cwd=str(cwd), close_fds=True)


def launch(root, exe, env_extra):
    env = os.environ.copy()
    env.update({k: str(v) for k, v in env_extra.items()})
    return subprocess.Popen([str(Path(root) / exe)], cwd=str(root), env=env, close_fds=True)


# --------------------------------------------------------------------------- install

def safe_dest(root, rel):
    """Map a manifest path inside `root`; refuse anything that could escape it."""
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if not rel or ":" in rel or rel.startswith(("/", "\\")) or ".." in parts or not parts:
        raise ModHubError(f"Percorso non valido nel pacchetto: {rel!r}")
    dest = Path(root).resolve().joinpath(*parts)
    if Path(root).resolve() not in dest.resolve().parents:
        raise ModHubError(f"Percorso fuori dalla cartella del gioco: {rel!r}")
    return dest


def new_executables(manifest, version, record):
    """Executable files this install would add or change (shown to the user before installing)."""
    old = {f["path"]: f["sha256"] for f in (record or {}).get("files", [])}
    return [f["path"] for f in manifest["versions"][version].get("files", [])
            if Path(f["path"]).suffix.lower() in EXEC_EXT and old.get(f["path"]) != f["sha256"]]


def _prune_empty(root, folder):
    root = Path(root).resolve()
    folder = Path(folder)
    while folder.resolve() != root and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def _remove(root, rel):
    dest = safe_dest(root, rel)
    if dest.is_file():
        dest.unlink()
    bak = dest.with_name(dest.name + BACKUP_SUFFIX)
    if bak.is_file():
        os.replace(bak, dest)
    _prune_empty(root, dest.parent)


def install(source, manifest, version, root, state, progress=None):
    """Install or update `manifest` to `version` inside game folder `root`.

    Only files whose hash differs from what is already on disk are downloaded. Everything is
    downloaded and verified into a staging folder first, so a failed download changes nothing.
    Returns how many files were downloaded.
    """
    pid = manifest["id"]
    if version not in manifest["versions"]:
        raise ModHubError(f"{manifest['name']}: la versione {version} non esiste.")
    ver = manifest["versions"][version]
    files = ver.get("files", [])
    root = Path(root)

    record = state.installed.get(pid)
    owned = {f["path"] for f in record["files"]} if record else set()
    owners = {f["path"]: other for other, rec in state.installed.items() if other != pid for f in rec["files"]}

    dests, todo = {}, []
    for f in files:
        if f["path"] in owners:
            raise ModHubError(f"{f['path']} è già installato da «{state.installed[owners[f['path']]]['name']}». "
                              "Disinstalla prima quel progetto.")
        dests[f["path"]] = dest = safe_dest(root, f["path"])
        if not (dest.is_file() and sha256_file(dest) == f["sha256"]):
            todo.append(f)

    total = sum(f["size"] for f in todo)
    done = 0

    def on_bytes(n):
        nonlocal done
        done += n
        if progress:
            progress(done, total)

    staging = root / STAGING_DIR
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for f in todo:
            tmp = staging / f["sha256"]
            if not tmp.exists():
                source.fetch_blob(f, tmp, on_bytes)

        placed = {}
        for f in todo:
            dest = dests[f["path"]]
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_file() and f["path"] not in owned:
                bak = dest.with_name(dest.name + BACKUP_SUFFIX)
                if not bak.exists():
                    os.replace(dest, bak)
            if f["sha256"] in placed:
                shutil.copyfile(placed[f["sha256"]], dest)
            else:
                os.replace(staging / f["sha256"], dest)
                placed[f["sha256"]] = dest

        for rel in owned - {f["path"] for f in files}:
            _remove(root, rel)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    state.installed[pid] = {
        "name": manifest["name"],
        "game": manifest.get("game"),
        "version": version,
        "files": [{"path": f["path"], "sha256": f["sha256"]} for f in files],
        "launch_profiles": ver.get("launch_profiles", []),
    }
    state.save()
    return len(todo)


def uninstall(pid, root, state):
    record = state.installed.get(pid)
    if not record:
        return
    for f in record["files"]:
        _remove(root, f["path"])
    del state.installed[pid]
    state.save()
