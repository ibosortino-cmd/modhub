"""Download, verify and unpack emulators from their official release pages into ModHub's own folder.

Only addresses on an allow-list are ever contacted (the project's own release host), every redirect is
checked too, files are verified with the SHA-256 the project publishes when there is one, and archives are
unpacked into a staging folder with path and symlink checks before anything is moved into place.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import core
import emulators

GITHUB_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
ALLOW_LOCAL = False  # tests only: also allow http://127.0.0.1
MAX_UNPACKED = 6 * 1024 ** 3
USER_AGENT = "ModHub/0.1 (+emulator installer)"


# --------------------------------------------------------------------------- network

def _hosts(spec):
    return set(spec.get("hosts", [])) | (GITHUB_HOSTS if spec["type"] == "github" else set())


def _check(url, hosts):
    parts = urllib.parse.urlparse(url)
    local = ALLOW_LOCAL and parts.hostname in ("127.0.0.1", "localhost")
    if not local and (parts.scheme != "https" or parts.hostname not in hosts):
        raise core.ModHubError(f"Indirizzo non consentito: {parts.hostname or url}")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, hosts):
    _check(url, hosts)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json, */*"})
    try:
        return urllib.request.build_opener(_SafeRedirect(hosts)).open(req, timeout=30)
    except urllib.error.HTTPError as e:
        raise core.ModHubError(f"Il sito ha risposto con errore {e.code} ({urllib.parse.urlparse(url).hostname}).") from e
    except (urllib.error.URLError, OSError) as e:
        raise core.ModHubError(f"Impossibile contattare {urllib.parse.urlparse(url).hostname}: {getattr(e, 'reason', e)}") from e


def _get_text(url, hosts):
    with _open(url, hosts) as r:
        return r.read(5_000_000).decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- what is the latest version?

def _version_label(tag, asset_name, stamp):
    for text in (tag, asset_name):
        m = re.search(r"\d+(?:\.\d+)+", text or "")
        if m:
            return m.group(0)
    return (stamp or "")[:10] or "ultima"


def resolve(spec):
    """Find the newest Windows download. Returns name, version, url, size, sha256 (or None), build, hosts."""
    kind, hosts = spec["type"], _hosts(spec)
    if kind == "github":
        api = spec.get("api", "https://api.github.com")
        data = json.loads(_get_text(f"{api}/repos/{spec['repo']}/releases/latest", hosts))
        pattern = re.compile(spec["asset"])
        assets = [a for a in data.get("assets", []) if pattern.search(a["name"])]
        if not assets:
            raise core.ModHubError("Nell'ultima versione non c'è un file per Windows riconoscibile.")
        asset = assets[0]
        digest = (asset.get("digest") or "").lower()
        sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
        if not sha:
            sidecar = next((x for x in data["assets"] if x["name"] == asset["name"] + ".sha256"), None)
            if sidecar:
                found = re.search(r"\b[0-9a-fA-F]{64}\b", _get_text(sidecar["browser_download_url"], hosts))
                sha = found.group(0).lower() if found else None
        stamp = asset.get("updated_at") or data.get("published_at")
        return {"name": asset["name"], "version": _version_label(data.get("tag_name"), asset["name"], stamp),
                "url": asset["browser_download_url"], "size": asset.get("size"), "sha256": sha,
                "build": f"{asset['name']}|{stamp}|{sha or ''}", "hosts": hosts}
    if kind == "json":
        data = json.loads(_get_text(spec["url"], hosts))
        art = next((a for a in data.get("artifacts", []) if a.get("system") == spec["system"]), None)
        if not art:
            raise core.ModHubError("Nel sito ufficiale non trovo la versione per Windows.")
        return {"name": art["url"].rsplit("/", 1)[-1], "version": str(data.get("shortrev", "")), "url": art["url"],
                "size": None, "sha256": None, "build": art["url"], "hosts": hosts}
    if kind == "page":
        text = _get_text(spec["page"], hosts)
        m = re.search(spec["pattern"], text)
        if not m:
            raise core.ModHubError("Nel sito ufficiale non trovo il link di download per Windows (la pagina è cambiata?).")
        url = urllib.parse.urljoin(spec["page"], m.group("url"))
        version = (m.groupdict().get("version") or "").replace("_", ".")
        return {"name": url.rsplit("/", 1)[-1], "version": version or "ultima", "url": url, "size": None,
                "sha256": None, "build": url, "hosts": hosts}
    raise core.ModHubError(f"Tipo di download sconosciuto: {kind}")


# --------------------------------------------------------------------------- download and unpack

def download(release, dest, progress=None):
    """Stream the file to `dest`, checking size and SHA-256 when the project publishes them."""
    h, done = hashlib.sha256(), 0
    with _open(release["url"], release["hosts"]) as r, open(dest, "wb") as out:
        total = int(r.headers.get("Content-Length") or release.get("size") or 0)
        while block := r.read(1 << 16):
            out.write(block)
            h.update(block)
            done += len(block)
            if progress:
                progress(done, total)
    if release.get("size") and done != release["size"]:
        raise core.ModHubError("Download incompleto: la dimensione non corrisponde.")
    if release.get("sha256") and h.hexdigest() != release["sha256"]:
        raise core.ModHubError("Il file scaricato non corrisponde all'hash SHA-256 pubblicato dal progetto: scarto tutto.")
    return h.hexdigest()


def _safe_member(name):
    parts = Path(name.replace("\\", "/")).parts
    return bool(name) and ":" not in name and not name.startswith(("/", "\\")) and ".." not in parts


def _unzip(archive, dest):
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise core.ModHubError("L'archivio è troppo grande una volta scompattato.")
        for i in infos:
            if not _safe_member(i.filename) or ((i.external_attr >> 16) & 0o170000) == 0o120000:
                raise core.ModHubError(f"Archivio rifiutato: contiene un percorso non sicuro ({i.filename!r}).")
        z.extractall(dest)


def _un7z(archive, dest):
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"  # bsdtar reads .7z (Windows 10/11)
    if not tar.is_file():
        raise core.ModHubError("Per aprire i file .7z serve il programma tar di Windows, che non trovo su questo PC.")
    proc = subprocess.run([str(tar), "-xf", str(archive), "-C", str(dest)], capture_output=True, text=True, timeout=900,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode:
        raise core.ModHubError(f"Impossibile aprire l'archivio: {proc.stderr.strip()[:200]}")


def extract(archive, dest):
    dest.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        _unzip(archive, dest)
    elif suffix == ".7z":
        _un7z(archive, dest)
    else:
        raise core.ModHubError(f"Formato di archivio non supportato: {suffix}")
    root = dest.resolve()
    for p in dest.rglob("*"):  # whatever the extractor did, nothing may point or lead outside
        if p.is_symlink() or root not in p.resolve().parents:
            raise core.ModHubError(f"Archivio rifiutato: «{p.name}» punta fuori dalla cartella di installazione.")


def _flatten(folder):
    """If an archive wraps everything in one folder (Dolphin-x64/...), step into it."""
    while True:
        entries = list(folder.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            folder = entries[0]
        else:
            return folder


# --------------------------------------------------------------------------- install / update / remove

def installs_dir():
    return core.home_dir() / "emulators"


_LEGACY_MARKER_TEXT = "Created by ModHub"


def _remove_legacy_marker(folder):
    """An earlier version wrote a sentence into portable.txt; PCSX2 takes that text as a folder name and fails."""
    marker = Path(folder) / "portable.txt"
    try:
        if marker.is_file() and marker.read_text("utf-8", errors="replace").startswith(_LEGACY_MARKER_TEXT):
            marker.unlink()
            return True
    except OSError:
        pass
    return False


def fix_legacy_markers(state):
    for eid in state.emulator_installs:
        exe = state.emulators.get(eid)
        for folder in {installs_dir() / eid, Path(exe).parent if exe else installs_dir() / eid}:
            _remove_legacy_marker(folder)


def install(emu, release, state, progress=None, stage=None):
    """Download `release` of `emu`, unpack it and register its program. Updates keep the user's own files."""
    stage = stage or (lambda _name: None)
    eid, spec = emu["id"], emu["install"]
    suffix = Path(release["name"]).suffix.lower()
    if suffix not in (".zip", ".7z"):
        raise core.ModHubError(f"Formato di archivio non supportato: {suffix or release['name']}")
    base = installs_dir()
    target, work = base / eid, base / f".{eid}.tmp"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    first_install = not target.exists()
    try:
        stage("Download")
        archive = work / f"download{suffix}"
        digest = download(release, archive, progress)
        stage("Estrazione")
        unpacked = work / "files"
        extract(archive, unpacked)
        src = _flatten(unpacked)
        exe = emulators.find_exe(src, {n.lower() for n in emu["exe_names"]}, 3)
        if not exe:
            raise core.ModHubError(f"Non trovo {emu['exe_names'][0]} nell'archivio scaricato.")
        rel_exe = exe.relative_to(src)
        stage("Installazione")
        if first_install:
            os.replace(src, target)
        else:
            try:
                shutil.copytree(src, target, dirs_exist_ok=True)  # new files win, saves and settings stay
            except PermissionError as e:
                raise core.ModHubError(f"{emu['name']} è aperto o un file è bloccato: chiudilo e riprova.") from e
        final_exe = target / rel_exe
        _remove_legacy_marker(final_exe.parent)
        marker = spec.get("portable_marker")  # must stay EMPTY: PCSX2 reads its text as a folder name
        if marker and not (final_exe.parent / marker).exists():
            (final_exe.parent / marker).write_bytes(b"")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    state.emulators[eid] = str(final_exe)
    state.emulator_installs[eid] = {"version": release["version"], "build": release["build"], "asset": release["name"],
                                    "sha256": digest, "verified": bool(release.get("sha256")),
                                    "date": datetime.date.today().isoformat()}
    state.save()
    return release["version"]


def uninstall(eid, state):
    """Delete an emulator ModHub installed. Only ever touches ModHub's own emulators folder."""
    if eid not in state.emulator_installs:
        raise core.ModHubError("Questo emulatore non è stato installato da ModHub.")
    base = installs_dir().resolve()
    target = (base / eid).resolve()
    if target.parent != base:
        raise core.ModHubError("Percorso non valido.")
    shutil.rmtree(target, ignore_errors=True)
    state.emulators.pop(eid, None)
    state.emulator_installs.pop(eid, None)
    state.save()
