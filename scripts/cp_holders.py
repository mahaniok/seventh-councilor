#!/usr/bin/env python3
"""cp_holders.py — Earth control-point map: who holds what, nation by nation.

One table, sorted by GDP: every nation's CP slots, which faction holds each, the player's
share, public opinion for the player's ideology, cohesion and federation. The question it
answers is the one the extractor's per-faction tallies can't: "in THIS set of countries,
who is in the way and where are the open seats?" — e.g. a Resistance player consolidating
Russia + the EU, or anyone sizing a theatre before a Control Nation push.

Usage:
    python3 cp_holders.py                          # newest save, every nation with GDP ≥ 0.05T
    python3 cp_holders.py <save> --min-gdp 0.3     # bigger economies only
    python3 cp_holders.py --filter DEU,FRA,GBR,ITA,ESP,POL   # nation templateName substrings
    python3 cp_holders.py --held                   # only nations where my faction holds a CP
    python3 cp_holders.py --json

Columns: GDP ($T) · CPs = held-by-anyone / slots · Mine · Holders (abbrev: Res Init Serv Prot
Acad HF Exo, `!` = crackdown-suppressed CP) · Opinion = player's ideology share (what Control
Nation / Public Campaign care about) · Cohesion/rest · Fed (federation lead nation, if any).
Read-only; faction ids resolved through the save, nothing inferred from names (P11).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_snapshot as er  # noqa: E402
from ti_config import newest_save, require_faction  # noqa: E402

_NS = 'PavonisInteractive.TerraInvicta.'
ABBREV = {'ResistCouncil': 'Res', 'ExploitCouncil': 'Init', 'SubmitCouncil': 'Serv',
          'AppeaseCouncil': 'Prot', 'CooperateCouncil': 'Acad', 'DestroyCouncil': 'HF',
          'EscapeCouncil': 'Exo', 'AlienCouncil': 'ALIEN'}
IDEOLOGY = {'ResistCouncil': 'Resist', 'ExploitCouncil': 'Exploit', 'SubmitCouncil': 'Submit',
            'AppeaseCouncil': 'Appease', 'CooperateCouncil': 'Cooperate',
            'DestroyCouncil': 'Destroy', 'EscapeCouncil': 'Escape'}


def build_rows(gs, faction_template):
    factions = er.get_factions(gs)
    fid = er.faction_id_by_template(factions, faction_template)
    tpl_of = {f: v.get('templateName') for f, v in factions.items()}
    nations = dict(er.kv_items(gs, _NS + 'TINationState'))
    cps = dict(er.kv_items(gs, _NS + 'TIControlPoint'))
    ideology = IDEOLOGY.get(faction_template, 'Resist')

    held = {}   # nid -> list of (faction template or None, disabled)
    for cid, cp in cps.items():
        nid = er.deref(cp.get('nation'))
        owner = er.fac_id_from_field(cp.get('faction'))
        held.setdefault(nid, []).append((tpl_of.get(owner) if owner is not None else None,
                                         bool(cp.get('benefitsDisabled'))))
    rows = []
    for nid, n in nations.items():
        if not n.get('regions'):
            continue
        slots = n.get('numControlPoints') or len(n.get('controlPoints') or [])
        holders = {}
        mine = 0
        mine_disabled = 0
        for tpl, dis in held.get(nid, []):
            if tpl is None:
                continue
            holders[tpl] = holders.get(tpl, 0) + 1
            if tpl == faction_template:
                mine += 1
                mine_disabled += dis
        fed = n.get('federation')
        fed_lead = None
        if fed:
            fed_id = er.deref(fed)
            lead = next((v for _, v in er.kv_items(gs, _NS + 'TIFederationState')
                         if er.deref(v.get('ID')) == fed_id), None)
            if lead is not None:
                ln = nations.get(er.deref(lead.get('leadNation')), {})
                fed_lead = ln.get('displayName') or ln.get('templateName')
        po = n.get('publicOpinion') or {}
        rows.append({
            'template': n.get('templateName'), 'name': n.get('displayName') or n.get('templateName'),
            'gdp_t': (n.get('GDP') or 0) / 1e12, 'slots': slots,
            'held': sum(holders.values()), 'mine': mine, 'mine_disabled': mine_disabled,
            'holders': holders, 'opinion': po.get(ideology, 0.0) or 0.0,
            'undecided': po.get('Undecided', 0.0) or 0.0,
            'cohesion': n.get('cohesion') or 0.0, 'rest': n.get('cohesionRestState_dailyCache'),
            'federation_lead': fed_lead, 'regions': len(n.get('regions') or []),
            'population_m': (n.get('historyPopulation') or [0])[0],
        })
    rows.sort(key=lambda r: -r['gdp_t'])
    return rows, fid


def fmt_holders(r):
    parts = []
    for tpl, k in sorted(r['holders'].items(), key=lambda kv: -kv[1]):
        parts.append(f"{ABBREV.get(tpl, tpl)} {k}")
    s = ', '.join(parts) or '—'
    if r['mine_disabled']:
        s += f" (!{r['mine_disabled']} mine suppressed)"
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('save', nargs='?')
    ap.add_argument('--faction', default=None)
    ap.add_argument('--filter', default=None, help='comma-separated templateName substrings')
    ap.add_argument('--min-gdp', type=float, default=0.05, help='$T, default 0.05')
    ap.add_argument('--held', action='store_true', help='only nations where my faction holds a CP')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    faction = require_faction(a.faction)
    save = a.save or (str(newest_save()) if newest_save() else None)
    if not save or not os.path.isfile(save):
        sys.exit('no save file found/given')
    gs = er.load_save(save)['gamestates']
    date, _ = er.get_game_date(gs)
    rows, _ = build_rows(gs, faction)
    if a.filter:
        subs = [s.strip() for s in a.filter.split(',') if s.strip()]
        rows = [r for r in rows if any(s in (r['template'] or '') for s in subs)]
    rows = [r for r in rows if r['gdp_t'] >= a.min_gdp and (r['mine'] > 0 or not a.held)]
    if a.json:
        print(json.dumps(rows, indent=1, default=str))
        return
    ideology = IDEOLOGY.get(faction, 'Resist')
    print(f"# Control-point holders — {faction} — {date} — {os.path.basename(save)}")
    print(f"| Nation | GDP $T | Rgn | CPs held/slots | Mine | Holders | {ideology} % | Undec % | Coh→rest | Federation lead |")
    print("|---|---:|---:|---:|---:|---|---:|---:|---|---|")
    for r in rows:
        rest = f"{r['rest']:.1f}" if r['rest'] is not None else '?'
        print(f"| {r['name']} (`{r['template']}`) | {r['gdp_t']:.2f} | {r['regions']} | "
              f"{r['held']}/{r['slots']} | {r['mine']} | {fmt_holders(r)} | "
              f"{100*r['opinion']:.0f} | {100*r['undecided']:.0f} | {r['cohesion']:.1f}→{rest} | "
              f"{r['federation_lead'] or '—'} |")
    tot_slots = sum(r['slots'] for r in rows)
    tot_mine = sum(r['mine'] for r in rows)
    tot_held = sum(r['held'] for r in rows)
    print(f"\n{len(rows)} nations · slots {tot_slots} · held by anyone {tot_held} · mine {tot_mine} · "
          f"GDP ${sum(r['gdp_t'] for r in rows):.1f}T")


if __name__ == '__main__':
    main()
