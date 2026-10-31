#!/usr/bin/env python3
"""save_trajectory.py — campaign-trajectory extractor across multiple Terra Invicta saves.

Given a list of save files (raw .json OR gzip-compressed .gz — gzip magic bytes are
sniffed regardless of extension, via extract_snapshot.load_save), extract one compact
metrics row per save for the player faction (--faction / config.json) and the aliens
(AlienCouncil), then print a markdown table sorted by in-game date plus a tempo
analysis (per-30.44-day-month deltas between consecutive snapshots, with
accelerating / steady / stalling verdicts per metric).

Per-save metrics:
  - in-game date (from TITimeState, not the filename)
  - finished global techs count (TIGlobalResearchState.finishedTechsNames — shared)
  - finished faction projects count (faction.finishedProjectNames)
  - research income /yr — LIVE 6-source formula (HQ + councilors + nations(CP-share +
    Advise) + habs + unused MC, × distribution bonus) via
    extract_snapshot.extract_snapshot_income_breakdown, wired exactly like
    extract_snapshot.build_snapshot. Falls back to cachedYearlyRevenue.Research
    labeled "(cache, stale)" if the live computation fails on an old save.
  - money / metals stockpiles (faction.resources)
  - MC used (faction.missionControlUsage) / MC cap (HQ + councilors + nations
    + hab-produced, the in-game tooltip's four sources)
  - CP count (TIControlPoint ownership)
  - habs count (TIHabState by faction) — player and aliens
  - ships + fleet tonnage (TISpaceShipState currentMass_kg) — player and aliens
  - assessedAlienHateOfMe (displayed alien hate; constant between
    Effect_FixAlienThreatMeter events — see extract_snapshot.extract_alien_hate_analysis)

Usage:
    python3 save_trajectory.py SAVE [SAVE ...] [--faction <templateName>] [-o out.md]
    python3 save_trajectory.py --auto            # one snapshot per in-game month,
                                                 # latest save of each month, from the
                                                 # canonical save dir (~10-14 picks are
                                                 # better made by hand; --auto is a
                                                 # convenience, it picks EVERY month)
    python3 save_trajectory.py --auto --months 2029-06 2030-06 2031-03 2032-05

Saves are ~60-90 MB JSON: each is loaded once, metrics extracted, then the dict is
freed before the next load (gc between saves keeps peak memory ~1 save).

Reuses parsing/extractors from extract_snapshot.py (same directory, import-safe).
"""

import argparse
import gc
import os
import re
import sys
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from extract_snapshot import (  # noqa: E402
    FACTION_DISPLAY,
    extract_control_points,
    extract_fleet_inventory,
    extract_hab_inventory,
    extract_mc_full,
    extract_snapshot_income_breakdown,
    fac_id_from_field,
    faction_id_by_template,
    get_factions,
    get_game_date,
    get_global_state,
    kv_items,
    load_game_templates,
    load_save,
)

from ti_config import find_save_dir, require_faction

DEFAULT_SAVE_DIR = str(find_save_dir() or "")
ALIEN_TEMPLATE = "AlienCouncil"

# Filename in-game-date pattern, e.g. Resistsave123456789_2032-5-8.json
FNAME_DATE_RE = re.compile(r"_(\d{4})-(\d{1,2})-(\d{1,2})\D")


def parse_fname_date(path):
    """Best-effort (year, month, day) from the save filename. Day is clamped to
    1..31 (some files carry user-suffixed days like '2032-5-71'). Returns None
    when the name has no embedded date (Autosave.json, ExitSave.json...)."""
    m = FNAME_DATE_RE.search(os.path.basename(path) + ".")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    while d > 31:
        d //= 10
    return (y, mo, max(1, d))


def extract_point(save_path, faction_template=None, templates=None):
    """Load one save, return a flat metrics dict, free the save."""
    faction_template = require_faction(faction_template)
    if templates is None:
        templates = load_game_templates()
    data = load_save(save_path)
    gs = data.get("gamestates", {})

    point = {"save": os.path.basename(str(save_path)), "errors": []}

    game_date, _ts = get_game_date(gs)
    point["date"] = game_date
    grs, _gvs = get_global_state(gs)
    factions = get_factions(gs)
    fid = faction_id_by_template(factions, faction_template)
    if fid is None:
        raise ValueError(f"Faction '{faction_template}' not in {save_path}")
    fac = factions[fid]

    # --- research / projects completed
    point["techs_done"] = len((grs or {}).get("finishedTechsNames", []) or [])
    point["projects_done"] = len(fac.get("finishedProjectNames", []) or [])

    # --- stockpiles
    res = fac.get("resources", {}) or {}
    point["money"] = res.get("Money", 0) or 0
    point["metals"] = res.get("Metals", 0) or 0

    # --- CPs + nations-MC (needed for MC cap and live research income)
    try:
        cp_data = extract_control_points(gs, factions)
        point["cps"] = cp_data["by_faction"].get(faction_template, 0)
        from extract_snapshot import extract_nation_mc_share
        mc_nations = extract_nation_mc_share(gs, fid)   # per-CP split = the tooltip's nations line
    except Exception as e:  # pragma: no cover — old-save schema drift
        point["errors"].append(f"control_points: {e}")
        point["cps"] = len(fac.get("controlPoints", []) or [])
        mc_nations = None

    # --- MC used / cap (four-source tooltip formula)
    point["mc_used"] = fac.get("missionControlUsage", 0) or 0
    mc_cap = None
    try:
        hab_inv = extract_hab_inventory(gs, fid)
        mc = extract_mc_full(gs, fid, hab_inv, factions)
        if mc_nations is not None:
            mc_cap = ((mc.get("mc_hq") or 0) + mc_nations
                      + (mc.get("mc_councilors") or 0)
                      + (mc.get("mc_hab_produced") or 0))
    except Exception as e:  # pragma: no cover
        point["errors"].append(f"mc_full: {e}")
    point["mc_cap"] = mc_cap

    # --- research income: live 6-source, fallback to stale cache
    point["research_yr"] = None
    point["research_live"] = False
    try:
        ri = extract_snapshot_income_breakdown(gs, fid, factions, templates)
        if ri and mc_cap:
            # Same wiring as build_snapshot: plug MC cap in, recompute totals.
            free_mc = max(0, mc_cap - (ri.get("mc_usage") or 0))
            base = (ri["hq_per_day"] + ri["councilors_per_day_estimate"]
                    + ri["nation_per_day_total"] + ri["habs_per_day"]
                    + free_mc * 0.075)
            total_day = base * (1 + ri["dist_bonus_pct"])
            point["research_yr"] = round(total_day * 365.25)
            point["research_live"] = True
        elif ri:
            point["research_yr"] = round(ri.get("total_per_year") or 0)
            point["research_live"] = True
    except Exception as e:  # pragma: no cover
        point["errors"].append(f"research_income: {e}")
    if not point["research_yr"]:
        point["research_yr"] = round((fac.get("cachedYearlyRevenue", {}) or {})
                                     .get("Research", 0) or 0)
        point["research_live"] = False

    # --- habs (player + aliens)
    habs_me = habs_alien = 0
    for _hid, hv in kv_items(gs, "PavonisInteractive.TerraInvicta.TIHabState"):
        tpl = factions.get(fac_id_from_field(hv.get("faction")), {}).get("templateName")
        if tpl == faction_template:
            habs_me += 1
        elif tpl == ALIEN_TEMPLATE:
            habs_alien += 1
    point["habs"] = habs_me
    point["alien_habs"] = habs_alien

    # --- fleets (player + aliens)
    try:
        fleets = extract_fleet_inventory(gs, factions)
        mine = fleets.get(faction_template, {})
        alien = fleets.get(ALIEN_TEMPLATE, {})
        point["ships"] = mine.get("ships", 0)
        point["fleet_kt"] = round(mine.get("total_mass_kg", 0) / 1e6, 1)
        point["alien_ships"] = alien.get("ships", 0)
        point["alien_fleet_kt"] = round(alien.get("total_mass_kg", 0) / 1e6, 1)
    except Exception as e:  # pragma: no cover
        point["errors"].append(f"fleets: {e}")
        point["ships"] = point["fleet_kt"] = None
        point["alien_ships"] = point["alien_fleet_kt"] = None

    # --- alien hate (displayed)
    point["hate"] = round(fac.get("assessedAlienHateOfMe", 0) or 0, 1)

    del data, gs
    gc.collect()
    return point


# ---------------------------------------------------------------------------
# Markdown rendering + tempo analysis
# ---------------------------------------------------------------------------

def _iso_to_date(s):
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return date(y, m, d)
    except Exception:
        return None


def _fmt(n):
    if n is None:
        return "?"
    if isinstance(n, float):
        return f"{n:,.1f}"
    return f"{n:,}"


def render_table(points):
    L = []
    L.append("| Date | Techs | Proj | Research/yr | Money | Metals | MC used/cap"
             " | CPs | Habs | Ships | Fleet kt | Alien habs | Alien ships"
             " | Alien kt | Hate |")
    L.append("|---|" + "---:|" * 14)
    for p in points:
        rsr = _fmt(p["research_yr"]) + ("" if p["research_live"] else " (cache, stale)")
        mc = f"{_fmt(p['mc_used'])}/{_fmt(p['mc_cap'])}"
        L.append(
            f"| {p['date']} | {p['techs_done']} | {p['projects_done']} | {rsr} "
            f"| {_fmt(round(p['money']))} | {_fmt(round(p['metals']))} | {mc} "
            f"| {p['cps']} | {p['habs']} | {_fmt(p['ships'])} | {_fmt(p['fleet_kt'])} "
            f"| {p['alien_habs']} | {_fmt(p['alien_ships'])} | {_fmt(p['alien_fleet_kt'])} "
            f"| {_fmt(p['hate'])} |")
    return "\n".join(L)


def _rate_series(points, key):
    """[(date_str, per-month rate since previous point)] over consecutive pairs."""
    out = []
    for a, b in zip(points, points[1:]):
        da, db = _iso_to_date(a["date"]), _iso_to_date(b["date"])
        va, vb = a.get(key), b.get(key)
        if None in (da, db, va, vb):
            continue
        months = (db - da).days / 30.44
        if months <= 0:
            continue
        out.append((b["date"], (vb - va) / months))
    return out


def _verdict(rates, last_n=3):
    """accelerating / steady / stalling: last-N avg per-month rate vs earlier avg."""
    if len(rates) < 2:
        return "n/a", 0.0, 0.0
    cut = max(1, len(rates) - last_n)
    early = sum(r for _, r in rates[:cut]) / cut
    late = sum(r for _, r in rates[cut:]) / (len(rates) - cut)
    if abs(early) < 1e-9:
        tag = "accelerating" if late > 0 else "steady"
    elif late > early * 1.25:
        tag = "accelerating"
    elif late < early * 0.75:
        tag = "stalling"
    else:
        tag = "steady"
    return tag, early, late


def render_tempo(points):
    """Tempo analysis: per-month deltas + verdicts for the headline metrics."""
    L = ["## Tempo analysis", ""]
    if len(points) < 2:
        return "\n".join(L + ["Need ≥2 snapshots for tempo."])

    metrics = [
        ("techs_done", "Tech completion", "techs/mo"),
        ("projects_done", "Project completion", "projects/mo"),
        ("research_yr", "Research income", "Δ(RP/yr)/mo"),
        ("mc_cap", "MC cap", "MC/mo"),
        ("mc_used", "MC used", "MC/mo"),
        ("cps", "Control points", "CPs/mo"),
        ("habs", "Habs", "habs/mo"),
        ("fleet_kt", "Player fleet tonnage", "kt/mo"),
        ("alien_fleet_kt", "Alien fleet tonnage", "kt/mo"),
        ("hate", "Alien hate (displayed)", "hate/mo"),
    ]
    L.append("| Metric | Whole-run avg | Early avg | Last-3-interval avg | Verdict |")
    L.append("|---|---:|---:|---:|---|")
    for key, label, unit in metrics:
        rates = _rate_series(points, key)
        if not rates:
            L.append(f"| {label} | ? | ? | ? | n/a |")
            continue
        tag, early, late = _verdict(rates)
        whole = sum(r for _, r in rates) / len(rates)
        L.append(f"| {label} | {whole:+,.1f} {unit} | {early:+,.1f} | {late:+,.1f}"
                 f" | **{tag}** |")

    # Fleet-tonnage gap vs aliens, point by point
    L.append("")
    L.append("### Fleet tonnage gap (player − alien, kt)")
    L.append("")
    L.append("| Date | Player kt | Alien kt | Gap | Ratio |")
    L.append("|---|---:|---:|---:|---:|")
    for p in points:
        if p.get("fleet_kt") is None or p.get("alien_fleet_kt") is None:
            continue
        gap = p["fleet_kt"] - p["alien_fleet_kt"]
        ratio = (p["fleet_kt"] / p["alien_fleet_kt"]) if p["alien_fleet_kt"] else float("inf")
        L.append(f"| {p['date']} | {p['fleet_kt']:,.0f} | {p['alien_fleet_kt']:,.0f}"
                 f" | {gap:+,.0f} | {ratio:,.2f}× |")
    return "\n".join(L)


def render_report(points, faction_template):
    name = FACTION_DISPLAY.get(faction_template, faction_template)
    points = sorted(points, key=lambda p: p["date"])
    L = [f"# Campaign trajectory — {name}", ""]
    L.append(f"{len(points)} snapshots, {points[0]['date']} → {points[-1]['date']}."
             " Research/yr is the live 6-source formula unless labeled stale."
             " Fleet kt = Σ currentMass_kg / 1e6.")
    L.append("")
    L.append(render_table(points))
    L.append("")
    L.append(render_tempo(points))
    errs = [(p["date"], e) for p in points for e in p.get("errors", [])]
    if errs:
        L.append("")
        L.append("### Extraction warnings")
        L.extend(f"- {d}: {e}" for d, e in errs)
    L.append("")
    L.append("### Snapshot sources")
    L.extend(f"- {p['date']} ← `{p['save']}`" for p in points)
    return "\n".join(L) + "\n"


def auto_pick(save_dir, months=None):
    """Pick the save with the latest filename in-game date per in-game month
    (mtime breaks ties). months: optional list of 'YYYY-MM' strings to keep."""
    best = {}
    for fn in os.listdir(save_dir):
        if not (fn.endswith(".json") or fn.endswith(".gz")):
            continue
        d = parse_fname_date(fn)
        if not d:
            continue
        key = f"{d[0]:04d}-{d[1]:02d}"
        if months and key not in months:
            continue
        path = os.path.join(save_dir, fn)
        rank = (d, os.path.getmtime(path))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, path)
    return [p for _, p in (best[k] for k in sorted(best))]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("saves", nargs="*", help="save files (.json or .gz)")
    ap.add_argument("--faction", default=None)
    ap.add_argument("--auto", action="store_true",
                    help=f"auto-pick latest save per in-game month from {DEFAULT_SAVE_DIR}")
    ap.add_argument("--months", nargs="*", default=None,
                    help="with --auto: restrict to these YYYY-MM months")
    ap.add_argument("--save-dir", default=DEFAULT_SAVE_DIR)
    ap.add_argument("-o", "--out", default=None, help="write markdown here (default stdout)")
    args = ap.parse_args()
    args.faction = require_faction(args.faction)

    paths = list(args.saves)
    if args.auto:
        if not args.save_dir:
            sys.exit("--auto: no save dir auto-detected; pass --save-dir or set "
                     "\"save_dir\" in config.json")
        paths += auto_pick(args.save_dir, args.months)
    if not paths:
        ap.error("no saves given (pass files or --auto)")

    templates = load_game_templates()
    points = []
    for p in paths:
        print(f"[trajectory] {os.path.basename(p)} ...", file=sys.stderr, flush=True)
        try:
            points.append(extract_point(p, args.faction, templates))
        except Exception as e:
            print(f"[trajectory]   FAILED: {e}", file=sys.stderr)
    report = render_report(points, args.faction)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[trajectory] wrote {args.out}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
