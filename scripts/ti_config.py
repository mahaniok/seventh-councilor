"""Shared campaign configuration for The Seventh Councilor's scripts.

Reads `config.json` at the repo root (copy `config.example.json` and edit).
Every value has a sane fallback so the scripts also work with no config at all:
save/install locations auto-detect across Windows, macOS (native + CrossOver),
Linux (native + Proton), and WSL.

Usage from any script in this folder:

    from ti_config import CONFIG, find_save_dir, newest_save, find_templates_dir

    save = newest_save()                # Path to the most recent save, or None
    faction = CONFIG["faction"]         # e.g. "ResistCouncil"
    rp_divisor = CONFIG["research_rate_pct"] / 100.0   # in-game RP cost = template / divisor
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "save_dir": None,
    "game_install_dir": None,
    "faction": None,               # scripts require --faction if unset
    "anchor_nations": None,        # nation templateNames for trend tables; None = auto-detect
                                   # (resolution helper: extract_snapshot.resolve_anchor_nations)
    "research_rate_pct": 100,
    "alien_progression_pct": 100,
    "difficulty": "Normal",
    # Game-data LAYERS the templates mirror is built from (sync_game_data.py):
    #   scenario: DLC scenario overlay folder under <install>/DLC_Content/*/, e.g.
    #             "2003_Scenario" (Dark Skies 2003 start) or "Broken_Earth_Scenario";
    #             None = default 2026 start, no overlay. setup_campaign.py detects it.
    #   mods:     "enabled" = every mod in <install>/Mods/Enabled (what the game loads),
    #             a list of mod folder names, or false for none.
    "scenario": None,
    "mods": "enabled",
}

SCENARIOS = ("2003_Scenario", "Broken_Earth_Scenario")

_FACTIONS = [
    "ResistCouncil", "ExploitCouncil", "SubmitCouncil", "AppeaseCouncil",
    "CooperateCouncil", "DestroyCouncil", "EscapeCouncil",
]


def _load_config() -> dict:
    cfg = dict(_DEFAULTS)
    path = _REPO_ROOT / "config.json"
    if path.is_file():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(f"config.json is unreadable or invalid JSON: {e}")
        for k in _DEFAULTS:
            if user.get(k) is not None:
                cfg[k] = user[k]
        if cfg["scenario"] is not None and cfg["scenario"] not in SCENARIOS:
            raise SystemExit(
                f"config.json: unknown scenario {cfg['scenario']!r}. "
                f"Valid values: {', '.join(SCENARIOS)} or null"
            )
        if cfg["faction"] is not None and cfg["faction"] not in _FACTIONS:
            raise SystemExit(
                f"config.json: unknown faction {cfg['faction']!r}. "
                f"Valid templateNames: {', '.join(_FACTIONS)}"
            )
    return cfg


CONFIG = _load_config()


# ---------------------------------------------------------------------------
# Save-folder discovery. The game writes saves under a per-user "My Games"
# directory whose location varies by platform/launcher.
# ---------------------------------------------------------------------------

def _candidate_save_dirs() -> list[Path]:
    home = Path.home()
    cands = [
        # Windows native
        home / "Documents" / "My Games" / "TerraInvicta" / "Saves",
        home / "OneDrive" / "Documents" / "My Games" / "TerraInvicta" / "Saves",
        # macOS: CrossOver/Wine bottle keeps a Windows-shaped user dir, but the
        # game usually resolves Documents to the real macOS ~/Documents
        home / "Documents" / "My Games" / "TerraInvicta" / "Saves",
        home / "Library" / "Application Support" / "CrossOver" / "Bottles" / "Steam"
             / "drive_c" / "users" / "crossover" / "Documents" / "My Games"
             / "TerraInvicta" / "Saves",
        # Linux / Steam Deck (Proton prefix)
        home / ".local" / "share" / "Steam" / "steamapps" / "compatdata" / "1176470"
             / "pfx" / "drive_c" / "users" / "steamuser" / "Documents" / "My Games"
             / "TerraInvicta" / "Saves",
    ]
    # WSL: scan every mounted Windows drive for the default profile layout
    mnt = Path("/mnt")
    if mnt.is_dir():
        for drive in mnt.iterdir():
            users = drive / "Users"
            if users.is_dir():
                for prof in users.iterdir():
                    cands.append(prof / "Documents" / "My Games" / "TerraInvicta" / "Saves")
                    cands.append(prof / "OneDrive" / "Documents" / "My Games"
                                 / "TerraInvicta" / "Saves")
    return cands


def find_save_dir() -> Path | None:
    """The configured save_dir if set, else the first auto-detected one."""
    if CONFIG["save_dir"]:
        p = Path(os.path.expanduser(CONFIG["save_dir"]))
        return p if p.is_dir() else None
    for p in _candidate_save_dirs():
        try:
            if p.is_dir():
                return p
        except OSError:
            continue
    return None


def newest_save(save_dir: Path | None = None) -> Path | None:
    """Most recently written save (.json or .gz), by mtime — filename dates lie."""
    d = save_dir or find_save_dir()
    if not d:
        return None
    saves = sorted(
        (p for p in d.iterdir() if p.suffix in (".json", ".gz")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return saves[0] if saves else None


# ---------------------------------------------------------------------------
# Game-data discovery (templates / localization). Preference order:
#   1. the local mirror created by sync_game_data.py (scripts/templates — gitignored)
#   2. the configured game_install_dir
#   3. auto-detected Steam library locations
# ---------------------------------------------------------------------------

def _candidate_install_dirs() -> list[Path]:
    home = Path.home()
    cands = [
        home / "Library" / "Application Support" / "CrossOver" / "Bottles" / "Steam"
             / "drive_c" / "Program Files (x86)" / "Steam" / "steamapps" / "common"
             / "Terra Invicta",
        home / "Library" / "Application Support" / "Steam" / "steamapps" / "common"
             / "Terra Invicta",
        home / ".local" / "share" / "Steam" / "steamapps" / "common" / "Terra Invicta",
        Path("C:/Program Files (x86)/Steam/steamapps/common/Terra Invicta"),
    ]
    mnt = Path("/mnt")
    if mnt.is_dir():
        for drive in mnt.iterdir():
            cands.append(drive / "Program Files (x86)" / "Steam" / "steamapps"
                         / "common" / "Terra Invicta")
            cands.append(drive / "Games" / "Steam" / "steamapps" / "common"
                         / "Terra Invicta")
    return cands


def find_install_dir() -> Path | None:
    """The game install root (config first, then auto-detect)."""
    roots = []
    if CONFIG["game_install_dir"]:
        roots.append(Path(os.path.expanduser(CONFIG["game_install_dir"])))
    roots += _candidate_install_dirs()
    for root in roots:
        if (root / "TerraInvicta_Data" / "StreamingAssets" / "Templates").is_dir():
            return root
    return None


def scenario_dirs(scenario: str | None = None, install: Path | None = None) -> tuple[Path | None, Path | None]:
    """(templates overlay dir, English localization overlay dir) for a DLC scenario.

    Dark Skies ships its scenarios as template OVERLAYS that the game layers on top of
    StreamingAssets/Templates: <install>/DLC_Content/<DLC>/<scenario>/Templates and
    <install>/DLC_Content/<DLC>/Localization/en/<scenario with spaces>/. Entries there
    override the base game per dataName (e.g. the 2003 start's `Effect_BS_*` penalties,
    `BS_*` techs) — a mirror of the base folder alone silently misses all of it.
    """
    scenario = scenario if scenario is not None else CONFIG["scenario"]
    install = install or find_install_dir()
    if not scenario or not install:
        return None, None
    dlc_root = install / "DLC_Content"
    if not dlc_root.is_dir():
        return None, None
    for dlc in sorted(dlc_root.iterdir()):
        t = dlc / scenario / "Templates"
        if t.is_dir():
            loc = dlc / "Localization" / "en" / scenario.replace("_", " ")
            return t, (loc if loc.is_dir() else None)
    return None, None


def dlc_localization_dirs(install: Path | None = None) -> list[Path]:
    """Top-level English localization folders of every installed DLC (shared strings)."""
    install = install or find_install_dir()
    if not install or not (install / "DLC_Content").is_dir():
        return []
    out = []
    for dlc in sorted((install / "DLC_Content").iterdir()):
        loc = dlc / "Localization" / "en"
        if loc.is_dir():
            out.append(loc)
    return out


def enabled_mod_dirs(install: Path | None = None) -> list[Path]:
    """Mod folders the game loads, per config `mods`: "enabled" → <install>/Mods/Enabled/*
    (the game copies every enabled mod there), a list → those names under Mods/Enabled
    (then Mods/Disabled), false/None → none. Alphabetical — the game's own load order
    isn't recorded on disk, so overlapping mods are a best effort (reported by the sync)."""
    mods = CONFIG["mods"]
    install = install or find_install_dir()
    if not mods or not install:
        return []
    enabled = install / "Mods" / "Enabled"
    if mods == "enabled":
        return sorted(p for p in enabled.iterdir() if p.is_dir()) if enabled.is_dir() else []
    out = []
    for name in mods:
        for base in (enabled, install / "Mods" / "Disabled"):
            if (base / name).is_dir():
                out.append(base / name)
                break
    return out


def find_templates_dir() -> Path | None:
    local = Path(__file__).resolve().parent / "templates"
    if (local / "TIProjectTemplate.json").is_file():
        return local
    roots = []
    if CONFIG["game_install_dir"]:
        roots.append(Path(os.path.expanduser(CONFIG["game_install_dir"])))
    roots += _candidate_install_dirs()
    for root in roots:
        t = root / "TerraInvicta_Data" / "StreamingAssets" / "Templates"
        if (t / "TIProjectTemplate.json").is_file():
            return t
    return None


def find_localization_dir() -> Path | None:
    local = Path(__file__).resolve().parent / "localization"
    if (local / "TITechTemplate.en").is_file():
        return local
    tdir = find_templates_dir()
    if tdir and tdir.name == "Templates":
        loc = tdir.parent / "Localization" / "en"
        if loc.is_dir():
            return loc
    return None


def require_faction(cli_value: str | None) -> str:
    """Resolve the player faction from CLI flag or config; exit with help if neither."""
    faction = cli_value or CONFIG["faction"]
    if not faction:
        raise SystemExit(
            "No faction set. Pass --faction <templateName> or set \"faction\" in "
            f"config.json. Valid values: {', '.join(_FACTIONS)}"
        )
    return faction
