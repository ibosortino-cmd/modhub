"""Read and edit a game's or emulator's settings file (TOML or INI) in place.

Only the values the user changes are rewritten; comments, key order and keys ModHub does not
know about are left exactly as the program wrote them. Which options exist (labels, types, ranges)
comes from a schema file, so any game or emulator can describe its own settings.
"""
import configparser
import json
import math
import os
import re
import shutil
import tomllib
from pathlib import Path

import core

BACKUP_SUFFIX = ".modhub-bak"
_TABLE = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")
_ASSIGN = re.compile(r"^(\s*)([A-Za-z0-9_\-]+)(\s*=\s*)(.*)$")


def flatten(data, prefix=""):
    out = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out.update(flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def _ini_value(raw):
    """INI has no types: read true/false, whole numbers and decimals as such, everything else as text."""
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    return raw


def parse(text, fmt="toml"):
    """Flat {'section.key': value} view of a settings file."""
    if fmt == "ini":
        parser = configparser.RawConfigParser(delimiters=("=",), strict=False, empty_lines_in_values=False,
                                              default_section="\0none")
        parser.optionxform = str  # keys are case-sensitive
        parser.read_string(text.lstrip("﻿"))
        return {f"{section}.{key}": _ini_value(value.strip()) for section in parser.sections()
                for key, value in parser.items(section)}
    return flatten(tomllib.loads(text))


def format_value(value, fmt="toml"):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".6g")  # same style the game writes: 0.0360465, 1
    return str(value) if fmt == "ini" else json.dumps(value, ensure_ascii=False)


def _split_value(rest, fmt="toml"):
    """Split 'value   # comment' into ('value', '   # comment')."""
    if fmt == "ini":  # no quoting and no trailing comments in INI: the value is the rest of the line
        stripped = rest.rstrip()
        return stripped, rest[len(stripped):]
    if rest.startswith('"'):
        i = 1
        while i < len(rest):
            if rest[i] == "\\":
                i += 2
                continue
            if rest[i] == '"':
                break
            i += 1
        return rest[:i + 1], rest[i + 1:]
    m = re.search(r"\s+#", rest)
    if m:
        return rest[:m.start()], rest[m.start():]
    stripped = rest.rstrip()
    return stripped, rest[len(stripped):]


def apply_changes(text, changes, fmt="toml"):
    """Return `text` with the dotted keys in `changes` set; everything else untouched."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    table, done = "", set()
    last_in_table, header_line = {}, {}
    for i, line in enumerate(lines):
        m = _TABLE.match(line)
        if m:
            table = m.group(1).strip()
            header_line[table] = i
            continue
        m = _ASSIGN.match(line)
        if not m:
            continue
        indent, key, eq, rest = m.groups()
        last_in_table[table] = i
        full = f"{table}.{key}" if table else key
        if full in changes:
            value, tail = _split_value(rest, fmt)
            lines[i] = f"{indent}{key}{eq}{format_value(changes[full], fmt)}{tail}"
            done.add(full)

    inserts, appended = {}, {}
    for full, value in changes.items():
        if full in done:
            continue
        tbl, _, key = full.rpartition(".")
        entry = f"{key} = {format_value(value, fmt)}"
        anchor = last_in_table.get(tbl, header_line.get(tbl))
        if anchor is not None:
            inserts.setdefault(anchor, []).append(entry)
        else:
            appended.setdefault(tbl, []).append(entry)

    out = []
    for i, line in enumerate(lines):
        out.append(line)
        out.extend(inserts.get(i, []))
    for tbl, entries in appended.items():
        out += ["", f"[{tbl}]"] if tbl else [""]
        out += entries
    result = newline.join(out)
    return result + newline if text.endswith("\n") or not text else result


def _fields(schema):
    result = {}
    for group in schema.get("groups", []):
        for section in group.get("sections", []):
            for field in section.get("fields", []):
                if field.get("type") != "info":
                    result[field["key"]] = field
    return result


def _coerce(field, raw, current):
    kind = field["type"]
    key = field["key"]
    if kind == "toggle":
        if not isinstance(raw, bool):
            raise core.ModHubError(f"{key}: atteso vero/falso.")
        return raw
    if kind in ("slider", "number"):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or abs(raw) > 1e9:
            raise core.ModHubError(f"{key}: numero non valido.")
        if "min" in field and raw < field["min"]:
            raise core.ModHubError(f"{key}: il valore minimo è {field['min']}.")
        if "max" in field and raw > field["max"]:
            raise core.ModHubError(f"{key}: il valore massimo è {field['max']}.")
        return int(round(raw)) if field.get("int") else float(raw)
    if kind == "select":
        allowed = [o["value"] for o in field["options"]]
        if raw not in allowed and raw != current:
            raise core.ModHubError(f"{key}: valore non ammesso.")
        return raw
    if kind == "text":
        if not isinstance(raw, str) or len(raw) > 500 or "\n" in raw or "\r" in raw:
            raise core.ModHubError(f"{key}: testo non valido.")
        if field.get("pattern") and not re.fullmatch(field["pattern"], raw):
            raise core.ModHubError(f"{field.get('label', key)}: formato non valido (esempio: {field.get('placeholder', '')}).")
        return raw
    raise core.ModHubError(f"{key}: tipo di impostazione sconosciuto.")


def settings_path(root, schema):
    return core.safe_dest(root, schema["file"])


def _documents():
    try:  # the real "Documents" folder, which may be redirected (OneDrive...)
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents"


def emulator_root(schema, exe_dir):
    """Folder with an emulator's settings: next to the program in portable mode, otherwise where it keeps them."""
    exe_dir = Path(exe_dir)
    candidates = []
    for entry in schema.get("roots", ["portable:"]):
        if entry.startswith("portable:"):
            if (exe_dir / schema.get("portableMarker", "portable.txt")).exists():
                candidates.append(exe_dir)
        else:
            candidates.append(Path(entry.replace("{documents}", str(_documents())).replace("{exe_dir}", str(exe_dir))))
    for c in candidates:
        if (c / schema["file"]).is_file():
            return c
    return candidates[0] if candidates else exe_dir


def _fmt(schema):
    return schema.get("format", "toml")


def describe(root, schema):
    path = settings_path(root, schema)
    if not path.is_file():
        return {"exists": False, "values": {}, "hasBackup": False}
    try:
        values = parse(path.read_bytes().decode("utf-8-sig"), _fmt(schema))
    except (OSError, ValueError, configparser.Error) as e:
        raise core.ModHubError(f"Impossibile leggere {schema['file']}: {e}") from e
    return {"exists": True, "values": values,
            "hasBackup": path.with_name(path.name + BACKUP_SUFFIX).is_file()}


def _check(schema, changes, current):
    """Validate user input against the schema; returns the typed values (plus any synced keys)."""
    fields = _fields(schema)
    coerced = {}
    for key, raw in changes.items():
        field = fields.get(key)
        if not field:
            raise core.ModHubError(f"Impostazione sconosciuta: {key}")
        value = _coerce(field, raw, current.get(key))
        coerced[key] = value
        for sync in field.get("sync", []):
            coerced[sync["key"]] = (value == sync["equals"])
    return coerced


def validate(root, schema, changes):
    """Check `changes` without writing anything (used to queue them for later)."""
    path = settings_path(root, schema)
    current = parse(path.read_bytes().decode("utf-8-sig"), _fmt(schema)) if path.is_file() else {}
    return _check(schema, changes, current)


def save(root, schema, changes):
    """Validate `changes` against the schema and write them. Returns the keys written."""
    path = settings_path(root, schema)
    if not path.is_file():
        raise core.ModHubError("Il file delle impostazioni non esiste ancora: avvia il programma almeno una volta.")
    fmt = _fmt(schema)
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    coerced = _check(schema, changes, parse(text, fmt))
    if not coerced:
        return []
    new_text = apply_changes(text, coerced, fmt)
    try:
        parse(new_text, fmt)
    except (ValueError, configparser.Error) as e:
        raise core.ModHubError(f"Modifica annullata: il file risultante non sarebbe valido ({e}).") from e
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".modhub-tmp")
    tmp.write_bytes((b"\xef\xbb\xbf" if bom else b"") + new_text.encode("utf-8"))
    os.replace(tmp, path)
    return sorted(coerced)


def ensure_lines(path, section, pairs, fmt="ini"):
    """Make sure `name = value` lines exist in a section, adding the missing ones and leaving every other line alone.

    A name may appear several times (an INI "list": PCSX2 allows more than one key binding per hotkey).
    Returns True if the file was changed; the first change keeps a copy as <file>.modhub-bak.
    """
    path = Path(path)
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    present, last_line, in_section = set(), None, False
    for i, line in enumerate(lines):
        m = _TABLE.match(line)
        if m:
            in_section = m.group(1).strip() == section
            continue
        a = _ASSIGN.match(line)
        if in_section and a:
            present.add((a.group(2), a.group(4).strip()))
            last_line = i
    missing = [(n, v) for n, v in pairs if (n, v) not in present]
    if not missing:
        return False
    entries = [f"{n} = {v}" for n, v in missing]
    if last_line is not None:
        lines[last_line + 1:last_line + 1] = entries
    else:
        header = next((i for i, l in enumerate(lines) if (m := _TABLE.match(l)) and m.group(1).strip() == section), None)
        if header is not None:
            lines[header + 1:header + 1] = entries
        else:
            lines += ["", f"[{section}]", *entries]
    new_text = newline.join(lines) + newline
    parse(new_text, fmt)  # never write something we cannot read back
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".modhub-tmp")
    tmp.write_bytes((b"\xef\xbb\xbf" if bom else b"") + new_text.encode("utf-8"))
    os.replace(tmp, path)
    return True


_PROFILE_LINE = re.compile(r"Loading game settings from '([^']+)'")


def profile_from_log(text):
    """The per-game settings file an emulator says it loaded (PCSX2: gamesettings/<serial>_<crc>.ini), if it exists.

    Such a file overrides the global one for the keys it contains, so those are the values that really apply.
    """
    found = _PROFILE_LINE.findall(text or "")
    path = Path(found[-1]) if found else None
    return path if path and path.is_file() else None


def profile_values(path, schema):
    """The schema's keys that a per-game file overrides, with the values it gives them."""
    try:
        values = parse(Path(path).read_bytes().decode("utf-8-sig"), _fmt(schema))
    except (OSError, ValueError, configparser.Error):
        return {}
    fields = _fields(schema)
    return {k: v for k, v in values.items() if k in fields}


def save_profile(path, schema, changes):
    """Write `changes` into the per-game file too, but only the keys it already overrides (it is a list of
    exceptions: adding keys would turn every setting into one). Returns the keys written."""
    path, fmt = Path(path), _fmt(schema)
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    current = parse(text, fmt)
    coerced = {k: v for k, v in _check(schema, changes, current).items() if k in current}
    if not coerced:
        return []
    new_text = apply_changes(text, coerced, fmt)
    parse(new_text, fmt)
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".modhub-tmp")
    tmp.write_bytes((b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + new_text.encode("utf-8"))
    os.replace(tmp, path)
    return sorted(coerced)


def restore_backup(root, schema):
    path = settings_path(root, schema)
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.is_file():
        raise core.ModHubError("Nessun backup da ripristinare.")
    shutil.copyfile(backup, path)


def game_running(root, exe):
    """True if the game's executable is locked, which on Windows means it is running."""
    return file_locked(Path(root) / exe)


def file_locked(path):
    path = Path(path)
    if os.name != "nt" or not path.is_file():
        return False
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False
