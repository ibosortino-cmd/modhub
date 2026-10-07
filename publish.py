"""Creator tool: publish a mod folder into a ModHub catalog.

A mod folder looks like:

    my-mod/
        modhub.json          id, name, author, game, version, summary, description, changelog,
                             optional launch_profiles
        files/               copied into the game folder, keeping the same relative paths
            mods/my_mod.dll

Usage:  python publish.py my-mod --catalog catalog
Bump "version" in modhub.json for every release; users then see "Aggiorna".
"""
import argparse
import datetime
import json
import shutil
import sys
from pathlib import Path

from core import ModHubError, sha256_file, vkey

REQUIRED = ("id", "name", "author", "game", "version")


def _read_json(path, default):
    return json.loads(path.read_text("utf-8")) if path.is_file() else default


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")


def publish(src, catalog):
    src, catalog = Path(src), Path(catalog)
    meta_path = src / "modhub.json"
    if not meta_path.is_file():
        raise ModHubError(f"{meta_path} non trovato.")
    meta = json.loads(meta_path.read_text("utf-8-sig"))  # Windows editors often add a BOM
    missing = [k for k in REQUIRED if not meta.get(k)]
    if missing:
        raise ModHubError(f"modhub.json: mancano i campi {', '.join(missing)}.")

    files = []
    files_dir = src / "files"
    for p in sorted(files_dir.rglob("*")) if files_dir.is_dir() else []:
        if not p.is_file():
            continue
        sha = sha256_file(p)
        blob = catalog / "blobs" / sha
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, blob)
        files.append({"path": p.relative_to(files_dir).as_posix(), "sha256": sha, "size": p.stat().st_size})

    pid, version = meta["id"], meta["version"]
    manifest_rel = f"projects/{pid}/manifest.json"
    manifest = _read_json(catalog / manifest_rel, {"versions": {}})
    existing = manifest["versions"].get(version)
    if existing and existing["files"] != files:
        raise ModHubError(f"La versione {version} di {pid} è già pubblicata con file diversi: "
                          "aumenta \"version\" in modhub.json.")

    manifest.update(id=pid, name=meta["name"], author=meta["author"], game=meta["game"],
                    summary=meta.get("summary", ""), description=meta.get("description", ""))
    manifest["versions"][version] = {
        "date": existing["date"] if existing else datetime.date.today().isoformat(),
        "changelog": meta.get("changelog", ""),
        "files": files,
        "launch_profiles": meta.get("launch_profiles", []),
    }
    manifest["latest"] = max(manifest["versions"], key=vkey)
    _write_json(catalog / manifest_rel, manifest)

    index = _read_json(catalog / "catalog.json", {"name": "ModHub catalog", "games": {}, "projects": []})
    entry = {"id": pid, "name": manifest["name"], "author": manifest["author"], "game": manifest["game"],
             "summary": manifest["summary"], "latest": manifest["latest"], "manifest": manifest_rel}
    index["projects"] = [e for e in index["projects"] if e["id"] != pid] + [entry]
    index["projects"].sort(key=lambda e: e["name"].lower())
    _write_json(catalog / "catalog.json", index)
    return pid, version, len(files)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="mod folder containing modhub.json and files/")
    ap.add_argument("--catalog", default="catalog", help="catalog folder to publish into (default: catalog)")
    args = ap.parse_args()
    try:
        pid, version, n = publish(args.folder, args.catalog)
    except ModHubError as e:
        sys.exit(f"Errore: {e}")
    print(f"Pubblicato {pid} {version} ({n} file) in {args.catalog}")


if __name__ == "__main__":
    main()
