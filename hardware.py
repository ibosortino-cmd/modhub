"""Find out what this PC has and estimate how demanding a game it can run comfortably.

The result is an estimate from the graphics card model, its memory and the RAM, not a benchmark.
A game's settings schema maps the PC "tier" (1 = basic ... 5 = very high) to one of its quality levels.
"""
import ctypes
import json
import os
import platform
import re
import subprocess

TIER_NAMES = {1: "Base", 2: "Medio-bassa", 3: "Media", 4: "Alta", 5: "Molto alta"}

_SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { @{ name = $_.Name; adapterRam = [int64]$_.AdapterRAM } })
$vram = @(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*' -ErrorAction SilentlyContinue |
  Where-Object { $_.'HardwareInformation.qwMemorySize' } | ForEach-Object { @{ desc = $_.DriverDesc; bytes = [int64]$_.'HardwareInformation.qwMemorySize' } })
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$ram = [int64](Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
@{ gpus = $gpus; vram = $vram; cpu = @{ name = "$($cpu.Name)".Trim(); cores = [int]$cpu.NumberOfCores; threads = [int]$cpu.NumberOfLogicalProcessors }; ram = $ram } | ConvertTo-Json -Compress -Depth 5
"""


def _num(pattern, name):
    m = re.search(pattern, name, re.I)
    return int(m.group(1)) if m else None


def gpu_tier(name):
    """Tier 1-5 for a graphics card model name, or None if the model is not recognised."""
    n = name or ""
    low = n.lower()
    # NVIDIA
    m = re.search(r"\b(RTX|GTX|GT)\s*(\d{3,4})(\s*Ti)?", n, re.I)
    if m and ("nvidia" in low or "geforce" in low):
        series, num, ti = m.group(1).upper(), int(m.group(2)), bool(m.group(3))
        if series == "RTX":
            if num >= 5000:
                return 4 if num < 5060 else 5
            if num >= 4000:
                return 4 if num < 4060 else 5
            if num >= 3000:
                return 4 if num < 3060 or (num == 3060 and not ti) else 5
            return 3 if num < 2060 else 4 if num < 2070 else 5
        if series == "GTX":
            if num >= 1600:
                return 3
            if num >= 1000:
                return 2 if num < 1060 else 3 if num < 1070 else 4
            return 3 if num >= 960 else 2 if num >= 750 else 1
        return 1
    # AMD
    m = re.search(r"\bRX\s*(\d{3,4})", n, re.I)
    if m and ("amd" in low or "radeon" in low):
        num = int(m.group(1))
        if num >= 9000:
            return 5
        if num >= 7000:
            return 4 if num < 7700 else 5
        if num >= 6000:
            return 3 if num < 6600 else 4 if num < 6700 else 5
        if num >= 5000:
            return 3 if num < 5600 else 4
        return 2 if num in (455, 460, 550, 560) or num < 460 else 3
    if "radeon vii" in low:
        return 5
    if "radeon" in low and re.search(r"vega\s*(56|64)", low):
        return 4
    if "radeon" in low and re.search(r"vega|graphics", low):
        return 2
    # Intel
    m = re.search(r"\bArc\b.*?\b([AB])(\d{3})\b", n, re.I)
    if m:
        return 3 if m.group(1).upper() == "A" and int(m.group(2)) < 500 else 4
    if "intel" in low and re.search(r"iris|xe", low):
        return 2
    if "intel" in low and re.search(r"uhd|hd graphics", low):
        return 1
    return None


def classify(gpus, vram_gb, ram_gb):
    """Overall tier for this PC and whether the graphics card model was recognised."""
    tiers = [(gpu_tier(g), g) for g in gpus]
    known = [t for t, _ in tiers if t]
    tier, recognised = (max(known), True) if known else (3, False)
    if vram_gb is not None and vram_gb < 3:
        tier = min(tier, 3)
    if ram_gb is not None and ram_gb < 8:
        tier = max(1, tier - 1)
    return tier, recognised


def recommended_level(hw, optimizer):
    """Quality level (1-based) a game's optimizer suggests for this PC."""
    by_tier = optimizer.get("byTier") or list(range(1, len(optimizer["levels"]) + 1))
    level = by_tier[min(max(hw["tier"], 1), len(by_tier)) - 1]
    return min(max(level, 1), len(optimizer["levels"]))


def _total_ram_gb():
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("totalPage", ctypes.c_ulonglong), ("availPage", ctypes.c_ulonglong),
                    ("totalVirt", ctypes.c_ulonglong), ("availVirt", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
    try:
        status = Status(length=ctypes.sizeof(Status))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return round(status.total / 1024 ** 3, 1)
    except (AttributeError, OSError):
        return None


def build(raw):
    """Turn the raw system query into the summary shown to the user."""
    as_list = lambda v: [v] if isinstance(v, dict) else (v or [])
    gpus_raw, vram_raw = as_list(raw.get("gpus")), as_list(raw.get("vram"))
    names = [g.get("name", "") for g in gpus_raw if g.get("name")]
    gpus = []
    for g in gpus_raw:
        name = g.get("name") or ""
        exact = next((v["bytes"] for v in vram_raw if v.get("desc") == name), None)
        adapter = g.get("adapterRam") or 0
        size = exact or (adapter if 0 < adapter < 4294967295 else None)  # AdapterRAM caps at 4 GB, so it is a last resort
        gpus.append({"name": name, "vramGb": round(size / 1024 ** 3, 1) if size else None, "tier": gpu_tier(name)})
    best = max(gpus, key=lambda g: (g["tier"] or 0, g["vramGb"] or 0), default=None)
    cpu = raw.get("cpu") or {}
    ram = round(raw["ram"] / 1024 ** 3, 1) if raw.get("ram") else _total_ram_gb()
    tier, recognised = classify(names, best["vramGb"] if best else None, ram)
    return {"gpu": best["name"] if best else "Non rilevata", "vramGb": best["vramGb"] if best else None, "gpus": gpus,
            "cpu": cpu.get("name") or platform.processor() or "Non rilevato", "cores": cpu.get("cores") or None,
            "threads": cpu.get("threads") or os.cpu_count(), "ramGb": ram,
            "tier": tier, "tierName": TIER_NAMES[tier], "recognised": recognised}


def detect():
    """Query this PC (Windows). Falls back to what Python alone can tell if the query fails."""
    raw = {}
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT], capture_output=True,
                              timeout=40, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        raw = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except (OSError, ValueError, subprocess.SubprocessError):
        raw = {}
    return build(raw)
