#!/usr/bin/env python3
"""
Terra Invicta save-file strategic-situation extractor.

Single-pass reader that produces a complete snapshot of a TI save:
research, faction comparison, hab module inventory (including the
critical built-vs-under-construction Operations Center count that
determines real MC capacity), fleet vs alien tonnage, nation/CP
control map, resource buffers, and faction-specific victory chain
status.

Usage:
    python extract_snapshot.py <save_file.json> [--faction <templateName>]
                                                [--output report.md]
                                                [--json]
                                                [--brief]

`--brief` skips the long completed-techs/projects lists so the report
fits in a single agent turn.

Faction template names (use with --faction):
    ResistCouncil       Resistance
    DestroyCouncil      Humanity First
    ExploitCouncil      Initiative
    SubmitCouncil       Servants
    AppeaseCouncil      Protectorate
    CooperateCouncil    Academy
    EscapeCouncil       Project Exodus
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from ti_config import newest_save, require_faction

# ---------------------------------------------------------------------------
# Optional game-template loader. If the user's TI install is reachable, we
# enrich the report with project friendly-names and drive specs (so we don't
# blindly recommend a drive that's worse than what they already have, or call
# `Project_GasCoreFissionReactorIV` "GCFR4" when the friendly name is
# "Terawatt Gas Core Fission Reactor I").
# ---------------------------------------------------------------------------

def find_templates_dir():
    # Single source of truth for game-data discovery: mirror-first, config-aware
    # (ti_config honors config.json game_install_dir and auto-detects installs).
    from ti_config import find_templates_dir as _ftd
    p = _ftd()
    return str(p) if p else None


def _safe_load_json(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:  # BOM: Windows saves
            return json.load(f)
    except Exception:
        return None


def load_game_templates():
    """Return dict with project_friendly, drive_specs, module_friendly,
    tech_templates. Empty dicts if templates are unreachable."""
    tdir = find_templates_dir()
    if not tdir:
        return {'project_friendly': {}, 'project_templates': {}, 'drive_specs': {},
                'module_friendly': {}, 'tech_templates': {}}

    projects = _safe_load_json(os.path.join(tdir, 'TIProjectTemplate.json')) or []
    drives = _safe_load_json(os.path.join(tdir, 'TIDriveTemplate.json')) or []
    modules = _safe_load_json(os.path.join(tdir, 'TIHabModuleTemplate.json')) or []
    techs = _safe_load_json(os.path.join(tdir, 'TITechTemplate.json')) or []
    tech_templates = {t['dataName']: t for t in techs if t.get('dataName')}
    project_templates = {p['dataName']: p for p in projects if p.get('dataName')}

    # Map data_name -> friendly_name for every project we can find
    project_friendly = {}
    for p in projects:
        dn = p.get('dataName')
        fn = p.get('friendlyName') or dn
        if dn:
            project_friendly[dn] = fn

    # TI's drive templates use internal enum names for `requiredPowerPlant`
    # that DON'T match the user-facing project names. Most painful: the project
    # `MoltenCoreFissionReactor` maps to enum `Liquid_Core_Fission`. Translate
    # internal → user-facing terminology so the analyzer's output matches what
    # the player sees in-game.
    REACTOR_CLASS_DISPLAY = {
        'Solid_Core_Fission':       'Solid Core Fission',
        'Liquid_Core_Fission':      'Molten Core Fission',    # the rename
        'Vapor_Core_Fission':       'Vapor Core Fission',
        'Gas_Core_Fission':         'Gas Core Fission',
        'Any_General':              'Any reactor',
        'Toroid_Magnetic_Confinement_Fusion': 'Toroid Magnetic Confinement Fusion',
        'Hybrid_Confinement_Fusion':           'Hybrid Confinement Fusion',
        'Z_Pinch_Fusion':                      'Z-Pinch Fusion',
        'Inertial_Confinement_Fusion':         'Inertial Confinement Fusion',
        'Antimatter_Plasma_Core':              'Antimatter Plasma Core',
    }
    # Drive friendlyName overrides — Pavonis sometimes renames drives in patches
    # without updating the templates the analyzer reads from. Map data_name
    # prefixes (before the `x1..x6` suffix) to the current in-game UI name.
    # User-confirmed renames (reference campaign, save version 1.0.32 mid-2026):
    #   NeutronFluxLantern    → "Poseidon Lantern"
    # Add more as the user surfaces them — when the user references a drive by
    # a name the script doesn't print, ASK before claiming it doesn't exist;
    # templates can be stale relative to the running game's UI/localization.
    DRIVE_FRIENDLY_OVERRIDE = {
        'NeutronFluxLantern': 'Poseidon Lantern',
    }
    # Map drive data_name -> (thrust_N, ev_kps, drive_class, project_required)
    drive_specs = {}
    for d in drives:
        dn = d.get('dataName')
        if not dn:
            continue
        cruise = d.get('thrust_N', 0) or 0
        cap = d.get('thrustCap', 1) or 1
        reactor_raw = d.get('requiredPowerPlant')
        # Resolve UI-display friendly name with override layer
        raw_friendly = d.get('friendlyName', dn)
        ui_friendly = raw_friendly
        for prefix, override in DRIVE_FRIENDLY_OVERRIDE.items():
            if dn.startswith(prefix):
                # Preserve the "x1".."x6" suffix on display
                suffix = dn[len(prefix):]
                ui_friendly = f'{override} {suffix}' if suffix else override
                break
        drive_specs[dn] = {
            'friendly': ui_friendly,
            'thrust_N': cruise,                  # cruise/sustained thrust
            'thrust_cap': cap,                   # combat multiplier
            'combat_thrust_N': cruise * cap,     # what matters for warships
            'ev_kps': d.get('EV_kps') or d.get('ev_kps') or 0,
            'class': d.get('thrustClass') or d.get('driveClassification') or '?',
            'required_project': d.get('requiredProjectName'),
            'required_reactor': reactor_raw,
            'required_reactor_display': REACTOR_CLASS_DISPLAY.get(reactor_raw, reactor_raw),
            'cooling': d.get('cooling') or '?',  # 'Closed' = no radiators needed, 'Calc' = needs radiators
            # The drive's required reactor power (GW). Drive template uses the
            # quirky key "req power" (with a literal space). Reactor needs to
            # supply at LEAST this much wattage. Excess wattage is wasted.
            # Some entries store the value as a string with a comma separator;
            # normalize to float here.
            'req_power_GW': (lambda v: float(str(v).replace(',', '')) if v else 0)(
                d.get('req power') or d.get('reqPower_GW') or 0
            ),
            'drive_classification': d.get('driveClassification') or '?',  # 'Fission_Thermal', etc — drive-family key
        }

    # Reactor (power plant) specs — needed to determine whether a reactor
    # refit actually benefits the drive on a given ship. Many "upgrades" are
    # actively HARMFUL: e.g. Gas Core III (3 t/GW) → Gas Core IV (10 t/GW)
    # makes Pharos hulls heavier without giving Pharos any usable wattage.
    plants = _safe_load_json(os.path.join(tdir, 'TIPowerPlantTemplate.json')) or []
    reactor_specs = {}
    for r in plants:
        dn = r.get('dataName')
        if not dn:
            continue
        reactor_specs[dn] = {
            'friendly': r.get('friendlyName', dn),
            'class': r.get('powerPlantClass') or '?',
            'maxOutput_GW': r.get('maxOutput_GW', 0) or 0,
            'specificPower_tGW': r.get('specificPower_tGW', 0) or 0,
            'efficiency': r.get('efficiency', 0) or 0,
            'crew': r.get('crew', 0) or 0,
        }

    module_friendly = {m.get('dataName'): m.get('friendlyName', m.get('dataName')) for m in modules}

    # Per-module research income (monthly) — used by extract_snapshot_income_breakdown.
    # The TIHabModuleState in saves only stores templateName; the actual income value
    # lives on the template. Critical join: state.templateName → template.incomeResearch_month.
    module_research_income = {
        m.get('dataName'): (m.get('incomeResearch_month', 0) or 0) for m in modules
    }

    return {
        'project_friendly': project_friendly,
        'project_templates': project_templates,
        'drive_specs': drive_specs,
        'reactor_specs': reactor_specs,
        'module_friendly': module_friendly,
        'module_research_income': module_research_income,
        'tech_templates': tech_templates,
    }


def fname(data_name, friendly_map):
    """Pretty-print a project name with both forms."""
    if not data_name:
        return '?'
    label = friendly_map.get(data_name)
    if label and label != data_name:
        return f"{label} (`{data_name}`)"
    return f"`{data_name}`"


def make_json_safe(obj):
    """Recursively normalize save/analyzer structures into JSON-safe shapes.

    `json.dumps(default=...)` only handles values, not invalid mapping keys. Some
    analyzer sections intentionally use tuple keys internally (for example
    hab inventory by `(type, tier)`), so normalize keys before serialization.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, tuple):
                key = " | ".join(str(part) for part in k)
            elif isinstance(k, (str, int, float, bool)) or k is None:
                key = k
            else:
                key = str(k)
            out[key] = make_json_safe(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [make_json_safe(v) for v in obj]
    if isinstance(obj, (Counter, defaultdict)):
        return make_json_safe(dict(obj))
    if isinstance(obj, set):
        return sorted(make_json_safe(v) for v in obj)
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)


# ---------------------------------------------------------------------------
# Reference data baked into the script. Update if Pavonis changes templates.
# ---------------------------------------------------------------------------

FACTION_DISPLAY = {
    'ResistCouncil':     'Resistance',
    'DestroyCouncil':    'Humanity First',
    'ExploitCouncil':    'Initiative',
    'SubmitCouncil':     'Servants',
    'AppeaseCouncil':    'Protectorate',
    'CooperateCouncil':  'Academy',
    'EscapeCouncil':     'Project Exodus',
    'AlienCouncil':      'Aliens (Hydra)',
}

# Faction-specific victory chain (project templateName -> human label).
# Order = research order. Player wins by completing the last entry.
VICTORY_CHAINS = {
    'ResistCouncil': [
        ('Project_ResistVictory',     'ResistVictory (kickoff)'),
        ('Project_Wormholes',         'Wormholes'),
        ('Project_TheirPurpose',      "Their Purpose"),
        ('Project_TheChokePoint',     'The Choke Point'),
        ('Project_TheFinalAssault',   'The Final Assault (WIN)'),
    ],
    'DestroyCouncil': [
        ('Project_HFVictory',         'HF Victory (kickoff)'),
        ('Project_Wormholes',         'Wormholes'),
        ('Project_TheirPurpose',      "Their Purpose"),
        ('Project_TheChokePoint',     'The Choke Point'),
        ('Project_KillTheHive',       'Kill the Hive (WIN)'),
    ],
    'ExploitCouncil': [
        ('Project_InitiativeVictory',  'Initiative Victory (kickoff)'),
        ('Project_Wormholes',          'Wormholes'),
        ('Project_TheirPurpose',       "Their Purpose"),
        ('Project_TheChokePoint',      'The Choke Point'),
        ('Project_EnslaveTheMasters',  'Enslave the Masters (WIN)'),
    ],
    'CooperateCouncil': [
        ('Project_AcademyVictory',     'Academy Victory (kickoff)'),
        ('Project_Wormholes',          'Wormholes'),
        ('Project_TheirPurpose',       "Their Purpose"),
        ('Project_TheChokePoint',      'The Choke Point'),
        ('Project_APermanentPeace',    'A Permanent Peace (WIN)'),
    ],
    'EscapeCouncil': [
        ('Project_ExodusVictory',      'Exodus Victory (kickoff)'),
        # Exodus path is build-out, not Choke Point. Tracked via FactionGoals,
        # but list its known terminal project here.
        ('Project_TheExodusFleet',     'The Exodus Fleet (WIN)'),
    ],
    'AppeaseCouncil': [
        ('Project_ProtectorateVictory',     'Protectorate Victory (kickoff)'),
        ('Project_ProtectorateAlliance',    'Protectorate Alliance (WIN)'),
    ],
    'SubmitCouncil': [
        ('Project_ServantVictory',         'Servant Victory (kickoff)'),
        ('Project_ServantAlliance',        'Servant Alliance (WIN)'),
    ],
}

# MC and Earth-CP-cap module contributions (from TIHabModuleTemplate.json,
# fields `missionControl` and `controlPointCapacity`). Two SEPARATE caps —
# both shown in the top resource bar as "X/Y" counters, often confused:
#  - **MC** (blue sphere icon) — limits habs/ships you can support
#  - **Earth CP cap** (red shield icon) — limits how many Earth political CPs
#    your faction can maintain; CP modules only count when in Earth LEO
#    (`LEOControlPointCapacity` specialRule).
# AdministrationNode gives +4 Earth-CP-cap but 0 MC.

# In-game MC tooltip formula (verified 2026-05-16):
#   available = baseIncomes_year.MissionControl  (faction HQ, usually 22)
#             + Σ org.incomeMissionControl over orgs assigned to player councilors  (councilors)
#             + Σ region.missionControl over regions in faction's majority nations  (nations)
#             + Σ module.missionControl over built modules where >0  (habs)
#   required  = Σ ship.missionControlConsumption  (ships)
#             + Σ |module.missionControl| over built modules where <0  (habs — cores, labs, etc.)
#             + mining-network maintenance cost  (`SpaceMineNetworkConsumption` proxy)

MODULE_MC = {
    # name:                  (+MC,  +CP_cap, project_for_unlock,           upgrade_path)
    'AdministrationNode':    (0,    4,       'Project_AdministrationNode',     None),
    'AdministrationTower':   (1,    12,      'Project_AdministrationTower',    'AdministrationNode'),
    'AdministrationComplex': (2,    30,      'Project_AdministrationComplex',  'AdministrationTower'),
    'OperationsCenter':      (4,    0,       'Project_OperationsCenter',       None),
    'CommandCenter':         (10,   0,       'Project_CommandCenter',          'OperationsCenter'),
    # Modules that CONSUME MC. Negative values.
    'OutpostCore':           (-2,   0,       None, None),
    'PlatformCore':          (-2,   0,       None, None),
    'AutomatedOutpostCore':  (-1,   0,       None, None),
    'AutomatedPlatformCore': (-1,   0,       None, None),
    'SettlementCore':        (-3,   0,       None, None),
    'OrbitalCore':           (-3,   0,       None, None),
    'ColonyCore':            (-4,   0,       None, None),
    'RingCore':              (-4,   0,       None, None),
    'ResearchCampus':        (-1,   0,       None, None),
    'ResearchUniversity':    (-2,   0,       None, None),
    'Helium-3Mine':          (-3,   0,       None, None),
    'SentinelComplex':       (-1,   0,       None, None),
    'InterstellarLaunchingLaser': (-20, 0,   None, None),
    # AlienWormholeFacility (+100 MC) intentionally omitted — alien-tech only.
}

# Schematic templates that meaningfully change a hab's role.
SCHEMATIC_TEMPLATES = {
    'ResearchHabSchematic':       'Research',
    'OperationsHabSchematic':     'Operations (MC)',
    'ShipbuildingHabSchematic':   'Shipbuilding',
    'MiningHabSchematic':         'Mining',
    'EconomyHabSchematic':        'Economy',
    'XenologyHabSchematic':       'Xenology',
    'MilitaryHabSchematic':       'Military',
    'PrestigeHabSchematic':       'Prestige',
}

# Drive tiers (rough — for tonnage/ship-tier reporting).
DRIVE_TIERS = [
    ('Chemical',  ['Liquid-FuelRockets', 'SuperheavyRockets', 'ReusableRockets']),
    ('Electric',  ['IonDrive', 'GridDrive', 'HeliconDrive', 'VASIMR', 'LaserEngine']),
    ('Fission',   ['NervaDrive', 'CermetNerva', 'BurnerDrive', 'FissionSpinnerDrive',
                   'FissionFragDrive', 'Dumbo', 'AdvancedNervaDrive']),
    ('GasCore',   ['LightbulbDrive', 'PharosDrive', 'QuartzDrive', 'PulsarDrive',
                   'TeardropDrive', 'PegasusDrive', 'LarsDrive', 'AdvancedPulsarDrive']),
    ('Fusion',    ['PebbleDrive', 'PlasmaWaveDrive', 'PulsedPlasmoidDrive',
                   'PonderomotiveVASIMR', 'SnareDrive', 'KiwiDrive', 'LorentzDrive',
                   'E-BeamDrive', 'AmplitronDrive', 'ColloidDrive']),
    ('Antimatter', ['AntimatterDrive', 'BeamCoreDrive']),
]
DRIVE_TO_TIER = {d: tier for tier, drives in DRIVE_TIERS for d in drives}


# ---------------------------------------------------------------------------
# Save-file helpers
# ---------------------------------------------------------------------------

def load_save(path):
    """Load a TI save file. Supports both raw .json saves AND gzip-compressed
    .gz saves (Terra Invicta uses gzip for older saves in the user's save
    directory; the file extension and the gzip magic bytes both indicate it).
    """
    import gzip
    path = str(path)
    if path.endswith('.gz'):
        with gzip.open(path, 'rb') as f:
            return json.loads(f.read())
    # Sniff magic bytes: 0x1f 0x8b = gzip even if not .gz extension
    with open(path, 'rb') as f:
        head = f.read(2)
    if head == b'\x1f\x8b':
        with gzip.open(path, 'rb') as f:
            return json.loads(f.read())
    with open(path, 'r', encoding='utf-8-sig') as f:  # BOM: Windows saves
        return json.load(f)


def kv_items(gs, state_key):
    """All entries in a state, returning (id, value) tuples. Handles both
    bare-dict and Key/Value-wrapped forms."""
    out = []
    for e in gs.get(state_key, []):
        if 'Key' in e and 'Value' in e:
            k = e['Key']
            kid = k.get('value') if isinstance(k, dict) else k
            out.append((kid, e['Value']))
        else:
            out.append((e.get('ID', {}).get('value'), e))
    return out


def deref(v):
    """Unwrap {value: N} references."""
    if isinstance(v, dict):
        return v.get('value')
    return v


def fac_id_from_field(v):
    """Pull a faction id from a field that may be a dict or scalar."""
    if isinstance(v, dict):
        return v.get('value')
    return v


def get_factions(gs):
    """Return {faction_id: faction_value_dict, ...}."""
    out = {}
    for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState'):
        out[fid] = fv
    return out


def faction_id_by_template(factions, template):
    for fid, fv in factions.items():
        if fv.get('templateName') == template:
            return fid
    return None


def nation_label(template_name):
    """Short display label for a nation templateName: '2026_USA' → 'USA'.
    (No localization file for nations is mirrored, so the templateName stem is
    the stable, rename-proof label — displayName changes on federation.)"""
    if not template_name:
        return '?'
    head, _, tail = template_name.partition('_')
    return tail if (tail and head.isdigit()) else template_name


def resolve_anchor_nations(gs, faction_template=None, cli_value=None):
    """Resolve the campaign's anchor nations for trend tables (Tables E/H).

    Precedence: explicit CLI --nations value > config.json "anchor_nations" >
    auto-detect. Auto-detect = top 3 nations by GDP among nations where the
    player's faction holds at least one control point; if the faction holds
    none, fall back to top-3 global GDP. Nations are identified by templateName
    (never displayName — that renames on federation).

    Returns a list of nation templateNames.
    """
    if cli_value:
        return [s.strip() for s in cli_value.split(',') if s.strip()]
    from ti_config import CONFIG
    if CONFIG.get('anchor_nations'):
        return list(CONFIG['anchor_nations'])
    nations = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TINationState'))
    controlled = set()
    if faction_template:
        fid = faction_id_by_template(get_factions(gs), faction_template)
        if fid is not None:
            for _, cp in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIControlPoint'):
                if fac_id_from_field(cp.get('faction')) == fid:
                    nid = deref(cp.get('nation'))
                    if nid is not None and nid in nations:
                        controlled.add(nid)
    pool = [nv for nid, nv in nations.items() if nid in controlled] \
        or list(nations.values())
    pool.sort(key=lambda nv: -(nv.get('GDP') or 0))
    return [nv.get('templateName') for nv in pool[:3] if nv.get('templateName')]


def get_game_date(gs):
    ts_list = gs.get('PavonisInteractive.TerraInvicta.TITimeState', [])
    if not ts_list:
        return '?', None
    v = ts_list[0].get('Value', ts_list[0])
    cd = v.get('currentDateTime') or v.get('currentDate') or {}
    y, m, d = cd.get('year', '?'), cd.get('month', '?'), cd.get('day', '?')
    try:
        return f"{y:04d}-{int(m):02d}-{int(d):02d}", v
    except (TypeError, ValueError):
        return f"{y}-{m}-{d}", v


def get_global_state(gs):
    grs = gs.get('PavonisInteractive.TerraInvicta.TIGlobalResearchState', [])
    gvs = gs.get('PavonisInteractive.TerraInvicta.TIGlobalValuesState', [])
    return (grs[0].get('Value', grs[0]) if grs else None,
            gvs[0].get('Value', gvs[0]) if gvs else None)


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------

def extract_faction_comparison(factions):
    """One-line summary per faction for the comparison table."""
    rows = []
    for fid, fv in factions.items():
        rev = fv.get('cachedYearlyRevenue', {}) or {}
        res = fv.get('resources', {}) or {}
        rows.append({
            'id': fid,
            'template': fv.get('templateName', '?'),
            'name': FACTION_DISPLAY.get(fv.get('templateName', '?'), fv.get('templateName', '?')),
            'research_yr': rev.get('Research', 0),
            'money_yr': rev.get('Money', 0),
            'influence_yr': rev.get('Influence', 0),
            'ops_yr': rev.get('Operations', 0),
            'boost_yr': rev.get('Boost', 0),
            'mc_yr': rev.get('MissionControl', 0),
            'money_stock': res.get('Money', 0),
            'metals_stock': res.get('Metals', 0),
            'water_stock': res.get('Water', 0),
            'fissiles_stock': res.get('Fissiles', 0),
            'exotics_stock': res.get('Exotics', 0),
            'mc_usage': fv.get('missionControlUsage', 0),
        })
    return rows


def load_core_module_tiers():
    """dataName -> tier for every hab CORE module, read from the templates.

    Hardcoding core names goes stale the moment a tier is added or renamed; the
    fallback below is the vanilla ladder (build ~1.0.49) for repos that haven't
    run sync_game_data.py yet."""
    try:
        from ti_config import find_templates_dir
        tdir = find_templates_dir()
        if tdir:
            import os as _os
            data = json.load(open(_os.path.join(str(tdir), 'TIHabModuleTemplate.json'),
                                  encoding='utf-8'))
            tiers = {m['dataName']: m.get('tier', 1) for m in data
                     if m.get('dataName', '').endswith('Core')}
            if tiers:
                return tiers
    except Exception:
        pass
    return {'OutpostCore': 1, 'AutomatedOutpostCore': 1, 'PlatformCore': 1,
            'AutomatedPlatformCore': 1, 'AlienOutpostCore': 1, 'AlienPlatformCore': 1,
            'SettlementCore': 2, 'OrbitalCore': 2, 'AlienSettlementCore': 2,
            'AlienOrbitalCore': 2, 'ColonyCore': 3, 'RingCore': 3,
            'AlienColonyCore': 3, 'AlienRingCore': 3}


def extract_hab_inventory(gs, faction_id):
    """For the given faction: hab counts by type/tier/location/schematic, and
    a module-level breakdown (built vs under construction).

    Returns dict with:
        habs: list of per-hab dicts
        modules_built: Counter of templateName
        modules_underway: Counter of templateName
        ops_centers_built / ops_centers_underway
        schematic_counts: Counter
        habs_by_body: Counter
    """
    space_bodies = {bid: bv.get('displayName') or bv.get('templateName') or '?'
                    for bid, bv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceBodyState')}
    hab_sites = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabSiteState'))
    sectors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISectorState'))

    # Map sector -> hab id
    sector_to_hab = {sid: deref(sv.get('hab')) for sid, sv in sectors.items()}

    # Faction hab list with metadata
    faction_hab_ids = set()
    habs_out = []
    for hid, hv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'):
        if fac_id_from_field(hv.get('faction')) != faction_id:
            continue
        faction_hab_ids.add(hid)
        site_id = deref(hv.get('habSite'))
        site = hab_sites.get(site_id, {})
        body_id = deref(site.get('parentBody')) if site else None
        habs_out.append({
            'id': hid,
            'name': hv.get('displayName') or hv.get('templateName') or '?',
            'type': hv.get('habType'),
            'tier': hv.get('tier'),
            'body': space_bodies.get(body_id, '(orbital)') if body_id else '(orbital)',
            'schematic': hv.get('habSchematicTemplateName'),
            'inEarthLEO': hv.get('inEarthLEO'),
        })

    # Module breakdown
    modules_built = Counter()
    modules_powered = Counter()   # built AND powered — the only ones producing/consuming MC
    modules_underway = Counter()
    for mid, mv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'):
        sid = deref(mv.get('sector'))
        hid = sector_to_hab.get(sid)
        if hid not in faction_hab_ids:
            continue
        name = mv.get('templateName') or '(empty/refit)'
        if mv.get('constructionCompleted'):
            modules_built[name] += 1
            if mv.get('powered'):
                modules_powered[name] += 1
        else:
            modules_underway[name] += 1

    schematics = Counter(h['schematic'] for h in habs_out if h['schematic'])
    by_body = Counter(h['body'] for h in habs_out)
    by_type_tier = Counter((h['type'], h['tier']) for h in habs_out)

    # Per-hab presence flags for the MC + Earth-CP-cap modules. Track separately
    # whether the hab has one *built* vs *built-or-pending*.
    hab_has = {h['id']: {'mc_built': False, 'mc_pending': False,
                          'admin_built': False, 'admin_pending': False}
               for h in habs_out}
    for mid, mv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'):
        sid = deref(mv.get('sector'))
        hid = sector_to_hab.get(sid)
        if hid not in faction_hab_ids: continue
        name = mv.get('templateName') or ''
        done = bool(mv.get('constructionCompleted'))
        if name in ('OperationsCenter', 'CommandCenter'):
            hab_has[hid]['mc_pending'] = True
            if done: hab_has[hid]['mc_built'] = True
        if name in ('AdministrationNode', 'AdministrationTower', 'AdministrationComplex'):
            hab_has[hid]['admin_pending'] = True
            if done: hab_has[hid]['admin_built'] = True
    habs_missing_mc_module_built = sum(1 for d in hab_has.values() if not d['mc_built'])
    habs_missing_mc_module_total = sum(1 for d in hab_has.values() if not d['mc_pending'])
    habs_missing_admin_module_built = sum(1 for d in hab_has.values() if not d['admin_built'])
    habs_missing_admin_module_total = sum(1 for d in hab_has.values() if not d['admin_pending'])

    # List of habs entirely missing an OperationsCenter (not built AND not pending) —
    # these are the actionable targets for "go build OpsCenters". OperationsCenter
    # requires T2 hab (Settlement or Orbital core); T1 bases need upgrade first.
    # Metal cost varies wildly by body — Earth LEO 75, Ganymede 185, Io 375, etc.
    # We use an approximate per-body multiplier to rank by cost (cheapest first).

    # Body → approximate metal cost for OperationsCenter (1000 t module, 75% metals).
    # Values calibrated against user-reported in-game values:
    #   Earth LEO 75, Ganymede 185, Io 375.
    # Other bodies estimated from gravity-well/distance pattern; user should
    # cross-check by hovering over the build button in-game for ground truth.
    BODY_METAL_COST = {
        '(orbital)': 75,        # Earth LEO assumed if inEarthLEO is true
        'Luna': 75, 'Mars': 110, 'Mercury': 110, 'Ceres': 110,
        'Deimos': 90, 'Phobos': 90, 'Linus': 110,
        'Ganymede': 185, 'Callisto': 185, 'Europa': 185, 'Io': 375,
        'Titan': 220, 'Enceladus': 220, 'Rhea': 220, 'Iapetus': 220,
        'Triton': 280, 'Oberon': 280, 'Titania': 280, 'Umbriel': 280,
        'Miranda': 280, 'Ariel': 280,
    }
    def estimate_metal_cost(h):
        if h.get('inEarthLEO'):
            return 75
        return BODY_METAL_COST.get(h['body'], 130)  # asteroid default ~130

    # Get site-level mining info (so we can flag "this T1 has zero local metals"
    # type insights when discussing upgrades).
    site_mining = {}  # hab_id -> {water_day, metals_day, ...}
    for hid, hv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'):
        if hid not in faction_hab_ids:
            continue
        site_id = deref(hv.get('habSite'))
        site = hab_sites.get(site_id, {})
        site_mining[hid] = {
            'metals_day': site.get('metals_day', 0) or 0,
            'water_day': site.get('water_day', 0) or 0,
            'volatiles_day': site.get('volatiles_day', 0) or 0,
            'nobles_day': site.get('nobles_day', 0) or 0,
            'fissiles_day': site.get('fissiles_day', 0) or 0,
        }

    # For each player hab, find pending core modules. This tells us whether
    # a T1 hab is "still being founded" (initial PlatformCore/OutpostCore
    # under construction with no prior) or "T1→T2 upgrade in progress"
    # (SettlementCore/OrbitalCore being built with prior=OutpostCore/PlatformCore).
    # Cuts spurious "this T1 needs upgrade" recommendations when an upgrade
    # is already queued and finishing soon.
    # Core names/tiers come from the TEMPLATES, not a hardcoded pair of sets: the
    # old T1/T2-only lists missed ColonyCore and RingCore, so a hab with a TIER-3
    # core in flight was classified 'stable' and re-recommended for the upgrade it
    # was already doing — the exact E6 mistake this block exists to prevent
    # (caught 2033-11-01: all three "stable T1" habs had a ColonyCore building).
    CORE_TIERS = load_core_module_tiers()
    T1_CORES = {n for n, t in CORE_TIERS.items() if t == 1}
    UPGRADE_CORES = {n for n, t in CORE_TIERS.items() if t >= 2}
    pending_core_by_hab = {}  # hab_id -> {'status': 'founding'|'upgrading', 'target': name, 'completion': iso}
    for mid, mv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'):
        if mv.get('constructionCompleted'):
            continue
        name = mv.get('templateName') or ''
        if name not in CORE_TIERS:
            continue
        sid = deref(mv.get('sector'))
        hid = sector_to_hab.get(sid)
        if hid not in faction_hab_ids:
            continue
        prior = mv.get('priorModuleTemplateName') or ''
        completion = mv.get('completionDate', '')
        if name in UPGRADE_CORES and prior:
            status = 'upgrading'  # tier upgrade in progress (T1→T2, T1→T3, T2→T3)
        elif name in T1_CORES and not prior:
            status = 'founding'   # Initial T1 construction
        elif name in UPGRADE_CORES and not prior:
            status = 'founding-t2'  # Initial T2+ construction (direct higher-tier build)
        else:
            status = 'other'
        pending_core_by_hab[hid] = {
            'status': status,
            'target': name,
            'prior': prior,
            'completion': completion,
        }

    hab_by_id = {h['id']: h for h in habs_out}
    habs_missing_mc_t2 = []  # eligible for OpsCenter now
    habs_missing_mc_t1 = []  # need T1→T2 upgrade first
    for hid, state in hab_has.items():
        if state['mc_pending']:  # already has OpsCenter or one under construction
            continue
        h = hab_by_id[hid]
        pcore = pending_core_by_hab.get(hid)
        entry = {
            'name': h['name'],
            'type': h['type'],
            'tier': h['tier'],
            'body': h['body'],
            'inEarthLEO': h.get('inEarthLEO', False),
            'estimated_metal_cost': estimate_metal_cost(h),
            'metals_day': site_mining.get(hid, {}).get('metals_day', 0),
            'pending_core_status': pcore['status'] if pcore else 'stable',
            'pending_core_target': pcore['target'] if pcore else None,
            'pending_core_prior': pcore['prior'] if pcore else None,
            'pending_core_completion': pcore['completion'] if pcore else None,
        }
        if h['tier'] == 1:
            habs_missing_mc_t1.append(entry)
        else:
            habs_missing_mc_t2.append(entry)

    # T2: sort by metal cost ascending (cheapest first)
    habs_missing_mc_t2.sort(key=lambda h: (h['estimated_metal_cost'], h['name']))
    # T1: sort by local metals_day descending (best upgrade candidates first)
    habs_missing_mc_t1.sort(key=lambda h: -h['metals_day'])

    # Combined list (for back-compat)
    habs_missing_mc_list = habs_missing_mc_t2 + habs_missing_mc_t1

    # MC contribution split: producers (positive missionControl) vs consumers (negative).
    # Producers show up as "MC from habs" in-game; consumers as "MC for habs".
    # ONLY POWERED modules produce/consume MC — an unpowered Admin Tower / Ops Center
    # gives nothing. Using modules_built here over-counted hab-produced MC by exactly the
    # unpowered-producer total (2033-03-01: +30 from 30 unpowered AdminTowers + 8 from 2
    # unpowered OpsCenters = +38, which reproduced the game's 352 vs the old 390). Fixed
    # 2026-07-04 to use modules_powered. See docs/lessons/LESSONS-economy.md E25.
    mc_produced_built = sum(modules_powered.get(n, 0) * spec[0]
                            for n, spec in MODULE_MC.items() if spec[0] > 0)
    mc_consumed_built = sum(modules_powered.get(n, 0) * -spec[0]
                            for n, spec in MODULE_MC.items() if spec[0] < 0)
    mc_net_pending = sum(modules_underway.get(n, 0) * spec[0] for n, spec in MODULE_MC.items())
    # CP cap contribution (LEO only — we don't filter by orbit here, treat as
    # upper bound; tag the gap in advice for the operator to verify).
    station_cp_cap_max = sum(modules_built.get(n, 0) * spec[1] for n, spec in MODULE_MC.items())

    # Upgrade gaps: count of each module that could be upgraded one tier.
    upgrade_gain = {}
    for new_name, (new_mc, new_cp, _, upgrades_from) in MODULE_MC.items():
        if not upgrades_from: continue
        base = MODULE_MC[upgrades_from]
        delta_mc = new_mc - base[0]
        delta_cp = new_cp - base[1]
        count = modules_built.get(upgrades_from, 0)
        upgrade_gain[f"{upgrades_from} → {new_name}"] = {
            'count_upgradable': count,
            'delta_mc_each': delta_mc,
            'delta_cp_cap_each': delta_cp,
            'total_mc_gain': count * delta_mc,
            'total_cp_cap_gain': count * delta_cp,
            'requires_project': MODULE_MC[new_name][2],
        }

    return {
        'habs': habs_out,
        'modules_built': modules_built,
        'modules_underway': modules_underway,
        'ops_centers_built': modules_built.get('OperationsCenter', 0),
        'ops_centers_underway': modules_underway.get('OperationsCenter', 0),
        'command_centers_built': modules_built.get('CommandCenter', 0),
        'admin_nodes_built': modules_built.get('AdministrationNode', 0),
        'admin_towers_built': modules_built.get('AdministrationTower', 0),
        'admin_complexes_built': modules_built.get('AdministrationComplex', 0),
        'mc_produced_built': mc_produced_built,
        'mc_consumed_built': mc_consumed_built,
        'mc_net_pending': mc_net_pending,
        'station_cp_cap_max': station_cp_cap_max,
        'habs_missing_mc_module_built': habs_missing_mc_module_built,
        'habs_missing_mc_module_total': habs_missing_mc_module_total,
        'habs_missing_admin_module_built': habs_missing_admin_module_built,
        'habs_missing_admin_module_total': habs_missing_admin_module_total,
        'habs_missing_mc_list': habs_missing_mc_list,
        'habs_missing_mc_t2': habs_missing_mc_t2,
        'habs_missing_mc_t1': habs_missing_mc_t1,
        'upgrade_gain': upgrade_gain,
        'schematics': schematics,
        'habs_by_body': by_body,
        'habs_by_type_tier': by_type_tier,
        'habs_without_schematic': sum(1 for h in habs_out if not h['schematic']),
    }


def extract_fleet_inventory(gs, factions):
    """Per-faction fleet stats: ship counts, mass, delta-V, weapons.
    Returns {faction_template: {...}}."""
    fleet_to_fac = {}
    for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceFleetState'):
        fac_id = fac_id_from_field(fv.get('faction'))
        fac_tpl = factions.get(fac_id, {}).get('templateName', '?')
        fleet_to_fac[fid] = fac_tpl

    stats = defaultdict(lambda: {
        'ships': 0,
        'total_mass_kg': 0,
        'capital_count': 0,    # >15kt
        'large_count': 0,      # 5-15kt
        'medium_count': 0,     # 2-5kt
        'small_count': 0,      # <2kt
        'dv_kps': [],
        'combat_accel': [],
        'nose_weapons': Counter(),
        'hull_weapons': Counter(),
        'modules': Counter(),
        # Weapon class roll-up: lasers / missiles / coilguns / railguns / lances /
        # particle / PD lasers / point-defense kinetic. Counted by family substring
        # match on moduleTemplateName because TI uses verbose names like
        # `UV_NLaser_DH_120cm_GreenArc` (laser) vs `CopperheadMissileBay` (missile).
        # See the ship-designs lesson — the reference campaign had ~25+ ships with
        # lasers (reference-campaign battleships and battlecruisers), so a
        # "no laser weapons in fleet" claim was nonsense.
        'weapon_class_counts': Counter(),
    })

    def classify_weapon(name):
        """Return a weapon class tag for grouping. None if not recognized.

        Tested against the 2032-05-01 save's actual moduleTemplateNames:
        - `720cmGreenArcLaserCannon` (laser, large)
        - `PointDefenseArcLaserTurret` (PD laser)
        - `IonCannon`, `IonBattery`, `LightIonBattery` (ion = particle beam family)
        - `PointDefenseIonBattery` (PD ion)
        - `CopperheadMissileBay`, `ApolloTorpedoBay` (missiles)
        - `AlienLightParticleCannon`, `Alien256cmVioletLaserCannon` (alien variants)
        """
        if not name:
            return None
        n = name.lower()
        # Order matters — most specific first
        if 'missile' in n or 'torpedo' in n:
            return 'Missiles/Torpedoes'
        # Point defense — must come before generic laser/ion checks
        if 'pointdefense' in n or n.startswith('pd_') or 'pdlaser' in n:
            return 'Point Defense'
        if 'coilgun' in n:
            return 'Coilguns'
        if 'railgun' in n:
            return 'Railguns'
        # Particle / ion family — Ion* and *Particle* both fall here
        if 'particle' in n or 'ion' in n:
            return 'Particle/Ion'
        if 'lance' in n:
            return 'Lances'
        # Laser family — any cannon/battery with "Laser" in the name (Arc, Green,
        # Violet, UV, IR etc. are sub-variants). Catch laser-cannons and the
        # less common laser-batteries.
        if 'laser' in n:
            return 'Lasers'
        if 'magcannon' in n or 'maggun' in n:
            return 'Mag Cannons'
        if 'plasma' in n:
            return 'Plasma'
        return 'Other'

    for sid, sv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceShipState'):
        fleet = deref(sv.get('fleet'))
        fac_tpl = fleet_to_fac.get(fleet, '?')
        s = stats[fac_tpl]
        s['ships'] += 1
        mass = sv.get('currentMass_kg', 0) or 0
        s['total_mass_kg'] += mass
        t = mass / 1000  # tonnes
        if t >= 15000:    s['capital_count'] += 1
        elif t >= 5000:   s['large_count'] += 1
        elif t >= 2000:   s['medium_count'] += 1
        else:             s['small_count'] += 1
        s['dv_kps'].append(sv.get('currentMaxDeltaV_kps', 0) or 0)
        s['combat_accel'].append(sv.get('combatAcceleration_mps2', 0) or 0)
        for m in sv.get('noseWeapons', []) or []:
            n = m.get('moduleTemplateName') if isinstance(m, dict) else None
            if n:
                s['nose_weapons'][n] += 1
                cls = classify_weapon(n)
                if cls: s['weapon_class_counts'][cls] += 1
        for m in sv.get('hullWeapons', []) or []:
            n = m.get('moduleTemplateName') if isinstance(m, dict) else None
            if n:
                s['hull_weapons'][n] += 1
                cls = classify_weapon(n)
                if cls: s['weapon_class_counts'][cls] += 1
        for m in sv.get('utilityModules', []) or []:
            n = m.get('moduleTemplateName') if isinstance(m, dict) else None
            if n: s['modules'][n] += 1
    return dict(stats)


# Per-control-point cost against the Earth CP cap — the tooltip's "Each Cost Against Cap".
# Decompiled `TINationState.ControlPointMaintenanceCost` (build 1.0.39 DLL):
#   perCP = (GDP/1e9) ** controlPointCostScaling / (2 * nation.numControlPoints)
#   → 0 for the alien nation, and 0 for any CP whose `benefitsDisabled` is set (crackdown).
# controlPointCostScaling = 0.6 — the constant lives in TIGlobalConfig (not mirrored into the
# save/templates), calibrated against the in-game CP tooltip across 4 reference-save nations
# and reproducing the save's Capacity Used to the decimal.
# numControlPoints is the NATION's total CP slots (clamped to 6), NOT the count a
# faction holds — so each of a nation's CPs costs the same, and holding k of n costs k×perCP.
CONTROL_POINT_COST_SCALING = 0.6


def cp_cost_against_cap(nation_value):
    """Per-CP economic weight against the Earth CP cap for one nation (see constant above).
    Caller must still zero out crackdown-suppressed CPs (`benefitsDisabled`) individually."""
    if not isinstance(nation_value, dict):
        return 0.0
    gdp = nation_value.get('GDP', 0) or 0
    if gdp <= 0 or nation_value.get('alienNation'):
        return 0.0
    n = min(nation_value.get('numControlPoints')
            or nation_value.get('numControlPoints_unclamped')
            or len(nation_value.get('controlPoints', []) or []) or 1, 6)
    return (gdp / 1e9) ** CONTROL_POINT_COST_SCALING / (2 * n)


def extract_control_points(gs, factions):
    """Returns nation control map, per-faction CP totals, CP MC-priority
    breakdown, and Earth-side MC totals per faction-of-majority.

    NOTE on MC-priority: setting MC priority on a CP only meaningfully shifts
    a nation's policy mix when the faction holds majority in that nation.
    Minority/coup-setup CPs in nations dominated by rivals don't benefit from
    MC priority. We compute two separate "zero MC priority" tallies — one
    over all CPs (raw), and one filtered to majority-held nations only
    (actionable). DO NOT use the raw number as a lever recommendation.
    """
    nations = {nid: nv for nid, nv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TINationState')}
    nation_name = {nid: nv.get('displayName') or nv.get('templateName', '?')
                   for nid, nv in nations.items()}
    cps = kv_items(gs, 'PavonisInteractive.TerraInvicta.TIControlPoint')

    # First pass: tally CPs by nation and faction
    by_faction = Counter()            # SEAT headcount per faction
    cp_load_by_faction = Counter()    # CP LOAD (economic weight against cap) per faction
    nation_cp = defaultdict(Counter)
    cp_type_by_faction = defaultdict(Counter)
    cp_priority_totals_by_fac = defaultdict(Counter)
    for cid, cv in cps:
        fid = fac_id_from_field(cv.get('faction'))
        fac_tpl = factions.get(fid, {}).get('templateName') if fid else None
        by_faction[fac_tpl or '(unowned)'] += 1
        nat = deref(cv.get('nation'))
        nation_cp[nat][fac_tpl or None] += 1
        if fac_tpl:
            cp_type_by_faction[fac_tpl][cv.get('controlPointType', '?')] += 1
            # CP LOAD: this CP's cost against the cap (0 while crackdown-suppressed)
            if not cv.get('benefitsDisabled'):
                cp_load_by_faction[fac_tpl] += cp_cost_against_cap(nations.get(nat))
            pri = cv.get('controlPointPriorities', {}) or {}
            for k, v in pri.items():
                if v > 0:
                    cp_priority_totals_by_fac[fac_tpl][k] += v

    # Determine nation-of-majority assignments first (needed to filter MC priority)
    majority = defaultdict(list)
    nation_top = {}
    for nat, fids in nation_cp.items():
        filled = sum(c for fid, c in fids.items() if fid)
        if filled == 0:
            continue
        owners = [(fid, c) for fid, c in fids.items() if fid]
        owners.sort(key=lambda x: -x[1])
        top_fac, top_count = owners[0]
        if top_count > filled / 2:
            nation_top[nat] = top_fac  # template name
            majority[top_fac].append({
                'nation': nation_name.get(nat, f'nation{nat}'),
                'held': top_count,
                'filled': filled,
            })

    # Second pass: MC priority by faction, partitioned by "in majority nation" vs "minority"
    cp_mc_priority = defaultdict(lambda: {
        'total_weight': 0,
        'cps': 0,
        'majority_cps': 0,
        'majority_total_weight': 0,
        'majority_zero_mc': 0,
        'majority_zero_mc_examples': [],  # nation/type/priorities for first 5
        'minority_cps': 0,
        'minority_total_weight': 0,
    })
    for cid, cv in cps:
        fid = fac_id_from_field(cv.get('faction'))
        fac_tpl = factions.get(fid, {}).get('templateName') if fid else None
        if not fac_tpl:
            continue
        pri = cv.get('controlPointPriorities', {}) or {}
        mc_w = pri.get('MissionControl', 0) or 0
        nat = deref(cv.get('nation'))
        in_majority = nation_top.get(nat) == fac_tpl
        rec = cp_mc_priority[fac_tpl]
        rec['cps'] += 1
        rec['total_weight'] += mc_w
        if in_majority:
            rec['majority_cps'] += 1
            rec['majority_total_weight'] += mc_w
            if mc_w == 0:
                rec['majority_zero_mc'] += 1
                if len(rec['majority_zero_mc_examples']) < 8:
                    active = {k: v for k, v in pri.items() if v > 0}
                    rec['majority_zero_mc_examples'].append({
                        'nation': nation_name.get(nat, f'nation{nat}'),
                        'type': cv.get('controlPointType', '?'),
                        'active_priorities': active,
                    })
        else:
            rec['minority_cps'] += 1
            rec['minority_total_weight'] += mc_w

    # Earth-side MC by faction-of-majority — sum each region's missionControl
    # field for regions whose nation is held by that faction.
    regions = kv_items(gs, 'PavonisInteractive.TerraInvicta.TIRegionState')
    earth_mc_by_faction = Counter()
    earth_mc_unowned = 0
    top_regions = []
    for rid, rv in regions:
        mc = rv.get('missionControl', 0) or 0
        if mc <= 0: continue
        nat = deref(rv.get('nation'))
        owner = nation_top.get(nat)
        if owner:
            earth_mc_by_faction[owner] += mc
        else:
            earth_mc_unowned += mc
        top_regions.append({
            'nation': nation_name.get(nat, f'nation{nat}'),
            'mc': mc,
            'owner': owner,
        })
    top_regions.sort(key=lambda x: -x['mc'])

    return {
        'total_cps': len(cps),
        'by_faction': by_faction,
        'cp_load_by_faction': cp_load_by_faction,
        'cp_type_by_faction': cp_type_by_faction,
        'majority': majority,
        'cp_mc_priority': dict(cp_mc_priority),
        'cp_priority_totals_by_fac': dict(cp_priority_totals_by_fac),
        'earth_mc_by_faction': earth_mc_by_faction,
        'earth_mc_unowned': earth_mc_unowned,
        'top_mc_regions': top_regions[:25],
    }


def extract_org_marketplace(gs, faction_id):
    """The orgs actually offered to the player right now, from
    `faction.availableOrgs`. NOT every unassigned org in the game — those
    aren't necessarily purchasable (eligibility rules: requiresNationality,
    requiredOwnerTraits, etc., are pre-filtered by the game into this list).
    """
    fs = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
               if fid == faction_id), {})
    avail_refs = fs.get('availableOrgs', []) or []
    all_orgs = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIOrgState'))
    out = []
    for r in avail_refs:
        oid = deref(r)
        o = all_orgs.get(oid)
        if not o: continue
        stats = {k: o.get(k, 0) or 0 for k in
                 ['persuasion', 'command', 'investigation', 'espionage',
                  'administration', 'science', 'security']}
        nonzero_stats = {k: v for k, v in stats.items() if v}
        out.append({
            'id': oid,
            'name': o.get('displayNameWithArticle') or o.get('templateName', '?'),
            'tier': o.get('tier'),
            'stats': nonzero_stats,
            'stat_sum': sum(nonzero_stats.values()),
            'cost_money': o.get('costMoney', 0) or 0,
            'cost_influence': o.get('costInfluence', 0) or 0,
            'cost_ops': o.get('costOps', 0) or 0,
            'cost_boost': o.get('costBoost', 0) or 0,
            'income_money_m': o.get('incomeMoney_month', 0) or 0,
            'income_inf_m': o.get('incomeInfluence_month', 0) or 0,
            'income_ops_m': o.get('incomeOps_month', 0) or 0,
            'income_mc': o.get('incomeMissionControl', 0) or 0,
            'income_research_m': o.get('incomeResearch_month', 0) or 0,
            'requires_nationality': o.get('requiresNationality'),
            'required_traits': o.get('requiredOwnerTraits', []),
        })
    out.sort(key=lambda x: -x['stat_sum'])
    return out


def extract_councilor_roster(gs, faction_id):
    """Compute the actual in-game 'core' stats for each councilor by applying
    trait statMods to the save's `attributes` field.

    Key correction (vs earlier buggy version): `attributes` in the save is
    NOT "final stats including orgs". It's base + augmentations + XP, WITHOUT
    trait statMods. Orgs apparently never add to the displayed `attributes`
    field — they contribute at mission time only. So:
        UI core stats = attributes + sum(applicable trait statMods)

    Conditional trait mods (ElderStatesman, Ethical, Streetwise, Addict's
    money-conditional, etc.) are evaluated against the councilor's current
    location nation and faction resource state.

    Also reads the player's actual recruit pool from
    `faction.availableCouncilors` (NOT every unfactioned councilor — that's
    the global pool, not what's offered for hire).
    """
    tdir = find_templates_dir()
    trait_by_name = {}
    if tdir:
        traits_tmpl = _safe_load_json(os.path.join(tdir, 'TITraitTemplate.json')) or []
        trait_by_name = {t['dataName']: t for t in traits_tmpl}

    councilors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TICouncilorState'))
    nations = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TINationState'))
    regions = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIRegionState'))
    orgs = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIOrgState'))
    fs_player = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
                      if fid == faction_id), {})
    money = (fs_player.get('resources', {}) or {}).get('Money', 0) or 0

    STATS = ['Persuasion', 'Investigation', 'Espionage', 'Command',
             'Administration', 'Science', 'Security']

    def region_nation(rid):
        r = regions.get(rid, {})
        nat_id = deref(r.get('nation')) if r else None
        return nations.get(nat_id, {})

    def apply_trait_mods(c):
        attrs = c.get('attributes', {}) or {}
        core = {s: attrs.get(s, 0) for s in STATS}
        loc_id = deref(c.get('location'))
        nation = region_nation(loc_id)
        home_id = deref(c.get('homeRegion'))
        in_home = (loc_id == home_id) if home_id else False
        applied_conditionals = []
        for tn in (c.get('traitTemplateNames') or []):
            t = trait_by_name.get(tn)
            if not t:
                continue
            for m in (t.get('statMods') or []):
                if not isinstance(m, dict) or not m:
                    continue
                stat = m.get('stat')
                if stat not in STATS:
                    continue
                try:
                    val = int(m.get('strValue', '0'))
                except (TypeError, ValueError):
                    continue
                cond = m.get('condition')
                apply_it = True
                if cond:
                    ct = cond.get('$type', '')
                    cs = cond.get('sign', '')
                    try:
                        cv = float(cond.get('strValue', '0'))
                    except (TypeError, ValueError):
                        cv = 0
                    str_v = (cond.get('strValue') or '').upper()
                    if 'fDemocracy' in ct:
                        v = nation.get('democracy', 0) or 0
                        apply_it = (v >= cv) if 'Greater' in cs else (v <= cv)
                    elif 'fInequality' in ct:
                        v = nation.get('inequality', 0) or 0
                        apply_it = (v <= cv) if 'Less' in cs else (v >= cv)
                    elif 'bInHomeNation' in ct:
                        apply_it = in_home if str_v == 'TRUE' else (not in_home)
                    elif 'efResourceValue' in ct:
                        res_name = cond.get('strIdx', 'Money')
                        rv = (fs_player.get('resources', {}) or {}).get(res_name, 0) or 0
                        apply_it = (rv <= cv) if 'Less' in cs else (rv >= cv)
                    else:
                        # Unknown condition — apply optimistically
                        apply_it = True
                    if apply_it:
                        applied_conditionals.append(f"{tn}({stat}{val:+d})")
                if apply_it:
                    core[stat] = core.get(stat, 0) + val
        return core, applied_conditionals

    def render_councilor(cid, c):
        a = c.get('attributes', {}) or {}
        core, applied_cond = apply_trait_mods(c)
        # Sum org-provided stats (informational only — orgs don't add to display
        # stats, but they contribute at mission time)
        org_sum = {s: 0 for s in STATS}
        for oref in (c.get('orgs') or []):
            oid = deref(oref)
            o = orgs.get(oid)
            if not o: continue
            for s in STATS:
                v = o.get(s.lower(), 0) or 0
                if v: org_sum[s] += v
        # Mission tracking: `priorMissionTemplateName` is the last mission the
        # councilor ran (or is currently running, since priorMission gets set
        # when a mission is selected). `priorMissionTarget` is the target ref.
        # Critical for understanding nation-IP oscillation: Advise Nation gives
        # a temporary IP bonus that toggles on/off with the mission's ~15-day
        # cycle + cooldown gap.
        prior_mission = c.get('priorMissionTemplateName') or None
        pmt = c.get('priorMissionTarget', {}) or {}
        prior_mission_target_id = deref(pmt) if pmt else None
        prior_mission_target_type = pmt.get('$type', '') if isinstance(pmt, dict) else ''
        advise_target_name = None
        if prior_mission == 'Advise' and 'TINationState' in prior_mission_target_type:
            target_n = nations.get(prior_mission_target_id, {})
            advise_target_name = (target_n.get('displayName')
                                   or target_n.get('templateName')
                                   or f'nation_id {prior_mission_target_id}')
        # Display label for the mission column
        mission_display = '—'
        if prior_mission:
            if advise_target_name:
                mission_display = f'**Advise {advise_target_name}**'
            else:
                mission_display = f'{prior_mission} (target {prior_mission_target_id})' if prior_mission_target_id else prior_mission
        return {
            'id': cid,
            'name': f"{c.get('personalName','?')} {c.get('familyName','?')}",
            'type': c.get('typeTemplateName', '?'),
            'xp': c.get('XP', 0),
            'loyalty': a.get('Loyalty', 0),
            'apparent_loyalty': a.get('ApparentLoyalty', 0),
            'attributes_save': {s: a.get(s, 0) for s in STATS},
            'core_stats': core,
            'core_stats_total': sum(core.values()),
            'traits': c.get('traitTemplateNames', []) or [],
            'conditional_mods_applied': applied_cond,
            'orgs': [deref(o) for o in (c.get('orgs') or [])],
            'org_stat_sum': org_sum,
            'prior_mission': prior_mission,
            'prior_mission_target_id': prior_mission_target_id,
            'advise_target_name': advise_target_name,
            'mission_display': mission_display,
        }

    # Player councilors
    player_councilors = []
    for cid, c in councilors.items():
        if fac_id_from_field(c.get('faction')) == faction_id:
            player_councilors.append(render_councilor(cid, c))

    # Actual recruit pool from faction.availableCouncilors
    recruit_ids = [deref(r) for r in (fs_player.get('availableCouncilors') or [])]
    recruit_pool = []
    for rid in recruit_ids:
        c = councilors.get(rid)
        if c:
            recruit_pool.append(render_councilor(rid, c))

    # Enemy councilors with DANGEROUS-TO-KILL traits.
    # Martyr trait: specialTraitRule 'GlobalPropagandaIfKilled', value 35 — when
    # killed, fires a global opinion shift toward the victim's faction's ideology.
    # Empirically: killing a Martyr-trait Protectorate councilor caused +27pp Appease
    # ideology in player's 3 majority nations on 2031-10-20. Other Martyr traits
    # exist; the analyzer should flag any enemy councilor with these so the user
    # doesn't accidentally Assassinate them.
    DANGEROUS_TO_KILL_TRAITS = {
        'Martyr': 'GlobalPropagandaIfKilled (+~25-30pp opinion swing toward their ideology in major nations)',
        # If more such traits surface, add them here. They have
        # specialTraitRule in the trait template that activates on death.
    }
    enemy_dangerous_councilors = []
    for cid, c in councilors.items():
        # Skip player + dead/archived councilors
        cfid = fac_id_from_field(c.get('faction'))
        if cfid == faction_id:
            continue
        if c.get('dead') or c.get('archived'):
            continue
        c_traits = c.get('traitTemplateNames', []) or []
        dangerous = [t for t in c_traits if t in DANGEROUS_TO_KILL_TRAITS]
        if dangerous:
            # Find faction name
            fac_data = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState') if fid == cfid), None)
            fac_tpl = fac_data.get('templateName') if fac_data else '?'
            fac_name = (fac_data.get('displayName') if fac_data else None) or fac_tpl
            enemy_dangerous_councilors.append({
                'id': cid,
                'name': f"{c.get('personalName','?')} {c.get('familyName','?')}",
                'type': c.get('typeTemplateName', '?'),
                'faction': fac_name,
                'faction_template': fac_tpl,
                'dangerous_traits': dangerous,
                'effects': [DANGEROUS_TO_KILL_TRAITS[t] for t in dangerous],
                'all_traits': c_traits,
            })

    return {
        'player_councilors': player_councilors,
        'recruit_pool': recruit_pool,
        'recruit_pool_size': len(recruit_pool),
        'enemy_dangerous_councilors': enemy_dangerous_councilors,
    }


def extract_mine_inventory(gs, faction_id):
    """Score every faction mining module by per-day site yields, weighted
    by which resources the player is most constrained on. Splits mines into
    active / unpowered / under-construction and ranks each list by score.

    Used to advise: which active mines to power down (when MC-tight), which
    powered-down mines to keep off, and which under-construction mines to
    cancel (zero or near-zero metals on a metals-constrained run).

    Returns dict with 'active', 'off', 'building' lists, each sorted by score
    descending. Each entry includes hab name, body, module template, and the
    site's per-day yields (water/volatiles/metals/nobles/fissiles).
    """
    # Default resource value weights. Calibrated for late-game where every
    # resource matters but metals/nobles are most universally consumed.
    # WATER & VOLATILES are weighted as heavily as nobles because they are
    # PROPELLANT + universal build input, NOT inert stockpile:
    #   - water is reaction mass for NSWR/NTR drives (Neutron Flux Lantern,
    #     Poseidon Lantern, etc.) and is consumed in BURSTS when ships refuel
    #     or new hulls are built — burn the static yearly-income denominator
    #     does NOT capture. Days-cover from cachedYearlyRevenue therefore
    #     OVERSTATES water runway. (Hard lesson 2032-11: advised cutting water
    #     mines at "16d cover" and the player ran dry.)
    #   - volatiles are ~10% of nearly every build cost.
    # Under-weighting these is how a metals-focused shutoff plan silently
    # drains the propellant economy. See days_cover_multiplier + PROTECT_COVER.
    DEFAULT_WEIGHTS = {
        'metals': 8.0,
        'nobles': 5.0,
        'fissiles': 3.0,
        'water': 5.0,
        'volatiles': 4.0,
    }
    # Below this many days of cover, a resource is "scarce" and its producing
    # mines are PROTECTED from shutoff recommendations. Propellant/consumables
    # (water, volatiles) get a HIGHER guard than the cover number suggests,
    # because real consumption is bursty and runs ahead of static yearly income.
    PROTECT_COVER = {
        'metals': 15.0, 'nobles': 10.0, 'fissiles': 15.0,
        'water': 45.0, 'volatiles': 30.0,
    }
    # Adaptive multiplier based on days of stockpile cover — graduated rather
    # than a hard 2× / 0.5× threshold. A resource with <5 days of cover is
    # near-depletion (4×, must dominate); >365 days is genuinely plentiful.
    def days_cover_multiplier(days):
        if days < 5:     return 4.0
        elif days < 10:  return 2.5
        elif days < 30:  return 1.7
        elif days < 90:  return 1.0
        elif days < 180: return 0.7
        elif days < 365: return 0.5
        else:            return 0.35

    fs = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
               if fid == faction_id), {})
    res = fs.get('resources', {}) or {}
    rev = fs.get('cachedYearlyRevenue', {}) or {}
    weights = dict(DEFAULT_WEIGHTS)
    cover_map = {
        'metals': 'Metals', 'nobles': 'NobleMetals', 'fissiles': 'Fissiles',
        'water': 'Water', 'volatiles': 'Volatiles',
    }
    days_cover = {}
    for key, save_key in cover_map.items():
        stock = res.get(save_key, 0) or 0
        yearly = rev.get(save_key, 0) or 0
        days = stock / (yearly / 365.25) if yearly > 0 else 9999
        days_cover[key] = days
        weights[key] *= days_cover_multiplier(days)

    # Which resources are currently scarce enough that we must NOT recommend
    # turning off mines that produce them (protect the propellant/metals base).
    scarce = {k for k in cover_map if days_cover.get(k, 9999) < PROTECT_COVER[k]}

    def mine_protection(yields):
        """Return (protected: bool, reasons: list[str]). A mine is protected
        from shutoff if it is a MEANINGFUL producer (>= 0.5/day) of any
        currently-scarce resource — cutting it would deepen a depletion crisis."""
        reasons = []
        for k in cover_map:
            if k in scarce and (yields.get(k, 0) or 0) >= 0.5:
                reasons.append(f"{k} {days_cover[k]:.0f}d cover (+{yields[k]:.2f}/d)")
        return (len(reasons) > 0, reasons)

    # Locate Resistance mine modules
    tdir = find_templates_dir()
    mod_tmpl = _safe_load_json(os.path.join(tdir, 'TIHabModuleTemplate.json')) if tdir else []
    mine_module_names = {m['dataName'] for m in mod_tmpl if m.get('mine')}
    if not mine_module_names:
        # Fallback hardcoded list (without templates)
        mine_module_names = {'OutpostMiningComplex', 'AutomatedMiningComplex',
                             'SettlementMiningComplex', 'ColonyMiningComplex'}

    sectors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISectorState'))
    sector_to_hab = {sid: deref(sv.get('hab')) for sid, sv in sectors.items()}
    habs = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'))
    sites = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabSiteState'))
    space_bodies = {bid: bv.get('displayName') or bv.get('templateName') or '?'
                    for bid, bv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceBodyState')}

    by_status = defaultdict(list)
    for mid, mv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'):
        sid = deref(mv.get('sector'))
        hid = sector_to_hab.get(sid)
        if hid not in habs: continue
        hv = habs[hid]
        if fac_id_from_field(hv.get('faction')) != faction_id: continue
        name = mv.get('templateName') or ''
        if name not in mine_module_names: continue
        site_id = deref(hv.get('habSite'))
        site = sites.get(site_id, {})
        body_id = deref(site.get('parentBody'))
        body = space_bodies.get(body_id, '?') if body_id else '(orbital)'
        metals = site.get('metals_day', 0) or 0
        water = site.get('water_day', 0) or 0
        vol = site.get('volatiles_day', 0) or 0
        nobles = site.get('nobles_day', 0) or 0
        fissiles = site.get('fissiles_day', 0) or 0
        score = (metals * weights['metals']
                 + nobles * weights['nobles']
                 + fissiles * weights['fissiles']
                 + water * weights['water']
                 + vol * weights['volatiles'])
        built = bool(mv.get('constructionCompleted'))
        powered = bool(mv.get('powered'))
        status = 'active' if (built and powered) else 'off' if built else 'building'
        yields = {'metals': metals, 'water': water, 'volatiles': vol,
                  'nobles': nobles, 'fissiles': fissiles}
        protected, protect_reasons = mine_protection(yields)
        by_status[status].append({
            'hab': hv.get('displayName') or hv.get('templateName') or '?',
            'body': body,
            'module': name,
            'metals': metals, 'water': water, 'volatiles': vol,
            'nobles': nobles, 'fissiles': fissiles,
            'score': score,
            'protected': protected,
            'protect_reasons': protect_reasons,
        })
    for status in by_status:
        by_status[status].sort(key=lambda m: -m['score'])

    return {
        'weights_used': weights,
        'days_cover': days_cover,
        'scarce': sorted(scarce),
        'protect_cover': PROTECT_COVER,
        'active': by_status['active'],
        'off': by_status['off'],
        'building': by_status['building'],
    }


def extract_mc_latent_demand(gs, faction_id):
    """Surfaces hidden MC pressure the user is managing manually:
      - mines shut down to save MC (will spike network cost when re-enabled)
      - idle shipyards (each new ship adds 1-3 MC consumption)
    The "MC slack" number in isolation is misleading when these are sitting
    behind the scenes.

    Returns dict with mine counts (active/shut-down/under-construction) and
    shipyard counts (active vs idle, queues vs empty).
    """
    # All "mine" modules per the template
    mine_module_names = {
        'OutpostMiningComplex', 'AutomatedMiningComplex',
        'SettlementMiningComplex', 'ColonyMiningComplex',
    }
    shipyard_module_names = {'Shipyard', 'SpaceDock'}

    habs = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'))
    sectors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISectorState'))
    sector_to_hab = {sid: deref(sv.get('hab')) for sid, sv in sectors.items()}
    faction_hab_ids = {hid for hid, hv in habs.items()
                       if fac_id_from_field(hv.get('faction')) == faction_id}

    mines_active = Counter()
    mines_unpowered = Counter()
    mines_underway = Counter()
    shipyards_active = 0
    shipyards_unpowered = 0
    shipyards_underway = 0
    for mid, mv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'):
        sid = deref(mv.get('sector'))
        hid = sector_to_hab.get(sid)
        if hid not in faction_hab_ids: continue
        name = mv.get('templateName') or ''
        done = bool(mv.get('constructionCompleted'))
        powered = bool(mv.get('powered'))
        if name in mine_module_names:
            if not done: mines_underway[name] += 1
            elif powered: mines_active[name] += 1
            else: mines_unpowered[name] += 1
        elif name in shipyard_module_names:
            if not done: shipyards_underway += 1
            elif powered: shipyards_active += 1
            else: shipyards_unpowered += 1

    # Shipyard queues: faction has nShipyardQueues, each {Key:yard, Value:queued ships}
    fs = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
               if fid == faction_id), {})
    yards = fs.get('nShipyardQueues', []) or []
    empty_yards = sum(1 for q in yards if not (q.get('Value') or []))
    queued_ships = sum(len(q.get('Value') or []) for q in yards)

    n_active = sum(mines_active.values())
    n_unpowered = sum(mines_unpowered.values())
    n_underway = sum(mines_underway.values())

    # Mining-network MC cost is QUADRATIC (decompiled TIFactionState.cs:3369,
    # GetMissionControlRequirementFromMineNetwork):
    #   n = networkSize - SafeMineNetworkSize ; cost = max(0, n*n // 2)
    # SafeMineNetworkSize = spaceMineFreebies (config) + Σ MCFreeSpaceMineNetwork
    # effects (the Space Mine Freebies tech line). The MARGINAL mine ≈ n MC, NOT a
    # flat ~4 — at n=16 (e.g. 46 active − 30 free) turning off one mine frees 16 MC.
    # Verified 2032-06-16: n=16, cost=128, observed per-mine saving 16. The old
    # "*4 linear" estimate under-counted ~4× and badly under-stated the cost of
    # under-construction mines.
    free_mines = 0
    es_list = gs.get('PavonisInteractive.TerraInvicta.TIEffectsState', [])
    if es_list:
        es = es_list[0].get('Value', {})
        for entry in es.get('factionEffectsNames', []) or []:
            if (entry.get('Key') or {}).get('value') != faction_id:
                continue
            for eff in (entry.get('Value', {}).get('MCFreeSpaceMineNetwork') or []):
                free_mines += int(''.join(c for c in eff if c.isdigit()) or 0)

    def mine_network_cost(network_size):
        n = network_size - free_mines
        return max(0, n * n // 2) if n > 0 else 0

    base_cost = mine_network_cost(n_active)

    return {
        'mines_active': dict(mines_active),
        'mines_unpowered': dict(mines_unpowered),
        'mines_underway': dict(mines_underway),
        'mine_count_active': n_active,
        'mine_count_unpowered': n_unpowered,
        'mine_count_underway': n_underway,
        'mine_free_count': free_mines,
        'mine_network_cost': base_cost,                              # current cost(n_active)
        'mine_mc_per_next': mine_network_cost(n_active + 1) - base_cost,   # marginal +1
        'mine_mc_per_off': base_cost - mine_network_cost(n_active - 1),    # saving −1
        'mine_mc_if_unpowered_resume': mine_network_cost(n_active + n_unpowered) - base_cost,
        'mine_mc_if_underway_complete': mine_network_cost(n_active + n_underway) - base_cost,
        'mine_mc_if_all_run': mine_network_cost(n_active + n_unpowered + n_underway) - base_cost,
        'shipyards_active': shipyards_active,
        'shipyards_unpowered': shipyards_unpowered,
        'shipyards_underway': shipyards_underway,
        'shipyard_queues': len(yards),
        'shipyard_queues_empty': empty_yards,
        'shipyard_queues_with_work': len(yards) - empty_yards,
        'ships_queued_total': queued_ships,
    }


def extract_cp_cap(gs, faction_id, factions):
    """Earth political CP-cap analysis. Surfaces the inputs to the in-game
    CP-cap calculation; the exact formula isn't fully recoverable from save
    fields, but the inputs we can see explain ~most of the cap."""
    # CP count by type for this faction + accurate CP-cap LOAD (Capacity Used)
    nations = {nid: nv for nid, nv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TINationState')}
    type_counts = Counter()
    cp_load = 0.0                 # Σ per-CP cost against cap (matches the tooltip's Capacity Used)
    cp_load_crackdown_excluded = 0
    for cid, cv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIControlPoint'):
        fid = fac_id_from_field(cv.get('faction'))
        if fid == faction_id:
            type_counts[cv.get('controlPointType', '?')] += 1
            if cv.get('benefitsDisabled'):
                cp_load_crackdown_excluded += 1
            else:
                cp_load += cp_cost_against_cap(nations.get(deref(cv.get('nation'))))
    total_cps = sum(type_counts.values())

    # ControlPointMaintenanceBonus effects assigned to faction
    effs_state = gs.get('PavonisInteractive.TerraInvicta.TIEffectsState', [])
    bonus_effects = Counter()
    if effs_state:
        for fe in effs_state[0].get('Value', {}).get('factionEffectsNames', []):
            if (fe.get('Key', {}).get('value')) != faction_id:
                continue
            for e in fe.get('Value', {}).get('ControlPointMaintenance', []) or []:
                bonus_effects[e] += 1
    # Value each effect from the template (ControlPointMaintenance context, Additive:
    # NEGATIVE value = cap bonus, e.g. Effect_ControlPointMaintenanceBonus40 = -40, the
    # 2003 scenario's Effect_BS_CPMaintenancePenalty = +40). Falling back to the digits
    # in the name is wrong in both directions: `…Bonus160` is actually -120 and the
    # un-numbered `…Penalty` / `…MakingHistoryA` effects (+40 / -60) read as 0.
    effect_values = {}
    tdir = find_templates_dir()
    if tdir:
        for e in _safe_load_json(os.path.join(tdir, 'TIEffectTemplate.json')) or []:
            if 'ControlPointMaintenance' in (e.get('contexts') or []):
                effect_values[e.get('dataName')] = (e.get('operation', 'Additive'), e.get('value', 0) or 0)
    bonus_total = 0
    for effect_name, c in bonus_effects.items():
        if effect_name in effect_values:
            op, v = effect_values[effect_name]
            if op == 'Additive':
                bonus_total += -v * c
            continue
        m = ''.join(ch for ch in effect_name if ch.isdigit())   # template missing: old heuristic
        if m:
            bonus_total += int(m) * c

    # Admin modules in LEO (controlPointCapacity contribution)
    habs = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'))
    sectors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISectorState'))
    sector_to_hab = {sid: deref(sv.get('hab')) for sid, sv in sectors.items()}
    in_leo_habs = {hid for hid, hv in habs.items()
                   if fac_id_from_field(hv.get('faction')) == faction_id and hv.get('inEarthLEO')}
    admin_cp_cap_leo = 0
    admin_modules_in_leo = Counter()
    for mid, mv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'):
        if not mv.get('constructionCompleted'): continue
        sid = deref(mv.get('sector'))
        hid = sector_to_hab.get(sid)
        if hid not in in_leo_habs: continue
        name = mv.get('templateName') or ''
        spec = MODULE_MC.get(name)
        if spec and spec[1] > 0:
            admin_cp_cap_leo += spec[1]
            admin_modules_in_leo[name] += 1

    # Global freebies (visible in TIGlobalValuesState)
    gvs = gs.get('PavonisInteractive.TerraInvicta.TIGlobalValuesState', [])
    freebies = (gvs[0].get('Value', {}) if gvs else {}).get('controlPointMaintenanceFreebies', 0) or 0

    return {
        'total_cps': total_cps,
        'cps_by_type': type_counts,
        'maintenance_bonus_total': bonus_total,
        'maintenance_bonus_breakdown': bonus_effects,
        'admin_cp_cap_leo': admin_cp_cap_leo,
        'admin_modules_in_leo': admin_modules_in_leo,
        'in_leo_hab_count': len(in_leo_habs),
        'freebies': freebies,
        # Accurate CP-cap LOAD (the tooltip's "Capacity Used") from the decompiled per-CP formula
        # — reproduces the in-game number to the decimal. This REPLACES the old flat ~32/CP guess.
        'cp_load': cp_load,
        'cp_load_crackdown_excluded': cp_load_crackdown_excluded,
    }


def extract_nation_mc_share(gs, faction_id):
    """Nation-side Mission Control the faction actually receives — the tooltip's
    "nations" line. Each nation's regional `missionControl` total is split ACROSS its
    control points: every CP gets `total // numControlPoints`, and the remainder goes
    to the highest-positioned CPs (`positionInNation >= numCP - remainder`). Crackdown
    CPs (`benefitsDisabled`) get nothing. Summing a majority nation's whole total
    (the old "majority-of-filled-CPs" shortcut) over-reads for every nation you don't
    hold outright — a 3/4-CP Russia read 8 instead of 6, and a 1/3-CP Poland 4 instead
    of 1 (save-verified 2006-07: this split gives 22 = the in-game 27 − HQ 2 − orgs 3;
    the shortcut gave 30)."""
    nations = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TINationState'))
    regions = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIRegionState'))
    nation_mc_total = {}
    total = 0
    for cid, cp in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIControlPoint'):
        if fac_id_from_field(cp.get('faction')) != faction_id or cp.get('benefitsDisabled'):
            continue
        nid = deref(cp.get('nation'))
        n = nations.get(nid) or {}
        if nid not in nation_mc_total:
            nation_mc_total[nid] = sum((regions.get(deref(r)) or {}).get('missionControl', 0) or 0
                                       for r in (n.get('regions') or []))
        cur = nation_mc_total[nid]
        ncp = max(n.get('numControlPoints') or 1, 1)
        share = cur // ncp
        if (cp.get('positionInNation') or 0) >= ncp - (cur % ncp):
            share += 1
        total += share
    return total


def extract_mc_full(gs, faction_id, hab_inv, factions, mining_cost=None):
    """Compute the four-source MC tooltip formula and three-consumer
    requirements, matching the in-game UI:
        available = HQ + councilors + nations + habs(positive)
        required  = ships + habs(consume) + mining
    Plus the pending net gain from in-flight modules.

    CALIBRATION vs the in-game tooltip (2033-01-20 screenshot, ground truth):
      | component      | game | this recon | delta |
      | HQ             |  22  |  22        |  ✓    |
      | councilors     |  11  |  11        |  ✓    |
      | nations avail  | 324  | 307        | −17   |  region.missionControl sum reads slightly low
      | habs produced  | 408  | 445        | +37   |  positive-module sum over-counts
      | ships required | 161  | 167        | +6    |
      | habs required  | 527  | 415        | −112  |  <-- BIG: static per-module MC (template
      |                |      |            |       |       `missionControl`, only 20 module types)
      |                |      |            |       |       MISSES a dynamic per-hab consumption term
      | mining         |  18  | 18 (quad)  |  ✓    |  now EXACT via quadratic (was 192 residual!)

    The old code computed mining as a RESIDUAL (`missionControlUsage − ships −
    habs`), which silently dumped the −112 hab-consumption miss (and the stale
    `missionControlUsage` field, 774 vs the game's true 706 used) into "mining"
    → 192 instead of 18. We now take the EXACT quadratic mine cost from
    extract_mc_latent_demand (verified: n=6 → 6²/2 = 18, "next mine 24", 36
    free) and expose the leftover as a labelled hab-consumption shortfall
    instead of mislabelling it as mining. Trust the in-game top bar for the
    precise number; this reconstruction is structurally correct but hab-side
    consumption still reads ~100 MC low until the dynamic term is modelled.
    """
    # 1. HQ
    hq = (factions[faction_id].get('baseIncomes_year') or {}).get('MissionControl', 0) or 0

    # 2. Councilors — sum of org.incomeMissionControl over orgs assigned to a
    #    councilor of this faction. Orgs reference assignedCouncilor.
    councilor_ids = {cid for cid, cv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TICouncilorState')
                     if fac_id_from_field(cv.get('faction')) == faction_id}
    councilor_mc = 0
    for oid, ov in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIOrgState'):
        ac = ov.get('assignedCouncilor')
        ac_id = deref(ac) if isinstance(ac, dict) else ac
        if ac_id in councilor_ids:
            councilor_mc += ov.get('incomeMissionControl', 0) or 0

    # 3. Nations — pulled from the control-points data; we'll add it from outside.
    #    Pass 0 here; the caller will plug in the value.

    # 4. Habs (positive contributions) + consumers from hab_inv.
    hab_produced = hab_inv['mc_produced_built']
    hab_consumed = hab_inv['mc_consumed_built']

    # 5. Ships — the per-ship `missionControlConsumption` is the BASE (un-reduced) value.
    #    Actual ship MC applies two reductions (verified 2033-03-25: base 176 → game 142):
    #      • tech `Effect_TotalShipMissionControlCost` = ×0.85 each (stackable, faction-wide)
    #      • Flag Bridge module (`FlagBridge`, specialModuleValue 0.8) = ×0.8 on its whole FLEET
    #    So: ship_mc = Σ over fleets ( fleet_base × 0.85^techstacks × 0.8^has_FlagBridge ).
    #    Reproduced game 142 to ±1 (140.6). Summing the raw field over-counts ~24%.
    fleet_to_fac = {}
    for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceFleetState'):
        fleet_to_fac[fid] = fac_id_from_field(fv.get('faction'))
    fac_state = factions.get(faction_id, {})
    fb_designs = {d.get('dataName') for d in (fac_state.get('shipDesigns') or [])
                  if any((e.get('moduleName') == 'FlagBridge')
                         for e in (d.get('moduleTemplateEntries') or []))}
    ship_tech_stacks = 0
    es_list = gs.get('PavonisInteractive.TerraInvicta.TIEffectsState', [])
    if es_list:
        for fe in es_list[0].get('Value', {}).get('factionEffectsNames', []):
            if (fe.get('Key') or {}).get('value') == faction_id:
                ship_tech_stacks = len(fe.get('Value', {}).get('ShipMissionControlReduction', []) or [])
                break
    tech_mult = 0.85 ** ship_tech_stacks
    fleet_base = defaultdict(float)
    fleet_has_fb = defaultdict(bool)
    ship_mc_base = 0.0
    for sid, sv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceShipState'):
        fleet = deref(sv.get('fleet'))
        if fleet_to_fac.get(fleet) != faction_id:
            continue
        base = sv.get('missionControlConsumption', 0) or 0
        ship_mc_base += base
        fleet_base[fleet] += base
        if sv.get('templateName') in fb_designs:
            fleet_has_fb[fleet] = True
    ship_mc = sum(b * tech_mult * (0.8 if fleet_has_fb[fl] else 1.0)
                  for fl, b in fleet_base.items())

    # 6. Mining network: EXACT quadratic cost (n²/2 above the free-mine count),
    #    passed in from extract_mc_latent_demand. This replaces the old residual
    #    method, which mislabelled reconstruction error as mining (192 vs true
    #    18). Fall back to the residual only if the quadratic wasn't supplied.
    # `missionControlUsage` is the game's EXACT total used (verified 2033-01-20:
    # field = 706 = the top-bar "706/765"). Ships (Σ consumption field) and
    # mining (quadratic) are both exactly computable and SMALL/medium; habs is
    # the LARGE term with a dynamic per-hab cost the static module table misses.
    # So the right decomposition makes HABS the residual, not mining:
    #   habs_consumed = usage − ships − mining_quadratic
    # This yields ~521 vs the game's 527 (the ~6 gap is our ships reading +6
    # high) — structurally correct, unlike the old "mining = residual" which
    # produced 192 instead of 18. The static module-sum (hab_consumed_static) is
    # kept as a lower-bound reference.
    reported_used = factions[faction_id].get('missionControlUsage', 0) or 0
    hab_consumed_static = hab_consumed
    if mining_cost is None:
        mining_cost = max(0, reported_used - ship_mc - hab_consumed_static)
        hab_consumed_recon = hab_consumed_static
    else:
        hab_consumed_recon = max(0, reported_used - ship_mc - mining_cost)

    return {
        'mc_hq': hq,
        'mc_councilors': councilor_mc,
        'mc_hab_produced': hab_produced,
        'mc_hab_consumed': hab_consumed_recon,          # residual-attributed (≈ game)
        'mc_hab_consumed_static': hab_consumed_static,  # static module-sum floor
        'mc_ships': ship_mc,
        'mc_mining': mining_cost,                        # exact quadratic
        'mc_mining_residual': mining_cost,              # back-compat alias
        'mc_used_reported': reported_used,
        'mc_pending_net': hab_inv['mc_net_pending'],
    }


def extract_leo_slot_capacity(gs, faction_id):
    """For each LEO hab, count total slots / built / under-construction / empty.
    Surfaces which LEO habs have spare capacity for stacking under-cap bonus
    modules (SpaceScience, Energy, Materials, LifeScience, Nanofactory, etc.).
    SpaceScienceResearchCenter and similar are multi-per-hab; OperationsCenter
    is one-per-hab. So an empty-slot-rich hab is the right target for stacking
    LEO bonus modules, separately from the OpsCenter buildout.
    """
    sectors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISectorState'))
    modules_by_id = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'))
    habs = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'))

    sectors_by_hab = defaultdict(list)
    for sid, sv in sectors.items():
        hid = deref(sv.get('hab'))
        sectors_by_hab[hid].append((sid, sv))

    rows = []
    for hid, hv in habs.items():
        if fac_id_from_field(hv.get('faction')) != faction_id:
            continue
        if not hv.get('inEarthLEO'):
            continue
        # A hab's TISectorState list is pre-allocated for EVERY tier (5 sectors on a
        # brand-new T1 platform); only sectors with `faction` set are open — the rest
        # unlock with core upgrades. Count open sectors only, and report MODULE slots
        # the way the hab screen does: the core occupies a slot of its own, so a T1
        # station = 4 buildable slots (save-verified 2006-07, a T1 platform read
        # 21 slots / 16 empty while the game showed 4 / 0).
        hab_sectors = sectors_by_hab.get(hid, [])

        def _modules(sv):
            for mref in (sv.get('habModules') or []):
                m = modules_by_id.get(deref(mref))
                if m and (m.get('templateName') or '').strip():
                    yield m

        # Open = the game has assigned the sector a faction, or something is built in it.
        # If NO sector qualifies (older save schema without the field), fall back to all.
        open_sectors = [(sid, sv) for sid, sv in hab_sectors
                        if sv.get('faction') is not None or any(True for _ in _modules(sv))]
        if not open_sectors:
            open_sectors = hab_sectors
        slots = 0
        built = 0
        underway = 0
        for sid, sv in open_sectors:
            slots += sv.get('slots', 0) or 0
            for m in _modules(sv):
                if (m.get('templateName') or '').endswith('Core'):
                    slots -= 1          # the core's own slot is not buildable space
                    continue
                if m.get('constructionCompleted'):
                    built += 1
                else:
                    underway += 1
        empty = max(0, slots - built - underway)
        rows.append({
            'hab': hv.get('displayName') or hv.get('templateName') or '?',
            'tier': hv.get('tier'),
            'slots': slots,
            'built': built,
            'underway': underway,
            'empty': empty,
        })
    rows.sort(key=lambda r: -r['empty'])  # most empty first
    return rows


def extract_nation_investment_dashboard(gs, faction_id):
    """Per-nation snapshot of what determines monthly investment points (IP) +
    the pip distribution the player has set on their CPs.

    Critical fields:
    - `baseInvestmentPoints_month` — the actual IP/mo number; this drives
      ALL priority output. More IP = each pip produces more.
    - `cohesion` vs `cohesionRestState_dailyCache` — if current cohesion is
      well below rest state, the nation has headroom (cohesion will recover
      naturally) and Unity pips can speed it up. If at rest state, you'd need
      to raise the rest state itself (different policies / events).
    - `priorityEffectPopScaling` — population scaling factor; smaller
      countries get more bang per pip. Cannot be changed by player except
      through annexation / fragmenting.

    Cohesion change reasons (from tracker) tell us WHY cohesion is where it
    is. The most actionable contributors:
      - UnityPriority +Δ — adding Unity pips
      - KnowledgePriority +Δ — Knowledge pips also help (bonus)
      - OppressionPriority −Δ — Oppression HURTS cohesion (trade-off)
      - Effect −Δ — events / projects (mostly fixed)
      - MovementToRestValue ± — drift toward rest state
    """
    nations = {nid: nv for nid, nv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TINationState')}
    # Map nation_id → (player's CP count, CP records with pips)
    cps_by_nation = defaultdict(list)
    for cid, cv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIControlPoint'):
        fid = fac_id_from_field(cv.get('faction'))
        if fid != faction_id:
            continue
        nat = deref(cv.get('nation'))
        if nat is None:
            continue
        pips = {k: v for k, v in (cv.get('controlPointPriorities', {}) or {}).items() if v > 0}
        cps_by_nation[nat].append({
            'cp_type': cv.get('controlPointType', '?'),
            'total_pips': cv.get('totalWeightsForControlPoint', 0),
            'num_priorities_set': cv.get('numPrioritiesWithWeight', 0),
            'pips': pips,
        })

    dashboard = []
    for nat, cps in cps_by_nation.items():
        nv = nations.get(nat, {})
        # Aggregate pip allocation across this faction's CPs in the nation
        pip_total_by_category = Counter()
        total_pips_set = 0
        for cp in cps:
            for cat, p in cp['pips'].items():
                pip_total_by_category[cat] += p
            total_pips_set += cp['total_pips']

        # Cohesion change drivers (current quarter + all-time)
        ccr_current = nv.get('tracker_CohesionChangeReason_CurrentTrackingPeriod', {}) or {}
        ccr_alltime = nv.get('tracker_CohesionChangeReason_AllTime', {}) or {}
        # Filter to numeric significant entries
        def clean_tracker(d):
            return {k: round(v, 3) for k, v in d.items()
                    if isinstance(v, (int, float)) and abs(v) > 0.005}

        cohesion = nv.get('cohesion', 0)
        cohesion_rest = nv.get('cohesionRestState_dailyCache', 0)
        cohesion_gap = cohesion_rest - cohesion
        ip_month = nv.get('baseInvestmentPoints_month', 0)

        # Heuristic IP improvement estimate if cohesion closed the gap.
        # From observed nations, IP roughly scales linearly with cohesion in
        # the 0-6 range (China cohesion 4.54 → 46 IP; USA cohesion 2.07 → 38 IP).
        # ΔIP ≈ ip_month × (cohesion_rest - cohesion) / max(1, cohesion + 1)
        if cohesion > 0 and cohesion_gap > 0.5:
            ip_potential_gain = ip_month * (cohesion_gap / max(1.0, cohesion))
            ip_potential_gain = min(ip_potential_gain, ip_month)  # cap at doubling
        else:
            ip_potential_gain = 0

        # Inert "Initiate-X" pips: priorities of the form
        # `Civilian_InitiateSpaceflightProgram`, `Military_InitiateNuclearProgram`,
        # `Military_FoundMilitary` go INERT once the corresponding program exists
        # on the nation. Per-game-mechanic confirmation: these pips are HIDDEN
        # from the UI (player cannot remove them) AND they don't draw from the
        # nation's IP pool (zero share of investment). They're save-state
        # artifacts left over from before the program was founded.
        #
        # Therefore: DO NOT recommend reallocating them (impossible) and DO NOT
        # count them in pip-share denominators for IP-share calculations. The
        # effective pip total excludes them.
        INITIATE_PRIORITY_PROGRAM_FIELD = {
            'Civilian_InitiateSpaceflightProgram': 'spaceFlightProgram',
            'Military_InitiateNuclearProgram': 'nuclearProgram',
            'Military_FoundMilitary': 'foundedMilitary',
        }
        dead_pips = {}
        for prio_name, prog_field in INITIATE_PRIORITY_PROGRAM_FIELD.items():
            pips_here = pip_total_by_category.get(prio_name, 0)
            if pips_here > 0 and nv.get(prog_field):
                dead_pips[prio_name] = pips_here
        # Effective pips = total minus inert ones (these are the pips that
        # actually compete for IP). All downstream share/IP-allocation math
        # should use this denominator, not raw total.
        effective_pips_by_category = {k: v for k, v in pip_total_by_category.items()
                                       if k not in dead_pips}
        total_pips_effective = sum(effective_pips_by_category.values())
        dead_pips_total = sum(dead_pips.values())

        dashboard.append({
            'nation_id': nat,
            'nation_name': nv.get('displayName') or nv.get('templateName', '?'),
            'n_cps': len(cps),
            'ip_per_month': ip_month,
            'dead_pips': dead_pips,
            'dead_pips_total': dead_pips_total,
            'has_spaceflight_program': bool(nv.get('spaceFlightProgram')),
            'has_nuclear_program': bool(nv.get('nuclearProgram')),
            'cohesion': cohesion,
            'cohesion_rest_state': cohesion_rest,
            'cohesion_gap': cohesion_gap,
            'ip_potential_gain_if_cohesion_recovers': ip_potential_gain,
            'democracy': nv.get('democracy', 0),
            'unrest': nv.get('unrest', 0),
            'unrest_rest_state': nv.get('unrestRestState_dailyCache', 0),
            'sustainability': nv.get('sustainability', 0),
            'inequality': nv.get('inequality', 0),
            'education': nv.get('education', 0),
            'economy_score': nv.get('economyScore', 0),
            'gdp': nv.get('GDP', 0),
            'priority_pop_scaling': nv.get('priorityEffectPopScaling', 0),
            'military_tech_level': nv.get('militaryTechLevel', 0),
            # `nv.get('population')` is usually None — the live population value
            # is in `historyPopulation[0]` (millions; newest-first). Use that.
            'pop_millions': ((nv.get('historyPopulation') or [0])[0]
                              if nv.get('historyPopulation')
                              else (nv.get('population', 0) / 1e6 if nv.get('population') else 0)),
            # Live monthly values from history arrays. CRITICAL: use [0] (newest);
            # arrays are stored newest-first. Using [-1] silently reports values
            # ~2 months stale — verified against the 2032-04-28 RUS federation
            # expansion where historyGDP[0]=$19.15T (correct) vs [-1]=$13.42T.
            'research_per_month': ((nv.get('historyResearch') or [0])[0] if nv.get('historyResearch') else 0),
            'funding_per_month': ((nv.get('historySpaceFunding') or [0])[0] if nv.get('historySpaceFunding') else 0),
            'regions_count': len(nv.get('regions') or []),
            # advisingCouncilors — who is advising this nation right now?
            # Note: this list can be empty during the brief inter-cycle gap
            # (Advise has ~15-day duration + short cooldown per the existing
            # ti-save-analyzer doc); councilors with priorMissionTemplateName ==
            # 'Advise' may still be intending to re-advise next tick. See
            # councilor_roster section for the "intended" view.
            'advising_councilor_ids': [(r.get('value') if isinstance(r, dict) else r)
                                        for r in (nv.get('advisingCouncilors') or [])],
            'total_pips_set': total_pips_set,
            'total_pips_effective': total_pips_effective,
            'pip_distribution': dict(pip_total_by_category),
            'pip_distribution_effective': dict(effective_pips_by_category),
            'cps': cps,
            'cohesion_drivers_current': clean_tracker(ccr_current),
            'cohesion_drivers_alltime': clean_tracker(ccr_alltime),
            # Cohesion rest-state diagnostics — see TI in-game tooltip for the
            # full formula. The key save-derivable inputs:
            #   - inequality × ~2 → negative penalty (mitigated by education < 10)
            #   - population (bigger nation = bigger negative)
            #   - geographic population distribution (computed; not directly in save)
            #   - hostile claims count
            #   - wars active (+2 each)
            #   - near-peer rivals (+1)
            #   - elite-public ideology variance
            #   - public opinion variance
            #   - autocracy (5 - democracy if democracy < 5, gives + bonus, reduced by unrest)
            'cohesion_factors': {
                'inequality': nv.get('inequality', 0),
                'education': nv.get('education', 0),
                'inequality_estimated_penalty': -(nv.get('inequality', 0) * 2.05),
                'democracy': nv.get('democracy', 0),
                'autocracy_estimated_bonus': max(0, 5 - nv.get('democracy', 5)) * 0.7,
                'at_war': len(nv.get('wars', []) or []) > 0,
                'wars_count': len(nv.get('wars', []) or []),
                'hostile_claims': len(nv.get('hostileClaims', []) or []),
                'regions_count': len(nv.get('regions', []) or []),
                'unrest': nv.get('unrest', 0),
            },
            # Public opinion + ideology drift tracking. Two of these can swing
            # the cohesion rest state by ±1-2 EACH if they shift significantly:
            # - elite-public ideology variance (max -1.9 penalty, not in save —
            #   computed at runtime from elite + public distributions)
            # - public opinion distribution variance (sum-of-squares proxy)
            #
            # The Oct 2031 global rest crash was driven by these two metrics
            # spiking simultaneously across ~80% of nations — likely a coordinated
            # alien/rival faction Public Campaign event.
            'public_opinion': nv.get('publicOpinion') or {},
            'public_opinion_concentration_hhi': sum(
                (v ** 2) for v in (nv.get('publicOpinion') or {}).values()),
            'public_opinion_dominant': (
                max((nv.get('publicOpinion') or {}).items(),
                    key=lambda x: x[1], default=('?', 0))
            ),
        })
    dashboard.sort(key=lambda n: -n['ip_per_month'])
    return dashboard


def extract_cp_priority_value_per_bonus(gs, faction_id):
    """Sum the player's CP priority weight across all their CPs, grouped by
    LEO-bonus name. The value of a LEO bonus equals (bonus%) × (CP priority
    weight allocated to that axis). High CP weight on Knowledge means the
    Knowledge bonus is very valuable; low CP weight on Government means the
    Government bonus barely matters and is cheap to drop.

    Returns dict: {LEO bonus name: total CP priority weight on that axis}.
    """
    # Mapping from CP-priority key in save → LEO bonus name. Conservative —
    # some CP priorities don't have a 1:1 LEO bonus (Funding, Spoils, Unity,
    # nuclear programs); they're left out of the value calculation.
    CP_PRI_TO_LEO_BONUS = {
        'Government': 'Government',
        'Knowledge': 'Knowledge',
        'Welfare': 'Welfare',
        'Environment': 'Environment',
        'Economy': 'Economy',
        'Oppression': 'Oppression',
        'MissionControl': 'MissionControl',
        'LaunchFacilities': 'LaunchFacilities',
        # Multiple CP priorities can drive ArmyCombatValue / Miltech — sum them
        'Military_BuildArmy': 'ArmyCombatValue',
        'Military': 'ArmyCombatValue',
        'Military_BuildSpaceDefenses': 'Miltech',
        'Military_BuildNavy': 'Miltech',
    }
    value = defaultdict(float)
    for cid, cv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIControlPoint'):
        fid = fac_id_from_field(cv.get('faction'))
        if fid != faction_id:
            continue
        for cp_pri, weight in (cv.get('controlPointPriorities', {}) or {}).items():
            if weight <= 0:
                continue
            bonus = CP_PRI_TO_LEO_BONUS.get(cp_pri)
            if bonus:
                value[bonus] += weight
    return dict(value)


def extract_leo_saturation(gs, faction_id):
    """For each LEO-bonus stack (Government, Knowledge, Welfare, Environment,
    ArmyCombatValue, Miltech, LaunchFacilities, Economy, Oppression,
    HumanDetection, AlienDetection): show the cap, the current contribution
    summed across powered LEO-bonus modules, and a per-module breakdown of
    which station contributes what.

    Detection bonuses use a per-module-tier override (+1/+2/+3) because the
    template's `specialRulesValue` field doesn't apply to those — it's used
    for the % oppression / % surveillance side of the same module.

    Also identifies consolidation opportunities: when a stack has multiple
    lower-tier copies AND a higher-tier project is researched, the player can
    decommission lower-tier copies + upgrade one in-place to save module slots
    with zero effective bonus loss.
    """
    # Per-module-tier detection point values (from in-game verification)
    DETECTION_OVERRIDES = {
        'ListeningPost':            {'HumanDetection': 1},
        'ReconnaissanceArray':      {'HumanDetection': 2},
        'ArgusComplex':             {'HumanDetection': 3},
        'XenologyLab':              {'AlienDetection': 1},
        'XenoscienceResearchCenter':{'AlienDetection': 2},
        'XenoscienceInstitute':     {'AlienDetection': 3},
    }
    # Cap per bonus type
    CAP_PERCENT = 0.30
    CAP_DETECTION = 9
    DETECTION_BONUSES = {'HumanDetection', 'AlienDetection'}
    # Make detection set accessible to the markdown formatter at module scope
    globals()['DETECTION_BONUSES_LOCAL'] = DETECTION_BONUSES

    # Module template lookup
    tdir = find_templates_dir()
    if not tdir:
        return None
    mod_tmpl = _safe_load_json(os.path.join(tdir, 'TIHabModuleTemplate.json')) or []

    # Per-module bonus contributions: data_name -> [(bonus, value, unit, tier)]
    mod_bonuses = defaultdict(list)
    mod_tier = {}
    for m in mod_tmpl:
        name = m['dataName']
        mod_tier[name] = m.get('tier')
        rules = m.get('specialRules', []) or []
        val = m.get('specialRulesValue', 0) or 0
        for r in rules:
            # Exclude meta flags + the AdminTower/Complex CP-cap rule (that one
            # is reported in the Earth CP-cap section instead, not as a 30%
            # saturating bonus).
            if r in ('LEOControlPointCapacity', 'TechBonusDiminishingReturns',
                     'EarthLEOOnly'):
                continue
            if not r.startswith('LEOBonus'):
                continue
            bonus = r[len('LEOBonus'):]
            mod_bonuses[name].append((bonus, val, '%', m.get('tier')))
    for name, overrides in DETECTION_OVERRIDES.items():
        # Strip auto-detected % entries for detection bonuses (those values
        # actually apply to the co-located oppression/etc. effects, not
        # detection points).
        mod_bonuses[name] = [(b, v, u, t) for b, v, u, t in mod_bonuses[name]
                             if b not in overrides]
        tier = mod_tier.get(name)
        for b, v in overrides.items():
            mod_bonuses[name].append((b, v, 'pts', tier))

    # LEO habs for player
    sectors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISectorState'))
    sector_to_hab = {sid: deref(sv.get('hab')) for sid, sv in sectors.items()}
    leo_hab_names = {}
    for hid, hv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'):
        if fac_id_from_field(hv.get('faction')) != faction_id:
            continue
        if hv.get('inEarthLEO'):
            leo_hab_names[hid] = hv.get('displayName') or hv.get('templateName') or '?'

    # Sum contributions per bonus, per module. Include BOTH built and
    # under-construction modules — the latter as `under_construction=True`,
    # so the report can show "after current builds finish" projection
    # (critical when the user has already queued LEO-bonus modules).
    contributions = defaultdict(list)
    for mid, mv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'):
        sid = deref(mv.get('sector'))
        hid = sector_to_hab.get(sid)
        if hid not in leo_hab_names: continue
        name = mv.get('templateName') or ''
        completed = bool(mv.get('constructionCompleted'))
        powered = bool(mv.get('powered'))
        for bonus, val, unit, tier in mod_bonuses.get(name, []):
            contributions[bonus].append({
                'hab': leo_hab_names[hid],
                'module': name,
                'tier': tier,
                'val': val,
                'unit': unit,
                'powered': powered,
                'completed': completed,
                'under_construction': not completed,
            })

    # Build summary
    summary = []
    finished_projects = set()
    fs = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
               if fid == faction_id), {})
    finished_projects = set(fs.get('finishedProjectNames', []) or [])

    for bonus in sorted(contributions.keys()):
        rows = contributions[bonus]
        powered = [r for r in rows if r['powered'] and r.get('completed', True)]
        building = [r for r in rows if r.get('under_construction')]
        is_detection = bonus in DETECTION_BONUSES
        unit = 'pts' if is_detection else '%'
        cap = CAP_DETECTION if is_detection else CAP_PERCENT
        current = sum(r['val'] for r in powered)
        # Pending = what'll arrive when all under-construction modules finish.
        # Critical for matching the user's mental model when they've already
        # queued LEO bonus modules — don't tell them "room for 2 more" when
        # they've already started 2.
        pending = sum(r['val'] for r in building)
        after_pending = current + pending

        # Detect consolidation opportunity: do we have multiple modules of
        # this bonus at lower tier when a higher-tier version exists and is
        # researched? E.g., 2× ListeningPost when ReconnaissanceArray
        # researched → swap.
        # Find the highest tier in MOD_BONUSES that contributes to this bonus
        # AND whose required project is researched.
        tier_counts = Counter(r['tier'] for r in powered)
        # Sort modules contributing to this bonus by tier ascending
        bonus_modules = sorted({(n, t) for n, blist in mod_bonuses.items()
                                for b, v, u, t in blist if b == bonus},
                               key=lambda x: x[1] or 0)
        # Find higher-tier modules that are researched
        # (template lookup: requiredProjectName per module)
        proj_for_module = {m['dataName']: m.get('requiredProjectName') for m in mod_tmpl}
        researched_higher = []
        for name, tier in bonus_modules:
            proj = proj_for_module.get(name)
            if proj in finished_projects or proj is None:
                researched_higher.append((name, tier))
        # Suggest consolidation if powered modules have a low-tier subset
        # that could be collapsed
        consolidation = None
        low_tier_powered = [r for r in powered if r['tier'] is not None
                            and r['tier'] < max((t for _, t in researched_higher), default=0)]
        if len(low_tier_powered) >= 2 and researched_higher:
            high_name, high_tier = max(researched_higher, key=lambda x: x[1])
            # Each pair of T1 modules can be replaced by 1 T2 module (net -1 slot)
            # More generally: contribution preserved at same total, slot count
            # reduces by (low_count − ceil(low_total / high_val)).
            high_val = next(
                v for n, blist in mod_bonuses.items() for b, v, u, t in blist
                if n == high_name and b == bonus
            )
            low_total = sum(r['val'] for r in low_tier_powered)
            new_slots = -(-int(low_total / high_val)) if high_val else 0  # ceil
            # Approximate: if low_total is exact multiple of high_val
            if high_val and low_total % high_val == 0:
                new_slots = int(low_total // high_val)
            slots_saved = len(low_tier_powered) - new_slots
            if slots_saved > 0:
                display_current = current * 100 if unit == '%' else current
                consolidation = {
                    'low_modules': [(r['module'], r['hab'], r['tier']) for r in low_tier_powered],
                    'high_module': high_name,
                    'high_tier': high_tier,
                    'slots_saved': slots_saved,
                    'note': f"Consolidate {len(low_tier_powered)} {low_tier_powered[0]['module']}s "
                            f"into {new_slots} {high_name}(s): −{slots_saved} slot(s), "
                            f"same {display_current:.0f}{unit} effective contribution.",
                }

        summary.append({
            'bonus': bonus,
            'cap': cap,
            'unit': unit,
            'current': current,
            'pending': pending,
            'after_pending': after_pending,
            'pending_over_cap': max(0, after_pending - cap),
            'pending_under_cap': max(0, cap - after_pending),
            'is_detection': is_detection,
            'over_cap': max(0, current - cap),
            'under_cap': max(0, cap - current),
            'powered_count': len(powered),
            'building_count': len(building),
            'rows': rows,  # all modules (built + under construction)
            'consolidation': consolidation,
            # Populated by caller with extract_cp_priority_value_per_bonus
            'cp_priority_weight': 0,
        })

    return {
        'leo_hab_count': len(leo_hab_names),
        'leo_hab_names': list(leo_hab_names.values()),
        'bonuses': summary,
    }


def extract_alien_pressure(gs, faction_id=None):
    """Earth-side alien activity indicators.

    IMPORTANT — hidden-info handling: xenoforming and alien-facility levels in
    a given region are only "known" to the player if that region appears in
    `faction.knownAlienSites`. For unsurveilled regions, the analyzer should
    REDACT identifying info (region name → "****", numeric level → "##") so
    we don't leak intel the in-game UI hides.

    We return both `xenoforming_known` (visible to player, with real values)
    and `xenoforming_hidden_count` (count only, no identifying info), so the
    markdown formatter can present them appropriately.
    """
    af = gs.get('PavonisInteractive.TerraInvicta.TIRegionAlienFacilityState', [])
    built = sum(1 for e in af if e['Value'].get('built'))
    xeno = gs.get('PavonisInteractive.TerraInvicta.TIRegionXenoformingState', [])

    # Collect player's known-alien-sites region IDs
    known_region_ids = set()
    if faction_id is not None:
        fs_player = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
                          if fid == faction_id), {})
        for entry in fs_player.get('knownAlienSites', []) or []:
            rid = (entry.get('Key') or {}).get('value') if isinstance(entry.get('Key'), dict) else None
            if rid is not None:
                known_region_ids.add(rid)

    # Need region names for known xenoforming display
    regions = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIRegionState'))
    xeno_known = []
    xeno_hidden = 0
    for e in xeno:
        v = e['Value']
        lvl = v.get('xenoformingLevel', 0) or 0
        if lvl <= 0:
            continue
        rid = (v.get('region') or {}).get('value') if isinstance(v.get('region'), dict) else None
        if (faction_id is None) or (rid in known_region_ids):
            region = regions.get(rid, {})
            region_name = (region.get('displayName') or region.get('templateName')
                           or v.get('templateName', '?'))
            xeno_known.append((region_name, lvl))
        else:
            xeno_hidden += 1
    xeno_known.sort(key=lambda x: -x[1])

    ul = gs.get('PavonisInteractive.TerraInvicta.TIRegionUFOLandingState', [])
    landings = sum(1 for e in ul if e['Value'].get('landingPresent'))

    return {
        'alien_facilities_built': built,
        'alien_facility_sites_tracked': len(af),
        'xenoforming_known': xeno_known,
        'xenoforming_hidden_count': xeno_hidden,
        # Kept for back-compat (callers used this name) — but only KNOWN ones
        'xenoforming_regions': xeno_known,
        'ufo_landings_active': landings,
    }


def extract_alien_progress(gs, factions=None, faction_template=None):
    """Factual alien (Hydra) space-military footprint — SAVE GROUND TRUTH.

    Returns {alien_ships, alien_fleets, alien_kt, alien_bases,
             player_ships, player_kt}. Aliens use the same entity types as
    humans (TISpaceShipState / TISpaceFleetState / TIHabState), distinguished
    only by faction. Ships carry no serialized combat-value scalar (only a
    `spaceCombatValueDataDirty` flag — the value is recomputed at runtime), so
    tonnage (Σ currentMass_kg) is the strength proxy, consistent with Table F's
    "fleet kt". `alien_bases` = count of alien-owned TIHabState (AlienHQ etc.).

    ⚠️ HIDDEN-INFO: this is full ground truth and includes alien ships/bases the
    player has NOT detected in-game. Correct for a backward-looking historical
    record (Table A); do NOT quote as live intel while advising active play."""
    if factions is None:
        factions = get_factions(gs)
    if faction_template is None:
        faction_template = require_faction(None)
    alien_id = next((fid for fid, fv in factions.items()
                     if fv.get('templateName') == 'AlienCouncil'), None)
    player_id = next((fid for fid, fv in factions.items()
                      if fv.get('templateName') == faction_template), None)
    fleet_fac = {fid: fac_id_from_field(fv.get('faction'))
                 for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceFleetState')}
    a_ships = p_ships = 0
    a_mass = p_mass = 0.0
    for _sid, sv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceShipState'):
        fac = fleet_fac.get(deref(sv.get('fleet')))
        m = sv.get('currentMass_kg', 0) or 0
        if fac == alien_id:
            a_ships += 1
            a_mass += m
        elif fac == player_id:
            p_ships += 1
            p_mass += m
    a_fleets = sum(1 for _f, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceFleetState')
                   if fac_id_from_field(fv.get('faction')) == alien_id)
    a_bases = sum(1 for _h, hv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState')
                  if fac_id_from_field(hv.get('faction')) == alien_id)
    return {'alien_ships': a_ships, 'alien_fleets': a_fleets, 'alien_kt': a_mass / 1e6,
            'alien_bases': a_bases, 'player_ships': p_ships, 'player_kt': p_mass / 1e6}


def extract_alien_hate_analysis(gs, faction_id):
    """Compute alien hate floor + above-floor + masking-project status.

    Hate formula (best inferred fit; verified by Dec 26 2-kill no-change event):

        TWO separate values:

        displayed_hate (assessedAlienHateOfMe):
          = snapshot of true_hate at last Effect_FixAlienThreatMeter event
          = stays CONSTANT between fix events regardless of player actions

        true_hate (internal, not in save):
          = max(floor, action_hate)

        floor = max(20, MC_consumption × difficulty_modifier × 0.8^N_masking)
        action_hate accumulates discretely:
          + 10                          per xenoform killed
          + alien_hull.structuralIntegrity per alien ship killed
            (Corvette=10, Destroyer=24, Battlecruiser=48, Titan=90)
          + ~10                         per alien hab destroyed
        decay: -1 hate/month while action_hate > floor

    At Effect_FixAlienThreatMeter events: displayed_hate := true_hate
    (one-time recalibration → visible hate jump).

    Empirical confirmation: in the reference campaign's Dec 4 → Jan 1 saves, displayed hate
    stayed at exactly 255.86 across 26 saves despite MC growing +7 and
    2 alien ship kills happening on Dec 26. lastDateOfFixedAlienHate was
    constant at 2031-10-30 across the entire window. The two prior +71
    and +3 fix-event jumps in Feb→Jun and Oct→Nov 2031 coincide exactly
    with lastDateOfFixedAlienHate updates.

    Mirror effect (community guide's 0.25-0.5× transfer of hate from
    anti-Servants/Protectorate actions) is unsupported by data and by
    reference-campaign councilor-kill observations. Don't model it.

    Difficulty modifier (from TIGlobalValuesState.difficulty):
        0 = Cinematic → 0.05×
        1 = Normal    → 0.4×
        2 = Veteran   → 0.6×
        3 = Brutal    → 1.0×

    War threshold: 50 hate. Above 50 = aliens dispatch strike fleets when fleet
    production allows. Below 20 = peace (Tolerance). 20-50 = Conflicted.

    Masking projects (each grants Effect_MCUsageMasking, multiplicative 0.8×,
    stackable per template):
        - Project_StrategicDeception (10k RP Xeno; prereq ArrivalSecurity +
          HydraInterrogation)
        - Project_Maskirovka (15k RP Xeno; QuantumEncryption +
          StrategicDeception + TheirTechnology)
        - Project_OperationalSecurity (2.5k RP InfoSci; Resistance-only;
          MilitarizationofSpace + ResistVictory)
        - Project_OperationalMisdirection (20k RP Xeno; FleetLogistics +
          StrategicDeception)

    Hard-reset path (Effect_SetAlienHate0) exists only via
    Project_CoexistencePact → AppeaseCouncil only. Not available to other
    anti-alien factions.

    Their Purpose / Their Demands / Hydra Diplomacy all grant
    Effect_FixAlienThreatMeter — `instantEffect: UpdateAlienThreatMeter_Accurate`,
    `effectDuration: instant`. This RECALCULATES the meter once, it does NOT
    freeze it for a period (despite my earlier assumption). Useful for
    bringing the displayed hate in line with the true formula state, but
    doesn't reduce hate by itself.
    """
    fac = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
                if fid == faction_id), {})
    mc = fac.get('missionControlUsage', 0) or 0
    actual_hate = fac.get('assessedAlienHateOfMe', 0) or 0
    xenoforms_killed = fac.get('aliensRemoved', 0) or 0
    investigations = fac.get('alienInvestigations', 0) or 0
    space_strength = fac.get('highestSpaceStrengthSinceLastAlienKnockdown', 0) or 0
    last_fixed = fac.get('lastDateOfFixedAlienHate', None)

    # Difficulty modifier — code-verified 2026-06-11 against decompiled
    # TIGlobalConfig.cs: enum switch is 1=Cinematic, 3=Veteran, 4=Brutal,
    # default(2)=Normal, and the per-MC hate multipliers are C/N/V/B =
    # 0.05/0.3/0.6/1.0. (The old 0-3 mapping here mislabeled difficulty 2 as
    # Veteran 0.6 — wrong on both name and number.)
    DIFFICULTY_MOD = {1: 0.05, 2: 0.3, 3: 0.6, 4: 1.0}
    DIFFICULTY_NAME = {1: 'Cinematic', 2: 'Normal', 3: 'Veteran', 4: 'Brutal'}
    gvs_list = gs.get('PavonisInteractive.TerraInvicta.TIGlobalValuesState', [])
    difficulty = gvs_list[0]['Value'].get('difficulty', 2) if gvs_list else 2
    diff_mod = DIFFICULTY_MOD.get(difficulty, 0.3)

    # Masking projects status
    MASKING_PROJECTS = [
        ('Project_StrategicDeception', 10000, 'Xenology',
         ['ArrivalSecurity', 'Project_HydraInterrogation']),
        ('Project_Maskirovka', 15000, 'Xenology',
         ['QuantumEncryption', 'Project_StrategicDeception', 'Project_TheirTechnology']),
        ('Project_OperationalSecurity', 2500, 'InformationScience (Resistance-only)',
         ['MilitarizationofSpace', 'Project_ResistVictory']),
        ('Project_OperationalMisdirection', 20000, 'Xenology',
         ['FleetLogistics', 'Project_StrategicDeception']),
    ]
    finished_p = set(fac.get('finishedProjectNames', []) or [])
    avail_p = set(fac.get('availableProjectNames', []) or [])
    grs_list = gs.get('PavonisInteractive.TerraInvicta.TIGlobalResearchState', [])
    finished_t = set(grs_list[0]['Value'].get('finishedTechsNames', []) or []) if grs_list else set()

    masking_status = []
    n_done = 0
    next_target = None  # first not-yet-done masking project
    for pname, cost, cat, prereqs in MASKING_PROJECTS:
        done = pname in finished_p
        if done:
            n_done += 1
        prereq_status = []
        for pr in prereqs:
            if pr.startswith('Project_'):
                ps = ('done' if pr in finished_p else ('avail' if pr in avail_p else 'locked'))
            else:
                ps = ('done' if pr in finished_t else 'locked')
            prereq_status.append((pr, ps))
        avail_now = (not done) and all(s == 'done' for _, s in prereq_status)
        entry = {
            'project': pname, 'cost': cost, 'category': cat,
            'done': done, 'prereqs': prereq_status, 'available_now': avail_now,
        }
        masking_status.append(entry)
        if not done and next_target is None:
            next_target = entry

    masking_mult = 0.8 ** n_done
    floor_raw = mc * diff_mod
    floor_masked = floor_raw * masking_mult
    floor_effective = max(20.0, floor_masked)
    above_floor = max(0, actual_hate - floor_effective)
    # War threshold = 50 (anti-alien factions)
    WAR_THRESHOLD = 50
    PEACE_THRESHOLD = 20
    if actual_hate >= WAR_THRESHOLD:
        war_status = 'WAR'
    elif actual_hate >= PEACE_THRESHOLD:
        war_status = 'CONFLICTED'
    else:
        war_status = 'TOLERANCE'

    # Months for above-floor to decay back to floor (assume 1/month decay)
    months_to_decay = int(above_floor)

    # Floor reduction available
    next_floor = max(20.0, mc * diff_mod * 0.8 ** (n_done + 1)) if next_target else floor_effective
    next_floor_delta = floor_effective - next_floor

    return {
        'difficulty': difficulty,
        'difficulty_name': DIFFICULTY_NAME.get(difficulty, '?'),
        'difficulty_modifier': diff_mod,
        'mc_consumption': mc,
        'actual_hate': actual_hate,
        'floor_raw': floor_raw,
        'floor_masked': floor_masked,
        'floor_effective': floor_effective,
        'masking_multiplier_current': masking_mult,
        'masking_projects_done': n_done,
        'masking_projects_total': len(MASKING_PROJECTS),
        'masking_status': masking_status,
        'next_masking_target': next_target,
        'next_masking_floor_delta': next_floor_delta,
        'above_floor': above_floor,
        'months_to_decay_to_floor': months_to_decay,
        'xenoforms_killed': xenoforms_killed,
        'alien_investigations': investigations,
        'highest_space_strength_since_knockdown': space_strength,
        'last_fixed_date': last_fixed,
        'war_threshold': WAR_THRESHOLD,
        'peace_threshold': PEACE_THRESHOLD,
        'war_status': war_status,
    }


def extract_faction_threat_assessment(gs, faction_id, factions):
    """For each non-player faction (including aliens): mutual hate, fleet
    strength, fleet locations. Surfaces (a) immediate threats — hostile
    factions with fleets near player assets — and (b) opportunistic targets
    (weak hostile factions, good for first-strike).

    Used to answer "where do I need to build ships?" — defensive priorities
    show up in the immediate-threat list, offensive opportunities in the
    opportunistic-target list.
    """
    player_fac = factions.get(faction_id, {})
    player_hate_of = {item['Key']['value']: item['Value']
                     for item in (player_fac.get('factionHate') or [])
                     if isinstance(item, dict)}

    # Build hab locations for the player (to compute "near player asset" later)
    space_bodies = {bid: bv.get('displayName') or bv.get('templateName') or '?'
                    for bid, bv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceBodyState')}
    hab_sites = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabSiteState'))
    player_bodies = set()  # bodies where player has habs
    for hid, hv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'):
        if fac_id_from_field(hv.get('faction')) != faction_id:
            continue
        site = hab_sites.get(deref(hv.get('habSite')), {})
        body_id = deref(site.get('parentBody'))
        if body_id is not None:
            player_bodies.add(space_bodies.get(body_id, '?'))
        elif hv.get('inEarthLEO'):
            player_bodies.add('Earth')

    # Build fleet locations per faction
    fleet_locs_by_faction = defaultdict(lambda: Counter())  # fid -> Counter(body_name -> ship_count)
    fleet_mass_by_faction = defaultdict(float)  # fid -> total mass kg
    fleet_ship_count_by_faction = defaultdict(int)
    ship_to_fleet = {}
    fleet_by_id = {}
    for fid_, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceFleetState'):
        fleet_by_id[fid_] = fv
        for s in (fv.get('ships') or []):
            sid = deref(s) if isinstance(s, dict) else s
            ship_to_fleet[sid] = fid_

    for sid, sv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceShipState'):
        fid_ = ship_to_fleet.get(sid)
        if not fid_:
            continue
        f = fleet_by_id.get(fid_, {})
        fac_id_ = fac_id_from_field(f.get('faction'))
        if fac_id_ is None:
            continue
        bid = deref(f.get('barycenter')) or deref(f.get('dockedLocation'))
        loc = space_bodies.get(bid, '?') if bid is not None else '?'
        fleet_locs_by_faction[fac_id_][loc] += 1
        fleet_mass_by_faction[fac_id_] += sv.get('currentMass_kg', 0) or 0
        fleet_ship_count_by_faction[fac_id_] += 1

    # Hab counts per faction (just totals, for context)
    hab_counts = Counter()
    for hid, hv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'):
        fid_ = fac_id_from_field(hv.get('faction'))
        if fid_ is not None:
            hab_counts[fid_] += 1

    # Threat rows
    rows = []
    ALIEN_FID = next((fid for fid, fv in factions.items()
                      if fv.get('templateName') == 'AlienCouncil'), None)
    for fid_, fv in factions.items():
        if fid_ == faction_id:
            continue
        # Mutual hate
        rival_hate_of = {item['Key']['value']: item['Value']
                         for item in (fv.get('factionHate') or [])
                         if isinstance(item, dict)}
        my_hate_of_them = player_hate_of.get(fid_, 0)
        their_hate_of_me = rival_hate_of.get(faction_id, 0)
        locs = fleet_locs_by_faction.get(fid_, Counter())
        # Immediate threat: their fleet at a body where we have habs (could attack)
        contested_bodies = sorted(
            ((b, n) for b, n in locs.items() if b in player_bodies),
            key=lambda x: -x[1],
        )
        # Total ship count + mass
        n_ships = fleet_ship_count_by_faction.get(fid_, 0)
        mass_kt = fleet_mass_by_faction.get(fid_, 0) / 1e6
        # Faction threat classification
        if fid_ == ALIEN_FID:
            kind = 'alien'
        elif their_hate_of_me >= 50 or my_hate_of_them >= 50:
            kind = 'hostile-war'
        elif their_hate_of_me >= 20 or my_hate_of_them >= 20:
            kind = 'hostile-conflicted'
        else:
            kind = 'neutral'
        rows.append({
            'faction_id': fid_,
            'faction_name': fv.get('displayName') or fv.get('templateName'),
            'template': fv.get('templateName'),
            'my_hate_of_them': my_hate_of_them,
            'their_hate_of_me': their_hate_of_me,
            'kind': kind,
            'ships': n_ships,
            'fleet_mass_kt': mass_kt,
            'habs': hab_counts.get(fid_, 0),
            'fleet_locations': dict(locs),
            'fleet_at_player_bodies': contested_bodies,
        })

    # Sort: highest mutual hate / strongest fleet first
    rows.sort(key=lambda r: (-max(r['my_hate_of_them'], r['their_hate_of_me']),
                              -r['fleet_mass_kt']))

    # Identify immediate threats (hostile + fleet at a body where you have habs)
    immediate_threats = [r for r in rows if r['kind'] in ('alien','hostile-war','hostile-conflicted')
                          and r['fleet_at_player_bodies']]
    # Opportunistic targets (high mutual hostility, weak fleet)
    opportunistic = [r for r in rows
                     if r['kind'] in ('hostile-war','hostile-conflicted')
                     and r['ships'] <= 10 and max(r['my_hate_of_them'], r['their_hate_of_me']) >= 30]

    return {
        'rows': rows,
        'player_bodies': sorted(player_bodies),
        'immediate_threats': immediate_threats,
        'opportunistic_targets': opportunistic,
    }


def extract_unlockable_projects(gs, faction_id, templates):
    """Identify faction projects whose prereqs are met but the game hasn't yet
    surfaced them in `availableProjectNames` (the visible menu).

    TI project unlock mechanic (per `TIProjectTemplate.json` schema):
      - `prereqs`: list of REQUIRED prereqs. Each can be either a finished
        global tech name (e.g. "OrbitalShipbuilding") or a finished project
        name (e.g. "Project_RingCore"). ALL must be met.
      - `altPrereq{N}`: ALTERNATIVE that can substitute for `prereqs[N]`.
        So if a project has `prereqs = [Project_RingCore, X]` and
        `altPrereq0 = Project_ColonyCore`, then EITHER Ring Core OR
        Colony Core satisfies the structural slot — Fission Reactor Farm,
        Solar Farm, all Institutes, Spaceworks, Marine Battalion Barracks,
        and Nanofacturing Complex are designed this way (a single late-hab
        prereq is interchangeable between Ring and Colony lines).
      - `factionAvailableChance`: 0-100, base chance the project will EVER
        appear for a given faction. 100 = guaranteed eventual appearance;
        20 = 20% chance to ever roll for you; 0 = never.
      - `initialUnlockChance` / `deltaUnlockChance` / `maxUnlockChance`:
        per-month roll. Each qualifying month the chance increments by
        `deltaUnlockChance` from `initialUnlockChance`, up to
        `maxUnlockChance`. Once it rolls true, the project moves into
        `availableProjectNames`.

    Returns rows sorted by (cost asc, friendly name) grouped by techCategory.
    Each row exposes the prereq check breakdown so the markdown writer can
    show which alt-prereq saved a slot.
    """
    if not templates or not templates.get('project_friendly'):
        return []
    # Need raw project template data — re-load if not cached on templates.
    # The main loader exposes only friendly_name + module_research_income;
    # walk the raw templates file once here to access prereq fields.
    tdir = find_templates_dir()
    if not tdir:
        return []
    raw = _safe_load_json(os.path.join(tdir, 'TIProjectTemplate.json')) or []

    factions = get_factions(gs)
    faction = factions.get(faction_id, {})
    faction_tpl_name = faction.get('templateName')
    finished_proj = set(faction.get('finishedProjectNames') or [])
    available_proj = set(faction.get('availableProjectNames') or [])
    hidden_proj = set(faction.get('hiddenProjects') or [])
    # Terminally-dead projects: failed their factionAvailableChance roll or were
    # claimed by another faction (oneTimeGlobally). They will never unlock, so
    # they must not appear as "waiting to roll" (bug found 2026-07-17: 12 dead
    # projects listed as rolling on the 2033-08-20 save).
    missed_proj = set(faction.get('missedProjects') or [])
    grs_list = gs.get('PavonisInteractive.TerraInvicta.TIGlobalResearchState', [])
    grs = (grs_list[0].get('Value') if grs_list else {}) or {}
    finished_tech = set(grs.get('finishedTechsNames') or [])
    done = finished_proj | finished_tech

    rows = []
    for p in raw:
        dn = p.get('dataName')
        if not dn:
            continue
        if dn in finished_proj or dn in available_proj or dn in missed_proj:
            continue
        fac_chance = p.get('factionAvailableChance', 100)
        if fac_chance == 0:
            continue
        # Faction gate: `factionPrereq` (list of faction templateNames) means
        # ONLY those factions can ever roll this project. Without this filter
        # the table wrongly listed Their Weakness / Hydra Diplomacy /
        # AppeaseVictory / DestroyVictory etc. for ResistCouncil (bug found
        # 2026-06-11: none of those are in ResistCouncil.availableProjectNames
        # and their factionPrereq excludes it).
        fprereq = p.get('factionPrereq')
        if fprereq and faction_tpl_name not in fprereq:
            continue
        prereqs = p.get('prereqs') or []
        # Resolve each prereq slot, allowing altPrereq{N} substitution
        slots = []
        all_met = True
        for i, pr in enumerate(prereqs):
            alt = p.get(f'altPrereq{i}')
            if pr in done:
                slots.append({'index': i, 'required': pr, 'alt': alt, 'status': 'direct-met'})
            elif alt and alt in done:
                slots.append({'index': i, 'required': pr, 'alt': alt, 'status': 'alt-met'})
            else:
                slots.append({'index': i, 'required': pr, 'alt': alt, 'status': 'missing'})
                all_met = False
        if not all_met:
            continue
        # Milestone / objective gates BEYOND tech prereqs (bug found 2026-07-03, restored
        # 2026-07-04 after a parallel-session file overwrite dropped it: Griffin/Salamander
        # Interrogation + Megafauna/WarDog Necropsy were listed as "waiting to roll" while
        # actually blocked on capture milestones like AccessLiveGriffin — `requiredMilestone`
        # vs faction.milestones AND `requiredObjectiveName` vs faction.objectiveNames).
        milestone = p.get('requiredMilestone')
        milestone_met = (not milestone) or (milestone in set(faction.get('milestones') or []))
        objective = p.get('requiredObjectiveName')
        obj_status = (faction.get('objectiveNames') or {}).get(objective) if objective else None
        objective_met = (not objective) or obj_status in ('Completed', 'Unlocked')
        rows.append({
            'data_name': dn,
            'friendly': p.get('friendlyName', dn),
            'cost': p.get('researchCost', 0),
            'category': p.get('techCategory', '?'),
            'fac_chance': fac_chance,
            'init_chance': p.get('initialUnlockChance', 0),
            'delta_chance': p.get('deltaUnlockChance', 0),
            'max_chance': p.get('maxUnlockChance', 100),
            'is_rolling': dn in hidden_proj,
            'one_time_globally': p.get('oneTimeGlobally', False),
            'prereq_slots': slots,
            'ai_critical': p.get('AI_criticalTech', False),
            'milestone': milestone,
            'milestone_met': milestone_met,
            'objective': objective,
            'objective_met': objective_met,
        })
    rows.sort(key=lambda r: (r['category'], r['cost'], r['friendly']))
    return rows


def extract_snapshot_income_breakdown(gs, faction_id, factions, templates):
    """Compute the in-game 🧪 tooltip's 6-source research breakdown for a faction.

    Validated against the reference-campaign 2032-04-28 Resistance save: tooltip showed
    1 + 26.5 + 180.3 + 31.2 + 5.7 = 244.7 base, × 1.30 = 318.1/day,
    × 30.44 = 9,683/mo, × 365.25 = 116.2K/yr. All numbers match.

    Sources (per `UI.GeneralControls.*IncomeTip` localization):
      1. HQ            — `baseIncomes_year.Research / 365.25` (always 1/day)
      2. Councilors    — heuristic from `councilors[].attributes.Science`
                         (≈ 4/councilor + Science × ~0.5; coefficient is
                         observation-fit, not from binary)
      3. Nations       — Σ over my CPs in nation N of
                         `N.historyResearch[0] × CP_share`. **CRITICAL:**
                         use `[0]` (newest), NOT `[-1]` (oldest); arrays are
                         stored newest-first. Plus Advise direct boost from
                         `N.advisingCouncilors[]` (Sci+Adm of each adviser,
                         diminishing returns past first).
      4. Habs          — Σ `module.templateName → TIHabModuleTemplate.incomeResearch_month`
                         over every powered+completed module in player habs.
                         **DO NOT** look for `incomeResearch_month` on the
                         TIHabModuleState; it's on the TEMPLATE.
      5. Unused MC     — `(MC_cap − missionControlUsage) × 0.075/day` per
                         `UI.GeneralControls.MissionControlDetail`.
      6. Distribution  — `(sum 1-5) × 0.30` exactly (multiplicative tech bonus
                         when the relevant research-distribution tech is done).

    Returns a dict with per-source daily/monthly/annual values + a breakdown
    of advisers and category bonuses. Some components (councilors, MC cap)
    use heuristics; flag is_estimated=True when so.
    """
    faction = factions.get(faction_id) if isinstance(factions, dict) else factions[faction_id]
    if not faction:
        return None
    module_rsr_income = (templates or {}).get('module_research_income', {})

    # ---- 1. HQ (exact)
    hq_per_year = (faction.get('baseIncomes_year') or {}).get('Research', 0) or 0
    hq_per_day = hq_per_year / 365.25

    # ---- 2. Councilors (heuristic; documented as approximate)
    councilors_db = {c['Value']['ID']['value']: c['Value']
                     for c in gs.get('PavonisInteractive.TerraInvicta.TICouncilorState', [])}
    player_cids = [ref.get('value') for ref in (faction.get('councilors') or [])]
    council_sci_total = sum((councilors_db.get(cid, {}).get('attributes', {}) or {}).get('Science', 0)
                            for cid in player_cids if cid)
    # Empirical fit from the 2032-04-28 tooltip: 6 councilors with Sci=6 → 26.5/day.
    # Best-fit single-term formula: ≈ 4.4/councilor base, Sci contribution is
    # collapsed inside that (Science 0..few). Be conservative: report base + Sci.
    council_per_day_estimate = 4.0 * len(player_cids) + council_sci_total * 0.5

    # ---- 3. Nations via CP share + Advise direct boost
    nations_db = {n['Value']['ID']['value']: n['Value']
                  for n in gs.get('PavonisInteractive.TerraInvicta.TINationState', [])}
    cps_db = {cp['Value']['ID']['value']: cp['Value']
              for cp in gs.get('PavonisInteractive.TerraInvicta.TIControlPoint', [])}
    player_cp_ids = [ref.get('value') for ref in (faction.get('controlPoints') or [])]
    nation_cps_owned = defaultdict(int)
    for cpid in player_cp_ids:
        cp = cps_db.get(cpid)
        # EXCLUDE crackdown-suppressed CPs: benefitsDisabled → 0 income (Funding/research/
        # boost/MC/IP all zero until crackdownExpiration). Matches research_income.py and the
        # in-game panel (a rump nation's cracked-down CPs show income share 0.0). LESSONS-politics C20.
        if cp and not cp.get('benefitsDisabled'):
            nid = (cp.get('nation') or {}).get('value')
            if nid:
                nation_cps_owned[nid] += 1

    nation_share_per_day = 0.0
    advise_total_attrs = 0   # sum of Sci+Cmd+Adm across the player's advisers in major nations
    advise_breakdown = []    # [(nation_name, councilor_name, sci, cmd, adm)]
    nation_contribs = []
    for nid, ncps in nation_cps_owned.items():
        n = nations_db.get(nid, {})
        total_cps_in_nation = len(n.get('controlPoints', []) or []) or 1
        share = ncps / total_cps_in_nation
        # CRITICAL: historyResearch[0] is the newest (current month). Using [-1]
        # would silently report a value ~2 months stale; verified against the
        # 2032-04-28 RUS federation expansion. See docs/lessons/REFERENCE.md § Nation history arrays.
        history = n.get('historyResearch') or [0]
        cur_research_mo = history[0] if history else 0
        n_contrib_day = (cur_research_mo / 30.44) * share
        nation_share_per_day += n_contrib_day
        nation_contribs.append({
            'nation_template': n.get('templateName', '?'),
            'nation_name': n.get('displayName', '?'),
            'cps_owned': ncps,
            'total_cps': total_cps_in_nation,
            'share_pct': round(share * 100, 1),
            'monthly_research': round(cur_research_mo, 1),
            'my_share_per_day': round(n_contrib_day, 2),
        })
        # Advise contribution — count MY councilors advising this nation
        for adv_ref in (n.get('advisingCouncilors') or []):
            adv_cid = adv_ref.get('value') if isinstance(adv_ref, dict) else adv_ref
            if adv_cid not in player_cids:
                continue
            adv = councilors_db.get(adv_cid, {})
            attrs = adv.get('attributes', {}) or {}
            sci, cmd, adm = attrs.get('Science', 0), attrs.get('Command', 0), attrs.get('Administration', 0)
            advise_total_attrs += sci + cmd + adm
            advise_breakdown.append({
                'nation': n.get('displayName', '?'),
                'nation_template': n.get('templateName', '?'),
                'councilor': adv.get('displayName', '?'),
                'sci': sci, 'cmd': cmd, 'adm': adm,
            })

    # Advise direct boost — observation-fit on 2032-04-28:
    # 3 advisers on USA/CHN/RUS with totals (Sci 5 + Cmd 17 + Adm 33 = 55)
    # account for ~31/day of nation research above CP-proportional.
    # Coefficient ≈ 31 / 55 ≈ 0.56 per attribute. Apply with diminishing returns
    # via 1/(adviser_index+1) per `TIMissionTemplate.description.Advise`:
    # "Additional advising councilors provide diminishing benefits."
    # Group by nation for diminishing-returns calc.
    advise_by_nation = defaultdict(list)
    for a in advise_breakdown:
        advise_by_nation[a['nation_template']].append(a)
    advise_direct_per_day = 0.0
    for tpl, advisers in advise_by_nation.items():
        for i, a in enumerate(advisers):
            factor = 1.0 / (i + 1)   # 1.0, 0.5, 0.33, ...
            advise_direct_per_day += (a['sci'] + a['cmd'] + a['adm']) * 0.18 * factor
    # Total nation contribution = CP-share + Advise direct
    nation_per_day_total = nation_share_per_day + advise_direct_per_day

    # ---- 4. Habs (exact via template join)
    habs_db = {h['Value']['ID']['value']: h['Value']
               for h in gs.get('PavonisInteractive.TerraInvicta.TIHabState', [])}
    sec_to_hab = {}
    for hid, h in habs_db.items():
        for sec_ref in (h.get('sectors') or []):
            sec_to_hab[sec_ref.get('value')] = hid
    hab_rsr_per_mo = 0
    hab_modules_counted = 0
    for m in gs.get('PavonisInteractive.TerraInvicta.TIHabModuleState', []):
        v = m.get('Value', {})
        if not v.get('constructionCompleted', False):
            continue
        if not v.get('powered', True):
            continue
        sec_id = (v.get('sector') or {}).get('value')
        hab_id = sec_to_hab.get(sec_id)
        if hab_id is None:
            continue
        hab = habs_db.get(hab_id, {})
        if (hab.get('faction') or {}).get('value') != faction_id:
            continue
        income = module_rsr_income.get(v.get('templateName'), 0)
        if income:
            hab_rsr_per_mo += income
            hab_modules_counted += 1
    hab_per_day = hab_rsr_per_mo / 30.44

    # ---- 5. Unused MC
    mc_usage = faction.get('missionControlUsage', 0) or 0
    # MC capacity comes from nations + orgs + habs + HQ — not directly stored.
    # Heuristic: compute from extract_mc_full if available; otherwise skip.
    # The 0.075 coefficient is from `UI.GeneralControls.MissionControlDetail`:
    # "Excess mission control … is converted to research and money."
    # On 2032-04-28: cap 550, usage 474, free 76, 76 × 0.075 = 5.7/day ✓.
    # If cap unknown, report unused_mc_per_day as None and flag.
    mc_per_day = None
    mc_capacity = None
    # Try to find a passed-in capacity. For now leave as None; the markdown
    # writer can fill from extract_mc_full output if available.

    # ---- 6. Distribution bonus — multiplicative tech bonus on the base
    # Read the live applied effects from TIEffectsState.factionEffectsNames,
    # which stores the accumulated stack of effects per faction grouped by
    # "Context" (the modifier slot the engine sums into).
    # Research-distribution-bonus lives under context "ControlPointResearch"
    # and is what `Effect_CPResearch10`, `Effect_CPResearch20`, `Effect_CPResearch30`
    # write into. Each multiplies by 1.10 / 1.20 / 1.30. They stack
    # multiplicatively on the per-stack level (verified: 2 × CPResearch10 = ×1.21).
    dist_bonus_pct = 0.0
    effects_states = gs.get('PavonisInteractive.TerraInvicta.TIEffectsState', [])
    if effects_states:
        es = effects_states[0].get('Value', {})
        for entry in es.get('factionEffectsNames', []) or []:
            if (entry.get('Key') or {}).get('value') != faction_id:
                continue
            ctx_map = entry.get('Value', {}) or {}
            cpr_effects = ctx_map.get('ControlPointResearch', []) or []
            multiplier = 1.0
            for eff_name in cpr_effects:
                if eff_name == 'Effect_CPResearch10':
                    multiplier *= 1.10
                elif eff_name == 'Effect_CPResearch20':
                    multiplier *= 1.20
                elif eff_name == 'Effect_CPResearch30':
                    multiplier *= 1.30
            dist_bonus_pct = multiplier - 1.0
            break
    # Note: the in-game tooltip's "research distribution bonus" line can exceed
    # this calculation in some cases (observed ~30% in 2032-04 vs 21% from
    # ControlPointResearch effects alone). The gap is from additional bonuses
    # applied at other contexts (e.g. specific category bonuses, LEO modifiers).
    # If the live tooltip is available, prefer it; this is a lower bound.

    base_sources_per_day = hq_per_day + council_per_day_estimate + nation_per_day_total + hab_per_day
    if mc_per_day is not None:
        base_sources_per_day += mc_per_day
    dist_bonus_per_day = base_sources_per_day * dist_bonus_pct
    total_per_day = base_sources_per_day + dist_bonus_per_day

    return {
        # ---- direct values ----
        'hq_per_day': round(hq_per_day, 2),
        'councilors_per_day_estimate': round(council_per_day_estimate, 2),
        'nation_per_day_total': round(nation_per_day_total, 2),
        'nation_cp_share_per_day': round(nation_share_per_day, 2),
        'advise_direct_per_day': round(advise_direct_per_day, 2),
        'habs_per_day': round(hab_per_day, 2),
        'habs_per_month': round(hab_rsr_per_mo, 1),
        'hab_modules_with_research': hab_modules_counted,
        'unused_mc_per_day': mc_per_day,  # filled by caller if MC cap known
        'mc_usage': mc_usage,
        'base_sources_per_day': round(base_sources_per_day, 2),
        'dist_bonus_pct': dist_bonus_pct,
        'dist_bonus_per_day': round(dist_bonus_per_day, 2),
        'total_per_day': round(total_per_day, 2),
        'total_per_month': round(total_per_day * 30.44, 1),
        'total_per_year': round(total_per_day * 365.25, 1),
        # ---- detail ----
        'councilors_count': len(player_cids),
        'councilor_science_total': council_sci_total,
        'nation_contribs': sorted(nation_contribs, key=lambda r: -r['my_share_per_day']),
        'advise_breakdown': advise_breakdown,
        'advise_total_attrs': advise_total_attrs,
        # ---- accuracy flags ----
        'is_estimated': True,  # councilors, advise, MC are heuristic; HQ + habs + nation-CP-share are exact
        'cached_yearly_revenue_research': (faction.get('cachedYearlyRevenue') or {}).get('Research', 0),
        'cached_is_stale_warning': 'cachedYearlyRevenue.Research is often ~2× off — DO NOT use it. The live tooltip value is the ground truth; this breakdown reconstructs it from primary save data.',
    }


def extract_rival_resources(gs, factions, player_faction_id=None):
    """Per-faction full-stockpile snapshot + cachedYearlyRevenue + advise count.

    Captures every resource (Money / Influence / Operations / Boost /
    Water / Volatiles / Metals / NobleMetals / Fissiles / Antimatter /
    Exotics) — the existing extract_faction_comparison only captures a
    subset.

    Note `faction.resources['Research']` is always 0 — research is
    distributed daily, not accumulated. For research income, use
    extract_snapshot_income_breakdown or `cachedYearlyRevenue.Research`
    (flag the latter as stale — see docs/lessons/REFERENCE.md).
    """
    RESOURCES = ['Money', 'Influence', 'Operations', 'Boost',
                 'Water', 'Volatiles', 'Metals', 'NobleMetals',
                 'Fissiles', 'Antimatter', 'Exotics']
    INCOME_KEYS = ['Research', 'Money', 'Influence', 'Operations',
                   'Boost', 'MissionControl']

    # Count CPs per faction by walking the TIControlPoint list (faster than
    # reading each faction's list because lists can be stale after CP loss).
    cp_counts = Counter()
    for cp in gs.get('PavonisInteractive.TerraInvicta.TIControlPoint', []):
        v = cp.get('Value', {})
        fac = (v.get('faction') or {}).get('value')
        if fac is not None:
            cp_counts[fac] += 1

    # Count active Advise missions per faction (player's councilors advising
    # any nation — captures the income-boost effect).
    councilors_db = {c['Value']['ID']['value']: c['Value']
                     for c in gs.get('PavonisInteractive.TerraInvicta.TICouncilorState', [])}
    advise_count_by_faction = Counter()
    for n in gs.get('PavonisInteractive.TerraInvicta.TINationState', []):
        nv = n.get('Value', {})
        for adv_ref in (nv.get('advisingCouncilors') or []):
            adv_cid = adv_ref.get('value') if isinstance(adv_ref, dict) else adv_ref
            adv = councilors_db.get(adv_cid, {})
            adv_fac = (adv.get('faction') or {}).get('value')
            if adv_fac is not None:
                advise_count_by_faction[adv_fac] += 1

    rows = []
    for fid, fv in factions.items():
        if 'alien' in (fv.get('displayName', '') or '').lower():
            continue
        res = fv.get('resources', {}) or {}
        rev = fv.get('cachedYearlyRevenue', {}) or {}
        rows.append({
            'id': fid,
            'template': fv.get('templateName', '?'),
            'name': fv.get('displayName', fv.get('templateName', '?')),
            'cps': cp_counts.get(fid, 0),
            'advise_count': advise_count_by_faction.get(fid, 0),
            'is_player': (fid == player_faction_id),
            'stockpile': {k: res.get(k, 0) for k in RESOURCES},
            'yearly_income_cached': {k: rev.get(k, 0) for k in INCOME_KEYS},
            'mc_usage': fv.get('missionControlUsage', 0),
        })
    # Sort: player first, then by CP count desc
    rows.sort(key=lambda r: (not r['is_player'], -r['cps']))
    return rows


def extract_snapshot_status(gs, faction, grs):
    """Original research extraction logic, lightly cleaned."""
    result = {
        'finished_techs': sorted(grs.get('finishedTechsNames', [])) if grs else [],
        'active_global_techs': [],
        'endgame_techs': (grs.get('endGameTechsCompletedByCategory', {}) if grs else {}),
        'research_weights': faction.get('researchWeights', []),
        'yearly_research': (faction.get('cachedYearlyRevenue', {}) or {}).get('Research', 0),
        'finished_projects': sorted(faction.get('finishedProjectNames', [])),
        'available_projects': sorted(faction.get('availableProjectNames', [])),
        'missed_projects': faction.get('missedProjects', []),
        'hidden_projects': sorted(faction.get('hiddenProjects', [])),
        'auto_triggers': [],
        'active_faction_projects': [],
        'paused_faction_projects': [],
    }
    faction_id = faction.get('ID', {}).get('value', 0)
    weights = result['research_weights']

    if grs:
        for entry in grs.get('techProgress', []):
            faction_contrib = 0
            for contrib in entry.get('factionContributions', []):
                if contrib.get('Key', {}).get('value') == faction_id:
                    faction_contrib = contrib.get('Value', 0)
            result['active_global_techs'].append({
                'name': entry.get('techTemplateName', '?'),
                'total_progress': round(entry.get('accumulatedResearch', 0), 1),
                'faction_contribution': round(faction_contrib, 1),
            })

    for proj in faction.get('currentProjectProgress', []) or []:
        entry = {
            'name': proj.get('projectTemplateName', '?'),
            'progress': round(proj.get('accumulatedResearch', 0), 1),
            'slot': proj.get('slot', -1),
        }
        if 3 <= entry['slot'] <= 5 and entry['slot'] < len(weights):
            entry['weight'] = weights[entry['slot']]
            result['active_faction_projects'].append(entry)
        else:
            result['paused_faction_projects'].append(entry)

    for t in faction.get('activeProjectTriggers', []) or []:
        result['auto_triggers'].append({
            'name': t.get('projectTemplateName', '?'),
            'trigger_value': round(t.get('monthlyTriggerValue', 0), 1),
        })

    return result


# Tech friendlyName overrides — Pavonis renames techs in patches without
# updating the bundled TITechTemplate.json the analyzer reads from. Map the
# stable internal data_name to the current in-game UI name so the report
# matches what the player actually sees in the research menu.
# User-confirmed renames (reference campaign):
#   OrbitalTorusHabs → "Ring Habs"   (template still says "Orbital Torus Habs")
# When the user references a tech by a name the script doesn't print, ASK
# before claiming it doesn't exist — templates can be stale relative to the
# running game's UI/localization.
TECH_FRIENDLY_OVERRIDE = {
    'OrbitalTorusHabs': 'Ring Habs',
}

PROJECT_FRIENDLY_OVERRIDE = {
    'Project_NeutronFluxLantern': 'Poseidon Lantern',
    'Project_NeutronFluxTorch': 'Poseidon Torch',
}


def extract_next_global_techs(gs, templates):
    """Identify global techs whose prereqs are all done but the tech itself
    isn't researched yet. These are the actual menu of options when a global
    slot opens. Global techs CANNOT be swapped mid-research; the player can
    only pick the next one when a slot completes.

    Returns a list of dicts sorted by relevance (cheapest cost-effective first,
    but no real prioritization — the operator should choose based on goals).
    """
    tdir = find_templates_dir()
    if not tdir:
        return []
    tech_template = _safe_load_json(os.path.join(tdir, 'TITechTemplate.json')) or []

    grs_list = gs.get('PavonisInteractive.TerraInvicta.TIGlobalResearchState', [])
    grs = grs_list[0].get('Value', grs_list[0]) if grs_list else {}
    finished_techs = set(grs.get('finishedTechsNames', []) or [])
    in_progress = {t.get('techTemplateName') for t in grs.get('techProgress', []) or []}

    next_avail = []
    for t in tech_template:
        dn = t.get('dataName')
        if not dn or dn in finished_techs or dn in in_progress:
            continue
        prereqs = t.get('requiredTechs') or t.get('prereqs') or []
        if not prereqs:
            prereqs = []
        # Some templates store prereqs as objects with .name, others as strings.
        prereq_names = []
        for p in prereqs:
            if isinstance(p, dict):
                prereq_names.append(p.get('dataName') or p.get('name') or '')
            else:
                prereq_names.append(p)
        prereq_names = [p for p in prereq_names if p]
        if not all(p in finished_techs for p in prereq_names):
            continue
        next_avail.append({
            'data_name': dn,
            'friendly': TECH_FRIENDLY_OVERRIDE.get(dn, t.get('friendlyName', dn)),
            'category': t.get('techCategory') or t.get('category') or t.get('researchCategory') or '?',
            'cost': t.get('researchCost', 0) or 0,
            'prereqs': prereq_names,
        })
    next_avail.sort(key=lambda x: (x['cost'], x['data_name']))
    return next_avail


# Opinionated default research chains used to annotate the research report.
# Written for a Resistance-style anti-alien campaign; other factions/doctrines
# may want to edit or extend these — they only affect report annotations,
# never the underlying save extraction.
STRATEGIC_RESEARCH_CHAINS = {
    'command_center_mc': [
        'IntegratedEarthSpaceEconomy',
        'FleetLogistics',
        'OrbitalTorusHabs',
        'Project_RingCore',
        'Project_CommandCenter',
    ],
    'poseidon_torch_logistics': [
        'HighTemperatureSuperconductors',
        'MagneticPlasmaConfinementTechniques',
        'MagneticNozzles',
        'Project_NeutronFluxLantern',
        'Project_NeutronFluxTorch',
    ],
    'resistance_victory': [
        'Project_TheChokePoint',
        'Project_TheFinalAssault',
    ],
    'cp_cap_economy': [
        'Project_AdministrationTower',
        'AdministrationAlgorithms',
        'ArrivalGovernance',
        'Project_AdministrationComplex',
    ],
    'plasma_extraction': [
        'ElectrostaticPlasmaConfinement',
        'Project_PlasmaExtractionTechniques',
    ],
    'fusion_hedge': [
        'DeuteriumTritiumFusion',
    ],
    'outer_planets_reach': [
        'MissiontotheOuterPlanets',
    ],
    'shared_global_risk': [
        'FallofEmpires',
        'OrbitalFighters',
    ],
    'military_contingency': [
        'AdvancedMissileWarfareDoctrine',
        'PlasmaWeapons',
        'Coilguns',
        'UltravioletCombatLasers',
        'FissionPulseDrives',
    ],
}


ALWAYS_DOSSIER_RESEARCH_IDS = {
    item
    for chain in STRATEGIC_RESEARCH_CHAINS.values()
    for item in chain
}


def _research_kind(data_name):
    return 'project' if str(data_name).startswith('Project_') else 'global_tech'


def _research_template(data_name, templates):
    if _research_kind(data_name) == 'project':
        return (templates.get('project_templates') or {}).get(data_name, {})
    return (templates.get('tech_templates') or {}).get(data_name, {})


def _research_friendly(data_name, templates):
    if _research_kind(data_name) == 'project':
        p = _research_template(data_name, templates)
        return (PROJECT_FRIENDLY_OVERRIDE.get(data_name)
                or p.get('friendlyName')
                or (templates.get('project_friendly') or {}).get(data_name)
                or data_name)
    t = _research_template(data_name, templates)
    return TECH_FRIENDLY_OVERRIDE.get(data_name, t.get('friendlyName', data_name))


def _research_category(data_name, templates):
    row = _research_template(data_name, templates)
    return row.get('techCategory') or row.get('category') or row.get('researchCategory') or '?'


def _research_prereqs(data_name, templates):
    row = _research_template(data_name, templates)
    prereqs = row.get('prereqs') or row.get('requiredTechs') or []
    out = []
    for p in prereqs:
        if isinstance(p, dict):
            out.append(p.get('dataName') or p.get('name') or '')
        else:
            out.append(p)
    return [p for p in out if p]


def _research_cost(data_name, templates):
    row = _research_template(data_name, templates)
    return row.get('researchCost', 0) or 0


def _project_unlock_info(data_name, templates):
    row = (templates.get('project_templates') or {}).get(data_name, {})
    return {
        'faction_available_chance': row.get('factionAvailableChance'),
        'initial_unlock_chance': row.get('initialUnlockChance'),
        'delta_unlock_chance': row.get('deltaUnlockChance'),
        'max_unlock_chance': row.get('maxUnlockChance'),
        'one_time_globally': row.get('oneTimeGlobally', False),
        'repeatable': row.get('repeatable', False),
    }


def _prereq_statuses(data_name, templates, finished_techs, finished_projects):
    statuses = []
    done = finished_techs | finished_projects
    for prereq in _research_prereqs(data_name, templates):
        status = 'done' if prereq in done else 'missing'
        statuses.append({
            'data_name': prereq,
            'kind': _research_kind(prereq),
            'ui_name': _research_friendly(prereq, templates),
            'status': status,
        })
    return statuses


def build_research_options(snapshot):
    """Create the R0 research-option universe for agent dossiers.

    The manifest intentionally forces every emitted option into exactly one
    downstream state. Agents can then work from `needs_dossier` while the
    final assembly can still prove no active/queueable option silently vanished.
    """
    templates = snapshot.get('templates') or {}
    rs = snapshot.get('research') or {}
    player = snapshot.get('player_faction') or {}
    income = snapshot.get('research_income') or {}

    finished_techs = set(rs.get('finished_techs') or [])
    finished_projects = set(rs.get('finished_projects') or [])
    active_global = {t.get('name'): t for t in (rs.get('active_global_techs') or [])}
    active_projects = {p.get('name'): p for p in (rs.get('active_faction_projects') or [])}
    paused_projects = {p.get('name'): p for p in (rs.get('paused_faction_projects') or [])}
    available_projects = set(rs.get('available_projects') or [])
    hidden_projects = set(rs.get('hidden_projects') or [])
    auto_triggers = {t.get('name'): t for t in (rs.get('auto_triggers') or [])}
    queueable_globals = {t.get('data_name'): t for t in (snapshot.get('next_global_techs') or [])}
    unlockable = {u.get('data_name'): u for u in (snapshot.get('unlockable_projects') or [])}

    weights = rs.get('research_weights') or []
    total_weight = sum(weights) or 1
    monthly_research = income.get('total_per_month')

    options = {}

    def status_for(data_name):
        kind = _research_kind(data_name)
        if kind == 'global_tech':
            if data_name in finished_techs:
                return 'finished_global_tech'
            if data_name in active_global:
                return 'active_global'
            if data_name in queueable_globals:
                return 'queueable_global'
            return 'gated_global'
        if data_name in finished_projects:
            return 'finished_project'
        if data_name in active_projects:
            return 'active_faction_project'
        if data_name in paused_projects:
            return 'paused_project_with_progress'
        if data_name in available_projects:
            return 'available_project'
        if data_name in unlockable:
            return 'unlockable_rolling'
        if data_name in hidden_projects:
            return 'hidden_rolling'
        if data_name in auto_triggers:
            return 'auto_trigger'
        return 'gated_project'

    def default_downstream_state(data_name, status, buckets):
        if status.startswith('finished_'):
            return 'excluded_with_reason', 'already completed; included only as chain context'
        if data_name in ALWAYS_DOSSIER_RESEARCH_IDS:
            return 'needs_dossier', ''
        if status in {'active_global', 'active_faction_project', 'paused_project_with_progress'}:
            return 'needs_dossier', ''
        if 'queueable_at_next_opening' in buckets and data_name in {
            'AdvancedMissileWarfareDoctrine',
            'UltravioletCombatLasers',
            'PlasmaWeapons',
            'Coilguns',
            'FissionPulseDrives',
            'DeuteriumTritiumFusion',
            'MissiontotheOuterPlanets',
            'FallofEmpires',
            'OrbitalFighters',
        }:
            return 'needs_dossier', ''
        return 'deferred_with_reason', 'outside current critical-path or risk cluster; keep visible but do not spend dossier budget'

    def upsert(data_name, bucket, strategic_tag=None, source=None):
        if not data_name:
            return None
        kind = _research_kind(data_name)
        oid = f"{kind}:{data_name}"
        status = status_for(data_name)
        if oid not in options:
            tmpl_cost = _research_cost(data_name, templates)
            opt = {
                'option_id': oid,
                'kind': kind,
                'data_name': data_name,
                'ui_name': _research_friendly(data_name, templates),
                'category': _research_category(data_name, templates),
                'template_cost_rp': tmpl_cost,
                'displayed_cost_rp': None,
                'displayed_cost_note': 'not in save; verify in game UI if cost scaling matters',
                'status': status,
                'buckets': [],
                'strategic_tags': [],
                'prereqs': _prereq_statuses(data_name, templates, finished_techs, finished_projects),
                'current_progress_rp': None,
                'remaining_template_rp': tmpl_cost if tmpl_cost else None,
                'slot': None,
                'weight': None,
                'lane_share_pct': None,
                'own_lane_monthly_rp_estimate': None,
                'eta_months_at_current_weight_estimate': None,
                'eta_note': None,
                'unlock': _project_unlock_info(data_name, templates) if kind == 'project' else None,
                'source': source or [],
            }
            if status == 'active_global' and data_name in active_global:
                live = active_global[data_name]
                progress = live.get('total_progress') or 0
                opt['current_progress_rp'] = progress
                opt['remaining_template_rp'] = None
                # TIGlobalResearchState.techProgress order corresponds to slots 0-2.
                for idx, live_entry in enumerate(rs.get('active_global_techs') or []):
                    if live_entry.get('name') == data_name:
                        opt['slot'] = idx
                        if idx < len(weights):
                            opt['weight'] = weights[idx]
                        break
                opt['faction_contribution_rp'] = live.get('faction_contribution')
                opt['displayed_cost_note'] = (
                    'active global UI applies campaign research-rate cost scaling, category '
                    'bonuses, shared contributions, and other factions; use in-game Estimated '
                    'Completion unless those multipliers are explicitly captured'
                )
            elif status in {'active_faction_project', 'paused_project_with_progress'}:
                live = active_projects.get(data_name) or paused_projects.get(data_name) or {}
                progress = live.get('progress') or 0
                opt['current_progress_rp'] = progress
                opt['remaining_template_rp'] = max(0, tmpl_cost - progress) if tmpl_cost else None
                opt['slot'] = live.get('slot')
                opt['weight'] = live.get('weight')
                if status == 'active_faction_project':
                    opt['remaining_template_rp'] = None
                    opt['displayed_cost_note'] = (
                        'active project UI applies campaign research-rate cost scaling and '
                        'category bonuses; use in-game Estimated Completion unless those '
                        'multipliers are explicitly captured'
                    )
            elif status == 'unlockable_rolling' and data_name in unlockable:
                opt['unlockable_roll'] = unlockable[data_name]
            elif status == 'auto_trigger' and data_name in auto_triggers:
                opt['auto_trigger'] = auto_triggers[data_name]

            if opt['weight'] is not None:
                opt['lane_share_pct'] = round(opt['weight'] / total_weight * 100, 1)
                if monthly_research:
                    opt['own_lane_monthly_rp_estimate'] = round(monthly_research * opt['weight'] / total_weight, 1)
                    remaining = opt.get('remaining_template_rp')
                    if opt['status'] in {'active_global', 'active_faction_project'}:
                        opt['eta_note'] = (
                            'suppressed: extractor-only ETA is unsafe for active research; '
                            'read the UI Estimated Completion'
                        )
                    elif remaining is not None and opt['own_lane_monthly_rp_estimate']:
                        opt['eta_months_at_current_weight_estimate'] = round(
                            remaining / opt['own_lane_monthly_rp_estimate'], 1
                        )
            options[oid] = opt
        opt = options[oid]
        if bucket not in opt['buckets']:
            opt['buckets'].append(bucket)
        if strategic_tag and strategic_tag not in opt['strategic_tags']:
            opt['strategic_tags'].append(strategic_tag)
        if source:
            opt.setdefault('source', [])
            for item in source:
                if item not in opt['source']:
                    opt['source'].append(item)
        state, reason = default_downstream_state(data_name, opt['status'], set(opt['buckets']))
        # Preserve needs_dossier if any later bucket upgrades a previously deferred option.
        if opt.get('downstream_state') != 'needs_dossier' or state == 'needs_dossier':
            opt['downstream_state'] = state
            opt['downstream_reason'] = reason
        return opt

    for t in rs.get('active_global_techs') or []:
        upsert(t.get('name'), 'active_global', source=['save:active_global_techs'])
    for t in snapshot.get('next_global_techs') or []:
        upsert(t.get('data_name'), 'queueable_at_next_opening', source=['extract_next_global_techs'])
    for p in rs.get('active_faction_projects') or []:
        upsert(p.get('name'), 'active_faction_project', source=['save:currentProjectProgress'])
    for p in rs.get('paused_faction_projects') or []:
        upsert(p.get('name'), 'paused_project_with_progress', source=['save:currentProjectProgress'])
    for p in sorted(available_projects):
        upsert(p, 'available_faction_project', source=['save:availableProjectNames'])
    for p in snapshot.get('unlockable_projects') or []:
        upsert(p.get('data_name'), 'unlockable_rolling', source=['extract_unlockable_projects'])
    for p in rs.get('auto_triggers') or []:
        upsert(p.get('name'), 'auto_trigger', source=['save:activeProjectTriggers'])

    # Expand named strategic chains by actual prereqs, not a fixed depth limit.
    def add_with_prereq_closure(seed, chain_name, seen=None):
        seen = seen or set()
        if not seed or seed in seen:
            return
        seen.add(seed)
        upsert(seed, 'strategic_chain', strategic_tag=chain_name,
               source=[f'strategic_chain:{chain_name}'])
        for prereq in _research_prereqs(seed, templates):
            add_with_prereq_closure(prereq, chain_name, seen)

    for chain_name, seeds in STRATEGIC_RESEARCH_CHAINS.items():
        for seed in seeds:
            add_with_prereq_closure(seed, chain_name)

    options_list = sorted(
        options.values(),
        key=lambda o: (
            {'needs_dossier': 0, 'deferred_with_reason': 1, 'excluded_with_reason': 2}.get(o['downstream_state'], 9),
            o['kind'],
            o['category'],
            o['template_cost_rp'] or 0,
            o['ui_name'],
        )
    )
    manifest = []
    valid_states = {'needs_dossier', 'deferred_with_reason', 'excluded_with_reason'}
    for opt in options_list:
        state = opt.get('downstream_state')
        if state not in valid_states:
            raise ValueError(f"Research option {opt['option_id']} has invalid downstream_state {state!r}")
        manifest.append({
            'option_id': opt['option_id'],
            'bucket': ','.join(opt['buckets']),
            'downstream_state': state,
            'reason': opt.get('downstream_reason') or '',
        })

    return {
        'metadata': {
            'save_path': snapshot.get('save_path'),
            'game_date': snapshot.get('game_date'),
            'difficulty': snapshot.get('difficulty'),
            'save_version': snapshot.get('save_version'),
            'faction': snapshot.get('faction_name'),
            'monthly_research_estimate': monthly_research,
            'research_weights': weights,
        },
        'manifest': manifest,
        'options': options_list,
        'counts': Counter(item['downstream_state'] for item in manifest),
    }


def format_research_options_markdown(research_options):
    meta = research_options['metadata']
    options = research_options['options']
    counts = research_options['counts']
    lines = []
    lines.append(f"# Research options artifact — {meta.get('faction')}")
    lines.append("")
    lines.append(f"- **Game date:** {meta.get('game_date')}")
    lines.append(f"- **Save:** `{meta.get('save_path')}`")
    lines.append(f"- **Version:** {meta.get('save_version')} · **Difficulty:** {meta.get('difficulty')}")
    lines.append(f"- **Monthly research estimate:** {meta.get('monthly_research_estimate')}")
    lines.append(f"- **Research weights:** {meta.get('research_weights')}")
    lines.append("")
    lines.append("## Conservation manifest")
    lines.append("")
    lines.append("| State | Count |")
    lines.append("|---|---:|")
    for state in ['needs_dossier', 'deferred_with_reason', 'excluded_with_reason']:
        lines.append(f"| {state} | {counts.get(state, 0)} |")
    lines.append("")

    for state, title in [
        ('needs_dossier', 'Needs dossier'),
        ('deferred_with_reason', 'Deferred with reason'),
        ('excluded_with_reason', 'Excluded with reason'),
    ]:
        rows = [o for o in options if o['downstream_state'] == state]
        if not rows:
            continue
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| Status | Kind | Category | Template cost | Progress | Weight | ETA mo | ETA note | Name | Buckets | Reason |")
        lines.append("|---|---|---|---:|---:|---:|---:|---|---|---|---|")
        for o in rows:
            progress = o['current_progress_rp']
            progress_s = '' if progress is None else f"{progress:,.0f}"
            weight = o['weight']
            weight_s = '' if weight is None else str(weight)
            eta = o['eta_months_at_current_weight_estimate']
            eta_s = '' if eta is None else str(eta)
            eta_note = o.get('eta_note') or ''
            cost = o['template_cost_rp'] or 0
            reason = o.get('downstream_reason') or ''
            buckets = ', '.join(o.get('buckets') or [])
            lines.append(
                f"| {o['status']} | {o['kind']} | {o['category']} | {cost:,} | "
                f"{progress_s} | {weight_s} | {eta_s} | {eta_note} | {o['ui_name']} "
                f"| {buckets} | {reason} |"
            )
        lines.append("")
    return "\n".join(lines)


def write_research_options_artifacts(snapshot, output_dir):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    research_options = build_research_options(snapshot)

    options_json = output_path / 'research_options.json'
    manifest_json = output_path / 'manifest.json'
    options_md = output_path / 'research_options.md'

    options_json.write_text(
        json.dumps(make_json_safe(research_options), indent=2, ensure_ascii=False),
        encoding='utf-8',
    )
    manifest_json.write_text(
        json.dumps(make_json_safe(research_options['manifest']), indent=2, ensure_ascii=False),
        encoding='utf-8',
    )
    options_md.write_text(format_research_options_markdown(research_options), encoding='utf-8')

    return {
        'dir': str(output_path),
        'research_options_json': str(options_json),
        'manifest_json': str(manifest_json),
        'research_options_md': str(options_md),
        'counts': research_options['counts'],
    }


def extract_drive_comparison(gs, faction_id, templates):
    """Rank the player's currently-researched drives vs unresearched drives
    that are available to research. Sort by warship-relevant figure of merit
    that uses COMBAT thrust (cruise × thrustCap), not cruise thrust alone.

    Critical correction: TI drives have a `thrustCap` multiplier that represents
    the burst thrust available during combat maneuvering. Pharos has thrustCap
    16, Lodestar 20, Firestar 22 — these are designed for warships. But
    Poseidon Lantern ( template) has thrustCap=2 — it's a CRUISE/transit drive, not a
    combat drive, despite its high cruise thrust and EV. Ranking on cruise
    thrust alone misleads catastrophically (it suggests NFL > Lodestar when in
    combat Lodestar has ~8× the burst thrust of NFL).

    Goal: never recommend a drive for warship use without checking combat thrust.
    Also catches the "Pebble Drive / electric drives are worse than mid-tier
    fission" trap (electrics have tiny thrust + tiny thrustCap).
    """
    drive_specs = templates.get('drive_specs', {})
    if not drive_specs:
        return None

    # Player's finished + available drive projects
    fs = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
               if fid == faction_id), {})
    finished = set(fs.get('finishedProjectNames', []) or [])
    available = set(fs.get('availableProjectNames', []) or [])

    # Group drive variants (x1..x6) by their shared project. Each project
    # unlocks a family with multiple variants — show the user the full
    # variant spectrum so they can pick the right thruster count for the
    # hull they're designing (NOT just the max). Previous version deduped
    # by project and kept the smallest variant, which made tables show
    # "Lodestar x1 = 220 MN combat" without revealing x6 = 1,320 MN.
    import re as _re
    families = {}  # project -> list of variant dicts (one per x1..x6)
    for drive_name, spec in drive_specs.items():
        proj = spec.get('required_project')
        if not proj:
            continue
        ev = spec.get('ev_kps', 0) or 0
        if ev < 10:  # chemical / sub-orbital — irrelevant for in-space combat
            continue
        if proj in finished:
            status = 'RESEARCHED'
        elif proj in available:
            status = 'AVAILABLE'
        else:
            continue
        # Parse the thruster count from the dataName suffix
        m = _re.search(r'x(\d+)$', drive_name)
        n_thrusters = int(m.group(1)) if m else 1
        thrust = spec.get('thrust_N', 0) or 0
        thrust_cap = spec.get('thrust_cap', 1) or 1
        families.setdefault(proj, []).append({
            'n_thrusters': n_thrusters,
            'drive': spec.get('friendly', drive_name),
            'data_name': drive_name,
            'thrust_N': thrust,
            'thrust_cap': thrust_cap,
            'combat_thrust_N': spec.get('combat_thrust_N', 0) or (thrust * thrust_cap),
            'ev_kps': ev,
            'class': spec.get('class', '?'),
            'required_reactor': spec.get('required_reactor'),
            'required_reactor_display': spec.get('required_reactor_display') or spec.get('required_reactor'),
            'cooling': spec.get('cooling', '?'),
            'project': proj,
            'status': status,
            'req_power_GW': spec.get('req_power_GW', 0),
        })

    # For each family, sort variants by thruster count and build a summary
    # row using the max variant (typically x6) for sorting. Keep all
    # variants in a nested 'variants' list for the report renderer.
    rows = []
    for proj, variants in families.items():
        variants.sort(key=lambda v: v['n_thrusters'])
        max_variant = variants[-1]
        row = dict(max_variant)   # use max for top-level stats
        row['variants'] = variants
        # Warship FoM uses COMBAT thrust (cruise × thrustCap) at the
        # max-variant level — same as before. Cruise alone mis-ranks drives
        # like Poseidon Lantern (huge cruise but 2× combat cap).
        row['fom_warship'] = row['combat_thrust_N'] * (row['ev_kps'] ** 0.5)
        row['fom_cruise']  = row['thrust_N'] * (row['ev_kps'] ** 0.5)
        rows.append(row)
    rows.sort(key=lambda x: -x['fom_warship'])
    return rows


def extract_ship_refit_candidates(gs, faction_id):
    """Group player ship designs by reactor family, surface within-family refit
    opportunities and "stranded at family max" designs.

    REFIT RULE (enforced by the in-game refit UI): a ship's reactor can ONLY be
    swapped to a higher tier in the *same family*. Cross-family refits
    (SolidCore X → GasCore IV, MoltenSalt I → GasCore IV, etc.) are blocked.

    Returns dict with:
      'refit_candidates': list of designs where a within-family upgrade exists
      'stranded': list of designs at the family max for their reactor
      'family_max': {family: highest_tier_str_finished}
      'live_per_design': {design_dataName: live_ship_count}
    """
    import re
    fac = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
                if fid == faction_id), {})
    finished = set(fac.get('finishedProjectNames', []) or [])
    designs = fac.get('shipDesigns', []) or []
    obsolete = set(fac.get('obsoleteShipDesigns', []) or [])

    # Roman numeral parser. Reactor templates use I, II, III, IV, V, VI, VII,
    # VIII, IX, X (TI's fission ladders top out at X in SolidCore so far).
    ROMAN = {'I':1,'II':2,'III':3,'IV':4,'V':5,'VI':6,'VII':7,'VIII':8,'IX':9,'X':10,
             'XI':11,'XII':12}
    ROMAN_RE = re.compile(r'^(.*?)(I{1,3}|IV|VI{0,3}|IX|X{1,2})$')

    def parse_family_tier(name):
        """'SolidCoreFissionReactorVIII' → ('SolidCoreFissionReactor', 8).
        Returns (None, None) if the name doesn't end in a Roman numeral."""
        if not name:
            return None, None
        m = ROMAN_RE.match(name)
        if not m:
            return name, None  # no tier suffix (e.g., a unique reactor)
        family, tier_roman = m.group(1), m.group(2)
        return family, ROMAN.get(tier_roman)

    # Build family → max researched tier from finished project names.
    family_max = {}
    family_max_roman = {}
    for proj in finished:
        if not proj.startswith('Project_'):
            continue
        bare = proj[len('Project_'):]
        # Only consider reactor projects (heuristic)
        if 'Reactor' not in bare and 'Lantern' not in bare:
            continue
        family, tier = parse_family_tier(bare)
        if family is None or tier is None:
            continue
        if tier > family_max.get(family, 0):
            family_max[family] = tier
            # Recover the Roman numeral string
            for rom, n in ROMAN.items():
                if n == tier:
                    family_max_roman[family] = rom
                    break

    # Live ship counts per design dataName (via fleet → ship → faction).
    fleets = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceFleetState'))
    ship_to_fac = {}
    for fid_, fv in fleets.items():
        fac_id_ = fac_id_from_field(fv.get('faction'))
        for s in (fv.get('ships') or []):
            sid = deref(s) if isinstance(s, dict) else s
            ship_to_fac[sid] = fac_id_
    live_per_design = Counter()
    for sid, sv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceShipState'):
        if ship_to_fac.get(sid) == faction_id:
            live_per_design[sv.get('templateName')] += 1

    refit_candidates = []
    stranded = []
    for d in designs:
        data = d.get('dataName')
        reactor = d.get('powerPlantName') or ''
        family, tier = parse_family_tier(reactor)
        live = live_per_design.get(data, 0)
        if live == 0:
            continue  # no live ships, refit moot
        entry = {
            'design': data,
            'name': d.get('_displayName') or d.get('friendlyName') or d.get('hullName'),
            'hull': d.get('hullName'),
            'reactor': reactor,
            'drive': d.get('driveName'),
            'family': family,
            'tier': tier,
            'live': live,
            'is_obsoleted_design': data in obsolete,
        }
        if family is None or tier is None:
            entry['status'] = 'unknown'
            stranded.append(entry)
            continue
        fmax = family_max.get(family)
        if fmax is None:
            entry['status'] = 'unknown-family'
            stranded.append(entry)
            continue
        if tier < fmax:
            entry['refit_target_tier_roman'] = family_max_roman[family]
            entry['refit_target_data'] = family + family_max_roman[family]
            entry['tier_gap'] = fmax - tier
            entry['status'] = 'refit-available'

            # CHECK: is this reactor refit actually beneficial, or does the
            # drive not need the extra wattage? A higher-tier reactor with
            # higher specificPower_tGW (Terawatt I @ 10 t/GW vs Gas Core III
            # @ 3 t/GW) ADDS mass without giving the drive more thrust unless
            # the drive's req_power_GW exceeds the current reactor's max.
            templates = (gs.get('templates') or {})
            r_specs = templates.get('reactor_specs') or {}
            d_specs = templates.get('drive_specs') or {}
            drive_name = d.get('driveName') or ''
            # Determine the per-drive-group power need by stacking the x1..x6 number
            import re as _re
            m_x = _re.search(r'x(\d+)$', drive_name)
            n_thr = int(m_x.group(1)) if m_x else 1
            # Find the drive's x1 entry to read req_power_GW
            drive_base = _re.sub(r'x\d+$', 'x1', drive_name) if drive_name else None
            drive_req_GW = (d_specs.get(drive_base, {}).get('req_power_GW') or 0) * n_thr if drive_base else 0
            cur_max_GW = r_specs.get(reactor, {}).get('maxOutput_GW', 0)
            tgt_max_GW = r_specs.get(entry['refit_target_data'], {}).get('maxOutput_GW', 0)
            cur_spec_t_per_GW = r_specs.get(reactor, {}).get('specificPower_tGW', 0)
            tgt_spec_t_per_GW = r_specs.get(entry['refit_target_data'], {}).get('specificPower_tGW', 0)
            entry['drive_req_power_GW'] = drive_req_GW
            entry['cur_reactor_max_GW'] = cur_max_GW
            entry['tgt_reactor_max_GW'] = tgt_max_GW
            entry['cur_spec_t_per_GW'] = cur_spec_t_per_GW
            entry['tgt_spec_t_per_GW'] = tgt_spec_t_per_GW
            # Compute the mass delta the refit would add at the drive's
            # actual power point (the smaller of drive_req or reactor_max).
            power_point = drive_req_GW or min(cur_max_GW, tgt_max_GW)
            if power_point > 0 and cur_spec_t_per_GW > 0 and tgt_spec_t_per_GW > 0:
                cur_mass = power_point * cur_spec_t_per_GW
                tgt_mass = power_point * tgt_spec_t_per_GW
                entry['reactor_mass_delta_t'] = tgt_mass - cur_mass
            # Harmful = drive doesn't need more wattage AND target reactor is
            # heavier per GW at the drive's actual power point.
            if (drive_req_GW > 0
                    and cur_max_GW >= drive_req_GW
                    and tgt_spec_t_per_GW > cur_spec_t_per_GW):
                entry['harmful_refit'] = True
                entry['harmful_reason'] = (
                    f"Drive needs {drive_req_GW:.0f} GW; current reactor already supplies "
                    f"{cur_max_GW:.0f} GW. Target reactor adds "
                    f"{tgt_spec_t_per_GW - cur_spec_t_per_GW:.1f} t/GW × {drive_req_GW:.0f} GW = "
                    f"+{(tgt_spec_t_per_GW - cur_spec_t_per_GW)*drive_req_GW:.0f}t dead mass "
                    f"with no thrust gain. Skip unless also refitting drive to a "
                    f"higher-power model."
                )
            refit_candidates.append(entry)
        else:
            entry['status'] = 'family-max'
            stranded.append(entry)

    # Sort refit candidates by (tier gap desc, live count desc)
    refit_candidates.sort(key=lambda e: (-e.get('tier_gap', 0), -e['live']))
    stranded.sort(key=lambda e: -e['live'])

    return {
        'family_max': family_max,
        'family_max_roman': family_max_roman,
        'refit_candidates': refit_candidates,
        'stranded': stranded,
        'live_per_design': dict(live_per_design),
    }


def extract_player_ship_instances(gs, faction_id, factions):
    """Resolve live player ships to class, fleet location, design loadout,
    and queued-refit status.

    The class-level refit table is useful for broad planning, but metal-tight
    decisions need instance-level priority: a monitor at Earth and one
    at Callisto are not the same strategic asset.
    """
    faction = factions.get(faction_id, {})
    designs = {d.get('dataName'): d for d in (faction.get('shipDesigns') or [])
               if d.get('dataName')}
    obsolete = set(faction.get('obsoleteShipDesigns') or [])

    space_bodies = {bid: bv.get('displayName') or bv.get('templateName') or '?'
                    for bid, bv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceBodyState')}
    fleets = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceFleetState'))
    habs = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'))

    queued_refits = {}
    for q in faction.get('nShipyardQueues') or []:
        for item in q.get('Value') or []:
            if not item.get('isRefit'):
                continue
            sid = deref(item.get('originalSpaceShipState'))
            if sid is None:
                continue
            refit_cost = {}
            for c in (item.get('resourcesCost') or {}).get('resourceCosts') or []:
                refit_cost[c.get('resource')] = c.get('value')
            queued_refits[sid] = {
                'to_design': item.get('shipDesignTemplateName'),
                'from_design': item.get('refit_originalShipDesignTemplateName'),
                'days_to_completion': item.get('daysToCompletion'),
                'cost': refit_cost,
            }

    rows = []
    for sid, sv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceShipState'):
        flid = deref(sv.get('fleet'))
        fleet = fleets.get(flid, {})
        if fac_id_from_field(fleet.get('faction')) != faction_id:
            continue

        design_id = sv.get('templateName')
        design = designs.get(design_id, {})
        body_id = deref(fleet.get('barycenter')) or deref(fleet.get('dockedLocation'))
        dock_hab = habs.get(deref(fleet.get('dockedLocation')), {})
        dock_fid = fac_id_from_field(dock_hab.get('faction')) if dock_hab else None
        dock_fac = factions.get(dock_fid, {}) if dock_fid is not None else {}
        qrefit = queued_refits.get(sid)

        utility_modules = [m.get('moduleName') for m in (design.get('moduleTemplateEntries') or [])
                           if m.get('moduleName')]
        hull_weapons = [m.get('moduleName') for m in (design.get('hullWeaponTemplateEntries') or [])
                        if m.get('moduleName')]
        nose_weapons = [m.get('moduleName') for m in (design.get('noseWeaponTemplateEntries') or [])
                        if m.get('moduleName')]

        row = {
            'ship_id': sid,
            'ship_name': sv.get('displayName') or '?',
            'design': design_id,
            'class_name': (design.get('_displayName') or design.get('friendlyName')
                           or design.get('hullName') or design_id),
            'hull': design.get('hullName'),
            'drive': design.get('driveName'),
            'reactor': design.get('powerPlantName'),
            'radiator': design.get('radiatorName'),
            'propellant_tanks': design.get('propellantTanks'),
            'armor': {
                'material': ((design.get('noseArmor') or {}).get('materialName')
                             or (design.get('lateralArmor') or {}).get('materialName')
                             or (design.get('tailArmor') or {}).get('materialName')),
                'nose': (design.get('noseArmor') or {}).get('armorValue'),
                'lateral': (design.get('lateralArmor') or {}).get('armorValue'),
                'tail': (design.get('tailArmor') or {}).get('armorValue'),
            },
            'utility_modules': utility_modules,
            'hull_weapons': hull_weapons,
            'nose_weapons': nose_weapons,
            'role': design.get('role'),
            'design_obsoleted': design_id in obsolete,
            'fleet_id': flid,
            'fleet_name': fleet.get('displayName') or '(unnamed)',
            'location': space_bodies.get(body_id, '?') if body_id is not None else '?',
            'docked_at': dock_hab.get('displayName') if dock_hab else None,
            'docked_at_faction': ((dock_fac.get('displayName') or dock_fac.get('templateName'))
                                  if dock_fac else None),
            'current_mass_t': (sv.get('currentMass_kg') or 0) / 1000,
            'delta_v_kps': sv.get('currentMaxDeltaV_kps') or sv.get('currentDeltaV_kps'),
            'combat_accel_mps2': sv.get('combatAcceleration_mps2'),
            'combat_accel_g': ((sv.get('combatAcceleration_mps2') or 0) / 9.81),
            'cruise_accel_mps2': sv.get('cruiseAcceleration_mps2'),
            'mission_control': sv.get('missionControlConsumption'),
            'queued_refit': qrefit is not None,
            'queued_refit_info': qrefit,
        }

        if qrefit:
            to_design = designs.get(qrefit.get('to_design'), {})
            from_design = designs.get(qrefit.get('from_design'), {})
            row['queued_refit_info']['to_class_name'] = (
                to_design.get('_displayName') or to_design.get('friendlyName') or qrefit.get('to_design'))
            row['queued_refit_info']['from_class_name'] = (
                from_design.get('_displayName') or from_design.get('friendlyName') or qrefit.get('from_design'))

        rows.append(row)

    rows.sort(key=lambda r: (r['location'] or '', r['fleet_name'] or '',
                             r['class_name'] or '', r['ship_name'] or ''))
    return rows


def _lenient_load_json(path):
    """Load a TI template file that may use the game's lenient JSON dialect
    (trailing commas). Tries strict json first, then strips trailing commas."""
    import re as _re
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:  # BOM: Windows saves
            raw = f.read()
    except OSError:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return json.loads(_re.sub(r',(\s*[}\]])', r'\1', raw))
        except json.JSONDecodeError:
            return None


def extract_councilor_attrition(gs, faction_id, factions):
    """Roster-integrity check (deep-review Phase 1, 2026-06-11).

    Answers "why does the extract show N councilors when the doctrine says
    we run a stable roster?" The save does NOT retain dead/dismissed
    councilors — they are deleted from TICouncilorState entirely. The only
    ghosts left behind are entries in the faction's own bookkeeping maps:

    - `internalCouncilorSuspicion` keys = councilors the faction tracked as
      ITS OWN members (suspicion-of-defection meter). Keys that no longer
      exist in TICouncilorState, or now belong to another faction, are
      former members.
    - `turnedCouncilors` = councilors this faction successfully Turned. They
      stay on the RIVAL's roster but carry `agentForFaction` = us (double
      agents). They are NOT part of our 6-slot roster.
    - `lastRecordedLoyalty` keys include both own members and investigated
      enemies — only usable as corroboration, not membership proof.
    """
    councilors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TICouncilorState'))
    fac_tpl = {fid: fv.get('templateName') for fid, fv in factions.items()}
    pf = factions.get(faction_id, {})

    active = []
    for cid, c in councilors.items():
        if fac_id_from_field(c.get('faction')) == faction_id:
            rd = c.get('recruitDate') or {}
            active.append({
                'id': cid,
                'name': f"{c.get('personalName','?')} {c.get('familyName','?')}",
                'type': c.get('typeTemplateName','?'),
                'recruited': f"{rd.get('year','?')}-{rd.get('month',0):02d}-{rd.get('day',0):02d}" if rd else '?',
                'status': c.get('status'),
            })
    active_ids = {a['id'] for a in active}

    # Per-faction roster sizes (sanity: what does "full roster" look like now?)
    per_faction = Counter()
    for cid, c in councilors.items():
        fid_ = fac_id_from_field(c.get('faction'))
        per_faction[fac_tpl.get(fid_, 'unfactioned' if fid_ is None else fid_)] += 1

    # Double agents we control inside rival councils
    turned_agents = []
    for ref in (pf.get('turnedCouncilors') or []):
        cid = deref(ref)
        c = councilors.get(cid)
        if not c:
            turned_agents.append({'id': cid, 'name': '(no longer in save)',
                                  'current_faction': None, 'still_our_agent': False})
            continue
        host_fid = fac_id_from_field(c.get('faction'))
        turned_agents.append({
            'id': cid,
            'name': f"{c.get('personalName','?')} {c.get('familyName','?')}",
            'type': c.get('typeTemplateName','?'),
            'current_faction': fac_tpl.get(host_fid, host_fid),
            'still_our_agent': deref(c.get('agentForFaction')) == faction_id,
        })

    # Former own members — ghosts in internalCouncilorSuspicion
    former = []
    for e in (pf.get('internalCouncilorSuspicion') or []):
        cid = (e.get('Key') or {}).get('value')
        if cid is None or cid in active_ids:
            continue
        c = councilors.get(cid)
        if c is None:
            former.append({'id': cid, 'name': '(record purged from save)',
                           'fate': 'dead or dismissed — TI deletes the state'})
        else:
            host_fid = fac_id_from_field(c.get('faction'))
            former.append({'id': cid,
                           'name': f"{c.get('personalName','?')} {c.get('familyName','?')}",
                           'fate': f"now on {fac_tpl.get(host_fid, host_fid)} roster (defected/turned away)"})

    return {
        'active': sorted(active, key=lambda a: a['id']),
        'active_count': len(active),
        'per_faction_counts': dict(per_faction),
        'turned_agents': turned_agents,
        'former_members': former,
        'recruit_pool_offered': len(pf.get('availableCouncilors') or []),
    }


def extract_rival_victory_race(gs, factions, templates):
    """Per-faction victory-chain status + formation-project (one-time-globally)
    race table (deep-review Phase 1, 2026-06-11).

    Victory chains come from VICTORY_CHAINS; status per faction is derived
    from finishedProjectNames / currentProjectProgress / availableProjectNames.
    Formation projects = TIProjectTemplate entries with `oneTimeGlobally`
    (first faction to finish removes it for everyone — the actual race).
    """
    # CP counts per faction (filled control points)
    cp_count = Counter()
    fac_tpl = {fid: fv.get('templateName') for fid, fv in factions.items()}
    for cpid, cv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIControlPoint'):
        fid_ = fac_id_from_field(cv.get('faction'))
        if fid_ is not None:
            cp_count[fac_tpl.get(fid_, fid_)] += 1

    rows = []
    for fid, fv in factions.items():
        tpl = fv.get('templateName')
        if tpl == 'AlienCouncil':
            continue
        chain = VICTORY_CHAINS.get(tpl, [])
        fin = set(fv.get('finishedProjectNames') or [])
        avail = set(fv.get('availableProjectNames') or [])
        prog = {p.get('projectTemplateName'): p.get('accumulatedResearch', 0)
                for p in (fv.get('currentProjectProgress') or [])}
        steps = []
        done_steps = 0
        for proj, label in chain:
            if proj in fin:
                status = 'DONE'; done_steps += 1
            elif proj in prog:
                status = f'IN-PROGRESS {prog[proj]:,.0f}'
            elif proj in avail:
                status = 'AVAILABLE'
            else:
                status = 'locked'
            steps.append({'project': proj, 'label': label, 'status': status})
        rows.append({
            'template': tpl,
            'name': FACTION_DISPLAY.get(tpl, tpl),
            'chain_steps': steps,
            'chain_done': done_steps,
            'chain_len': len(chain),
            'cached_research_yr': (fv.get('cachedYearlyRevenue') or {}).get('Research', 0),
            'cps': cp_count.get(tpl, 0),
            'top_active_projects': sorted(((p, round(v)) for p, v in prog.items() if p),
                                          key=lambda x: -x[1])[:5],
        })
    rows.sort(key=lambda r: (-r['chain_done'], -r['cached_research_yr']))

    # Formation-project race
    formation = []
    tdir = find_templates_dir()
    if tdir:
        projects = _lenient_load_json(os.path.join(tdir, 'TIProjectTemplate.json')) or []
        friendly = templates.get('project_friendly', {}) if templates else {}
        onetime = [p for p in projects if p.get('oneTimeGlobally')]
        for p in onetime:
            dn = p['dataName']
            done_by, in_prog_by, avail_for = [], [], []
            for fid, fv in factions.items():
                tpl = fv.get('templateName')
                if tpl == 'AlienCouncil':
                    continue
                nm = FACTION_DISPLAY.get(tpl, tpl)
                if dn in set(fv.get('finishedProjectNames') or []):
                    done_by.append(nm)
                else:
                    prog = {pp.get('projectTemplateName'): pp.get('accumulatedResearch', 0)
                            for pp in (fv.get('currentProjectProgress') or [])}
                    if dn in prog:
                        in_prog_by.append(f"{nm} ({prog[dn]:,.0f}/{p.get('researchCost')})")
                    elif dn in set(fv.get('availableProjectNames') or []):
                        avail_for.append(nm)
            if done_by or in_prog_by or avail_for:
                formation.append({
                    'dataName': dn,
                    'friendly': friendly.get(dn, p.get('friendlyName', dn)),
                    'requiresNation': p.get('requiresNation') or '-',
                    'cost': p.get('researchCost'),
                    'done_by': done_by,
                    'in_progress_by': in_prog_by,
                    'available_for': avail_for,
                })
    return {'factions': rows, 'formation_race': formation}


def extract_earth_orbit_threat(gs, faction_id, factions):
    """Earth-orbit force balance (deep-review Phase 1, 2026-06-11).

    Hostile + friendly fleets whose barycenter resolves to Earth, broken down
    by ship/tonnage/weapons; the player's LEO hab defenses (defense modules
    via sector→hab mapping — note module states point at `sector`, NOT hab);
    and rival LEO habs (their staging points / targets).
    """
    fac_tpl = {fid: fv.get('templateName') for fid, fv in factions.items()}
    space_bodies = {bid: bv.get('displayName') or bv.get('templateName') or '?'
                    for bid, bv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceBodyState')}
    fleets = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceFleetState'))
    ships = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceShipState'))
    habs = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'))

    earth_fleets = []
    for flid, fv in fleets.items():
        bid = deref(fv.get('barycenter')) or deref(fv.get('dockedLocation'))
        loc = space_bodies.get(bid) if bid is not None else None
        shiplist = [deref(s) for s in (fv.get('ships') or [])]
        if loc != 'Earth' or not shiplist:
            continue
        fid_ = fac_id_from_field(fv.get('faction'))
        dock_id = deref(fv.get('dockedLocation'))
        dock_hab = habs.get(dock_id, {})
        weapons = Counter()
        classes = Counter()
        mass = 0.0
        for sid in shiplist:
            sv = ships.get(sid, {})
            mass += (sv.get('currentMass_kg') or 0) / 1e6
            classes[sv.get('displayName', '?')] += 1
            for m in (sv.get('noseWeapons') or []) + (sv.get('hullWeapons') or []):
                n = m.get('moduleTemplateName') if isinstance(m, dict) else None
                if n:
                    weapons[n] += 1
        earth_fleets.append({
            'fleet_id': flid,
            'fleet_name': fv.get('displayName') or '(unnamed)',
            'faction': FACTION_DISPLAY.get(fac_tpl.get(fid_), fac_tpl.get(fid_, '?')),
            'is_player': fid_ == faction_id,
            'ships': len(shiplist),
            'mass_kt': round(mass, 1),
            'ship_classes': dict(classes),
            'weapons': dict(weapons),
            'docked_at': (dock_hab.get('displayName') if dock_hab else None),
            'docked_at_faction': FACTION_DISPLAY.get(
                fac_tpl.get(fac_id_from_field(dock_hab.get('faction'))), None) if dock_hab else None,
        })
    earth_fleets.sort(key=lambda r: (r['is_player'], -r['mass_kt']))

    # Player LEO hab defenses (module state → sector → hab)
    sectors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISectorState'))
    sec_to_hab = {sid: deref(sv.get('hab')) for sid, sv in sectors.items()}
    DEFENSE_KEYS = ('DefenseArray', 'Battlestations', 'MarinePlatoonBarracks',
                    'MarineCompanyBarracks', 'MarineBattalionBarracks', 'SentinelComplex')
    leo_defenses = defaultdict(Counter)
    player_leo_habs = []
    for hid, hv in habs.items():
        if fac_id_from_field(hv.get('faction')) == faction_id and hv.get('inEarthLEO'):
            player_leo_habs.append(hv.get('displayName', '?'))
    for mid, mv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'):
        hid = sec_to_hab.get(deref(mv.get('sector')))
        h = habs.get(hid)
        if not h or fac_id_from_field(h.get('faction')) != faction_id or not h.get('inEarthLEO'):
            continue
        tn = mv.get('templateName') or ''
        if any(k in tn for k in DEFENSE_KEYS):
            if mv.get('constructionCompleted'):
                st = 'powered' if mv.get('powered') else 'UNPOWERED'
            else:
                st = 'building'
            leo_defenses[h.get('displayName', '?')][f"{tn} [{st}]"] += 1
    undefended = [h for h in player_leo_habs if h not in leo_defenses]

    # Rival habs in Earth LEO (staging points)
    rival_leo_habs = []
    for hid, hv in habs.items():
        fid_ = fac_id_from_field(hv.get('faction'))
        if hv.get('inEarthLEO') and fid_ is not None and fid_ != faction_id:
            tpl = fac_tpl.get(fid_)
            if tpl == 'AlienCouncil':
                continue
            rival_leo_habs.append({'hab': hv.get('displayName', '?'),
                                   'faction': FACTION_DISPLAY.get(tpl, tpl),
                                   'tier': hv.get('tier')})

    return {
        'earth_fleets': earth_fleets,
        'player_leo_habs': sorted(player_leo_habs),
        'leo_defenses': {k: dict(v) for k, v in sorted(leo_defenses.items())},
        'undefended_leo_habs': sorted(undefended),
        'rival_leo_habs': sorted(rival_leo_habs, key=lambda r: r['faction']),
    }


def extract_economy_ledger(gs, faction_id, game_date_str):
    """Cash-flow forensics from the faction Transactions ledger
    (deep-review Phase 1, 2026-06-11).

    The ledger keeps ~12 months of per-category entries. 'Daily Income' is
    the NET passive daily tick (gross incomes minus org upkeep, hab support
    materials, ship maintenance, CP influence maintenance) — this is why it
    runs far below `cachedYearlyRevenue` (which is gross). Mission costs,
    org purchases, augmentations etc. appear as separate categories.
    """
    from datetime import date as _date
    pf = next((fv for fid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIFactionState')
               if fid == faction_id), {})
    tr = pf.get('Transactions') or {}
    try:
        y, m, d = (int(x) for x in game_date_str.split('-'))
        today = _date(y, m, d)
    except (ValueError, AttributeError):
        return None

    def d2(dd):
        try:
            return _date(dd['year'], dd['month'], dd['day'])
        except (KeyError, TypeError, ValueError):
            return None

    flows = {}
    for window in (30, 90):
        per_res = defaultdict(lambda: defaultdict(float))
        for cat, entries in tr.items():
            for e in entries or []:
                ed = d2(e.get('Date') or {})
                if ed and (today - ed).days <= window:
                    per_res[e.get('Resource')][cat] += e.get('Amount', 0)
        flows[window] = {res: dict(cats) for res, cats in per_res.items()}

    # Recent daily net income (last 7 days mean per resource)
    recent = defaultdict(list)
    for e in tr.get('Daily Income', []) or []:
        ed = d2(e.get('Date') or {})
        if ed and (today - ed).days <= 7:
            recent[e.get('Resource')].append(e.get('Amount', 0))
    daily_net = {res: round(sum(v) / len(v), 2) for res, v in recent.items() if v}

    # Org portfolio
    orgs = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIOrgState'))
    councilors = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TICouncilorState'))
    org_rows = []

    def add_org(oid, holder):
        o = orgs.get(oid)
        if not o:
            return
        org_rows.append({
            'name': o.get('displayName') or o.get('templateName'),
            'tier': o.get('tier'),
            'holder': holder,
            'money_mo': o.get('incomeMoney_month', 0) or 0,
            'influence_mo': o.get('incomeInfluence_month', 0) or 0,
            'ops_mo': o.get('incomeOps_month', 0) or 0,
            'boost_mo': o.get('incomeBoost_month', 0) or 0,
            'research_mo': o.get('incomeResearch_month', 0) or 0,
            'mc': o.get('incomeMissionControl', 0) or 0,
            'proj_capacity': o.get('projectCapacityGranted', 0) or 0,
        })

    for cid, c in councilors.items():
        if fac_id_from_field(c.get('faction')) != faction_id:
            continue
        nm = f"{c.get('personalName','?')} {c.get('familyName','?')}"
        for oref in (c.get('orgs') or []):
            add_org(deref(oref), nm)
    for oref in (pf.get('unassignedOrgs') or []):
        add_org(deref(oref), '(unassigned)')
    org_totals = defaultdict(float)
    for r in org_rows:
        for k in ('money_mo', 'influence_mo', 'ops_mo', 'boost_mo', 'research_mo',
                  'mc', 'proj_capacity'):
            org_totals[k] += r[k]

    # Exotics / Antimatter provenance — every ledger entry ever
    rare = []
    for cat, entries in tr.items():
        for e in entries or []:
            if e.get('Resource') in ('Exotics', 'Antimatter'):
                ed = e.get('Date') or {}
                rare.append({'date': f"{ed.get('year','?')}-{ed.get('month',0):02d}-{ed.get('day',0):02d}",
                             'category': cat, 'resource': e.get('Resource'),
                             'amount': e.get('Amount', 0)})
    rare.sort(key=lambda r: r['date'])

    return {
        'flows': flows,
        'daily_net_7d_avg': daily_net,
        'org_rows': org_rows,
        'org_totals': dict(org_totals),
        'org_count': len(org_rows),
        'rare_resource_ledger': rare,
        'base_incomes_year': pf.get('baseIncomes_year') or {},
        'cached_yearly': pf.get('cachedYearlyRevenue') or {},
    }


def extract_alien_posture(gs, faction_id, factions):
    """Intel-filtered alien order of battle (deep-review Phase 1, 2026-06-11).

    SPOILER GUARD: only fleets/habs present in the player faction's `intel`
    map with value > 0 are reported with identifying detail. Unknown assets
    are returned as counts only. (Same hidden-info discipline as
    extract_alien_pressure's knownAlienSites filter.)
    """
    alien_fid = next((fid for fid, fv in factions.items()
                      if fv.get('templateName') == 'AlienCouncil'), None)
    pf = factions.get(faction_id, {})
    intel = {}
    for e in (pf.get('intel') or []):
        k = e.get('Key') or {}
        intel[((k.get('$type') or '').split('.')[-1], k.get('value'))] = e.get('Value', 0)

    space_bodies = {bid: bv.get('displayName') or bv.get('templateName') or '?'
                    for bid, bv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceBodyState')}
    hab_sites = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabSiteState'))
    orbits = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIOrbitState'))
    ships = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceShipState'))

    known_fleets, unknown_fleets = [], 0
    unknown_ships = 0
    loc_rollup = Counter()
    for flid, fv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TISpaceFleetState'):
        if fac_id_from_field(fv.get('faction')) != alien_fid:
            continue
        shiplist = [deref(s) for s in (fv.get('ships') or [])]
        if not shiplist:
            continue
        iv = intel.get(('TISpaceFleetState', flid), 0) or 0
        bid = deref(fv.get('barycenter')) or deref(fv.get('dockedLocation'))
        loc = space_bodies.get(bid, '?') if bid is not None else '?'
        mass = sum((ships.get(s, {}).get('currentMass_kg') or 0) for s in shiplist) / 1e6
        if iv > 0:
            known_fleets.append({'fleet': fv.get('displayName') or '(unnamed)',
                                 'intel': iv, 'location': loc,
                                 'ships': len(shiplist), 'mass_kt': round(mass, 1)})
            loc_rollup[loc] += len(shiplist)
        else:
            unknown_fleets += 1
            unknown_ships += len(shiplist)
    known_fleets.sort(key=lambda r: -r['mass_kt'])

    known_habs, unknown_habs = [], 0
    for hid, hv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'):
        if fac_id_from_field(hv.get('faction')) != alien_fid:
            continue
        iv = intel.get(('TIHabState', hid), 0) or 0
        if iv <= 0:
            unknown_habs += 1
            continue
        site = hab_sites.get(deref(hv.get('habSite')), {})
        body = space_bodies.get(deref(site.get('parentBody')))
        if not body:
            ob = orbits.get(deref(hv.get('orbitState')), {})
            body = ob.get('displayName') or ob.get('templateName') or '?'
        known_habs.append({'hab': hv.get('displayName', '?'), 'intel': iv,
                           'location': body, 'tier': hv.get('tier'),
                           'type': hv.get('habType')})
    known_habs.sort(key=lambda r: r['location'])

    # Xenoforming status (TIRegionXenoformingState.xenoformingLevel > 0) +
    # built alien ground facilities. NOTE: the save stores xenoforming levels
    # globally (no per-faction detection flag on the region state itself);
    # the player gets LogXenoformingDetected notifications, so treat as
    # player-visible but flag it in the report.
    regions = dict(kv_items(gs, 'PavonisInteractive.TerraInvicta.TIRegionState'))
    xeno_regions = []
    for xid, x in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIRegionXenoformingState'):
        lvl = x.get('xenoformingLevel', 0) or 0
        if lvl > 0:
            r = regions.get(deref(x.get('region')), {})
            xeno_regions.append({'region': r.get('displayName', '?'),
                                 'level': round(lvl, 1)})
    xeno_regions.sort(key=lambda r: -r['level'])

    alien_ground = []
    for aid, a in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIRegionAlienFacilityState'):
        if a.get('built'):
            r = regions.get(deref(a.get('region')), {})
            alien_ground.append({'region': r.get('displayName', '?'),
                                 'facility': a.get('templateName', '?'),
                                 'hp': a.get('currentHP')})

    return {
        'known_fleets': known_fleets,
        'known_fleet_ship_total': sum(f['ships'] for f in known_fleets),
        'unknown_fleet_count': unknown_fleets,
        'unknown_ship_count': unknown_ships,
        'ships_by_location': dict(loc_rollup.most_common()),
        'known_habs': known_habs,
        'unknown_hab_count': unknown_habs,
        'xenoforming_regions': xeno_regions,
        'xenoforming_total': round(sum(r['level'] for r in xeno_regions), 1),
        'alien_ground_facilities': alien_ground,
    }


def extract_victory_chain(faction, chain):
    """For the given faction, walk the victory chain and tag each step."""
    finished = set(faction.get('finishedProjectNames', []) or [])
    available = set(faction.get('availableProjectNames', []) or [])
    paused = {p.get('projectTemplateName'): p.get('accumulatedResearch', 0)
              for p in (faction.get('currentProjectProgress', []) or [])}
    out = []
    for tpl, label in chain:
        if tpl in finished:
            status = 'DONE'
            progress = None
        elif tpl in available:
            status = 'AVAILABLE'
            progress = paused.get(tpl)
        elif tpl in paused:
            status = 'PAUSED'
            progress = paused[tpl]
        else:
            status = 'LOCKED'
            progress = None
        out.append({'project': tpl, 'label': label, 'status': status, 'progress': progress})
    return out


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------

def fmt_amount(n):
    if n is None: return '-'
    if abs(n) >= 1000:
        return f"{n:,.0f}"
    return f"{n:.1f}"


def days_cover(stock, yearly):
    if not yearly or yearly == 0:
        return '∞' if stock > 0 else '-'
    return f"{stock / (yearly/365.25):.0f}d"


def income_unit():
    """Player's income-display unit from config.json ('month' default, or 'day').
    NEVER 'year' — the game shows monthly or daily; we match it and never print yearly."""
    try:
        from ti_config import CONFIG
        u = (CONFIG.get('income_display') or 'month').lower()
    except Exception:
        u = 'month'
    return 'day' if u.startswith('d') else 'month'


def per_unit(yearly):
    """Convert a per-YEAR figure to the player's configured unit. Returns a float."""
    return (yearly or 0) / (365.25 if income_unit() == 'day' else 12.0)


def unit_suffix():
    return '/day' if income_unit() == 'day' else '/mo'


def format_markdown(snapshot, brief=False):
    s = snapshot
    L = []
    L.append(f"# Terra Invicta save snapshot — {s['faction_name']}")
    L.append(f"**Game date:** {s['game_date']} · "
             f"**Difficulty:** {s.get('difficulty','?')} · "
             f"**Version:** {s.get('save_version','?')}")
    L.append("")

    # -- Faction comparison --
    L.append("## Faction comparison")
    su = unit_suffix()
    L.append(f"| Faction | Research{su} | Money{su} | Boost{su} | Ops{su} | MC{su} | "
             "Habs | Ships | Fleet kt | CPs |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for f in s['faction_comparison']:
        tpl = f['template']
        fleet = s['fleet_by_faction'].get(tpl, {})
        habs = s['habs_by_faction'].get(tpl, 0)
        cps = s['cps_by_faction'].get(tpl, 0)
        flag = ' **(you)**' if tpl == s['player_template'] else ''
        live = f.get('research_live_day')
        research_cell = (fmt_amount(per_unit(live * 365.2421875)) if live is not None
                         else fmt_amount(per_unit(f['research_yr'])) + ' *')
        L.append(f"| {f['name']}{flag} | {research_cell} | "
                 f"{fmt_amount(per_unit(f['money_yr']))} | {fmt_amount(per_unit(f['boost_yr']))} | "
                 f"{fmt_amount(per_unit(f['ops_yr']))} | {fmt_amount(per_unit(f['mc_yr']))} | "
                 f"{habs} | {fleet.get('ships', 0)} | "
                 f"{fleet.get('total_mass_kg', 0)/1e6:.0f} | {cps} |")
    L.append("")
    if any(f.get('research_live_day') is not None for f in s['faction_comparison']):
        L.append(f"> **Note**: Research{su} is the LIVE model from `research_income.py` (the player's "
                 f"row matches the 🧪 tooltip; rival rows are the same formula on their CPs/orgs — "
                 f"what their Intel screen shows). Rows marked `*` fell back to the stale "
                 f"`cachedYearlyRevenue` cache. Money/Boost/Ops/MC are still the cache.")
    else:
        L.append(f"> **Note**: the Research column above is `cachedYearlyRevenue.Research` shown "
                 f"{su} — stale-cached (rough ±15% for rivals, often ~2× off for the player). "
                 "See \"Research income breakdown\" below for the canonical live formula.")
    L.append("")

    # -- Rival faction full stockpiles --
    rivals = s.get('rival_resources') or []
    if rivals:
        L.append("## Rival faction stockpiles + cached income")
        L.append("Full per-faction snapshot. **Research stockpile is always 0** "
                 "(research is distributed daily, not accumulated). "
                 "**Advise** = count of councilors from this faction currently "
                 "running a permanent Advise mission on any nation (boosts that "
                 "nation's research output + IP).")
        L.append("")
        L.append("| Faction | CPs | Advise | Money | Influence | Ops | Boost | Water | Volatiles | Metals | Noble | Fiss | Antim | Exo |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in rivals:
            flag = ' **(you)**' if r['is_player'] else ''
            sp = r['stockpile']
            L.append(f"| {r['name']}{flag} | {r['cps']} | {r['advise_count']} | "
                     f"{fmt_amount(sp['Money'])} | {fmt_amount(sp['Influence'])} | "
                     f"{fmt_amount(sp['Operations'])} | {fmt_amount(sp['Boost'])} | "
                     f"{fmt_amount(sp['Water'])} | {fmt_amount(sp['Volatiles'])} | "
                     f"{fmt_amount(sp['Metals'])} | {fmt_amount(sp['NobleMetals'])} | "
                     f"{fmt_amount(sp['Fissiles'])} | {fmt_amount(sp['Antimatter'])} | "
                     f"{fmt_amount(sp['Exotics'])} |")
        L.append("")

    # -- Research income breakdown (6-source canonical formula) --
    ri = s.get('research_income') or {}
    if ri:
        exact = ri.get('model') == 'exact'
        L.append("## Research income breakdown (canonical 6-source formula)")
        if exact:
            L.append("Matches the in-game 🧪 tooltip on the top resource bar — the code-verified "
                     "model from `research_income.py` (nation research formula, org/trait councilor "
                     "incomes, template-valued ControlPointResearch effects incl. scenario/mod layers, "
                     "per-slot distribution bonus). Validated to 0.1% on 2033-02/04 (1.0.39) and "
                     "2006-07-16 (1.0.63, 2003 scenario) saves.")
        else:
            L.append("Matches the in-game 🧪 tooltip on the top resource bar. Validated "
                     "to the daily decimal on the 2032-04-28 save (1+26.5+180.3+31.2+5.7 "
                     "= 244.7 × 1.30 = 318.1/day → 9.7K/mo). The breakdown "
                     "is reconstructed from primary save data; flagged components are "
                     "heuristics (councilors, Advise direct boost, MC cap)."
                     + (f" **Exact model unavailable: {ri['model_error']}**" if ri.get('model_error') else ""))
        L.append("")
        L.append("| Source | Daily | Monthly | Note |")
        L.append("|---|---:|---:|---|")
        L.append(f"| 1. Faction HQ | {ri['hq_per_day']:.2f} | "
                 f"{ri['hq_per_day']*30.44:.0f} | exact, from `baseIncomes_year.Research/365.25` |")
        if exact:
            L.append(f"| 2. Councilors | {ri['councilors_per_day_estimate']:.2f} | "
                     f"{ri['councilors_per_day_estimate']*30.44:.0f} | exact: Σ org + trait "
                     f"`incomeResearch` over {ri['councilors_count']} councilors (not detained) |")
            L.append(f"| 3. Nations (CP-share) | {ri['nation_cp_share_per_day']:.2f} | "
                     f"{ri['nation_cp_share_per_day']*30.44:.0f} | exact: Σ my CPs of "
                     f"research_month × KS × CP-research effects / numCPs (advisers inside) |")
        else:
            L.append(f"| 2. Councilors (est.) | {ri['councilors_per_day_estimate']:.2f} | "
                     f"{ri['councilors_per_day_estimate']*30.44:.0f} | heuristic: "
                     f"{ri['councilors_count']} councilors × ~4/each + Sci total "
                     f"{ri['councilor_science_total']} × ~0.5 |")
            L.append(f"| 3a. Nations (CP-share) | {ri['nation_cp_share_per_day']:.2f} | "
                     f"{ri['nation_cp_share_per_day']*30.44:.0f} | exact: Σ over my CPs of "
                     f"(N.historyResearch[0] × ownership_share) |")
            L.append(f"| 3b. Advise direct boost | {ri['advise_direct_per_day']:.2f} | "
                     f"{ri['advise_direct_per_day']*30.44:.0f} | heuristic: Σ adviser Sci+Cmd+Adm × 0.18 "
                     f"with 1/(n+1) diminishing returns per nation |")
        L.append(f"| 4. Habs | {ri['habs_per_day']:.2f} | "
                 f"{ri['habs_per_month']:.0f} | exact: Σ over {ri['hab_modules_with_research']} "
                 f"powered+completed research modules via TIHabModuleTemplate.json |")
        if ri.get('unused_mc_per_day') is not None:
            free_mc = ri.get('mc_capacity', 0) - ri.get('mc_usage', 0)
            L.append(f"| 5. Unused MC | {ri['unused_mc_per_day']:.2f} | "
                     f"{ri['unused_mc_per_day']*30.44:.0f} | exact: "
                     f"({ri.get('mc_capacity', '?')} − {ri.get('mc_usage', '?')}) "
                     f"× 0.075 = {free_mc} × 0.075 |")
        else:
            L.append(f"| 5. Unused MC | — | — | MC capacity not available; skip |")
        L.append(f"| Subtotal (base) | **{ri['base_sources_per_day']:.2f}** | "
                 f"{ri['base_sources_per_day']*30.44:.0f} | sum of 1-5 |")
        L.append(f"| 6. Distribution bonus | {ri['dist_bonus_per_day']:.2f} | "
                 f"{ri['dist_bonus_per_day']*30.44:.0f} | "
                 + (f"+{round(ri['dist_bonus_pct']*100)}% = 5% per research slot with weight > 0 |"
                    if exact else
                    f"×{int(ri['dist_bonus_pct']*100)}% tech-gated bonus on subtotal |"))
        L.append(f"| **TOTAL** | **{ri['total_per_day']:.1f}** | "
                 f"**{ri['total_per_month']:.0f}** | "
                 f"matches the in-game 🧪 tooltip |")
        L.append("")
        L.append(f"> **Stale cache warning**: `cachedYearlyRevenue.Research` (a raw annual "
                 f"field) = {per_unit(ri['cached_yearly_revenue_research']):.0f}{unit_suffix()} — "
                 f"often off by a wide margin from this live calculation. Use this breakdown, not the cache.")
        L.append("")

        # Per-nation research contribution
        if ri['nation_contribs']:
            L.append("### Per-nation research contribution to player")
            L.append("| Nation | My CPs | Total CPs | Share | Nation total/mo | My share/mo |")
            L.append("|---|---:|---:|---:|---:|---:|")
            for nc in ri['nation_contribs']:
                L.append(f"| {nc['nation_name']} | {nc['cps_owned']} | {nc['total_cps']} | "
                         f"{nc['share_pct']:.0f}% | {nc['monthly_research']:.0f} | "
                         f"{nc['my_share_per_day']*30.44:.0f} |")
            L.append("")

        # Active Advise missions detail
        if ri['advise_breakdown']:
            L.append("### Active Advise missions (your councilors → nations)")
            L.append("Per `TIMissionTemplate.description.Advise`: *\"The councilor's "
                     "Science, Command and Administration attributes will improve "
                     "the target's research output, combat capabilities, and "
                     "financial performance. Additional advising councilors provide "
                     "diminishing benefits.\"*")
            L.append("")
            L.append("| Councilor | Advising | Science | Command | Administration |")
            L.append("|---|---|---:|---:|---:|")
            for a in ri['advise_breakdown']:
                L.append(f"| {a['councilor']} | {a['nation']} | "
                         f"{a['sci']} | {a['cmd']} | {a['adm']} |")
            L.append("")

    # -- Your faction details --
    pf = s['player_faction']
    rev = pf.get('cachedYearlyRevenue', {}) or {}
    res = pf.get('resources', {}) or {}
    su = unit_suffix()
    L.append(f"## Your resource buffers (stockpile / income{su})")
    L.append(f"| Resource | Stockpile | Income{su} (stale cache) | Days cover |")
    L.append("|---|---:|---:|---:|")
    for r in ['Money', 'Influence', 'Operations', 'Boost', 'Water',
              'Volatiles', 'Metals', 'NobleMetals', 'Fissiles', 'Exotics']:
        stock = res.get(r, 0)
        yr = rev.get(r, 0)
        L.append(f"| {r} | {fmt_amount(stock)} | {fmt_amount(per_unit(yr))} | {days_cover(stock, yr)} |")
    L.append("")

    # -- Mission Control infrastructure --
    hi = s['hab_inventory']
    cp = s['nation_control']
    mc = s['mc_breakdown']
    mc_available = mc['mc_hq'] + mc['mc_councilors'] + mc['mc_nations'] + mc['mc_hab_produced']
    mc_required = mc['mc_used_reported']  # exact game total (missionControlUsage)
    overage_hist = pf.get('history_MCCapOverageByDay', []) or []
    L.append("## Mission Control")
    L.append("Matches in-game tooltip formula (HQ + councilors + nations + habs → produced;")
    L.append("ships + habs + mining → required).")
    L.append("")
    L.append("| Available source | MC |")
    L.append("|---|---:|")
    L.append(f"| Faction headquarters | {mc['mc_hq']:.0f} |")
    L.append(f"| Councilors (orgs `incomeMissionControl`) | {mc['mc_councilors']:.0f} |")
    L.append(f"| Nations (region.missionControl in majority nations) | {mc['mc_nations']:.0f} |")
    L.append(f"| Habs (positive-MC modules built) | {mc['mc_hab_produced']:.0f} |")
    L.append(f"| **Total available** | **{mc_available:.0f}** |")
    L.append("")
    L.append("| Required for | MC | source |")
    L.append("|---|---:|---|")
    L.append(f"| Ships | {mc['mc_ships']:.0f} | Σ base × 0.85^MC-tech × 0.8^FlagBridge-fleet (matches game ±1) |")
    L.append(f"| Habs (cores + labs + dynamic per-hab term) | {mc['mc_hab_consumed']:.0f} | "
             f"residual: usage − ships − mining (static module-sum floor: {mc.get('mc_hab_consumed_static', 0):.0f}) |")
    L.append(f"| Mining network | {mc['mc_mining']:.0f} | **quadratic n²/2, EXACT** (was residual → 192) |")
    L.append(f"| **Total required** | **{mc['mc_used_reported']:.0f}** | `missionControlUsage` (exact game total) |")
    L.append("")
    L.append(f"> *Calibrated vs the 2033-03-01 tooltip (game: HQ 22 + councilors 11 + nations 333 +"
             f" habs 352 = 718 avail; ships 142 + habs 554 + mining 18 = 714 used; slack +4). "
             f"**FIXED 2026-07-04:** hab-produced now counts only POWERED MC modules (was counting"
             f" built-but-unpowered — 30 idle AdminTowers + 2 OpsCenters = +38 phantom MC), so"
             f" hab-produced now matches the game exactly (352). **Total used (`missionControlUsage`),"
             f" mining (quadratic), hab-produced and nations are now exact** — nations is the per-CP"
             f" split of each nation's regional MC (crackdown CPs excluded; fixed 2026-10-17, the old"
             f" majority-nation sum over-read partially-held nations by ~7-8). For the precise"
             f" over/under-cap number still trust the in-game top bar.*")
    L.append("")
    L.append(f"**Visible slack: {mc_available - mc['mc_used_reported']:.0f}**  "
             f"(reconstruction; read the in-game MC tooltip for the exact figure)")
    L.append(f"Pending net change when in-flight modules finish: **{mc['mc_pending_net']:+.0f}**.")
    L.append("")

    # Latent / hidden MC demand
    lt = s['mc_latent']
    has_hidden = (lt['mine_count_unpowered'] > 0 or lt['shipyard_queues_empty'] > 0 or
                  lt['mine_count_underway'] > 0)
    if has_hidden:
        L.append("### Latent MC demand the headline slack hides")
        L.append("Visible MC slack is misleading whenever you're suppressing demand by")
        L.append("turning things off. The real MC need if you re-enable shut-down infrastructure:")
        L.append("")
        L.append(f"Mining MC is QUADRATIC (decompiled): cost = (active − free)² / 2, "
                 f"free = {lt.get('mine_free_count', 0)} (Space Mine Freebies). At your current "
                 f"{lt['mine_count_active']} active mines, n = {lt['mine_count_active'] - lt.get('mine_free_count',0)}, "
                 f"so each mine ON/OFF is ~{lt.get('mine_mc_per_off', 0)} MC (NOT ~4) — and adding "
                 f"mines gets steadily more expensive.")
        L.append("")
        L.append("| Suppressed demand | Count | Marginal MC cost (quadratic for mines) |")
        L.append("|---|---:|---:|")
        L.append(f"| Mines currently unpowered | {lt['mine_count_unpowered']} | "
                 f"~+{lt['mine_mc_if_unpowered_resume']:.0f} MC if you re-enable |")
        L.append(f"| Mines under construction | {lt['mine_count_underway']} | "
                 f"**~+{lt['mine_mc_if_underway_complete']:.0f} MC** when finished |")
        L.append(f"| Idle shipyards (empty queues) | {lt['shipyard_queues_empty']} | "
                 f"~+{lt['shipyard_queues_empty']*2} MC if all start building (rough, 2/ship) — "
                 f"but see materials caveat |")
        L.append(f"| **Total suppressed demand** | | "
                 f"**~+{lt['mine_mc_if_all_run'] + lt['shipyard_queues_empty']*2:.0f} MC** |")
        L.append("")
        L.append(f"Shipyard utilization: {lt['shipyard_queues_with_work']} of {lt['shipyard_queues']} "
                 f"queues active ({lt['ships_queued_total']} ships building). "
                 f"{lt['shipyard_queues_empty']} idle.")
        L.append(f"Mining: {lt['mine_count_active']} active, {lt['mine_count_unpowered']} powered down, "
                 f"{lt['mine_count_underway']} under construction.")
        L.append("")
        # -- Fleet build-out readiness: ACTIVE four-gate diagnosis (S22) --
        # Placed right where "N idle shipyards" tempts a "just build ships" call. Do NOT let a
        # cold-start reader treat idle yards / MC slack as "build now". Ships pay materials UP
        # FRONT from the STOCKPILE (E24) — flow only sets the bank rate. We surface the numbers +
        # the decision rule rather than emit an over-confident auto-verdict (a naive net-flow read
        # is itself a trap: on 2033-09-01 the 7-day net metals was +449/d yet stock was 1,347 and
        # no hull was buildable). Verdict lines fire only on unambiguous hard gates.
        _eco = (s.get('economy_ledger') or {}).get('daily_net_7d_avg') or {}
        def _net(r):
            v = _eco.get(r)
            return f"{v:+.0f}/d" if isinstance(v, (int, float)) else "n/a"
        _metals = res.get('Metals', 0) or 0
        _vol = res.get('Volatiles', 0) or 0
        _exo = res.get('Exotics', 0) or 0
        _slack = mc_available - mc['mc_used_reported']
        _empty = lt['shipyard_queues_empty']
        _busy = lt['shipyard_queues_with_work']
        _qtot = lt['shipyard_queues']
        L.append("### Fleet build-out readiness — which gate binds? (LESSONS-ships S22)")
        L.append("> ⚠ **Do NOT read idle shipyards or MC slack as \"build the fleet now.\"** A hull's")
        L.append("> metals/volatiles/exotics are paid UP FRONT from the STOCKPILE at build-click (E24),")
        L.append("> so you can lay one now only if **stock ≥ its cost** — flow just sets the bank rate.")
        L.append("> Fleet build-out is gated by whichever of these binds; check all four:")
        L.append("")
        L.append("| Gate | This save | Note |")
        L.append("|---|---|---|")
        L.append(f"| **Materials** (stock, paid up front) | metals {fmt_amount(_metals)} ({_net('Metals')}) · "
                 f"volatiles {fmt_amount(_vol)} ({_net('Volatiles')}) · exotics {fmt_amount(_exo)} ({_net('Exotics')}) | "
                 f"compare STOCK to hull cost, not flow |")
        L.append(f"| **Shipyards** | {_busy}/{_qtot} queues busy, {_empty} idle | need a queue AND a yard big enough for the hull |")
        L.append(f"| **Mission Control** | slack {_slack:+.0f}, pending {mc['mc_pending_net']:+.0f} | each hull adds MC consumption |")
        L.append(f"| **Tech / timing** | not auto-detected | is a better drive/armor/weapon ≲1 research cycle out? then WAIT |")
        L.append("")
        L.append("**Decision rule:** run `warship_optimizer.py` for the exact per-hull material cost, "
                 "compare to the STOCKPILE above. If stock < hull cost, idle yards and MC slack are "
                 "irrelevant — the gate is materials: free the pool (stop starting new hab modules, "
                 "E24/E32) or wait to bank. Exotics ≈ 0 blocks Hybrid anti-laser armor regardless "
                 "(raid aliens for exotics, A3). Never name a single gate without checking all four.")
        # Unambiguous hard-gate flags only (conservative — silence when unclear):
        _flags = []
        if _exo < 5:
            _flags.append(f"exotics ≈ 0 ({fmt_amount(_exo)}) → **Hybrid armor blocked**")
        if _metals < 2000:
            _flags.append(f"metals stock thin ({fmt_amount(_metals)}) → **can't build capitals now; bank first**")
        if _slack < 3:
            _flags.append(f"MC slack {_slack:+.0f} → **MC-tight; each hull needs headroom**")
        if _empty == 0 and _qtot > 0:
            _flags.append("all shipyard queues busy → **yard-limited** (materials/MC permitting)")
        if _flags:
            L.append("")
            L.append("Hard-gate signals in THIS save: " + "; ".join(_flags) + ".")
        L.append("")
        L.append("**True MC scarcity** = visible_slack − latent_demand. If this is negative, MC IS")
        L.append("the binding constraint on production even when the headline says you have slack.")
        L.append("")
    if overage_hist:
        # history_MCCapOverageByDay is NEWEST-FIRST (verified vs mine_shutdown_advisor.py and
        # REFERENCE § Nation history arrays). The recent week is the HEAD [:7], NOT the tail.
        # Bug fixed 2033-09-01: the old code took [-7:] (the OLDEST 7) and called it "last week",
        # which reported a stale month-old spike as a live over-cap problem.
        recent = overage_hist[:7]
        peak = max(overage_hist)
        recent_avg = sum(recent) / len(recent)
        L.append(f"MC overage history (NEWEST-FIRST, last {len(overage_hist)} days): {overage_hist}")
        L.append(f"→ **most recent 7-day avg {recent_avg:.1f}** (index 0 = today); "
                 f"all-time-in-window peak {peak}.")
        if recent_avg < 1 and peak > 15:
            L.append("⚠ Recent overage ≈ 0 while the window still contains an OLD spike — you are "
                     "**NOT currently over MC cap**. Do not read the old peak as a live problem. "
                     "The in-game blue-sphere MC counter is truth (E25).")
        L.append("")

    # -- Mine portfolio: which to power off / keep off / cancel --
    mn = s.get('mines')
    if mn and (mn['active'] or mn['off'] or mn['building']):
        L.append("## Mine portfolio — power-on/off prioritization")
        L.append("Score weighs site yields by per-resource value × stockpile-cover multiplier.")
        L.append("Base weights: metals 8 · nobles 5 · **water 5 · vol 4** · fissiles 3. Cover")
        L.append("multiplier: <5d → 4×, <10d → 2.5×, 10-30d → 1.7×, 30-90d → 1.0×, 90-180d → 0.7×, ")
        L.append("180-365d → 0.5×, >365d → 0.35×. **Water & volatiles are weighted like nobles, NOT")
        L.append("as inert stockpile** — water is PROPELLANT (NSWR/NTR drives) consumed in bursts on")
        L.append("ship refuel/build, so static days-cover OVERSTATES real water runway. A mine that")
        L.append("produces ≥0.5/d of a scarce resource (water <45d, vol <30d, metals/fiss <15d,")
        L.append("nobles <10d cover) is PROTECTED — never in the drop-first list, even at low score.")
        L.append("Mining-network MC cost is quadratic in active mines, so low-score UNPROTECTED")
        L.append("mines are powered down first.")
        L.append("")
        L.append(f"Active: **{len(mn['active'])}** · Unpowered: **{len(mn['off'])}** · "
                 f"Under construction: **{len(mn['building'])}**")
        L.append("")
        # Days-cover summary so the operator can sanity-check the weights
        dc = mn.get('days_cover', {})
        scarce = set(mn.get('scarce', []))
        pcov = mn.get('protect_cover', {})
        if dc:
            L.append("Your stockpile cover (drives the scoring weights):")
            for k in ['metals', 'nobles', 'fissiles', 'water', 'volatiles']:
                if k in dc:
                    days = dc[k]
                    cover_str = f"{days:.0f}d" if days < 9999 else "∞ (no income)"
                    flag = "  ⚠ SCARCE — protect its mines" if k in scarce else ""
                    L.append(f"  - {k.title()}: {cover_str}{flag}")
            L.append("")
        if scarce:
            names = ', '.join(f"{k} (<{pcov.get(k,0):.0f}d guard)" for k in sorted(scarce))
            L.append(f"> **⚠ Depletion guard active:** {names}. Mines producing these are marked")
            L.append("> 🔒 PROTECTED below and excluded from shutoff candidates — cutting them deepens")
            L.append("> the shortage. If you must free MC, take it from UNPROTECTED low-score mines,")
            L.append("> hab-side MC levers, or raise the MC ceiling (Command Center) instead.")
            L.append("")

        # Lowest-score active mines (turn off candidates) — PROTECTED ones excluded
        if mn['active']:
            droppable = [m for m in mn['active'] if not m.get('protected')]
            protected_active = [m for m in mn['active'] if m.get('protected')]
            L.append("### Lowest-value ACTIVE mines — drop these first to save MC")
            L.append("Protected mines (feeding a scarce resource) are EXCLUDED from this list.")
            L.append("")
            if droppable:
                L.append("| # | Score | Hab | Body | Metals/d | Nobles/d | Fiss/d | Water+Vol/d |")
                L.append("|---:|---:|---|---|---:|---:|---:|---:|")
                lowest_active = droppable[-8:]  # bottom 8 unprotected by score (sorted desc)
                for i, m in enumerate(reversed(lowest_active), 1):
                    wv = m['water'] + m['volatiles']
                    L.append(f"| {i} | {m['score']:.1f} | {m['hab']} | {m['body']} | "
                             f"{m['metals']:.2f} | {m['nobles']:.2f} | {m['fissiles']:.2f} | {wv:.2f} |")
                L.append("")
            else:
                L.append("**None.** Every active mine feeds a currently-scarce resource — there is")
                L.append("NO safe mine to power down. Free MC elsewhere (hab modules, Command Center).")
                L.append("")
            if protected_active:
                L.append(f"🔒 **{len(protected_active)} low-score active mines are PROTECTED** "
                         "(do NOT cut — feeding a scarce resource):")
                for m in sorted(protected_active, key=lambda x: x['score'])[:8]:
                    why = '; '.join(m.get('protect_reasons', []))
                    L.append(f"  - {m['hab']} ({m['body']}), score {m['score']:.1f} — {why}")
                L.append("")

        # Top-value active mines (keep these on)
        if len(mn['active']) >= 5:
            L.append("### Top-value ACTIVE mines — keep powered (your throughput backbone)")
            L.append("| # | Score | Hab | Body | Metals/d | Nobles/d | Fiss/d |")
            L.append("|---:|---:|---|---|---:|---:|---:|")
            for i, m in enumerate(mn['active'][:8], 1):
                L.append(f"| {i} | {m['score']:.1f} | {m['hab']} | {m['body']} | "
                         f"{m['metals']:.2f} | {m['nobles']:.2f} | {m['fissiles']:.2f} |")
            L.append("")

        # Powered-down mines — keep off vs turn on?
        if mn['off']:
            L.append("### Currently UNPOWERED mines — should you turn them on?")
            L.append("Compare scores to the lowest-score active mines above. Only turn on if its")
            L.append("score exceeds your lowest active mine (and you have MC to spare).")
            L.append("")
            L.append("| Score | Hab | Body | Metals/d | Notes |")
            L.append("|---:|---|---|---:|---|")
            for m in mn['off']:
                notes = []
                if m['metals'] < 0.5:
                    notes.append("near-zero metals")
                if m['fissiles'] > 0.3:
                    notes.append(f"fissiles +{m['fissiles']:.2f}")
                if m['nobles'] > 1.0:
                    notes.append(f"nobles +{m['nobles']:.2f}")
                note = ', '.join(notes) if notes else '—'
                L.append(f"| {m['score']:.1f} | {m['hab']} | {m['body']} | {m['metals']:.2f} | {note} |")
            L.append("")

        # Under-construction — ranking, flagging the low-value ones to leave OFF.
        # NOT cancellation candidates: materials were paid in full at click time
        # and cancel refunds nothing (E24), so the only lever left is declining
        # to power them when they complete.
        if mn['building']:
            L.append("### Under-construction mines — ranking (will come online soon)")
            L.append("| Score | Hab | Body | Metals/d | Water+Vol/d | Notes |")
            L.append("|---:|---|---|---:|---:|---|")
            for m in mn['building']:
                notes = []
                # "Low value" must respect the SAME protection rule as the active
                # list: a mine feeding a scarce resource is never low value, even
                # at 0 metals. Judging by metals/nobles/fissiles alone told a
                # volatiles-starved player to write off 500+ vol/mo Jovian sites
                # (2033-11-01) — water and volatiles are resources too.
                if m.get('protected'):
                    notes.append("🔒 **scarce-resource supplier — power it on completion** ("
                                 + '; '.join(m.get('protect_reasons') or []) + ")")
                elif (m['metals'] < 0.5 and m['nobles'] < 0.5 and m['fissiles'] < 0.3
                        and m['water'] < 0.5 and m['volatiles'] < 0.5):
                    notes.append("**low value — leave UNPOWERED on completion**")
                if m['metals'] > 3.5:
                    notes.append("high metals")
                if m['fissiles'] > 0.5:
                    notes.append(f"fissiles +{m['fissiles']:.2f}")
                note = ', '.join(notes) if notes else '—'
                L.append(f"| {m['score']:.1f} | {m['hab']} | {m['body']} | {m['metals']:.2f} "
                         f"| {m['water'] + m['volatiles']:.2f} | {note} |")
            L.append("")
            L.append("**Never cancel a mine in flight** (E24): the metals/volatiles were deducted at")
            L.append("click time and cancel refunds nothing, so canceling only destroys the option.")
            L.append("A completed-but-unpowered mine costs no MC and no upkeep — that is the lever.")
            L.append("")
            L.append("**Swap strategy:** as each high-value building mine completes, power down the")
            L.append("lowest-score active mine of equivalent or lower score. Keeps MC flat while")
            L.append("upgrading your portfolio (4+ metals/d mines replacing 0-1 metals/d mines).")
            L.append("")
            L.append("**Upgrade before you expand** (E38): mining MC is quadratic, so the NEXT new")
            L.append("mine costs (active−36)² / 2 − previous MC, while a tier UPGRADE of a mine you")
            L.append("already run replaces the module in place and costs **0 extra MC**. Run")
            L.append("`mine_upgrade_planner.py` before adding mine number n+1.")
            L.append("")

    # -- Councilor roster + recruit pool --
    cr = s.get('councilor_roster')
    if cr:
        L.append("## Councilor roster (core stats — attributes + applicable trait mods)")
        L.append("`Core` = save `attributes` field + sum of trait statMods that apply at current")
        L.append("location/faction state. **Orgs do NOT add to displayed stats** — they")
        L.append("contribute at mission time only. So firing a councilor doesn't drop their")
        L.append("displayed stats; you only lose access to assigned orgs (which return to pool).")
        L.append("")
        L.append("| Councilor | Type | XP | Loy | Core P/I/E/C/A/Sci/Sec | Total | Last/active mission |")
        L.append("|---|---|---:|---:|---|---:|---|")
        for c in cr['player_councilors']:
            core_str = "/".join(f"{c['core_stats'].get(s,0):>3}" for s in
                                ['Persuasion','Investigation','Espionage','Command',
                                 'Administration','Science','Security'])
            mission_str = c.get('mission_display', '—')
            L.append(f"| **{c['name']}** | {c['type']} | {c['xp']} | "
                     f"{c['loyalty']} | {core_str} | {c['core_stats_total']} | {mission_str} |")
        L.append("")
        # Surface advisement assignments specifically — these drive nation IP cycles
        advisers = [c for c in cr['player_councilors']
                    if c.get('prior_mission') == 'Advise']
        if advisers:
            L.append("### Active Advise Nation assignments")
            L.append("Advise Nation gives the target nation +IP/research while active (~15-day")
            L.append("duration, then short cooldown gap). The target nation's IP value will")
            L.append("OSCILLATE between unboosted (cooldown gap) and boosted (active). A nation's")
            L.append("IP showing a step-change across saves often reflects this cycle, NOT a real")
            L.append("change in CP/cohesion. To diagnose a real IP drop vs cycle: compare across")
            L.append("multiple saves over ≥30 days.")
            L.append("")
            L.append("| Councilor | Advising nation | Last mission target |")
            L.append("|---|---|---|")
            for c in advisers:
                L.append(f"| {c['name']} | {c.get('advise_target_name', '?')} | nation_id {c.get('prior_mission_target_id', '?')} |")
            L.append("")
        # Player Propaganda assignments — opinion-shifting work
        propagandists = [c for c in cr['player_councilors']
                          if c.get('prior_mission') == 'Propaganda']
        if propagandists:
            L.append("### Active Public Campaign (Propaganda) assignments")
            L.append("Public Campaign shifts public opinion in target nation toward your")
            L.append("ideology. Persuasion-based mission. Cycle-driven like Advise. Best with")
            L.append("Celebrity / high-Persuasion councilors + traits like OpinionLeader, MediaDarling.")
            L.append("")
            L.append("| Councilor | Target nation |")
            L.append("|---|---|")
            for c in propagandists:
                tid = c.get('prior_mission_target_id')
                # Find nation name
                for nn in (cr.get('player_councilors') or []):
                    pass
                # Simpler — match by prior target if it's a nation
                L.append(f"| {c['name']} | nation_id {tid} |")
            L.append("")

        # ⚠ Enemy councilors with dangerous traits — DON'T ASSASSINATE
        enemy_dangerous = cr.get('enemy_dangerous_councilors', [])
        if enemy_dangerous:
            L.append("### ⚠ Enemy councilors with KILL-PENALTY traits (do not Assassinate)")
            L.append("These councilors have traits with `specialTraitRule` that fire on death.")
            L.append("Killing a **Martyr**-trait councilor triggers `GlobalPropagandaIfKilled`")
            L.append("(magnitude 35) — public opinion across major nations swings ~25-30pp")
            L.append("TOWARD their faction's ideology. Empirically observed: assassinating")
            L.append("a Protectorate Martyr councilor on 2031-10-20 caused +27pp Appease in USA/CHN/RUS")
            L.append("and crashed rest state by ~2-3 points in 80% of nations worldwide.")
            L.append("")
            L.append("**Prefer Purge, Coup, or other non-lethal missions against these targets.**")
            L.append("If Assassinate is essential, ensure detection chance is minimal (high Espionage")
            L.append("+ traits like Furtive/Undercover) — though for Martyr the effect may fire")
            L.append("regardless of detection.")
            L.append("")
            L.append("| Name | Class | Faction | Dangerous trait | Effect on kill |")
            L.append("|---|---|---|---|---|")
            for c in enemy_dangerous:
                trait_str = ', '.join(c['dangerous_traits'])
                eff_str = '; '.join(c['effects'])
                L.append(f"| **{c['name']}** | {c['type']} | {c['faction']} | {trait_str} | {eff_str} |")
            L.append("")

        # Public opinion management recommendation (uses nation_dashboard data)
        # Only recommend if a major nation has Resist < safety threshold
        nd = s.get('nation_dashboard') or []
        SAFETY_THRESHOLD = 0.70  # below this, recommend Propaganda
        SATURATION_THRESHOLD = 0.85  # above this, no marginal benefit
        opinion_recs = []
        for n in nd:
            po = n.get('public_opinion') or {}
            if not po:
                continue
            resist = po.get('Resist', 0)
            n_cps = n.get('n_cps', 0)
            if n_cps < 1:
                continue
            if resist < SATURATION_THRESHOLD:
                # Compute urgency
                if resist < 0.5:
                    urgency = '🔴 URGENT'
                elif resist < SAFETY_THRESHOLD:
                    urgency = '🟡 INVEST'
                else:
                    urgency = '🟢 minor — consider only if councilor idle'
                opinion_recs.append((n['nation_name'], resist, urgency))
        if opinion_recs:
            L.append("### Public opinion investment recommendation")
            L.append("For each major nation where you have CPs, the threshold for proactive")
            L.append("Propaganda is **Resist < 70%** (below this, opinion variance starts hurting")
            L.append("cohesion rest state and your CP-maintenance bonuses weaken). Above 85% Resist,")
            L.append("diminishing returns — don't divert councilors from higher-value missions.")
            L.append("")
            L.append("| Nation | Resist % | Urgency |")
            L.append("|---|---:|---|")
            for nname, resist, urgency in opinion_recs:
                L.append(f"| {nname} | {resist*100:.1f}% | {urgency} |")
            L.append("")
            L.append("**How to invest** (in priority order):")
            L.append("1. **Public Campaign (Propaganda) mission** on the lowest-Resist nation. Use")
            L.append("   your highest-Persuasion councilor. Celebrity class is ideal. Traits like")
            L.append("   OpinionLeader (+Persuasion on Propaganda), MediaDarling, Pollyanna help.")
            L.append("   Fast lever (cycle-based, ~15-day shifts).")
            L.append("2. **Unity priority pips ALSO shift opinion** — they pull public opinion toward")
            L.append("   the elite (CP-holder) ideology over time. SLOW background drift, but reliable.")
            L.append("   Empirically, USA's 18 Unity pips drove gradual Resist recovery from 55%→87%")
            L.append("   over Oct-Dec 2031 with no Propaganda missions running on USA.")
            L.append("3. **Watch for Martyr-trait enemy councilors** (table above) and avoid killing")
            L.append("   them. Their death triggers `GlobalPropagandaIfKilled` (+25-30pp opinion swing")
            L.append("   toward their ideology in major nations). The effect IS temporary (~60-day")
            L.append("   decay) but punishing.")
            L.append("4. **Wartime 'rally around the flag' events** consolidate opinion toward")
            L.append("   the dominant elite ideology in major nations sharing a war. Allied war")
            L.append("   declarations + visible combat victories can cause +30pp Resist jumps in 1-2 days.")
            L.append("   Side effect of military activity, not directly controllable.")
            L.append("5. **Knowledge priority** pulls COHESION (not opinion) toward 5. Indirect only.")
            L.append("6. **Don't over-invest**: above 85% Resist, marginal gain is tiny.")
            L.append("")

        # Org marketplace — what's actually offered for purchase right now
        om = s.get('org_marketplace') or []
        if om:
            L.append(f"### Org marketplace ({len(om)} offered right now)")
            L.append("From `faction.availableOrgs` — the orgs the game is actually offering")
            L.append("for purchase, AFTER applying eligibility filters (nationality, required")
            L.append("traits, etc.). Don't recommend orgs NOT in this list — they're either")
            L.append("locked behind eligibility or simply not on the current marketplace rotation.")
            L.append("")
            L.append("| Org | T | Stats | Money | Influence | Ops | Income (m/i/ops/MC/res) |")
            L.append("|---|:-:|---|---:|---:|---:|---|")
            for o in om:
                stats_str = ', '.join(f"{k[:3].title()}{v:+d}" for k, v in o['stats'].items())
                income_parts = []
                if o['income_money_m']:    income_parts.append(f"${o['income_money_m']:+.0f}")
                if o['income_inf_m']:      income_parts.append(f"Inf{o['income_inf_m']:+.1f}")
                if o['income_ops_m']:      income_parts.append(f"Ops{o['income_ops_m']:+.1f}")
                if o['income_mc']:         income_parts.append(f"MC{o['income_mc']:+.0f}")
                if o['income_research_m']: income_parts.append(f"Res{o['income_research_m']:+.0f}")
                inc = ', '.join(income_parts) or '—'
                L.append(f"| {o['name']} | {o['tier']} | {stats_str or '—'} | "
                         f"{o['cost_money']:.0f} | {o['cost_influence']:.0f} | "
                         f"{o['cost_ops']:.0f} | {inc} |")
            L.append("")

        if cr['recruit_pool']:
            L.append(f"### Actual recruit pool ({cr['recruit_pool_size']} offered)")
            L.append("From `faction.availableCouncilors` — the specific candidates the game has")
            L.append("rolled for you. NOT the global unfactioned pool.")
            L.append("")
            L.append("| Candidate | Type | Loy | Core P/I/E/C/A/Sci/Sec | Total | Notable traits |")
            L.append("|---|---|---:|---|---:|---|")
            for c in cr['recruit_pool']:
                core_str = "/".join(f"{c['core_stats'].get(s,0):>3}" for s in
                                    ['Persuasion','Investigation','Espionage','Command',
                                     'Administration','Science','Security'])
                # Drop augmentation traits from display
                non_aug = [t for t in c['traits'] if t not in
                           ('Datajack','IntelligentAssistant','PersonalProtectionMonitor',
                            'EmbeddedSensorPackage','ForceTracker','Scrambler','ManagementAI')]
                L.append(f"| {c['name']} | {c['type']} | {c['loyalty']} | {core_str} | "
                         f"{c['core_stats_total']} | {', '.join(non_aug[:5])} |")
            L.append("")

    # -- Nation investment dashboard (per-nation IP/month + cohesion analysis) --
    nd = s.get('nation_dashboard') or []
    if nd:
        L.append("## Nation investment dashboard")
        L.append("`IP/mo` is the actual monthly investment-point pool each nation puts into CP")
        L.append("priorities (from `baseInvestmentPoints_month`). Pips compete for this pool —")
        L.append("more IP/mo = each pip produces more. **The biggest lever is cohesion:** if a")
        L.append("nation's cohesion is below its rest-state, you have free IP growth waiting.")
        L.append("")
        L.append("| Nation | CPs | IP/mo | Cohesion | Rest-state | Δ | Demo | Unrest | Sust | GDP ($T) | Pop-scaling |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for n in nd:
            gap_marker = ""
            if n['cohesion_gap'] > 1.0:
                gap_marker = " ↑"
            elif n['cohesion'] < n['cohesion_rest_state'] - 0.1:
                gap_marker = " ↗"
            elif n['cohesion'] > n['cohesion_rest_state'] + 0.1:
                gap_marker = " ↘"
            L.append(f"| {n['nation_name']} | {n['n_cps']} | "
                     f"**{n['ip_per_month']:.1f}** | "
                     f"{n['cohesion']:.2f}{gap_marker} | {n['cohesion_rest_state']:.2f} | "
                     f"{n['cohesion_gap']:+.2f} | "
                     f"{n['democracy']:.1f} | {n['unrest']:.2f} | "
                     f"{n['sustainability']:.2f} | "
                     f"{n['gdp']/1e12:.1f} | {n['priority_pop_scaling']:.3f} |")
        L.append("")
        L.append("Cohesion gap (Rest − Current): positive = headroom for growth. "
                 "↑ = significant gap (>1.0), ↗ = small gap, ↘ = above rest-state (will decay).")
        L.append("")

        # -- Nation health snapshot (Table E equivalent for playthrough notes) --
        L.append("### Nation health snapshot (cohesion / rest / inequality / GDP / pop / research / funding)")
        L.append("Per-nation full health line — covers Table E format in playthrough notes. "
                 "Research/mo and Funding/mo come from `historyResearch[0]` and "
                 "`historySpaceFunding[0]` (NEWEST values; history arrays are stored "
                 "newest-first — see docs/lessons/REFERENCE.md § Nation history arrays).")
        L.append("")
        L.append("| Nation | Rgn | CPs | IP/mo | Cohesion | Rest | Inequality | GDP ($T) | Pop (M) | Research/mo | Funding/mo | Education | Advise |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for n in nd:
            advise = len(n.get('advising_councilor_ids') or [])
            L.append(f"| {n['nation_name']} | {n.get('regions_count', 0)} | {n['n_cps']} | "
                     f"{n['ip_per_month']:.1f} | {n['cohesion']:.2f} | {n['cohesion_rest_state']:.2f} | "
                     f"{n['inequality']:.2f} | {n['gdp']/1e12:.1f} | {n['pop_millions']:.0f} | "
                     f"{n.get('research_per_month', 0):.0f} | {n.get('funding_per_month', 0):.0f} | "
                     f"{n['education']:.1f} | {advise} |")
        L.append("")

        # Inert "Initiate-X" pips: informational only. These are save-state
        # artifacts on priorities like `Civilian_InitiateSpaceflightProgram`
        # AFTER the nation has founded the corresponding program. Per in-game
        # behavior: the priority is hidden from the UI (player CANNOT remove
        # the pips) AND the pips DON'T draw from the IP pool. They're inert.
        #
        # DO NOT flag these as actionable. Show as informational only, and
        # use the "effective" pip distribution (which excludes them) for all
        # IP-share math below.
        dead_total = sum(n.get('dead_pips_total', 0) for n in nd)
        if dead_total > 0:
            L.append(f"### Inert post-founding pips (informational — {dead_total} total)")
            L.append("Priorities like `Civilian_InitiateSpaceflightProgram` /")
            L.append("`Military_InitiateNuclearProgram` go inert after the corresponding program")
            L.append("is founded. **They're not actionable** — the UI hides the priority so you")
            L.append("can't remove the pips, and they don't draw from the nation's IP pool.")
            L.append("They're save-state leftovers from before founding. Effective pip counts")
            L.append("(used for IP-share math below) exclude them.")
            L.append("")
            L.append("| Nation | Inert pips | Detail |")
            L.append("|---|---:|---|")
            for n in nd:
                if n.get('dead_pips_total', 0) == 0:
                    continue
                parts = [f"{k.split('_')[-1].replace('Program','').replace('Initiate','')}={v}"
                         for k, v in n['dead_pips'].items()]
                L.append(f"| {n['nation_name']} | {n['dead_pips_total']} | {', '.join(parts)} |")
            L.append("")

        # Per-nation pip distribution — use EFFECTIVE pip totals (excluding
        # inert post-founding pips) for share + IP/mo math. Raw totals would
        # over-inflate the denominator and understate real priorities.
        L.append("### Pip distribution per nation (effective allocation, excluding inert pips)")
        L.append("Each pip's share of IP = pip_count / effective_total (inert post-founding")
        L.append("pips excluded from denominator since they don't compete for IP). To raise a")
        L.append("category's share, add pips there or remove pips from competing categories.")
        L.append("")
        for n in nd:
            effective_total = n.get('total_pips_effective', 0)
            if effective_total == 0:
                continue
            pip_dist = n.get('pip_distribution_effective') or n['pip_distribution']
            L.append(f"**{n['nation_name']}** — {n['n_cps']} CPs, "
                     f"{effective_total} effective pips"
                     + (f" ({n.get('dead_pips_total',0)} inert excluded)" if n.get('dead_pips_total',0) > 0 else "")
                     + f", {n['ip_per_month']:.1f} IP/mo")
            sorted_pips = sorted(pip_dist.items(), key=lambda x: -x[1])
            for cat, pips in sorted_pips:
                share = pips / effective_total * 100 if effective_total else 0
                ip_to_cat = pips * n['ip_per_month'] / effective_total if effective_total else 0
                L.append(f"  - {cat}: **{pips} pips** ({share:.0f}% share, ~{ip_to_cat:.1f} IP/mo)")
            L.append("")

        # Cohesion improvement opportunities
        L.append("### Cohesion improvement opportunities")
        L.append("Nations where current cohesion is well below rest-state have free IP growth")
        L.append("incoming. Adding Unity pips speeds this up; Knowledge pips also help. Avoid")
        L.append("Oppression pips (they REDUCE cohesion despite useful surveillance bonus).")
        L.append("")
        for n in nd:
            if n['cohesion_gap'] < 0.5:
                continue
            L.append(f"**{n['nation_name']}**: cohesion {n['cohesion']:.2f} → rest "
                     f"{n['cohesion_rest_state']:.2f} (gap {n['cohesion_gap']:+.2f}). "
                     f"Current IP {n['ip_per_month']:.1f}; estimated potential "
                     f"+{n['ip_potential_gain_if_cohesion_recovers']:.1f} IP/mo if gap closes.")
            cdc = n['cohesion_drivers_current']
            if cdc:
                drivers = ', '.join(f"{k.replace('CohesionReason_','')}: {v:+.2f}"
                                    for k, v in sorted(cdc.items(), key=lambda x: -abs(x[1])))
                L.append(f"    - This quarter's drivers: {drivers}")
            cda = n['cohesion_drivers_alltime']
            if cda:
                top = sorted(cda.items(), key=lambda x: -abs(x[1]))[:4]
                drivers = ', '.join(f"{k.replace('CohesionReason_','')}: {v:+.2f}" for k, v in top)
                L.append(f"    - All-time top drivers: {drivers}")
        L.append("")
        L.append("**Trade-off note:** adding Unity pips dilutes pip share for other priorities,")
        L.append("but the resulting IP/mo growth typically more than compensates over months.")
        L.append("Rough rule of thumb: 1 cohesion point gained ≈ 5-10% IP/mo increase. Unity")
        L.append("pips alone add ~0.05-0.15 cohesion/quarter (from observed history).")
        L.append("")

        # Cohesion REST STATE diagnostics (per-nation breakdown of what's
        # pulling rest down). The game tooltip shows the full formula; we
        # surface the save-derivable inputs and flag actionable levers.
        L.append("### Cohesion rest-state breakdown — why rest is low and how to raise it")
        L.append("Rest state is where cohesion will settle absent intervention. Formula starts")
        L.append("at base 16, then subtracts/adds:")
        L.append("")
        L.append("- **Inequality × ~2** (mitigated when education < 10 — uneducated populations")
        L.append("  don't act on inequality awareness). **Single biggest factor for most nations.**")
        L.append("- **Population** (bigger = larger penalty; can't change without secession)")
        L.append("- **Geographic population distribution** (sprawling = penalty; can't change)")
        L.append("- **Hostile claims** on your regions (others want them) — mitigated by low gov score")
        L.append("- **+1** per near-peer or stronger rival")
        L.append("- **+2** per active war (so ending wars HURTS rest state)")
        L.append("- **Elite-public ideology variance** (penalty)")
        L.append("- **Public opinion distribution variance** (penalty)")
        L.append("- **Autocracy (5 − democracy)** scaled, reduced by unrest (bonus when low democracy)")
        L.append("")
        L.append("Player levers (what you can actually change):")
        L.append("- **Welfare priority pips** → reduces inequality over time → biggest lever")
        L.append("- **Knowledge priority pips** → directly pulls rest-state toward 5 (good when")
        L.append("  current rest < 5, bad if rest > 5). Also raises education over time.")
        L.append("- **Unity priority pips** → boosts CURRENT cohesion (not rest state) directly")
        L.append("- **Keep wars active** if rest is low (each war = +2 to rest)")
        L.append("- **Don't annex more regions** (annexation has long-term cohesion penalty in tracker)")
        L.append("")
        L.append("| Nation | IP/mo | Cohesion → Rest | Inequality | Education | Democracy | Wars | Hostile claims |")
        L.append("|---|---:|---|---:|---:|---:|---:|---:|")
        for n in nd:
            cf = n.get('cohesion_factors', {})
            war_marker = '✓' if cf.get('at_war') else '–'
            edu_marker = '' if cf.get('education', 0) >= 10 else ' (<10, mitigates ineq penalty)'
            L.append(f"| {n['nation_name']} | {n['ip_per_month']:.1f} | "
                     f"{n['cohesion']:.2f} → {n['cohesion_rest_state']:.2f} | "
                     f"**{cf.get('inequality', 0):.2f}** | "
                     f"{cf.get('education', 0):.2f}{edu_marker} | "
                     f"{cf.get('democracy', 0):.1f} | "
                     f"{cf.get('wars_count', 0)} | "
                     f"{cf.get('hostile_claims', 0)} |")
        L.append("")
        # Public opinion concentration per nation — a swing in this metric drove
        # the Oct 2031 global rest crash. Track it explicitly.
        L.append("**Public opinion concentration (Herfindahl-Hirschman index, 0-1).** Higher = more")
        L.append("concentrated support for a single faction → BETTER for rest state (less variance).")
        L.append("Lower = scattered support → WORSE for rest state. A drop of 0.1+ in HHI between")
        L.append("saves often coincides with a rest-state crash.")
        L.append("")
        L.append("| Nation | Dominant ideology | Share | HHI | Resist % | Undecided % |")
        L.append("|---|---|---:|---:|---:|---:|")
        for n in nd:
            po = n.get('public_opinion') or {}
            if not po:
                continue
            dom_name, dom_share = n.get('public_opinion_dominant', ('?', 0))
            hhi = n.get('public_opinion_concentration_hhi', 0)
            resist = po.get('Resist', 0)
            undecided = po.get('Undecided', 0)
            L.append(f"| {n['nation_name']} | {dom_name} | {dom_share*100:.0f}% | "
                     f"{hhi:.3f} | {resist*100:.0f}% | {undecided*100:.0f}% |")
        L.append("")
        # Flag the biggest-penalty nations
        worst = sorted(nd, key=lambda n: n['cohesion_rest_state'])[:2]
        if worst and worst[0]['cohesion_rest_state'] < 4.0:
            L.append("**Nations with very low rest-state (rest < 4.0):**")
            for n in worst:
                if n['cohesion_rest_state'] >= 4.0:
                    continue
                cf = n['cohesion_factors']
                ineq_pen = abs(cf.get('inequality_estimated_penalty', 0))
                msg = (f"- **{n['nation_name']}**: rest {n['cohesion_rest_state']:.2f}. "
                       f"Inequality {cf.get('inequality', 0):.2f} → ~−{ineq_pen:.1f} penalty alone. ")
                if cf.get('inequality', 0) > 3.5:
                    msg += "**Add Welfare priority pips** to drive inequality down. "
                if cf.get('education', 0) >= 10 and cf.get('inequality', 0) > 3:
                    msg += ("Education ≥10 means full inequality penalty applies — counterintuitively, "
                            "you can't fix this by adding Knowledge; need Welfare to attack inequality. ")
                if cf.get('education', 0) < 8:
                    msg += "Education is low; Knowledge pips raise it toward 10 (which then exposes full inequality penalty — Welfare first). "
                L.append(msg)
        L.append("")

    # -- Earth Control-Point cap (separate from MC) --
    cc = s['cp_cap']
    L.append("## Earth Control-Point cap")
    L.append("Top-bar X/Y counter with the red shield icon. SEPARATE from MC.")
    L.append("")
    L.append(f"- Total control-point SEATS held: **{cc['total_cps']}** "
             f"(a headcount — NOT a measure of power; see the ## Nation/CP control load table)")
    L.append(f"- **CP-cap LOAD (Capacity Used): ~{cc['cp_load']:.0f}** — the number that competes "
             f"for your cap. Computed per-CP as `(GDP/1e9)^{CONTROL_POINT_COST_SCALING} / "
             f"(2 × nation.numControlPoints)`, crackdown CPs excluded "
             f"(decompiled `TINationState.ControlPointMaintenanceCost`; reproduces the in-game "
             f"tooltip's Capacity Used to the decimal).")
    if cc.get('cp_load_crackdown_excluded'):
        L.append(f"  - ({cc['cp_load_crackdown_excluded']} of your CPs are crackdown-suppressed "
                 f"(`benefitsDisabled`) and correctly cost 0 against cap.)")
    L.append("")
    L.append("CP-cap inputs found in save:")
    L.append(f"  - Global `controlPointMaintenanceFreebies`: **+{cc['freebies']:.0f}**")
    L.append(f"  - Admin modules in Earth LEO (`controlPointCapacity`): "
             f"**+{cc['admin_cp_cap_leo']:.0f}** "
             f"(from {dict(cc['admin_modules_in_leo'])})")
    L.append(f"  - `ControlPointMaintenanceBonus` effects from researched projects: "
             f"**~+{cc['maintenance_bonus_total']:.0f}** "
             f"(stacked: {dict(cc['maintenance_bonus_breakdown'])})")
    L.append(f"  - Sum of explained inputs: "
             f"**~{cc['freebies'] + cc['admin_cp_cap_leo'] + cc['maintenance_bonus_total']:.0f}**")
    L.append("")
    L.append("**Real cap may differ from the sum above** — orgs, councilors, and other effects "
             "can also grant CP cap; cross-reference the in-game CP tooltip for ground truth. "
             "If the UI shows you OVER cap, your space assets get accident/seizure penalties.")
    L.append("")
    L.append(f"CPs held by type: {dict(cc['cps_by_type'])}")
    L.append("")
    L.append("**Levers if over CP cap:**")
    L.append("- Build `AdministrationNode` (4) / `Tower` (12) / `Complex` (30) in Earth-LEO habs. "
             "Habs outside LEO don't count for CP cap.")
    L.append("- Research more `ControlPointMaintenance*` projects (look in Social Science).")
    L.append("- Drop low-value CPs to reduce load — the worst overage-prone CPs are normally "
             "Mass Media / Trade Unions / Religion (low value, average weight).")
    L.append(f"- Found more LEO habs ({cc['in_leo_hab_count']} currently in LEO).")
    L.append("")

    # -- LEO module saturation (per-bonus, per-module breakdown) --
    leo = s.get('leo_saturation')
    if leo:
        L.append("## LEO module saturation")
        L.append(f"Across your {leo['leo_hab_count']} Earth-LEO habs: each LEO-bonus stack")
        L.append("caps at 30% (or +9 pts for detection). Modules above the cap are wasted —")
        L.append("safe candidates for decommission to free slots (e.g., for OperationsCenter).")
        L.append("Detection per-module values: ListeningPost +1, ReconnaissanceArray +2,")
        L.append("ArgusComplex +3 (HumanDetection); same scale for Xenology (AlienDetection).")
        L.append("")
        L.append("### Summary")
        L.append("`Current` = powered + built. `After pending` = projected when under-construction")
        L.append("modules finish (use this for new-build advice). `CP weight` = your empire-wide")
        L.append("CP priority weight on this axis — the LEO bonus only amplifies what your CPs")
        L.append("are already pushing, so **bonuses with low CP weight are cheap to drop**.")
        L.append("")
        L.append("| Bonus | Cap | Current | After pending | Status | CP weight |")
        L.append("|---|---:|---:|---:|---|---:|")
        for b in leo['bonuses']:
            if b['unit'] == '%':
                cap_d = f"{b['cap']*100:.0f}%"
                cur_d = f"{b['current']*100:.1f}%"
                if b['pending'] > 0:
                    after_d = f"{b['after_pending']*100:.1f}% (+{b['pending']*100:.0f})"
                else:
                    after_d = f"{b['after_pending']*100:.1f}%"
            else:
                cap_d = f"+{b['cap']} pts"
                cur_d = f"+{b['current']} pts"
                if b['pending'] > 0:
                    after_d = f"+{b['after_pending']} pts (+{b['pending']})"
                else:
                    after_d = f"+{b['after_pending']} pts"
            # Status is based on POST-PENDING state — that's what matters for advice
            if b['pending_over_cap'] > 0.001:
                delta = b['pending_over_cap']*100 if b['unit'] == '%' else b['pending_over_cap']
                status = f"**OVER by {delta:.1f}{b['unit']}**"
            elif b['pending_under_cap'] > 0.001:
                delta = b['pending_under_cap']*100 if b['unit'] == '%' else b['pending_under_cap']
                status = f"under by {delta:.1f}{b['unit']}"
            else:
                status = "AT CAP"
            cpw = b.get('cp_priority_weight', 0)
            cpw_d = f"{cpw:.0f}" if cpw > 0 else "—"
            L.append(f"| {b['bonus']} | {cap_d} | {cur_d} | {after_d} | {status} | {cpw_d} |")
        L.append("")
        # Per-bonus module breakdown — show all stacks with at least one
        # contributor (so under-cap stacks like SpaceScience MissionControl
        # show their existing modules and headroom for slot-recommendation).
        L.append("### Per-module breakdown")
        for b in leo['bonuses']:
            if not b['rows']:
                continue
            unit = b['unit']
            cur_disp = (f"{b['current']*100:.1f}%" if unit == '%' else f"+{b['current']} pts")
            cap_disp = (f"{b['cap']*100:.0f}%" if unit == '%' else f"+{b['cap']} pts")
            status_marker = ""
            if b['over_cap'] > 0.001:
                status_marker = " — **OVER**"
            elif b['under_cap'] > 0.001:
                status_marker = " — room for more"
            L.append(f"\n**{b['bonus']}** ({cur_disp} / {cap_disp}){status_marker}")
            for r in b['rows']:
                tag = "✓ POWERED" if r['powered'] else "  off    "
                if unit == '%':
                    contrib = f"+{r['val']*100:.1f}%"
                else:
                    contrib = f"+{r['val']} pts"
                L.append(f"  - {tag}  {r['module']} (T{r['tier']}) @ {r['hab']}  → {contrib}")
        L.append("")

        # Empty-slot capacity per LEO hab — where to stack bonus modules.
        slot_rows = s.get('leo_slots') or []
        if slot_rows:
            L.append("### LEO hab slot capacity")
            L.append("")
            L.append("| LEO hab | Total slots | Built | Building | Empty |")
            L.append("|---|---:|---:|---:|---:|")
            for r in slot_rows:
                empty_bold = f"**{r['empty']}**" if r['empty'] >= 10 else f"{r['empty']}"
                L.append(f"| {r['hab']} | {r['slots']} | {r['built']} | "
                         f"{r['underway']} | {empty_bold} |")
            L.append("")
            top_empty = slot_rows[0] if slot_rows else None
            if top_empty and top_empty['empty'] >= 8:
                L.append(f"Habs with many empty slots — like **{top_empty['hab']}** "
                         f"({top_empty['empty']} empty) — are the right target for stacking")
                L.append("multi-per-hab LEO bonus modules (research centers, listening posts, etc.)")
                L.append("without competing with the one-per-hab OperationsCenter buildout.")
                L.append("")

        # Slot-fill recommendations for under-cap stacks.
        # IMPORTANT: LEO bonuses are typically *buildout-rate* multipliers on
        # the underlying CP priority (e.g., LEOBonusMissionControl makes
        # MC-priority CPs build out new MC capacity faster — it does NOT
        # multiply already-accumulated MC). So these are long-term-investment
        # modules, NOT substitutes for the one-per-hab OperationsCenter that
        # gives flat +4 MC immediately.
        L.append("### Slot-fill recommendations for under-cap LEO bonuses")
        L.append("Each bonus stack caps at 30% (or +9 pts for detection). Stacking modules on")
        L.append("LEO habs with empty slots gets you to cap. **These are complementary to**, not")
        L.append("substitutes for, OperationsCenter (which is one-per-hab and gives flat +4 MC).")
        L.append("")
        L.append("**LEO bonuses are buildout-rate multipliers on CP-priority output**, not")
        L.append("multipliers on already-accumulated stock. E.g., LEOBonusMissionControl")
        L.append("accelerates how fast your Earth nations build new MC from MC-priority CPs;")
        L.append("it does NOT increase your current Earth MC of 188.")
        L.append("")
        L.append("| Bonus | Headroom | Module to build (T2) | LEO-only build? | How many to cap |")
        L.append("|---|---:|---|:---:|---:|")
        # Module + per-T2 contribution + whether the module is `EarthLEOOnly`
        # (must be built on LEO hab, vs build-anywhere with LEO-only bonus).
        module_for_bonus = {
            'MissionControl':     ('SpaceScienceResearchCenter', 0.06, False),
            'Government':         ('SocialScienceResearchCenter', 0.06, False),
            'Knowledge':          ('InformationScienceResearchCenter', 0.06, False),
            'Welfare':            ('LifeScienceResearchCenter', 0.06, False),
            'Environment':        ('ClimateResearchCenter', 0.06, True),     # LEO-only
            'ArmyCombatValue':    ('MilitaryScienceResearchCenter', 0.06, False),
            'Miltech':            ('MaterialsResearchCenter', 0.06, False),
            'LaunchFacilities':   ('EnergyResearchCenter', 0.06, False),
            'Economy':            ('Nanofactory', 0.02, False),
            'AlienDetection':     ('XenoscienceResearchCenter', 2, False),
            'HumanDetection':     ('ReconnaissanceArray', 2, True),          # LEO-only
            'Oppression':         ('ReconnaissanceArray', 0.06, True),       # LEO-only
            'PropagandaStrength': ('CommunicationsHub', 2, False),
        }
        for b in leo['bonuses']:
            # Use *after-pending* headroom — what's still needed once
            # currently-queued modules complete. Avoid recommending duplicates
            # of what the user has already started building.
            headroom = b['pending_under_cap']
            if headroom <= 0.001:
                continue
            mod, per, leo_only = module_for_bonus.get(b['bonus'], ('?', 0, False))
            unit = b['unit']
            if unit == '%':
                headroom_disp = f"{headroom*100:.0f}%"
            else:
                headroom_disp = f"+{int(headroom)} pts"
            n_to_cap = -(-int(headroom / per)) if per > 0 else '?'
            leo_tag = '**YES**' if leo_only else '—'
            L.append(f"| {b['bonus']} | {headroom_disp} | {mod} | {leo_tag} | {n_to_cap} |")
        L.append("")
        # Strategic note — only push MC bonus if it's actually under-cap
        # AFTER pending builds; otherwise the user has already queued enough.
        mc_bonus = next((b for b in leo['bonuses'] if b['bonus'] == 'MissionControl'), None)
        if mc_bonus and mc_bonus['pending_under_cap'] > 0.001:
            L.append("**Strategic note:** for an MC-binding save, prioritize")
            L.append("**SpaceScienceResearchCenter** because its bonus directly speeds Earth MC")
            L.append(f"buildout — and you still have {mc_bonus['pending_under_cap']*100:.0f}%")
            L.append("headroom even after pending builds finish. Stack on a LEO hab with empty")
            L.append("slots (see the LEO hab slot capacity table above).")
        elif mc_bonus and mc_bonus['pending'] > 0:
            L.append("**Strategic note:** MissionControl bonus will saturate at 30% once your")
            L.append(f"pending {mc_bonus['building_count']} SpaceScience module(s) complete. Next")
            L.append("priority for slot-fill: whichever remaining under-cap bonus you value most")
            L.append("(LaunchFacilities, Miltech, Welfare). Don't queue more SpaceScience — wasted.")
        else:
            L.append("**Strategic note:** pick whichever under-cap bonus matters most to your")
            L.append("current situation. For an MC-binding save, SpaceScience speeds Earth MC")
            L.append("buildout (gives flat +6% per T2 to MC-priority effectiveness).")
        L.append("")
        # Consolidation opportunities
        consolidation_rows = [b for b in leo['bonuses'] if b.get('consolidation')]
        if consolidation_rows:
            L.append("### Slot-saving consolidation opportunities")
            L.append("Lower-tier modules can be collapsed into higher-tier copies if the higher-")
            L.append("tier project is researched — same effective bonus, fewer module slots used.")
            L.append("")
            for b in consolidation_rows:
                c = b['consolidation']
                L.append(f"- **{b['bonus']}**: {c['note']}")
                for mod, hab, tier in c['low_modules']:
                    L.append(f"    - currently: {mod} (T{tier}) @ {hab}")
                L.append(f"    - upgrade target: {c['high_module']} (T{c['high_tier']})")
            L.append("")
        # Free-decommission summary: list every module that contributes to an
        # OVER-cap stack as a "no-effective-bonus-lost" decom candidate.
        free_decoms = []
        for b in leo['bonuses']:
            if b['over_cap'] <= 0.001:
                continue
            # over_cap quantified in per-module units, find smallest module that
            # would still leave the stack at-cap or just under
            over_amount = b['over_cap']
            for r in b['rows']:
                if not r['powered']: continue
                if r['val'] <= over_amount + 1e-6:
                    free_decoms.append({
                        'bonus': b['bonus'],
                        'module': r['module'],
                        'hab': r['hab'],
                        'val': r['val'],
                        'unit': b['unit'],
                    })
                    over_amount -= r['val']
                    if over_amount <= 0:
                        break
        if free_decoms:
            L.append("### Free decommission candidates (over-cap modules)")
            L.append("These modules are wasted because their bonus stack is already over cap.")
            L.append("Decommission to free the slot with **zero effective bonus lost**.")
            L.append("")
            L.append("| Bonus stack | Module | Hab | Contribution |")
            L.append("|---|---|---|---:|")
            for fd in free_decoms:
                if fd['unit'] == '%':
                    contrib = f"+{fd['val']*100:.1f}%"
                else:
                    contrib = f"+{fd['val']} pts"
                L.append(f"| {fd['bonus']} | {fd['module']} | {fd['hab']} | {contrib} |")
            L.append("")
        L.append("**Note on coupled bonuses:** ListeningPost and ReconnaissanceArray contribute")
        L.append("to BOTH HumanDetection (pts) and Oppression (%). Decommissioning one of these")
        L.append("hits both stacks — check both rows before committing.")
        L.append("")

        # Ranked "costed decommission" suggestions for at-cap stacks. When the
        # user needs to free a slot but no over-cap modules remain at that hab,
        # pick the at-cap stack with the LOWEST CP priority weight — dropping
        # 6% of a bonus the user barely uses costs almost nothing.
        at_cap_with_modules = [
            b for b in leo['bonuses']
            if b['pending_over_cap'] < 0.001 and b['pending_under_cap'] < 0.001
            and b['powered_count'] > 0
        ]
        if at_cap_with_modules:
            L.append("### Cheapest at-cap decommission candidates (when free decoms aren't enough)")
            L.append("If you need to free more LEO slots, drop modules from stacks with the")
            L.append("**lowest CP priority weight** — those bonuses have the least to amplify.")
            L.append("Decommissioning 1 module drops the stack from cap to (cap − per-module-value).")
            L.append("")
            L.append("Detection bonuses (Alien, Human) aren't CP-amplified — they have standalone")
            L.append("value (catching UFO landings / xenoforming), so don't drop them just because")
            L.append("their CP-weight column shows 0. Listening Post / Recon Array also couple")
            L.append("with Oppression — losing one hits both stacks.")
            L.append("")
            L.append("| Bonus | CP weight (lower = cheaper) | Per-module loss | Suggested module |")
            L.append("|---|---:|---:|---|")
            DETECTION_BONUS_NAMES = {'HumanDetection', 'AlienDetection'}
            ranked = sorted(at_cap_with_modules,
                            key=lambda b: (
                                b['bonus'] in DETECTION_BONUS_NAMES,  # detection bonuses last
                                b.get('cp_priority_weight', 0),
                            ))
            for b in ranked:
                cpw = b.get('cp_priority_weight', 0)
                if b['bonus'] in DETECTION_BONUS_NAMES:
                    cpw_d = "N/A (standalone)"
                elif cpw == 0:
                    cpw_d = "0 (unused — cheap to drop)"
                else:
                    cpw_d = f"{cpw:.0f}"
                # Pick representative module: first powered row in the stack
                rep = next((r for r in b['rows']
                            if r.get('powered') and r.get('completed', True)), None)
                if not rep:
                    continue
                if b['unit'] == '%':
                    loss = f"−{rep['val']*100:.0f}%"
                else:
                    loss = f"−{rep['val']} pts"
                couple_note = ""
                if rep['module'] in ('ListeningPost', 'ReconnaissanceArray', 'ArgusComplex'):
                    couple_note = " (couples with Oppression)"
                L.append(f"| {b['bonus']} | {cpw_d} | {loss} | "
                         f"{rep['module']}{couple_note} (any of {b['powered_count']}) |")
            L.append("")

    # MC module coverage gaps + upgrade opportunities
    L.append("### MC module coverage")
    L.append(f"- Habs with NO OperationsCenter/CommandCenter built: "
             f"**{hi['habs_missing_mc_module_built']} / {len(hi['habs'])}**; "
             f"after current builds finish: **{hi['habs_missing_mc_module_total']} / "
             f"{len(hi['habs'])}**. Each new OperationsCenter = +4 MC.")
    L.append(f"- Habs with NO Administration* module built (Earth-CP-cap, LEO only): "
             f"**{hi['habs_missing_admin_module_built']} / {len(hi['habs'])}**; "
             f"after current builds finish: **{hi['habs_missing_admin_module_total']} / "
             f"{len(hi['habs'])}**. These give Earth political CP bandwidth, not MC.")
    L.append("")
    # List habs missing OpsCenter, separating T2 (eligible to build now,
    # ranked by metal cost) from T1 (need upgrade first, often not worth it).
    missing_t2 = hi.get('habs_missing_mc_t2', [])
    missing_t1 = hi.get('habs_missing_mc_t1', [])
    if missing_t2 or missing_t1:
        L.append("**OperationsCenter build candidates** (OpsCenter requires T2 hab).")
        L.append("Metal cost is approximate — calibration: Earth LEO 75 / Ganymede 185 / Io 375. "
                 "Hover the build button in-game for ground truth.")
        L.append("")
    if missing_t2:
        L.append(f"### T2 habs missing OpsCenter ({len(missing_t2)}) — eligible to build now")
        L.append("Ranked by estimated metal cost (cheapest first).")
        L.append("")
        L.append("| Order | Hab | Type/Tier | Body | Est. metals | Cumulative | Local metals/day |")
        L.append("|---:|---|---|---|---:|---:|---:|")
        cum = 0
        for i, h in enumerate(missing_t2, 1):
            cum += h['estimated_metal_cost']
            leo_tag = ' (LEO)' if h.get('inEarthLEO') else ''
            L.append(f"| {i} | {h['name']} | {h['type']} T{h['tier']} | "
                     f"{h['body']}{leo_tag} | ~{h['estimated_metal_cost']} | "
                     f"{cum} | {h['metals_day']:.2f} |")
        L.append(f"\nTotal metal cost to build all {len(missing_t2)} OpsCenters: "
                 f"**~{cum} metals** "
                 f"(+{len(missing_t2)*4} MC). "
                 f"At ~33k metals/yr that's {cum/(33009/365):.1f} days of full diversion.")
        L.append("")
    if missing_t1:
        # Partition by pending-core status so we don't recommend upgrades on
        # habs that are already mid-upgrade or still being founded.
        upgrading = [h for h in missing_t1 if h.get('pending_core_status') == 'upgrading']
        founding  = [h for h in missing_t1 if h.get('pending_core_status') == 'founding']
        stable    = [h for h in missing_t1 if h.get('pending_core_status') in (None, 'stable', 'other', 'founding-t2')]
        L.append(f"### T1 habs missing OpsCenter ({len(missing_t1)})")
        L.append("OpsCenter requires T2 (SettlementCore / OrbitalCore). Before flagging a T1 hab")
        L.append("as needing upgrade, check whether a T2 core is **already** under construction —")
        L.append("if so, OpsCenter becomes buildable the day the upgrade completes.")
        L.append("")
        def _date_short(s):
            if not s or not isinstance(s, str):
                return '?'
            # ISO format like '2031-12-25T00:28:09.1370000' → '2031-12-25'
            return s.split('T', 1)[0]
        if upgrading:
            L.append(f"**T1→T2 upgrade in progress ({len(upgrading)})** — just wait, then build OpsCenter.")
            L.append("")
            L.append("| Hab | Body | T2 core target | Upgrade completes | Local metals/day |")
            L.append("|---|---|---|---|---:|")
            for h in sorted(upgrading, key=lambda x: x.get('pending_core_completion') or ''):
                L.append(f"| {h['name']} | {h['body']} | {h.get('pending_core_target') or '?'} | "
                         f"{_date_short(h.get('pending_core_completion'))} | {h['metals_day']:.2f} |")
            L.append("")
        if founding:
            L.append(f"**Still being founded as T1 ({len(founding)})** — initial OutpostCore/PlatformCore "
                     "under construction. Decide later whether to upgrade to T2 once it completes; "
                     "don't queue OpsCenter or T2-core upgrade yet.")
            L.append("")
            L.append("| Hab | Body | T1 core | T1 completes | Local metals/day |")
            L.append("|---|---|---|---|---:|")
            for h in sorted(founding, key=lambda x: x.get('pending_core_completion') or ''):
                L.append(f"| {h['name']} | {h['body']} | {h.get('pending_core_target') or '?'} | "
                         f"{_date_short(h.get('pending_core_completion'))} | {h['metals_day']:.2f} |")
            L.append("")
        if stable:
            L.append(f"**Stable T1, no upgrade in progress ({len(stable)})** — would need a T1→T2 "
                     "upgrade (~300-600 metals + ~40d build time) before OpsCenter is buildable. "
                     "**Don't upgrade T1 just for +4 MC — focus on T2 habs first.** Listed by local")
            L.append("metals/day descending (better self-supply = lower upgrade cost).")
            L.append("")
            L.append("| Hab | Type/Tier | Body | Local metals/day |")
            L.append("|---|---|---|---:|")
            for h in stable:
                L.append(f"| {h['name']} | {h['type']} T{h['tier']} | {h['body']} | {h['metals_day']:.2f} |")
            L.append("")
    L.append("### MC / CP-cap upgrade opportunities")
    L.append("| Upgrade | Modules eligible | +MC each | +CP cap each | Total MC gain | Total CP cap gain | Project |")
    L.append("|---|---:|---:|---:|---:|---:|---|")
    finished = set(pf.get('finishedProjectNames', []) or [])
    friendly = s.get('templates', {}).get('project_friendly', {})
    for label, u in hi['upgrade_gain'].items():
        proj = u['requires_project']
        proj_done = '✓' if proj in finished else '✗ research first'
        proj_label = friendly.get(proj, proj)
        L.append(f"| {label} | {u['count_upgradable']} | {u['delta_mc_each']} | "
                 f"{u['delta_cp_cap_each']} | {u['total_mc_gain']} | "
                 f"{u['total_cp_cap_gain']} | {proj_label} (`{proj}`) ({proj_done}) |")
    L.append("")

    # CP MC priority — partition by majority vs minority nations.
    you_pri = cp['cp_mc_priority'].get(s['player_template'], {})
    if you_pri:
        L.append("### Your CP MissionControl-priority allocation")
        L.append(f"- Total MC priority weight (raw): **{you_pri['total_weight']}** across "
                 f"{you_pri['cps']} CPs")
        L.append(f"- In MAJORITY nations (actionable — MC priority matters here): "
                 f"**{you_pri['majority_total_weight']}** weight across "
                 f"{you_pri['majority_cps']} CPs; "
                 f"**{you_pri['majority_zero_mc']} at zero**")
        L.append(f"- In MINORITY nations (MC priority does little — these are "
                 f"influence/coup-setup CPs): "
                 f"**{you_pri['minority_total_weight']}** weight across "
                 f"{you_pri['minority_cps']} CPs")
        if you_pri['majority_zero_mc'] > 0:
            L.append("- Majority-nation CPs at zero MC priority (real lever):")
            for ex in you_pri['majority_zero_mc_examples']:
                L.append(f"    - {ex['nation']} / {ex['type']}: "
                         f"currently {ex['active_priorities']}")
        L.append("**Only flag the MAJORITY 'zero MC' number as a lever**; minority-nation "
                 "CPs don't move regional MC output meaningfully when you're not majority "
                 "holder.")
    pri_totals = cp['cp_priority_totals_by_fac'].get(s['player_template'], {})
    if pri_totals:
        L.append("- Where your CP priority weight currently goes (across all your CPs):")
        for k, v in sorted(pri_totals.items(), key=lambda x: -x[1])[:10]:
            L.append(f"    - {k}: {v}")
    L.append("")
    L.append("### Top untapped Earth MC (regions held by rivals)")
    L.append("| Region nation | MC | Owner |")
    L.append("|---|---:|---|")
    for r in cp['top_mc_regions'][:15]:
        owner_label = FACTION_DISPLAY.get(r['owner'], r['owner'] or '(unowned)')
        if r['owner'] == s['player_template']:
            continue
        L.append(f"| {r['nation']} | {r['mc']:.0f} | {owner_label} |")
    L.append("")

    # Schematics
    L.append("### Hab schematics")
    L.append(f"- Habs with no schematic: **{hi['habs_without_schematic']} / {len(hi['habs'])}**")
    if hi['schematics']:
        for sch, c in hi['schematics'].most_common():
            label = SCHEMATIC_TEMPLATES.get(sch, sch)
            L.append(f"    - {label}: {c}")
    L.append("")

    # -- Hab inventory --
    L.append("## Your hab inventory")
    L.append(f"Total habs: **{len(hi['habs'])}**")
    L.append("By type/tier:")
    for (t, tier), c in sorted(hi['habs_by_type_tier'].items()):
        L.append(f"  - {t} tier {tier}: {c}")
    L.append("Top locations:")
    for body, c in hi['habs_by_body'].most_common(12):
        L.append(f"  - {body}: {c}")
    L.append("")
    L.append("Modules built — top 25:")
    L.append("| Module | Count |")
    L.append("|---|---:|")
    for n, c in hi['modules_built'].most_common(25):
        L.append(f"| {n} | {c} |")
    L.append("")
    if hi['modules_underway']:
        L.append("Modules under construction — top 15:")
        L.append("| Module | Count |")
        L.append("|---|---:|")
        for n, c in hi['modules_underway'].most_common(15):
            L.append(f"| {n} | {c} |")
        L.append("")

    # -- Fleet inventory --
    L.append("## Fleet inventory")
    L.append("| Faction | Ships | Mass kt | Capital | Large | Medium | Small | Avg ΔV (km/s) | Avg combat accel (m/s²) |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for f in s['faction_comparison']:
        tpl = f['template']
        fs = s['fleet_by_faction'].get(tpl)
        if not fs or fs['ships'] == 0:
            continue
        import statistics as st
        dv_avg = st.mean(fs['dv_kps']) if fs['dv_kps'] else 0
        ac_avg = st.mean(fs['combat_accel']) if fs['combat_accel'] else 0
        flag = ' **(you)**' if tpl == s['player_template'] else ''
        L.append(f"| {f['name']}{flag} | {fs['ships']} | "
                 f"{fs['total_mass_kg']/1e6:.1f} | {fs['capital_count']} | "
                 f"{fs['large_count']} | {fs['medium_count']} | {fs['small_count']} | "
                 f"{dv_avg:.0f} | {ac_avg:.1f} |")
    L.append("")
    # Weapon-class roll-up — answers "what kind of fleet does each faction
    # field" without forcing the reader to recognize 30+ verbose template names.
    L.append("### Weapon-class roll-up by faction")
    L.append("Class assignment from substring match on `moduleTemplateName` "
             "(e.g. `UV_NLaser_DH_120cm_GreenArc` → Lasers; `CopperheadMissileBay` "
             "→ Missiles/Torpedoes). Counts include both nose and hull mounts.")
    L.append("")
    # Discover all classes present
    all_classes = set()
    for f in s['faction_comparison']:
        fs = s['fleet_by_faction'].get(f['template'])
        if fs:
            all_classes.update(fs.get('weapon_class_counts', {}).keys())
    all_classes = sorted(all_classes)
    if all_classes:
        L.append("| Faction | " + " | ".join(all_classes) + " | Total |")
        L.append("|---|" + "---:|" * (len(all_classes) + 1))
        for f in s['faction_comparison']:
            tpl = f['template']
            fs = s['fleet_by_faction'].get(tpl)
            if not fs or fs['ships'] == 0:
                continue
            wcc = fs.get('weapon_class_counts', {})
            flag = ' **(you)**' if tpl == s['player_template'] else ''
            cells = [f"{wcc.get(cls, 0)}" for cls in all_classes]
            total = sum(wcc.get(cls, 0) for cls in all_classes)
            L.append(f"| {f['name']}{flag} | " + " | ".join(cells) + f" | {total} |")
        L.append("")

    you_fleet = s['fleet_by_faction'].get(s['player_template'], {})
    if you_fleet:
        L.append("Your nose weapons (top 8):")
        for n, c in you_fleet['nose_weapons'].most_common(8):
            L.append(f"  - {n}: {c}")
        L.append("")
        L.append("Your hull weapons (top 8):")
        for n, c in you_fleet['hull_weapons'].most_common(8):
            L.append(f"  - {n}: {c}")
        L.append("")
    alien_fleet = s['fleet_by_faction'].get('AlienCouncil', {})
    if alien_fleet:
        L.append("Alien nose weapons (top 8):")
        for n, c in alien_fleet['nose_weapons'].most_common(8):
            L.append(f"  - {n}: {c}")
        L.append("")

    # -- Nation control --
    cp = s['nation_control']
    L.append("## Nation/CP control")
    L.append("> **⚠ Seat count ≠ power. Compare CP LOAD, not seats.** A faction can hold the FEWEST")
    L.append("> seats and still DOMINATE Earth control by concentrating on a few high-GDP nations.")
    L.append("> **CP load** = economic weight against the CP cap = `Σ (GDP/1e9)^0.6 / (2·nCP)` over")
    L.append("> held CPs (crackdown CPs excluded) — the number that actually competes for cap and")
    L.append("> reflects real Earth income control. Rank factions by LOAD; use seats only as a")
    L.append("> secondary headcount. NEVER call a low seat count 'dead last / squeezed / starved'.")
    L.append("")
    L.append("### CP LOAD vs seats, per faction (rank by LOAD)")
    load = cp.get('cp_load_by_faction', {})
    seats = cp['by_faction']
    L.append("| Faction | CP load | Seats | Load/seat |")
    L.append("|---|---:|---:|---:|")
    # union of factions that hold any CP (skip the unowned bucket, which has no load)
    ranked = sorted(((f, load.get(f, 0.0)) for f in seats if f and f != '(unowned)'),
                    key=lambda x: -x[1])
    for fac_tpl, ld in ranked:
        label = FACTION_DISPLAY.get(fac_tpl, fac_tpl)
        sc = seats.get(fac_tpl, 0)
        star = " **(you)**" if fac_tpl == s['player_template'] else ""
        lps = f"{ld/sc:.1f}" if sc else "—"
        L.append(f"| {label}{star} | {ld:.0f} | {sc} | {lps} |")
    unowned = seats.get('(unowned)', 0)
    if unowned:
        L.append(f"| (unowned) | — | {unowned} | — |")
    L.append("")
    your_nations = cp['majority'].get(s['player_template'], [])
    L.append(f"Nations where you hold majority of filled CPs ({len(your_nations)}):")
    for n in your_nations:
        L.append(f"  - {n['nation']}: {n['held']}/{n['filled']} CPs")
    L.append("")
    L.append("Nations-of-majority count by faction:")
    for fac_tpl, lst in sorted(cp['majority'].items(), key=lambda x: -len(x[1])):
        label = FACTION_DISPLAY.get(fac_tpl, fac_tpl)
        L.append(f"  - {label}: {len(lst)}")
    L.append("")

    # -- Alien pressure --
    ap = s['alien_pressure']
    L.append("## Alien pressure on Earth")
    L.append(f"- Built alien facilities on Earth: {ap['alien_facilities_built']}")
    L.append(f"- Active UFO landings: {ap['ufo_landings_active']}")
    n_known = len(ap.get('xenoforming_known', []))
    n_hidden = ap.get('xenoforming_hidden_count', 0)
    L.append(f"- Xenoforming regions (KNOWN to your faction): {n_known}")
    for r, lvl in ap.get('xenoforming_known', [])[:15]:
        L.append(f"    - {r}: level {lvl:.2f}")
    if n_hidden > 0:
        L.append(f"- Xenoforming regions you have NOT surveilled: **{n_hidden}** "
                 f"(specific regions/levels REDACTED — you'd be cheating to see them)")
    L.append("")

    # -- Alien hate analysis (formula + masking projects + decay) --
    ah = s.get('alien_hate')
    if ah:
        L.append("## Alien hate analysis (the formula, not vibes)")
        L.append(f"**Current `assessedAlienHateOfMe`: {ah['actual_hate']:.1f}**")
        L.append("")
        L.append(f"Formula (anti-alien factions): `hate ≥ max(20, MC × difficulty × ∏(masking))`. ")
        L.append(f"War threshold: **{ah['war_threshold']}** (you're {ah['war_status']}). ")
        L.append(f"Peace threshold: ≤{ah['peace_threshold']}.")
        L.append("")
        L.append("**Your inputs:**")
        L.append(f"- MC consumption: **{ah['mc_consumption']}**")
        L.append(f"- Difficulty: **{ah['difficulty_name']}** → modifier **{ah['difficulty_modifier']}×**")
        L.append(f"- Masking projects done: **{ah['masking_projects_done']}/{ah['masking_projects_total']}** "
                 f"→ multiplier **{ah['masking_multiplier_current']:.3f}×**")
        L.append("")
        L.append("**Computed floor (community-guide formula, multiplicative-stackable masking):**")
        L.append(f"- Raw floor (MC × diff): **{ah['floor_raw']:.0f}**")
        L.append(f"- After masking: **{ah['floor_effective']:.0f}**")
        L.append(f"- Above-floor (assumed action-decay residual): **{ah['above_floor']:.0f}** "
                 f"— if 1/month decay holds, ~{ah['months_to_decay_to_floor']} months to settle at floor")
        L.append(f"- Decay floor stops at: max(20, masked-floor)")
        L.append("")
        L.append("**The displayed `assessedAlienHateOfMe` tracks true hate LIVE** (the old 'frozen")
        L.append("between fix events' theory was disproven 2032-02-27: two fleet kills moved the meter")
        L.append("+74.0 in real time with no fix event — see LESSONS-aliens A1). Plateaus between saves")
        L.append("are equilibrium (MC-growth ≈ above-floor decay), not staleness. Fix events (Their")
        L.append("Purpose / Their Demands / Hydra Diplomacy) recalculate once and typically change nothing.")
        L.append("")
        L.append("### MCUsageMasking projects (permanent hate-floor reducers)")
        L.append("Each project applies `Effect_MCUsageMasking` (Multiplicative 0.8×, stackable).")
        L.append("")
        L.append("| Project | Cost | Category | Status | Prereqs |")
        L.append("|---|---:|---|---|---|")
        for m in ah['masking_status']:
            if m['done']:
                status = '✓ DONE'
            elif m['available_now']:
                status = '○ AVAILABLE NOW'
            else:
                status = '🔒 locked'
            preq = ', '.join(f"{p}={'✓' if s=='done' else '🔒'}" for p, s in m['prereqs'])
            L.append(f"| `{m['project']}` | {m['cost']:,} | {m['category']} | {status} | {preq} |")
        L.append("")
        if ah['next_masking_target']:
            nt = ah['next_masking_target']
            L.append(f"**Next masking project to chase:** `{nt['project']}` ({nt['cost']:,} RP). ")
            L.append(f"Floor reduction once researched: **−{ah['next_masking_floor_delta']:.0f} hate** "
                     f"({ah['floor_effective']:.0f} → {ah['floor_effective']-ah['next_masking_floor_delta']:.0f}).")
            missing_prereqs = [p for p, st in nt['prereqs'] if st != 'done']
            if missing_prereqs:
                L.append(f"Missing prereqs to research first: {', '.join(missing_prereqs)}")
            L.append("")
        # Strategic caveats
        L.append("**Notes:**")
        L.append(f"- xenoforms killed (lifetime): {ah['xenoforms_killed']} (each adds ~10 hate above floor)")
        L.append(f"- alien investigations: {ah['alien_investigations']}")
        L.append(f"- `highestSpaceStrengthSinceLastAlienKnockdown`: **{ah['highest_space_strength_since_knockdown']:.3f}** "
                 f"— tracks your peak strength since last alien retaliation; no public threshold for trigger")
        L.append("- `Effect_FixAlienThreatMeter` (Their Purpose / Their Demands / Hydra Diplomacy) is INSTANT,")
        L.append("  not a duration freeze. It recalculates the meter once. Useful for syncing displayed hate")
        L.append("  to true formula state, NOT for reducing hate or buying a free-build window.")
        L.append("- Only `Effect_SetAlienHate0` (Coexistence Pact) hard-resets hate — Appease-faction only.")
        L.append("")

    # -- Threat assessment (per-faction fleet + immediate threats) --
    ta = s.get('threat_assessment')
    if ta:
        L.append("## Faction threat assessment — who, where, how dangerous")
        L.append("Lists every faction's fleet strength + their fleet locations vs your hab bodies.")
        L.append("**Immediate threat** = hostile faction with a fleet currently AT a body where you also")
        L.append("have habs. **Opportunistic target** = hostile, weak-fleet (≤10 ships), high mutual hate.")
        L.append("")
        L.append("| Faction | You→them | Them→you | Status | Ships | Fleet kt | Habs | Fleet locations (top 4) |")
        L.append("|---|---:|---:|---|---:|---:|---:|---|")
        for r in ta['rows']:
            top_locs = sorted(r['fleet_locations'].items(), key=lambda x: -x[1])[:4]
            loc_str = ', '.join(f"{b}({n})" for b, n in top_locs) or '—'
            kind_marker = {
                'alien': '🛸 alien',
                'hostile-war': '⚔ war',
                'hostile-conflicted': '⚠ conflicted',
                'neutral': '· neutral',
            }.get(r['kind'], r['kind'])
            L.append(f"| {r['faction_name']} | {r['my_hate_of_them']:.0f} | "
                     f"{r['their_hate_of_me']:.0f} | {kind_marker} | "
                     f"{r['ships']} | {r['fleet_mass_kt']:.0f} | {r['habs']} | {loc_str} |")
        L.append("")
        L.append(f"Bodies where YOU have habs: {', '.join(ta['player_bodies'])}")
        L.append("")
        if ta['immediate_threats']:
            L.append("### ⚠ Immediate threats — hostile fleet at body where you have habs")
            L.append("**Build defensive ships at these bodies first.**")
            L.append("")
            L.append("| Faction | Mutual hate | Hostile fleet at your body |")
            L.append("|---|---:|---|")
            for r in ta['immediate_threats']:
                contested = ', '.join(f"{b}: {n} ships" for b, n in r['fleet_at_player_bodies'])
                hate = max(r['my_hate_of_them'], r['their_hate_of_me'])
                L.append(f"| {r['faction_name']} | {hate:.0f} | {contested} |")
            L.append("")
        else:
            L.append("### ✓ No immediate threats")
            L.append("No hostile faction currently has a fleet at a body where you also have habs.")
            L.append("Defensive ship building can be opportunistic, not urgent.")
            L.append("")
        if ta['opportunistic_targets']:
            L.append("### Opportunistic strike targets — hostile + weak fleet")
            L.append("These factions have ≤10 ships and ≥30 mutual hate. Low risk to attack.")
            L.append("")
            L.append("| Faction | Mutual hate | Their ships | Habs | Where their fleet sits |")
            L.append("|---|---:|---:|---:|---|")
            for r in ta['opportunistic_targets']:
                top = sorted(r['fleet_locations'].items(), key=lambda x: -x[1])[:3]
                loc = ', '.join(f"{b}({n})" for b, n in top) or '—'
                hate = max(r['my_hate_of_them'], r['their_hate_of_me'])
                L.append(f"| {r['faction_name']} | {hate:.0f} | {r['ships']} | {r['habs']} | {loc} |")
            L.append("")
        L.append("**Where to build ships:**")
        L.append("- **Defensive (urgent)** → at bodies listed in 'Immediate threats' above")
        L.append("- **Offensive (opportunistic)** → at your bodies closest to opportunistic-target fleets")
        L.append("- **Anti-alien (strategic)** → at outer-system shipyards (Callisto/Ceres/Saturn-moons),")
        L.append("  since alien fleets concentrate beyond Jupiter. Building at LEO wastes 10-20 km/s ΔV.")
        L.append("")

    # -- Research --
    rs = s['research']

    # Cumulative tech + project counts (Table G equivalent in playthrough notes).
    # Throughput is the only meaningful research metric — cachedYearlyRevenue
    # is stale; the live tooltip isn't in the save. So count what HAS finished
    # and compare snapshot-to-snapshot.
    n_finished_techs = len(rs.get('finished_techs') or [])
    n_finished_projects = len(rs.get('finished_projects') or [])
    L.append("## Tech progress (cumulative)")
    L.append(f"- **Finished global techs**: {n_finished_techs}")
    L.append(f"- **Finished faction projects**: {n_finished_projects}")
    L.append(f"- **Total completions**: {n_finished_techs + n_finished_projects}")
    L.append(f"- **Active global tech slots**: {len(rs.get('active_global_techs') or [])} / 3")
    L.append(f"- **Active faction project slots**: {len(rs.get('active_faction_projects') or [])} / 3")
    L.append("")
    L.append("> Throughput = Δ(finished_techs + finished_projects) between adjacent "
             "saves. This is the only meaningful research metric — `cachedYearlyRevenue.Research` "
             "is stale (~2× off for player). For income/day, see the Research income "
             "breakdown section above.")
    L.append("")

    # -- Unlockable projects (prereqs met, not yet rolled to availability) --
    unlockable = s.get('unlockable_projects') or []
    if unlockable:
        L.append("## Unlockable projects (prereqs met, waiting to roll)")
        L.append("Projects whose prereqs are all satisfied (with `altPrereq` substitutions "
                 "applied), but the game's monthly unlock roll hasn't surfaced them in the "
                 "Available menu yet. Each tick they have an `initialUnlockChance + "
                 "deltaUnlockChance × elapsed_months` chance to appear (capped at "
                 "`maxUnlockChance`).")
        L.append("")
        L.append("- **`fac %`** = `factionAvailableChance` — base chance this project will EVER "
                 "appear for THIS faction. 100% = guaranteed eventual appearance; 20% = 20% "
                 "chance to ever roll; 0% would mean never.")
        L.append("- **`roll`** = `+deltaUnlockChance% / month → maxUnlockChance%` ceiling.")
        L.append("- **`alt-sub`** notes which prereq slot used the alternative prereq.")
        L.append("- **`gate`** = `requiredMilestone` / `requiredObjectiveName` beyond tech prereqs "
                 "(e.g. AccessLiveGriffin = capture a live griffin). **⛔ BLOCKED** rows will NOT "
                 "roll until the milestone is earned, regardless of prereqs.")
        L.append("")
        from collections import defaultdict as _dd
        by_cat = _dd(list)
        for u in unlockable:
            by_cat[u['category']].append(u)
        for cat in sorted(by_cat.keys()):
            cat_rows = by_cat[cat]
            L.append(f"### {cat} ({len(cat_rows)})")
            L.append("| Cost | Project | fac % | roll | One-time | gate | alt-sub | Prereqs satisfied |")
            L.append("|---:|---|---:|---|---|---|---|---|")
            for u in cat_rows:
                alt_subs = []
                preq_parts = []
                for slot in u['prereq_slots']:
                    if slot['status'] == 'alt-met':
                        alt_subs.append(f"slot {slot['index']}: {slot['alt']} for {slot['required']}")
                        preq_parts.append(f"{slot['required']} ✓ (via alt {slot['alt']})")
                    else:
                        preq_parts.append(f"{slot['required']} ✓")
                alt_str = ('; '.join(alt_subs) if alt_subs else '—')
                preq_str = '; '.join(preq_parts) if preq_parts else '(none)'
                one_time = 'yes' if u['one_time_globally'] else 'no'
                roll = (f"+{u['delta_chance']}%/mo → {u['max_chance']}%"
                        if u['delta_chance'] else f"max {u['max_chance']}%")
                gates = []
                if u.get('milestone'):
                    gates.append(('✓ ' if u.get('milestone_met') else '⛔ BLOCKED: ') + u['milestone'])
                if u.get('objective'):
                    gates.append(('✓ ' if u.get('objective_met') else '⛔ BLOCKED: ') + u['objective'])
                gate_str = '; '.join(gates) if gates else '—'
                L.append(f"| {u['cost']} | {u['friendly']} | {u['fac_chance']}% | {roll} | "
                         f"{one_time} | {gate_str} | {alt_str} | {preq_str} |")
            L.append("")

    L.append("## Research weight allocation")
    L.append("| Slot | Type | Weight | % |")
    L.append("|---|---|---:|---:|")
    total_w = sum(rs['research_weights']) or 1
    for i, w in enumerate(rs['research_weights']):
        slot_type = f"Global tech {i}" if i < 3 else f"Faction project {i-3}"
        L.append(f"| {i} | {slot_type} | {w} | {w/total_w*100:.0f}% |")
    L.append("")
    L.append("## Active global techs")
    L.append("| Tech | Progress | Your contribution |")
    L.append("|---|---:|---:|")
    for t in rs['active_global_techs']:
        L.append(f"| {t['name']} | {t['total_progress']:,.1f} | {t['faction_contribution']:,.1f} |")
    L.append("")
    L.append("## Active faction projects")
    L.append("| Slot | Project | Progress | Weight |")
    L.append("|---|---|---:|---:|")
    for p in rs['active_faction_projects']:
        L.append(f"| {p['slot']-3} | {p['name']} | {p['progress']:,.1f} | {p['weight']} |")
    L.append("")
    if rs['paused_faction_projects']:
        L.append("## Paused projects (have progress, not in active slots)")
        L.append("| Project | Progress |")
        L.append("|---|---:|")
        for p in sorted(rs['paused_faction_projects'], key=lambda x: -x['progress']):
            L.append(f"| {p['name']} | {p['progress']:,.1f} |")
        L.append("")
    if rs['auto_triggers']:
        L.append("## Auto-trigger queue (priority-sorted)")
        L.append("| Project | Trigger value |")
        L.append("|---|---:|")
        for t in sorted(rs['auto_triggers'], key=lambda x: -x['trigger_value']):
            L.append(f"| {t['name']} | {t['trigger_value']} |")
        L.append("")

    # -- Next global techs to queue (prereqs done, not yet researched) --
    # Global techs CANNOT be swapped mid-research. Use this list to advise the
    # operator about what to pick when a slot opens.
    nt = s.get('next_global_techs', [])
    if nt:
        L.append("## Next global techs you can queue (prereqs already complete)")
        L.append("Global techs cannot be swapped — these are your menu when a slot opens.")
        L.append("Sorted by cost; mid-game high-value picks are usually around 35-50k RP.")
        L.append("")
        # Tag techs that unlock alien-hate-floor reduction or critical strategic
        # paths. The next-masking-target's missing-prereq globals (transitively
        # back through the global-tech tree) get a 🛸 tag.
        hate_path_globals = set()
        ah_ctx = s.get('alien_hate') or {}
        nt_target = ah_ctx.get('next_masking_target')
        if nt_target:
            # Load global tech prereq map once
            tech_template = s.get('templates', {}).get('tech_templates', {}) or {}
            # Determine finished global techs by checking which prereqs were "done"
            # in the next_masking_target's prereqs and walking back.
            # Walk the dependency chain: any unfinished global tech in the path
            # from a masking-target prereq backward is part of the hate-path.
            unfinished_globals = set()
            # Seed: missing global-tech prereqs of the masking target
            for p, st in nt_target.get('prereqs', []):
                if st != 'done' and not p.startswith('Project_'):
                    unfinished_globals.add(p)
            # BFS back through global tech prereqs
            queue = list(unfinished_globals)
            while queue:
                cur = queue.pop(0)
                hate_path_globals.add(cur)
                tt = tech_template.get(cur)
                if not tt:
                    continue
                for pr in (tt.get('prereqs') or []):
                    if pr.startswith('Project_'):
                        continue
                    if pr in hate_path_globals:
                        continue
                    # Only walk back if this prereq is also not done
                    # (We rely on the 'next_global_techs' filter for ready-to-queue
                    # techs to surface only the actionable ones.)
                    queue.append(pr)

        # Tag Command Center chain globals (🏛). Command Center is the
        # ops-center upgrade: each OpsCenter that becomes a Command Center gives
        # +6 MC (10 vs 4). With dozens of OpsCenters built, this is the single
        # biggest one-shot MC unlock available mid-game.
        #
        # Chain (verified from TIProjectTemplate.json):
        #   Project_CommandCenter ← FleetLogistics (g) + Project_RingCore +
        #                           Project_OperationsCenter + QuantumEncryption (g)
        #   Project_RingCore     ← OrbitalTorusHabs (g) + Project_OrbitalCore
        #   FleetLogistics       ← IntegratedEarthSpaceEconomy (g) +
        #                           SpaceNavies (g) + ImprovedShipbuildingTechniques (g)
        #
        # We hard-code the set because project templates aren't loaded into the
        # in-memory tech_template map (that only carries TITechTemplate entries).
        COMMAND_CENTER_CHAIN_GLOBALS = {
            'IntegratedEarthSpaceEconomy',
            'SpaceNavies',
            'ImprovedShipbuildingTechniques',
            'FleetLogistics',
            'QuantumEncryption',
            'OrbitalTorusHabs',
        }
        # Only tag if Command Center isn't already researched by this faction.
        # `finished_projects` is the faction's `finishedProjectNames` set.
        finished_proj = set(rs.get('finished_projects', []) or [])
        command_center_done = 'Project_CommandCenter' in finished_proj
        cc_path_globals = set() if command_center_done else COMMAND_CENTER_CHAIN_GLOBALS

        L.append("| Cost | Category | Tech | Notes | Data name |")
        L.append("|---:|---|---|---|---|")
        for t in nt[:40]:
            notes = []
            if t['data_name'] in hate_path_globals:
                notes.append("🛸 unlocks next hate-floor-reduction project")
            if t['data_name'] in cc_path_globals:
                notes.append("🏛 Command Center chain (each OpsCenter→CmdCenter = +6 MC)")
            if t['data_name'] == 'DeuteriumTritiumFusion':
                notes.append("⚛ opens fusion reactor era")
            note_str = '; '.join(notes) if notes else ''
            L.append(f"| {t['cost']:,} | {t['category']} | {t['friendly']} | {note_str} | `{t['data_name']}` |")
        if len(nt) > 40:
            L.append(f"| … | … | … +{len(nt)-40} more | | … |")
        L.append("")

    # -- Drive comparison (warship-relevant ranking via COMBAT thrust) --
    dc = s.get('drive_compare')
    if dc:
        L.append("## Drives you have vs available — warship-relevant ranking")
        L.append("Warship FoM = **combat thrust × √EV**, where combat thrust = cruise thrust ×")
        L.append("`thrustCap`. **Cruise thrust alone misleads** — drives like Poseidon Lantern ( template)")
        L.append("have huge cruise + EV numbers but `thrustCap=2`, making them cruise/transit")
        L.append("drives, not warship drives. Lodestar (`thrustCap=20`), Pharos (16), Firestar")
        L.append("(22) are proper combat drives. Always sort warships by combat thrust.")
        L.append("")
        L.append("Each drive project unlocks **6 variants** (x1..x6 thruster count)")
        L.append("with proportionally higher thrust and mass. Pick the variant whose mass")
        L.append("the hull can absorb without losing combat acceleration. **For ship-")
        L.append("specific x1..x6 tradeoffs** (ΔV / accel / turn rate on a calibrated hull)")
        L.append("see `warship_optimizer.py`'s `print_variant_sweep()`.")
        L.append("")
        L.append("**Top-line table** (sorted by max-variant combat FoM):")
        L.append("")
        L.append("| Drive family | EV (km/s) | ×Cap | x1 combat (MN) | x3 combat (MN) | x6 combat (MN) | Cooling | Reactor | Status | Project |")
        L.append("|---|---:|---:|---:|---:|---:|---|---|---|---|")
        for d in dc[:20]:
            # Family name without the "x6" suffix (the dc entry's 'drive' is x6's friendly name)
            family_name = d['drive'].rstrip(' x123456').rstrip()
            # Find x1, x3, x6 from the variants list (some families may not have all 6 — fall back gracefully)
            variants_by_n = {v['n_thrusters']: v for v in d.get('variants', [])}
            x1_combat = variants_by_n.get(1, {}).get('combat_thrust_N', 0) / 1e6
            x3_combat = variants_by_n.get(3, {}).get('combat_thrust_N', 0) / 1e6
            x6_combat = variants_by_n.get(6, {}).get('combat_thrust_N', 0) / 1e6
            cap = d['thrust_cap']
            reactor = d.get('required_reactor_display') or d.get('required_reactor') or '?'
            cooling = d.get('cooling', '?')
            status_emoji = '✓' if d['status'] == 'RESEARCHED' else '○'
            L.append(f"| {family_name} | {d['ev_kps']:.1f} | {cap:g}× | "
                     f"{x1_combat:,.1f} | {x3_combat:,.1f} | **{x6_combat:,.1f}** | "
                     f"{cooling} | {reactor} | {status_emoji} {d['status']} | `{d['project']}` |")
        L.append("")
        L.append("**Combat thrust scales linearly with variant** (x6 = 6× x1 cruise; combat = cruise × thrustCap).")
        L.append("EV and thrustCap are constant across variants. Higher variants pay mass for more thrust.")
        L.append("")
        L.append("**Transit drives** (high EV, low thrustCap like Poseidon's 2×) are still useful for colony ships,")
        L.append("freighters, and non-combatants — sort those by cruise thrust × √EV instead.")
        L.append("")

    # -- Ship refit picture (family-aware) --
    rp = s.get('refit_picture')
    if rp and (rp['refit_candidates'] or rp['stranded']):
        L.append("## Ship reactor refits — within-family only")
        L.append("**Refit rule:** the in-game refit UI only permits swapping a reactor to a")
        L.append("**higher tier in the SAME family** (e.g. `GasCoreFissionReactor III → IV` is fine; ")
        L.append("`SolidCoreFissionReactor X → GasCoreFissionReactor IV` is BLOCKED — cross-family).")
        L.append("Ships at family-max have no refit path short of scrap+rebuild.")
        L.append("")
        if rp['family_max_roman']:
            L.append("**Your family max researched tiers:**")
            for fam in sorted(rp['family_max_roman'].keys()):
                L.append(f"- `{fam}` → tier **{rp['family_max_roman'][fam]}** "
                         f"(numeric {rp['family_max'][fam]})")
            L.append("")
        if rp['refit_candidates']:
            L.append("### Refit candidates (in-family upgrade available)")
            L.append("Ranked by tier gap × live-ship count. **⚠ HARMFUL** in the verdict")
            L.append("column means the target reactor is heavier per GW than the current one")
            L.append("AND the drive doesn't need the extra wattage — the refit adds dead mass")
            L.append("for no thrust gain. **Skip the reactor refit unless also upgrading the")
            L.append("drive to a higher-power model.**")
            L.append("")
            L.append("| Class | Hull | Live ships | Current reactor | → Refit target | Drive | Verdict |")
            L.append("|---|---|---:|---|---|---|---|")
            for e in rp['refit_candidates']:
                obs_tag = ' ⚠ obsoleted design' if e.get('is_obsoleted_design') else ''
                if e.get('harmful_refit'):
                    verdict = '⚠ HARMFUL'
                    if e.get('reactor_mass_delta_t'):
                        verdict += f' (+{e["reactor_mass_delta_t"]:.0f}t dead mass)'
                else:
                    # Show the actual mass saving — "beneficial" was too vague.
                    mass_delta = e.get('reactor_mass_delta_t')
                    if mass_delta is not None and mass_delta < 0:
                        verdict = f'✓ saves {-mass_delta:.0f}t/ship'
                    elif mass_delta == 0:
                        verdict = '⚪ neutral (same spec power)'
                    else:
                        verdict = '✓ beneficial'
                L.append(f"| **{e['name']}**{obs_tag} | {e['hull']} | {e['live']} | "
                         f"`{e['reactor']}` | `{e['refit_target_data']}` | "
                         f"{e['drive']} | {verdict} |")
            L.append("")
            # Detail block for harmful refits
            harmful = [e for e in rp['refit_candidates'] if e.get('harmful_refit')]
            if harmful:
                L.append("#### Why these are harmful")
                for e in harmful:
                    L.append(f"- **{e['name']}** ({e['hull']}, {e['drive']}): "
                             f"{e['harmful_reason']}")
                L.append("")
                L.append("**Better option for these ships:** if drive can be refit to a "
                         "higher-tier drive within the same `driveClassification` family "
                         "(e.g. `PharosDrive → LodestarFissionLantern`, both `Fission_Thermal`), "
                         "pair the drive refit with the reactor refit. The reactor's extra "
                         "wattage then powers the better drive.")
                L.append("")
        if rp['stranded']:
            L.append("### Stranded at family max (no refit path)")
            L.append("These ships are at the family's current research ceiling. Refit dialog will")
            L.append("not let them swap to a different family's reactor. Options:")
            L.append("- **Keep as legacy** (recommended in metals-constrained runs)")
            L.append("- **Scrap + rebuild** on a different family — only if metals-rich and the hull")
            L.append("  is critical to frontline fleet composition")
            L.append("")
            L.append("| Class | Hull | Live ships | Reactor (family max) | Drive |")
            L.append("|---|---|---:|---|---|")
            for e in rp['stranded']:
                L.append(f"| {e['name']} | {e['hull']} | {e['live']} | "
                         f"`{e['reactor']}` | {e['drive']} |")
            L.append("")

    # -- Councilor roster integrity (deep-review) --
    ca = s.get('councilor_attrition')
    if ca:
        L.append("## Councilor roster integrity")
        L.append(f"Active roster: **{ca['active_count']}** councilors. The save does NOT keep")
        L.append("dead/dismissed councilors — their TICouncilorState records are deleted; only")
        L.append("`internalCouncilorSuspicion` ghost-keys and `turnedCouncilors` reveal attrition.")
        L.append("")
        L.append("| Active councilor | Type | Recruited |")
        L.append("|---|---|---|")
        for a in ca['active']:
            L.append(f"| {a['name']} | {a['type']} | {a['recruited']} |")
        L.append("")
        if ca['turned_agents']:
            L.append("### Double agents (turned, on rival rosters, working for us)")
            L.append("| Agent | Type | Hosted by | Still ours? |")
            L.append("|---|---|---|---|")
            for t in ca['turned_agents']:
                L.append(f"| {t['name']} | {t.get('type','?')} | "
                         f"{FACTION_DISPLAY.get(t['current_faction'], t['current_faction'])} | "
                         f"{'✓' if t['still_our_agent'] else '✗ LOST'} |")
            L.append("")
        if ca['former_members']:
            L.append("### Former members (ghost entries)")
            for f_ in ca['former_members']:
                L.append(f"- id {f_['id']}: {f_['name']} — {f_['fate']}")
            L.append("")
        L.append(f"Per-faction roster sizes: {ca['per_faction_counts']}")
        L.append(f"Recruit pool currently offered: {ca['recruit_pool_offered']}")
        L.append("")

    # -- Rival victory race (deep-review) --
    rvr = s.get('rival_victory_race')
    if rvr:
        L.append("## Rival victory race")
        L.append(f"| Faction | Chain progress | Cached research{unit_suffix()} | CPs | Top active projects |")
        L.append("|---|---|---:|---:|---|")
        for r in rvr['factions']:
            done_labels = [st['label'] for st in r['chain_steps'] if st['status'] == 'DONE']
            nxt = next((st for st in r['chain_steps'] if st['status'] != 'DONE'), None)
            chain_txt = f"{r['chain_done']}/{r['chain_len']} done"
            if done_labels:
                chain_txt += f" ({', '.join(done_labels)})"
            if nxt:
                chain_txt += f"; next: {nxt['label']} [{nxt['status']}]"
            top = ', '.join(f"{p} ({v:,})" for p, v in r['top_active_projects'][:3])
            L.append(f"| {r['name']} | {chain_txt} | {per_unit(r['cached_research_yr']):,.0f} | "
                     f"{r['cps']} | {top} |")
        L.append("")
        if rvr['formation_race']:
            L.append("### Formation / one-time-globally project race")
            L.append("First faction to finish removes the project for everyone.")
            L.append("")
            L.append("| Project | Needs nation | Cost | Done by | In progress | Available for |")
            L.append("|---|---|---:|---|---|---|")
            for f_ in rvr['formation_race']:
                if not (f_['done_by'] or f_['in_progress_by']):
                    # keep the table readable: only show contested/finished ones
                    # plus anything available to 3+ factions
                    if len(f_['available_for']) < 3:
                        continue
                avail = (', '.join(f_['available_for'])
                         if len(f_['available_for']) < 7 else 'ALL 7')
                L.append(f"| {f_['friendly']} | {f_['requiresNation']} | {f_['cost'] or '-'} | "
                         f"{', '.join(f_['done_by']) or '—'} | "
                         f"{', '.join(f_['in_progress_by']) or '—'} | {avail or '—'} |")
            L.append("")

    # -- Earth-orbit threat (deep-review) --
    eot = s.get('earth_orbit_threat')
    if eot:
        L.append("## Earth-orbit threat assessment")
        L.append("| Fleet | Faction | Ships | kt | Docked at | Weapons |")
        L.append("|---|---|---:|---:|---|---|")
        for f_ in eot['earth_fleets']:
            dock = f_['docked_at'] or '(free orbit)'
            if f_['docked_at_faction']:
                dock += f" ({f_['docked_at_faction']})"
            wp = ', '.join(f"{k}×{v}" for k, v in sorted(f_['weapons'].items(),
                                                          key=lambda x: -x[1])[:5]) or '—'
            tag = ' **(you)**' if f_['is_player'] else ''
            L.append(f"| {f_['fleet_name']}{tag} | {f_['faction']} | {f_['ships']} | "
                     f"{f_['mass_kt']:.1f} | {dock} | {wp} |")
        L.append("")
        L.append("### Your LEO hab defenses")
        for hab, mods in eot['leo_defenses'].items():
            L.append(f"- {hab}: " + ', '.join(f"{k}×{v}" for k, v in mods.items()))
        if eot['undefended_leo_habs']:
            L.append(f"- **NO defense modules**: {', '.join(eot['undefended_leo_habs'])}")
        L.append("")
        if eot['rival_leo_habs']:
            L.append("### Rival habs in Earth LEO (their staging points / your targets)")
            for h in eot['rival_leo_habs']:
                L.append(f"- {h['hab']} — {h['faction']} (T{h['tier']})")
            L.append("")

    # -- Economy ledger (deep-review) --
    eco = s.get('economy_ledger')
    if eco:
        L.append("## Economy ledger (Transactions-based, NET cash flow)")
        L.append("`Daily Income` = net passive tick (gross minus org upkeep, hab support,")
        L.append("ship maintenance, CP influence maintenance). `cachedYearlyRevenue` is gross —")
        L.append("don't compare the two directly.")
        L.append("")
        L.append("7-day average daily NET income: " +
                 ', '.join(f"{k} {v:+.2f}/d" for k, v in sorted(eco['daily_net_7d_avg'].items())))
        L.append("")
        for window in (30, 90):
            fl = eco['flows'].get(window) or eco['flows'].get(str(window)) or {}
            L.append(f"### Flows, last {window} days")
            L.append("| Resource | Category | Amount |")
            L.append("|---|---|---:|")
            for res in ('Money', 'Influence', 'Operations', 'Boost'):
                cats = fl.get(res, {})
                for cat, v in sorted(cats.items(), key=lambda x: x[1]):
                    if abs(v) >= 1:
                        L.append(f"| {res} | {cat} | {v:+,.1f} |")
                net = sum(cats.values())
                L.append(f"| **{res}** | **NET** | **{net:+,.1f}** |")
            L.append("")
        L.append(f"### Org portfolio ({eco['org_count']} orgs)")
        t = eco['org_totals']
        L.append(f"Totals/mo: money {t.get('money_mo',0):+,.0f}, influence {t.get('influence_mo',0):+,.0f}, "
                 f"ops {t.get('ops_mo',0):+,.0f}, boost {t.get('boost_mo',0):+,.0f}, "
                 f"research {t.get('research_mo',0):+,.0f}, MC {t.get('mc',0):+,.0f}, "
                 f"project slots {t.get('proj_capacity',0):.0f}")
        L.append("")
        money_sinks = sorted(eco['org_rows'], key=lambda r: r['money_mo'])[:8]
        L.append("Top org money sinks (candidates to sell in a cash crunch — check stats first):")
        for r in money_sinks:
            if r['money_mo'] < 0:
                L.append(f"- {r['name']} (T{r['tier']}, {r['holder']}): "
                         f"${r['money_mo']:,.0f}/mo, research {r['research_mo']:+,.0f}/mo, MC {r['mc']:+,.0f}")
        L.append("")
        if eco['rare_resource_ledger']:
            L.append("### Exotics / Antimatter ledger (all recorded)")
            for r in eco['rare_resource_ledger']:
                L.append(f"- {r['date']} {r['category']}: {r['resource']} {r['amount']:+.2f}")
            L.append("")

    # -- Alien posture, intel-filtered (deep-review) --
    ap2 = s.get('alien_posture')
    if ap2:
        L.append("## Alien posture (intel-filtered — player-known assets only)")
        L.append(f"Known alien ships: **{ap2['known_fleet_ship_total']}** in "
                 f"{len(ap2['known_fleets'])} fleets. Unknown fleets: {ap2['unknown_fleet_count']} "
                 f"({ap2['unknown_ship_count']} ships — details REDACTED, no player intel).")
        L.append("")
        L.append("Known ship counts by location: " +
                 ', '.join(f"{k}: {v}" for k, v in ap2['ships_by_location'].items()))
        L.append("")
        L.append("| Alien fleet | Location | Ships | kt | Intel |")
        L.append("|---|---|---:|---:|---:|")
        for f_ in ap2['known_fleets']:
            L.append(f"| {f_['fleet']} | {f_['location']} | {f_['ships']} | "
                     f"{f_['mass_kt']:.0f} | {f_['intel']:.2f} |")
        L.append("")
        L.append(f"### Known alien habs ({len(ap2['known_habs'])}; "
                 f"{ap2['unknown_hab_count']} unknown/redacted)")
        L.append("| Hab | Location | Type | Tier |")
        L.append("|---|---|---|---:|")
        for h in ap2['known_habs']:
            L.append(f"| {h['hab']} | {h['location']} | {h['type']} | {h['tier']} |")
        L.append("")
        xr = ap2.get('xenoforming_regions') or []
        L.append(f"### Xenoforming on Earth ({len(xr)} regions, "
                 f"total level {ap2.get('xenoforming_total', 0)})")
        if xr:
            L.append("(`TIRegionXenoformingState.xenoformingLevel` — stored globally; "
                     "player receives LogXenoformingDetected notifications)")
            L.append("| Region | Level |")
            L.append("|---|---:|")
            for r in xr:
                L.append(f"| {r['region']} | {r['level']} |")
        else:
            L.append("- none active")
        L.append("")
        agf = ap2.get('alien_ground_facilities') or []
        if agf:
            L.append(f"### Alien ground facilities BUILT ({len(agf)})")
            for a in agf:
                L.append(f"- {a['region']}: {a['facility']} (HP {a['hp']})")
        else:
            L.append("### Alien ground facilities BUILT: none")
        L.append("")

    # -- Victory chain --
    if s['victory_chain']:
        L.append(f"## Victory chain ({FACTION_DISPLAY.get(s['player_template'], s['player_template'])})")
        L.append("| Step | Project | Status | Progress |")
        L.append("|---|---|---|---:|")
        for v in s['victory_chain']:
            prog = f"{v['progress']:,.1f}" if v['progress'] else '-'
            L.append(f"| {v['label']} | `{v['project']}` | **{v['status']}** | {prog} |")
        L.append("")

    # -- Long lists (optional) --
    if not brief:
        L.append(f"## Completed global techs ({len(rs['finished_techs'])})")
        for t in rs['finished_techs']:
            L.append(f"- {t}")
        L.append("")
        L.append(f"## Completed faction projects ({len(rs['finished_projects'])})")
        for p in rs['finished_projects']:
            L.append(f"- {p}")
        L.append("")
        L.append(f"## Available faction projects ({len(rs['available_projects'])})")
        for p in rs['available_projects']:
            L.append(f"- {p}")
        L.append("")
        if rs['missed_projects']:
            L.append(f"## Missed faction projects ({len(rs['missed_projects'])})")
            L.append("*Terminally dead for this faction — failed their `factionAvailableChance` roll or were claimed by another faction (one-time-globally). Excluded from the Unlockable table.*")
            for p in rs['missed_projects']:
                L.append(f"- {p}")
            L.append("")
        if rs['hidden_projects']:
            L.append(f"## Hidden faction projects ({len(rs['hidden_projects'])})")
            for p in rs['hidden_projects']:
                L.append(f"- {p}")
            L.append("")

    return "\n".join(L)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def build_snapshot(save_path, faction_template=None):
    faction_template = require_faction(faction_template)
    data = load_save(save_path)
    gs = data.get('gamestates', {})

    game_date, ts = get_game_date(gs)
    grs, gvs = get_global_state(gs)
    factions = get_factions(gs)
    player_fid = faction_id_by_template(factions, faction_template)
    if player_fid is None:
        raise ValueError(f"Faction '{faction_template}' not found. Available: "
                         f"{[v.get('templateName') for v in factions.values()]}")
    player_faction = factions[player_fid]
    templates = load_game_templates()

    # Habs per faction (count only)
    habs_by_faction = Counter()
    for hid, hv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState'):
        fid = fac_id_from_field(hv.get('faction'))
        tpl = factions.get(fid, {}).get('templateName')
        if tpl:
            habs_by_faction[tpl] += 1

    fleet_by_faction = extract_fleet_inventory(gs, factions)
    cp_data = extract_control_points(gs, factions)
    cps_by_faction = cp_data['by_faction']
    hab_inv = extract_hab_inventory(gs, player_fid)
    mc_latent = extract_mc_latent_demand(gs, player_fid)
    mc_breakdown = extract_mc_full(gs, player_fid, hab_inv, factions,
                                   mining_cost=mc_latent['mine_network_cost'])
    # Per-CP split of each nation's MC (the tooltip's "nations" line) — NOT the
    # majority-nation total in cp_data['earth_mc_by_faction'], which over-reads for
    # every partially-held nation (that total is still used for the untapped-MC table).
    mc_breakdown['mc_nations'] = extract_nation_mc_share(gs, player_fid)
    cp_cap = extract_cp_cap(gs, player_fid, factions)
    leo_saturation = extract_leo_saturation(gs, player_fid)
    leo_slots = extract_leo_slot_capacity(gs, player_fid)
    cp_value_per_bonus = extract_cp_priority_value_per_bonus(gs, player_fid)
    nation_dashboard = extract_nation_investment_dashboard(gs, player_fid)
    councilor_roster = extract_councilor_roster(gs, player_fid)
    org_marketplace = extract_org_marketplace(gs, player_fid)
    mines = extract_mine_inventory(gs, player_fid)
    # Annotate each LEO bonus with player's CP priority weight on that axis.
    # This is what determines "how expensive is it to drop 6% of this bonus":
    # high CP weight = expensive (bonus is amplifying lots of priority weight);
    # low CP weight = cheap (bonus has little to amplify).
    if leo_saturation:
        for b in leo_saturation['bonuses']:
            b['cp_priority_weight'] = cp_value_per_bonus.get(b['bonus'], 0)
    alien_pressure = extract_alien_pressure(gs, player_fid)
    alien_hate = extract_alien_hate_analysis(gs, player_fid)
    threat_assessment = extract_faction_threat_assessment(gs, player_fid, factions)
    research = extract_snapshot_status(gs, player_faction, grs)
    research_income = extract_snapshot_income_breakdown(gs, player_fid, factions, templates)
    # Plug in the MC cap from mc_breakdown if available so unused MC research
    # is computed correctly. mc_breakdown stores the components separately;
    # cap = HQ + nations + councilors + hab_produced. On the reference-campaign 2032-04-28 save:
    # 22 + 250 + 10 + 264 = 546 (in-game shows 550 — small rounding from mines).
    if research_income and mc_breakdown:
        mc_cap = ((mc_breakdown.get('mc_hq') or 0)
                  + (mc_breakdown.get('mc_nations') or 0)
                  + (mc_breakdown.get('mc_councilors') or 0)
                  + (mc_breakdown.get('mc_hab_produced') or 0))
        if mc_cap:
            free_mc = max(0, mc_cap - (research_income.get('mc_usage') or 0))
            research_income['unused_mc_per_day'] = round(free_mc * 0.075, 2)
            research_income['mc_capacity'] = mc_cap
            # Recompute totals with MC in
            base = (research_income['hq_per_day']
                    + research_income['councilors_per_day_estimate']
                    + research_income['nation_per_day_total']
                    + research_income['habs_per_day']
                    + research_income['unused_mc_per_day'])
            research_income['base_sources_per_day'] = round(base, 2)
            dist = base * research_income['dist_bonus_pct']
            research_income['dist_bonus_per_day'] = round(dist, 2)
            research_income['total_per_day'] = round(base + dist, 2)
            research_income['total_per_month'] = round((base + dist) * 30.44, 1)
            research_income['total_per_year'] = round((base + dist) * 365.25, 1)
    # Overlay the EXACT model (research_income.py: code-verified nation formula, org/trait
    # councilor incomes, template-valued ControlPointResearch effects incl. scenario/mod
    # layers, per-slot distribution bonus) on the heuristic breakdown above. The heuristic
    # stays as the fallback when the templates mirror is missing.
    faction_comparison = extract_faction_comparison(factions)
    if research_income:
        model = None
        try:
            import research_income as _ri   # lazy: research_income imports this module
            sr = _ri.SaveResearch(gs=gs)
            model = sr.breakdown(faction_template, with_mc=True)
            # Live research for EVERY faction's comparison row (replaces the stale cache).
            for row in faction_comparison:
                if row['template'] == 'AlienCouncil':
                    row['research_live_day'] = 0.0       # the aliens don't research
                    continue
                try:
                    b = sr.breakdown(row['template'], with_mc=True)
                    # Rivals: the ledger-anchored figure (their booked base × distribution) is
                    # the game's own number; the formula is only its explanation. The player's
                    # row uses the live model, which matches today's tooltip.
                    row['research_live_day'] = (b['total_day'] if row['template'] == faction_template
                                                else (b['ledger_total_day'] or b['total_day']))
                except Exception:
                    row['research_live_day'] = None
        except Exception as ex:                      # templates missing, faction mismatch…
            research_income['model_error'] = str(ex)
        if model:
            research_income.update({
                'councilors_per_day_estimate': round(model['councilors_day'], 2),
                'nation_cp_share_per_day': round(model['nations_day'], 2),
                'advise_direct_per_day': 0.0,        # adviser Science is inside the nation formula
                'nation_per_day_total': round(model['nations_day'], 2),
                'habs_per_day': round(model['habs_day'], 2),
                'habs_per_month': round(model['habs_day'] * 30.44, 1),
                'unused_mc_per_day': round(model['unused_mc_day'], 2),
                'base_sources_per_day': round(model['total_day'] / (1 + model['dist_pct']), 2),
                'dist_bonus_pct': model['dist_pct'],
                'dist_bonus_per_day': round(model['dist_day'], 2),
                'total_per_day': round(model['total_day'], 2),
                'total_per_month': round(model['total_month'], 1),
                'total_per_year': round(model['total_day'] * 365.25, 1),
                'is_estimated': False,
                'model': 'exact',
                'nation_contribs': [{
                    'nation_template': r['template'], 'nation_name': r['name'],
                    'cps_owned': r['my_cps'], 'total_cps': r['num_cps'],
                    'share_pct': round(100.0 * r['my_cps'] / max(r['num_cps'], 1), 1),
                    'monthly_research': round(r['research_month'], 1),
                    'my_share_per_day': round(r['player_month'] * 12 / 365.2421875, 2),
                } for r in model['nation_rows']],
                'booked_ledger_per_day': model['booked_day'],
            })
    rival_resources = extract_rival_resources(gs, factions, player_fid)
    unlockable_projects = extract_unlockable_projects(gs, player_fid, templates)
    chain = VICTORY_CHAINS.get(faction_template, [])
    victory_chain = extract_victory_chain(player_faction, chain) if chain else []
    councilor_attrition = extract_councilor_attrition(gs, player_fid, factions)
    rival_victory_race = extract_rival_victory_race(gs, factions, templates)
    earth_orbit_threat = extract_earth_orbit_threat(gs, player_fid, factions)
    economy_ledger = extract_economy_ledger(gs, player_fid, game_date)
    alien_posture = extract_alien_posture(gs, player_fid, factions)
    next_techs = extract_next_global_techs(gs, templates)
    drive_compare = extract_drive_comparison(gs, player_fid, templates)
    # Pass templates dict through so the refit function can detect harmful
    # reactor upgrades (drives that don't need the higher wattage but pay
    # mass cost for it).
    gs['templates'] = templates
    refit_picture = extract_ship_refit_candidates(gs, player_fid)
    player_ship_instances = extract_player_ship_instances(gs, player_fid, factions)

    snapshot = {
        'save_path': str(save_path),
        'game_date': game_date,
        'difficulty': (gvs or {}).get('difficulty'),
        'save_version': (gvs or {}).get('latestSaveVersion'),
        'player_template': faction_template,
        'faction_name': FACTION_DISPLAY.get(faction_template, faction_template),
        'player_faction': player_faction,
        'templates': templates,
        'faction_comparison': faction_comparison,
        'habs_by_faction': habs_by_faction,
        'fleet_by_faction': fleet_by_faction,
        'cps_by_faction': cps_by_faction,
        'nation_control': cp_data,
        'hab_inventory': hab_inv,
        'mc_breakdown': mc_breakdown,
        'cp_cap': cp_cap,
        'leo_saturation': leo_saturation,
        'leo_slots': leo_slots,
        'mc_latent': mc_latent,
        'mines': mines,
        'nation_dashboard': nation_dashboard,
        'councilor_roster': councilor_roster,
        'org_marketplace': org_marketplace,
        'alien_pressure': alien_pressure,
        'alien_hate': alien_hate,
        'threat_assessment': threat_assessment,
        'research': research,
        'research_income': research_income,
        'rival_resources': rival_resources,
        'unlockable_projects': unlockable_projects,
        'victory_chain': victory_chain,
        'next_global_techs': next_techs,
        'drive_compare': drive_compare,
        'refit_picture': refit_picture,
        'player_ship_instances': player_ship_instances,
        'councilor_attrition': councilor_attrition,
        'rival_victory_race': rival_victory_race,
        'earth_orbit_threat': earth_orbit_threat,
        'economy_ledger': economy_ledger,
        'alien_posture': alien_posture,
    }
    return snapshot


def main():
    parser = argparse.ArgumentParser(description='Terra Invicta save-file situation extractor.')
    parser.add_argument('save_file', nargs='?', help='Path to the .json save file')
    parser.add_argument('--newest', action='store_true',
                        help='Analyze the most recent save (auto-detected via ti_config)')
    parser.add_argument('--faction', default=None,
                        help='Faction template name (default: "faction" from config.json)')
    parser.add_argument('--output', '-o', help='Output file path (default: stdout)')
    parser.add_argument('--json', action='store_true',
                        help='Output structured JSON instead of markdown')
    parser.add_argument('--brief', action='store_true',
                        help='Omit the long completed/available/hidden project lists')
    parser.add_argument('--research-options-dir',
                        help='Write research_options.json, research_options.md, and manifest.json to this directory')
    args = parser.parse_args()

    save_path = args.save_file
    if save_path is None:
        if args.newest:
            save_path = newest_save()
            if save_path is None:
                parser.error('--newest: no Terra Invicta saves found; '
                             'set "save_dir" in config.json')
        else:
            parser.error('provide a save file path (or use --newest)')

    snapshot = build_snapshot(save_path, args.faction)

    if args.research_options_dir:
        artifact_info = write_research_options_artifacts(snapshot, args.research_options_dir)
        counts = artifact_info['counts']
        print(f"Research options written to {artifact_info['dir']} "
              f"(needs_dossier={counts.get('needs_dossier', 0)}, "
              f"deferred={counts.get('deferred_with_reason', 0)}, "
              f"excluded={counts.get('excluded_with_reason', 0)})")

    if args.json:
        text = json.dumps(make_json_safe(snapshot), indent=2, ensure_ascii=False)
    else:
        text = format_markdown(snapshot, brief=args.brief)

    if args.output:
        Path(args.output).write_text(text, encoding='utf-8')
        print(f"Written to {args.output}")
    else:
        # Windows consoles default to cp1252 and choke on ∞, →, etc. Force UTF-8.
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
        print(text)


if __name__ == '__main__':
    main()
