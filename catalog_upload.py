"""Upload a ModHub catalog folder to a GitHub repository with the GitHub CLI (no git needed).

    python catalog_upload.py OWNER/REPO [--catalog public-catalog] [--create] [--dry-run]

Only files that are new or changed are sent (GitHub keeps each file's hash). --create makes the repository
(public) first. You must be logged in with `gh auth login`; this script never handles passwords or tokens.
Users then point ModHub at https://raw.githubusercontent.com/OWNER/REPO/main/
"""
import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def gh(*args, data=None, ok_missing=False):
    proc = subprocess.run(["gh", *args], input=data, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        if ok_missing and ("Not Found" in proc.stderr or "404" in proc.stderr):
            return None
        sys.exit(f"gh {' '.join(args[:3])}...: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def git_blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def remote_files(repo, branch):
    """{path: git blob sha} of what is already in the repository (empty for a new one)."""
    out = gh("api", f"repos/{repo}/git/trees/{branch}?recursive=1", ok_missing=True)
    if not out:
        return {}
    return {e["path"]: e["sha"] for e in json.loads(out).get("tree", []) if e["type"] == "blob"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", help="OWNER/REPO on GitHub")
    ap.add_argument("--catalog", default="public-catalog")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--create", action="store_true", help="create the (public) repository first")
    ap.add_argument("--dry-run", action="store_true", help="only list what would be sent")
    ap.add_argument("--plain", action="store_true", help="any folder (for example ModHub's own sources), not a catalog")
    ap.add_argument("--description", default="Catalogo pubblico di ModHub (mod e profili, nessun gioco)")
    args = ap.parse_args()
    root = Path(args.catalog)
    if not args.plain and not (root / "catalog.json").is_file():
        sys.exit(f"{root / 'catalog.json'} non trovato.")
    if args.create and not args.dry_run:
        gh("repo", "create", args.repo, "--public", "--description", args.description)
    existing = remote_files(args.repo, args.branch) if not args.create else {}
    skip = {"__pycache__", "build", "dist", ".git"}
    files = sorted(p for p in root.rglob("*") if p.is_file() and not skip & set(p.relative_to(root).parts) and p.suffix != ".pyc")
    changed = [p for p in files if existing.get(p.relative_to(root).as_posix()) != git_blob_sha(p.read_bytes())]
    for p in changed:
        rel = p.relative_to(root).as_posix()
        print(("(prova) " if args.dry_run else "") + f"invio {rel}")
        if args.dry_run:
            continue
        body = {"message": f"ModHub: {rel}", "content": base64.b64encode(p.read_bytes()).decode(), "branch": args.branch}
        if rel in existing:
            body["sha"] = existing[rel]
        gh("api", "-X", "PUT", f"repos/{args.repo}/contents/{rel}", "--input", "-", data=json.dumps(body))
    print(f"{len(changed)} file inviati, {len(files) - len(changed)} già aggiornati.")
    print(f"Indirizzo per ModHub: https://raw.githubusercontent.com/{args.repo}/{args.branch}/")


if __name__ == "__main__":
    main()
