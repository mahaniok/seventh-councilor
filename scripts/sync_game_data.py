#!/usr/bin/env python3
"""Sync Terra Invicta game data (templates + English localization) into this repo.

Builds the local mirror the scripts read (scripts/templates, scripts/localization) the
way the GAME builds its own template set — as LAYERS, later layers overriding earlier
ones per `dataName`:

  1. base game      <install>/TerraInvicta_Data/StreamingAssets/Templates (+ Localization/en)
  2. DLC scenario   <install>/DLC_Content/<DLC>/<scenario>/Templates      (config "scenario";
                    e.g. Dark Skies "2003_Scenario" — its `Effect_BS_*` penalties, `BS_*`
                    techs and `Project_BS_*` projects exist ONLY here)
  3. enabled mods   <install>/Mods/Enabled/*/TI*Template.json             (config "mods")

A mirror of layer 1 alone is silently wrong for a scenario or modded campaign: unknown
effect names fall back to "no effect" in the analyzers (the 2003 start's ×0.5 CP research
penalty read as ×1.0 — research over-read by 30%). Provenance of every merged file is
written to scripts/templates/_LAYERS.json.

Before copying, prints a content-level DRIFT report (merged install data vs the mirror):
  * Template JSONs compared per-dataName entry: added / removed / changed entries.
  * .en localization files get a line-level summary.
  * Files present only in the source (never mirrored before) are reported as NEW.

Usage:
    python3 sync_game_data.py                 # report drift, then write the merged mirror
    python3 sync_game_data.py --dry-run       # report drift only, write nothing
    python3 sync_game_data.py --scenario 2003_Scenario   # override config "scenario"
    python3 sync_game_data.py --no-mods       # base + scenario only
    python3 sync_game_data.py \
        --src-templates /path/to/Templates \
        --src-localization /path/to/Localization/en \
        --dst-templates /path/to/templates \
        --dst-localization /path/to/localization

Defaults (no CLI args needed when auto-detection works):
    game install        = config.json "game_install_dir", else auto-detected via ti_config
    dest   templates    = scripts/templates      (next to this script)
    dest   localization = scripts/localization
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from ti_config import (CONFIG, SCENARIOS, find_install_dir, scenario_dirs,
                       dlc_localization_dirs, enabled_mod_dirs)

_SCRIPTS_DIR = Path(__file__).resolve().parent

_INSTALL = find_install_dir()
_GAME_SA = (_INSTALL / "TerraInvicta_Data" / "StreamingAssets") if _INSTALL else None
DEFAULT_SRC_TEMPLATES = (_GAME_SA / "Templates") if _GAME_SA else None
DEFAULT_SRC_LOCAL = (_GAME_SA / "Localization" / "en") if _GAME_SA else None
DEFAULT_DST_TEMPLATES = _SCRIPTS_DIR / "templates"
DEFAULT_DST_LOCAL = _SCRIPTS_DIR / "localization"
LAYERS_FILE = "_LAYERS.json"

PREVIEW_LEN = 110
MAX_EXAMPLES = 12


def compact(v) -> str:
    s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    if len(s) > PREVIEW_LEN:
        s = s[: PREVIEW_LEN - 1] + "…"
    return s


_COMMENT_RE = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r',(\s*[}\]])')


def load_json(path: Path):
    """Parse a template JSON. TIGlobalConfig.json (base game AND the DLC scenario
    overlays) is JSON-with-comments (`// …` and `/* … */`) and may carry trailing
    commas — strip both, outside strings."""
    text = path.read_text(encoding="utf-8-sig")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = _COMMENT_RE.sub(lambda m: m.group(1) or "", text)
        text = _TRAILING_COMMA_RE.sub(r"\1", text)
        return json.loads(text)


def index_by_dataname(data):
    """Return ({dataName: entry}, ok) — ok False if not a list of dataName dicts."""
    if not isinstance(data, list) or not data or not all(
        isinstance(e, dict) and "dataName" in e for e in data
    ):
        return None, False
    idx = {}
    for e in data:
        idx[e["dataName"]] = e  # last-wins on duplicate dataNames
    return idx, True


# ---------------------------------------------------------------------------
# Layer merging
# ---------------------------------------------------------------------------

def merge_template(base, overlay, name: str, report: list) -> object:
    """Merge one overlay file onto the base data of the same name, game-style:
    per-dataName override/append for template lists; key-wise merge for
    TIGlobalConfig ([{…}] with a single config dict); overlay wins otherwise."""
    if base is None:
        return overlay
    if name.startswith("TIGlobalConfig"):
        # One dict (dataName "globalConfig") — overlays carry only the keys they change,
        # so merge KEY-wise; a per-dataName replace would drop every other constant.
        b = base[0] if isinstance(base, list) and base else (base if isinstance(base, dict) else {})
        o = overlay[0] if isinstance(overlay, list) and overlay else (overlay if isinstance(overlay, dict) else {})
        changed = [k for k in o if b.get(k) != o.get(k)]
        merged = dict(b)
        merged.update(o)
        report.append(f"{name}: {len(changed)} config keys overridden "
                      f"({', '.join(changed[:6])}{', …' if len(changed) > 6 else ''})")
        return [merged] if isinstance(base, list) else merged
    b_idx, b_ok = index_by_dataname(base)
    o_idx, o_ok = index_by_dataname(overlay)
    if b_ok and o_ok:
        merged = {k: v for k, v in b_idx.items()}
        n_over = sum(1 for k in o_idx if k in b_idx)
        merged.update(o_idx)
        report.append(f"{name}: {len(o_idx)} entries ({n_over} override, {len(o_idx) - n_over} new)")
        return list(merged.values())
    report.append(f"{name}: whole-file replace (not a per-dataName file)")
    return overlay


def collect_layers(src_templates: Path, src_local: Path | None, scenario: str | None,
                   use_mods: bool):
    """Ordered list of layers: dicts with kind/name/templates dir/localization dirs."""
    layers = [{"kind": "base", "name": "StreamingAssets", "templates": src_templates,
               "localization": [src_local] if src_local else []}]
    for loc in dlc_localization_dirs(_INSTALL):
        # DLC-wide strings (shared by all its scenarios) ride along with the base layer
        layers[0]["localization"].append(loc)
    if scenario:
        t, loc = scenario_dirs(scenario, _INSTALL)
        if t is None:
            sys.exit(f"ERROR: scenario {scenario!r} not found under {_INSTALL / 'DLC_Content' if _INSTALL else '<install>'} "
                     f"— is the DLC installed? Valid names: {', '.join(SCENARIOS)}")
        layers.append({"kind": "scenario", "name": scenario, "templates": t,
                       "localization": [loc] if loc else []})
    if use_mods:
        for mdir in enabled_mod_dirs(_INSTALL):
            if any(mdir.glob("TI*.json")) or any(mdir.glob("*.en")):
                layers.append({"kind": "mod", "name": mdir.name, "templates": mdir,
                               "localization": [mdir]})
    return layers


def build_merged_templates(layers):
    """{filename: merged data}, plus a provenance record per file."""
    merged, prov, notes = {}, {}, []
    for layer in layers:
        for src in sorted(layer["templates"].glob("TI*.json")):
            try:
                data = load_json(src)
            except Exception as ex:
                notes.append(f"  !! {layer['name']}/{src.name}: unparseable ({ex}) — skipped")
                continue
            if src.name in merged:
                rep = []
                merged[src.name] = merge_template(merged[src.name], data, src.name, rep)
                notes.append(f"  {layer['kind']:8s} {layer['name']}: " + "; ".join(rep))
            else:
                merged[src.name] = data
                if layer["kind"] != "base":
                    notes.append(f"  {layer['kind']:8s} {layer['name']}: {src.name}: new file")
            prov.setdefault(src.name, []).append(f"{layer['kind']}:{layer['name']}")
    return merged, prov, notes


def _en_lines(path: Path):
    return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()


def build_merged_localization(layers):
    """{filename: text}. Keyed lines (`KEY=value`) from later layers override earlier
    ones in place; new keys append; unkeyed lines are kept from the first layer."""
    merged, prov = {}, {}
    for layer in layers:
        for ldir in layer["localization"]:
            for src in sorted(ldir.glob("*.en")):
                lines = _en_lines(src)
                if src.name not in merged:
                    merged[src.name] = lines
                else:
                    cur = merged[src.name]
                    pos = {ln.partition("=")[0]: i for i, ln in enumerate(cur) if "=" in ln}
                    for ln in lines:
                        if "=" not in ln:
                            continue
                        k = ln.partition("=")[0]
                        if k in pos:
                            cur[pos[k]] = ln
                        else:
                            pos[k] = len(cur)
                            cur.append(ln)
                prov.setdefault(src.name, []).append(f"{layer['kind']}:{layer['name']}")
    return {k: "\n".join(v) + "\n" for k, v in merged.items()}, prov


# ---------------------------------------------------------------------------
# Drift reporting (merged install data vs the local mirror)
# ---------------------------------------------------------------------------

def diff_json_data(name: str, s_data, dst: Path) -> bool:
    """Print drift for one template file. Returns True if any content drift."""
    if not dst.exists():
        n = len(s_data) if isinstance(s_data, list) else 1
        print(f"  ++ {name}: NEW (not previously mirrored) — {n} entries")
        return True
    try:
        d_data = load_json(dst)
    except Exception as ex:
        print(f"  !! {name}: mirror copy unparseable ({ex}) — will overwrite")
        return True

    s_idx, s_ok = index_by_dataname(s_data)
    d_idx, d_ok = index_by_dataname(d_data)
    if not (s_ok and d_ok):
        same = json.dumps(s_data, sort_keys=True) == json.dumps(d_data, sort_keys=True)
        if same:
            return False
        print(f"  ~~ {name}: content differs (not a per-dataName file; whole-file compare)")
        return True

    added = [k for k in s_idx if k not in d_idx]
    removed = [k for k in d_idx if k not in s_idx]
    changed = []
    for k in s_idx:
        if k in d_idx and s_idx[k] != d_idx[k]:
            keys = sorted(set(s_idx[k]) | set(d_idx[k]))
            ck = [kk for kk in keys if s_idx[k].get(kk) != d_idx[k].get(kk)]
            changed.append((k, ck))

    if not (added or removed or changed):
        return False

    print(f"  ~~ {name}: {len(added)} added, {len(removed)} removed, {len(changed)} changed "
          f"(mirror {len(d_idx)} → install {len(s_idx)} entries)")
    for k in added[:MAX_EXAMPLES]:
        print(f"       + {k}")
    if len(added) > MAX_EXAMPLES:
        print(f"       + … and {len(added) - MAX_EXAMPLES} more")
    for k in removed[:MAX_EXAMPLES]:
        print(f"       - {k}")
    if len(removed) > MAX_EXAMPLES:
        print(f"       - … and {len(removed) - MAX_EXAMPLES} more")
    for k, ck in changed[:MAX_EXAMPLES]:
        print(f"       ~ {k}: keys changed: {', '.join(ck)}")
        for kk in ck[:4]:
            old = compact(d_idx[k].get(kk))
            new = compact(s_idx[k].get(kk))
            print(f"           {kk}: {old} → {new}")
    if len(changed) > MAX_EXAMPLES:
        print(f"       ~ … and {len(changed) - MAX_EXAMPLES} more changed entries")
    return True


def diff_en_text(name: str, s_text: str, dst: Path) -> bool:
    """Line-level summary for a .en localization file. Returns True on drift."""
    if not dst.exists():
        n = sum(1 for ln in s_text.splitlines() if "=" in ln)
        print(f"  ++ {name}: NEW (not previously mirrored) — ~{n} tagged lines")
        return True
    d_text = dst.read_text(encoding="utf-8-sig", errors="replace")
    if s_text == d_text:
        return False
    s_lines = s_text.splitlines()
    d_lines = d_text.splitlines()
    s_set, d_set = set(s_lines), set(d_lines)
    added = [ln for ln in s_lines if ln not in d_set]
    removed = [ln for ln in d_lines if ln not in s_set]
    print(f"  ~~ {name}: {len(added)} lines added/changed, {len(removed)} lines removed/changed "
          f"(mirror {len(d_lines)} → install {len(s_lines)} lines)")
    for ln in added[:5]:
        print(f"       + {ln[:PREVIEW_LEN]}")
    if len(added) > 5:
        print(f"       + … and {len(added) - 5} more")
    for ln in removed[:5]:
        print(f"       - {ln[:PREVIEW_LEN]}")
    if len(removed) > 5:
        print(f"       - … and {len(removed) - 5} more")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src-templates", type=Path, default=DEFAULT_SRC_TEMPLATES)
    ap.add_argument("--src-localization", type=Path, default=DEFAULT_SRC_LOCAL)
    ap.add_argument("--dst-templates", type=Path, default=DEFAULT_DST_TEMPLATES)
    ap.add_argument("--dst-localization", type=Path, default=DEFAULT_DST_LOCAL)
    ap.add_argument("--scenario", default=CONFIG["scenario"],
                    help=f"DLC scenario overlay ({', '.join(SCENARIOS)}); default from config.json")
    ap.add_argument("--no-mods", action="store_true",
                    help="ignore <install>/Mods/Enabled even if config 'mods' is set")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the drift report but write nothing")
    args = ap.parse_args(argv)
    if args.scenario in ("", "none", "null"):
        args.scenario = None

    for p, label in [(args.src_templates, "source templates"),
                     (args.src_localization, "source localization")]:
        if p is None:
            sys.exit(f"ERROR: could not auto-detect the game install ({label}). "
                     "Set \"game_install_dir\" in config.json or pass "
                     "--src-templates / --src-localization.")
        if not p.is_dir():
            sys.exit(f"ERROR: {label} dir not found: {p}")

    global _INSTALL
    if _INSTALL is None:
        # --src-templates given by hand: the install root is 3 levels up from
        # <install>/TerraInvicta_Data/StreamingAssets/Templates (DLC + mods hang off it).
        cand = args.src_templates.resolve().parent.parent.parent
        if (cand / "TerraInvicta_Data").is_dir():
            _INSTALL = cand
    layers = collect_layers(args.src_templates, args.src_localization, args.scenario,
                            use_mods=not args.no_mods and bool(CONFIG["mods"]))

    print("=" * 72)
    print("GAME-DATA LAYERS (later overrides earlier, per dataName)")
    for i, layer in enumerate(layers, 1):
        print(f"  {i}. {layer['kind']:8s} {layer['name']}  ← {layer['templates']}")
    if not args.scenario:
        print("  (no DLC scenario overlay — config \"scenario\" is null; the default 2026 start)")
    print("=" * 72)

    merged_t, prov_t, notes = build_merged_templates(layers)
    merged_l, prov_l = build_merged_localization(layers)
    if notes:
        print("\n--- Overlay merge ---")
        for n in notes:
            print(n)

    print(f"\n--- DRIFT: merged install data vs local mirror ({len(merged_t)} template files) ---")
    print(f"  mirror: {args.dst_templates}")
    t_drift = 0
    for name in sorted(merged_t):
        if diff_json_data(name, merged_t[name], args.dst_templates / name):
            t_drift += 1
    print(f"  => {t_drift} template file(s) with drift, "
          f"{len(merged_t) - t_drift} identical at content level.")

    if args.dst_templates.is_dir():
        orphans = sorted(set(p.name for p in args.dst_templates.glob("*.json"))
                         - set(merged_t) - {LAYERS_FILE})
        for o in orphans:
            print(f"  ?? {o}: present in local mirror but NOT in install (left untouched)")

    print(f"\n--- Localization ({len(merged_l)} .en files) ---")
    l_drift = 0
    for name in sorted(merged_l):
        if diff_en_text(name, merged_l[name], args.dst_localization / name):
            l_drift += 1
    print(f"  => {l_drift} .en file(s) with drift, "
          f"{len(merged_l) - l_drift} byte-identical.")
    if args.dst_localization.is_dir():
        orphans = sorted(set(p.name for p in args.dst_localization.glob("*.en"))
                         - set(merged_l))
        for o in orphans:
            print(f"  ?? {o}: present in local mirror but NOT in install (left untouched)")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    print("\n--- Writing merged mirror ---")
    args.dst_templates.mkdir(parents=True, exist_ok=True)
    args.dst_localization.mkdir(parents=True, exist_ok=True)
    for name, data in merged_t.items():
        (args.dst_templates / name).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, text in merged_l.items():
        (args.dst_localization / name).write_text(text, encoding="utf-8")
    provenance = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "install": str(_INSTALL) if _INSTALL else None,
        "scenario": args.scenario,
        "layers": [{"kind": l["kind"], "name": l["name"], "templates": str(l["templates"])}
                   for l in layers],
        "templates": prov_t,
        "localization": prov_l,
    }
    (args.dst_templates / LAYERS_FILE).write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(merged_t)} template JSONs and {len(merged_l)} .en files "
          f"({len(layers)} layers; provenance in {LAYERS_FILE}).")


if __name__ == "__main__":
    main()
