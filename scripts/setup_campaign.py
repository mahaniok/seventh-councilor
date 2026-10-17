#!/usr/bin/env python3
"""One-shot campaign setup — detects everything from your newest save.

Reads the save to find: your faction (the non-AI player), difficulty, research
rate, and alien progression. Writes config.json at the repo root, mirrors game
templates/localization from your install (sync_game_data.py), and verifies.

Usage:
    python3 setup_campaign.py             # newest save, full setup
    python3 setup_campaign.py <save>      # specific save
    python3 setup_campaign.py --no-sync   # write config only, skip the mirror
    python3 setup_campaign.py --dry-run   # show what would be written, write nothing

Everything is detected, nothing guessed: if a field can't be read the script
says so and leaves the config value null rather than inventing it.
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

from ti_config import _REPO_ROOT, newest_save

DIFFICULTY_NAMES = {1: "Cinematic", 2: "Normal", 3: "Veteran", 4: "Brutal"}


def load_save(path: Path) -> dict:
    if str(path).endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8-sig") as f:
            return json.load(f)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def detect(save_path: Path) -> dict:
    gs = load_save(save_path)["gamestates"]

    def state(name):
        return gs.get(f"PavonisInteractive.TerraInvicta.{name}", [])

    def key(k):
        return k["value"] if isinstance(k, dict) else k

    # Player faction: the TIPlayerState with isAI == False, joined to the
    # faction whose `player` id matches.
    faction = None
    human_ids = {key(e["Key"]) for e in state("TIPlayerState")
                 if not e["Value"].get("isAI")}
    for e in state("TIFactionState"):
        v = e["Value"]
        if key(v.get("player")) in human_ids:
            faction = v.get("templateName")
            break

    gv = state("TIGlobalValuesState")[0]["Value"]
    sc = gv.get("scenarioCustomizations") or {}
    difficulty = DIFFICULTY_NAMES.get(gv.get("difficulty"))
    research = sc.get("researchSpeedMultiplier")
    progression = sc.get("alienProgressionSpeed")

    # Anchor nations for the campaign-log trend tables (E/H): existing config
    # value if set, else auto-detect = top 3 by GDP among nations where the
    # player's faction holds a control point (identified by templateName).
    import extract_snapshot as er
    anchors = er.resolve_anchor_nations(gs, faction_template=faction) or None

    return {
        "faction": faction,
        "anchor_nations": anchors,
        "difficulty": difficulty,
        "research_rate_pct": int(research * 100) if research else None,
        "alien_progression_pct": int(progression * 100) if progression else None,
        "scenario": detect_scenario(gs),
        "_game_date": (state("TITimeState") or [{}])[0].get("Value", {}).get("now"),
        "_save_version": gv.get("latestSaveVersion"),
        "_modding_active": bool(gv.get("moddingActive")),
    }


def detect_scenario(gs) -> str | None:
    """Which DLC scenario overlay the campaign runs on. The save carries no scenario
    tag, but scenario-only content does: every Dark Skies 2003-start tech/project is
    prefixed `BS_` (backstory) and every Broken Earth one `BSBE_`, and the research
    state references them from day one. None = the default 2026 start."""
    research = gs.get("PavonisInteractive.TerraInvicta.TIGlobalResearchState", [])
    blob = json.dumps([e.get("Value", {}) for e in research])
    # Faction project lists too — a fresh save may have no global tech queued yet.
    blob += json.dumps([(e.get("Value") or {}).get("finishedProjectsNames")
                        for e in gs.get("PavonisInteractive.TerraInvicta.TIFactionState", [])])
    blob += json.dumps([(e.get("Value") or {}).get("availableProjectNames")
                        for e in gs.get("PavonisInteractive.TerraInvicta.TIFactionState", [])])
    if '"BSBE_' in blob or '"Project_BSBE_' in blob:
        return "Broken_Earth_Scenario"
    if '"BS_' in blob or '"Project_BS_' in blob:
        return "2003_Scenario"
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("save", nargs="?", help="save file (default: newest)")
    ap.add_argument("--no-sync", action="store_true", help="skip the game-data mirror step")
    ap.add_argument("--dry-run", action="store_true", help="detect and print only")
    args = ap.parse_args()

    save = Path(args.save) if args.save else newest_save()
    if not save or not save.is_file():
        sys.exit("No save found. Pass a save path, or set save_dir in config.json.")

    print(f"Reading {save.name} …")
    det = detect(save)
    for label, val in [("Faction", det["faction"]), ("Difficulty", det["difficulty"]),
                       ("Research rate %", det["research_rate_pct"]),
                       ("Alien progression %", det["alien_progression_pct"]),
                       ("Anchor nations (Tables E/H)",
                        ", ".join(det["anchor_nations"]) if det["anchor_nations"] else None)]:
        print(f"  {label}: {val if val is not None else 'NOT DETECTED — set manually in config.json'}")
    print(f"  Scenario: {det['scenario'] or 'default 2026 start (no DLC overlay)'}")
    from ti_config import enabled_mod_dirs
    mods = enabled_mod_dirs()
    if det["_modding_active"] or mods:
        names = [m.name for m in mods]
        print(f"  Mods (game's Mods/Enabled, layered onto the templates): "
              f"{len(names)} — {', '.join(names[:8])}{', …' if len(names) > 8 else ''}"
              if names else
              "  Mods: the save says modding is active but no <install>/Mods/Enabled folder was "
              "found — template-changing mods won't be reflected in the analyzers")

    if args.dry_run:
        return

    cfg_path = _REPO_ROOT / "config.json"
    existing = {}
    if cfg_path.is_file():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    cfg = {
        "save_dir": existing.get("save_dir"),
        "game_install_dir": existing.get("game_install_dir"),
        "faction": det["faction"] or existing.get("faction"),
        "anchor_nations": det["anchor_nations"] or existing.get("anchor_nations"),
        "research_rate_pct": det["research_rate_pct"] or existing.get("research_rate_pct") or 100,
        "alien_progression_pct": det["alien_progression_pct"] or existing.get("alien_progression_pct") or 100,
        "difficulty": det["difficulty"] or existing.get("difficulty") or "Normal",
        # Income display unit: "month" (game's "Show monthly incomes" ON, the default) or "day".
        # NEVER yearly. All scripts read this; see LESSONS-process P17.
        "income_display": existing.get("income_display") or "month",
        # Game-data layers for the templates mirror (sync_game_data.py): the DLC scenario
        # overlay this campaign runs on (detected from the save) and which mods to layer.
        "scenario": det["scenario"],
        "mods": existing.get("mods", "enabled"),
    }
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {cfg_path}")

    if not args.no_sync:
        import ti_config
        ti_config.CONFIG.update({"scenario": cfg["scenario"], "mods": cfg["mods"]})
        import sync_game_data
        sync_game_data.main(["--scenario", cfg["scenario"] or "none"])


if __name__ == "__main__":
    main()
