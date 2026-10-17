#!/usr/bin/env python3
"""base_fix_audit.py — the recurring "what should I click on my bases right now?" report.

One pass over ONE save (they are 60-90 MB — never parse twice) answering the four
questions a hab-side turn actually consists of:

  1. UNPOWERED MODULES — what can I switch on, and is it worth the upkeep?
     Power verdicts come from `hab_power_audit.power_ledger`; each candidate is
     screened by its `supportMaterials_month` and SORTED so boost-free flips come
     first (E37: powerable ≠ worth powering — an AdministrationTower is +1 MC for
     4 boost/month, a CommandCenter's +10 MC costs no boost, a Farm costs nothing).
  2. HABS WITH NO OpsCenter/CommandCenter — separating the ones already building
     one (nothing to do — note an OC→CC upgrade DELETES the OC state at click time,
     E28, so those habs read as "missing" while the CC builds) from real gaps, and
     T1 habs that need a core first.
  3. OC→CC UPGRADES — `cc_upgrade_planner.collect_candidates`, ranked by BUILD TIME
     (the OpsCenter is dark for the whole build, so the real price is days × 4
     MC-days, E28) and gated on POWER (+200 net, E34).
  4. MINES — tier UPGRADES first (0 MC, E38) via `mine_upgrade_planner.mine_rows`,
     then bases with no live mine, each priced with the mining-MC quadratic margin
     the new mine would add.

Plus the MC headline the four answers hang off: capacity vs usage, and the latent
demand from mines under construction (quadratic — often several hundred MC).

Everything here delegates to the existing tools; this script is the ORDER and the
screening, not new mechanics. Ground truth remains the in-game tooltips.

Usage:
    python3 base_fix_audit.py [save.json|.gz] [--faction X] [--top N]
        [--sort days|metals]   OC→CC ranking key (default days)
        [--scarce-only]        section 4: only upgrades feeding a scarce resource
        [--json]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from extract_snapshot import (load_save, get_game_date, get_factions,
                              faction_id_by_template, extract_hab_inventory,
                              extract_mine_inventory, extract_mc_full,
                              extract_mc_latent_demand, extract_control_points)
from hab_power_audit import load_templates, power_ledger
from cc_upgrade_planner import collect_candidates
from mine_upgrade_planner import mine_rows, body_cost
from mine_completion_timeline import derive_K_income, kv_items, deref
from ti_config import CONFIG, newest_save

FREE_MINES = 36          # in-game Space Mine Freebies allowance (E19)
RES = ('metals', 'water', 'volatiles', 'nobles', 'fissiles')


def network_mc(active):
    """Mining-network MC = (active − free)² / 2, decompiled (E19/E38)."""
    return max(0, active - FREE_MINES) ** 2 / 2.0


def marginal_mine_mc(active):
    """MC the NEXT new mine adds. A tier upgrade adds 0 — that is the whole point."""
    return network_mc(active + 1) - network_mc(active)


def fmt_support(sm):
    return ', '.join(f"{k} {v:g}" for k, v in sm.items()) if sm else 'none'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('save', nargs='?', help='save file (default: newest)')
    ap.add_argument('--faction', default=CONFIG.get('faction'))
    ap.add_argument('--top', type=int, default=12, help='rows per section (default 12)')
    ap.add_argument('--sort', choices=('days', 'metals'), default='days',
                    help='OC→CC ranking key (default days — the MC blackout, E28)')
    ap.add_argument('--scarce-only', action='store_true',
                    help='section 4: only mine upgrades feeding a scarce resource')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    path = args.save or newest_save()
    if not path:
        raise SystemExit("no save found — pass a path or set save_dir in config.json")
    if not args.faction:
        raise SystemExit("faction unknown — set it in config.json or pass --faction")
    if not args.json:
        print(f"(save: {path})")

    import datetime
    gs = load_save(path)['gamestates']
    gd = get_game_date(gs)
    date_str = gd[0] if isinstance(gd, tuple) else str(gd)[:10]
    save_date = datetime.date.fromisoformat(date_str)
    fid = faction_id_by_template(get_factions(gs), args.faction)
    if fid is None:
        raise SystemExit(f"faction {args.faction!r} not found in save")

    mod_tmpl, body_tmpl = load_templates()
    out = {'save': path, 'date': date_str, 'faction': args.faction}

    # ---- MC headline -------------------------------------------------------
    # Same assembly extract_snapshot uses: mining cost comes from the latent-demand
    # extractor (exact quadratic), nations from the control-point table.
    hi = extract_hab_inventory(gs, fid)
    factions = get_factions(gs)
    latent_demand = extract_mc_latent_demand(gs, fid)
    mc = extract_mc_full(gs, fid, hi, factions,
                         mining_cost=latent_demand['mine_network_cost'])
    from extract_snapshot import extract_nation_mc_share
    mc['mc_nations'] = extract_nation_mc_share(gs, fid)   # per-CP split = the tooltip's nations line
    available = (mc['mc_hq'] + mc['mc_councilors'] + mc['mc_nations']
                 + mc['mc_hab_produced'])
    used = mc['mc_used_reported']
    mines = extract_mine_inventory(gs, fid)
    n_active = len(mines['active'])
    n_building = len(mines['building'])
    latent = network_mc(n_active + n_building) - network_mc(n_active)
    out['mission_control'] = {
        'available': round(available), 'used': round(used),
        'slack': round(available - used),
        'pending_from_inflight_modules': round(mc.get('mc_pending_net') or 0),
        'mines_active': n_active, 'mines_building': n_building,
        'latent_mining_mc_when_they_land': round(latent),
        'marginal_mc_next_new_mine': round(marginal_mine_mc(n_active), 1),
    }

    # ---- 1. unpowered modules ---------------------------------------------
    rows = power_ledger(gs, fid, mod_tmpl, body_tmpl)
    flips = []
    for r in rows:
        for v in r['unpowered']:
            if v['verdict'].startswith('SHORT'):
                continue
            sm = v.get('support_month') or {}
            flips.append({
                'hab': r['hab'], 'body': r['body'], 'module': v['module'],
                'verdict': v['verdict'], 'support_month': sm,
                'boost': sm.get('boost', 0),
                'mission_control': (mod_tmpl.get(v['module'], {}) or {}).get('missionControl', 0),
                'solar_estimated': r['solar_estimated'],
            })
    # boost-free first, then by MC gain: the free wins should never be buried under
    # a wall of AdministrationTowers the player has already decided against (E37).
    flips.sort(key=lambda f: (f['boost'] > 0, -f['mission_control'], f['hab']))
    out['power_on'] = flips

    # ---- 2. habs with no OC/CC --------------------------------------------
    out['mc_module_gaps'] = {
        'missing_built': hi.get('habs_missing_mc_module_built'),
        'missing_after_current_builds': hi.get('habs_missing_mc_module_total'),
        'buildable_now_t2': hi.get('habs_missing_mc_t2', []),
        'need_core_first_t1': hi.get('habs_missing_mc_t1', []),
        'habs_total': len(hi.get('habs', [])),
    }

    # ---- 3. OC→CC ----------------------------------------------------------
    cands, unpowered_ocs = collect_candidates(gs, fid, save_date, sort=args.sort)
    for c in cands:
        c['mc_days_dark'] = c['days'] * 4
    out['oc_to_cc'] = {'unpowered_opscenters': unpowered_ocs, 'candidates': cands}

    # ---- 4. mines ----------------------------------------------------------
    K, _ = derive_K_income(gs, fid)
    mine_modifiers = {m['dataName']: m.get('miningModifier', 1)
                      for m in json.load(open(os.path.join(
                          str(__import__('ti_config').find_templates_dir()),
                          'TIHabModuleTemplate.json'), encoding='utf-8')) if m.get('mine')}
    focus = list(RES)
    mrows, _ = mine_rows(gs, fid, K, focus, mine_modifiers)
    scarce = set(mines.get('scarce') or [])
    upgrades, new_sites = [], []
    for r in mrows:
        delta = r['delta']
        gain = {k: round(v, 1) for k, v in delta.items() if v > 0.5}
        if not r['mine']:
            if sum(r['pot'].values()) < 1:
                continue
            new_sites.append({
                'hab': r['hab'], 'body': r['body'], 'hab_tier': r['hab_tier'],
                'l3_potential': {k: round(v, 1) for k, v in r['pot'].items() if v > 0.5},
                'metals_cost_t2_t3': r['cost_t2_t3'][2], 'cost_known': r['cost_known'],
                'steps_to_L3': r['steps_to_L3'], 'ready': r['ready'],
                'destroyed_slots': r['destroyed_slots'],
                'mc_cost': round(marginal_mine_mc(n_active), 1),
            })
            continue
        if r['building'] or r['mine_mod'] >= 2.0 or not gain:
            continue
        if args.scarce_only and not (scarce & set(gain)):
            continue
        dm = delta['metals']
        upgrades.append({
            'hab': r['hab'], 'body': r['body'], 'ready': r['ready'],
            'gain_month': gain,
            'feeds_scarce': sorted(scarce & set(gain)),
            'metals_cost': r['cost_t2_t3'][2], 'cost_known': r['cost_known'],
            'volatiles_cost': r['cost_t2_t3'][1],
            'payback_months': round(r['cost_t2_t3'][2] / dm, 1) if dm > 1 else None,
            'mc_cost': 0,
        })
    # scarce-resource suppliers first, then cheapest metals — an upgrade that feeds
    # a resource under its depletion guard beats a faster metals payback (E38.4).
    upgrades.sort(key=lambda u: (not u['feeds_scarce'], u['metals_cost']))
    new_sites.sort(key=lambda n: -sum(n['l3_potential'].values()))
    out['mines'] = {'scarce_resources': sorted(scarce),
                    'upgrades_0_mc': upgrades, 'bases_with_no_mine': new_sites}

    if args.json:
        print(json.dumps(out, indent=1, default=str))
        return
    render(out, args)


def render(o, args):
    n = args.top
    m = o['mission_control']
    print(f"\n# Base fix audit — {o['faction']} — {o['date']}\n")
    print(f"## MC headline\n")
    print(f"Available {m['available']} · used {m['used']} · **slack {m['slack']:+}** "
          f"(reconstruction — the in-game top bar is truth); "
          f"pending from in-flight modules {m['pending_from_inflight_modules']:+}.")
    print(f"{m['mines_active']} active mines, {m['mines_building']} under construction → "
          f"**~+{m['latent_mining_mc_when_they_land']} MC of latent demand** when they land "
          f"(mining MC is quadratic). Next NEW mine costs {m['marginal_mc_next_new_mine']} MC; "
          f"a tier UPGRADE costs 0 (E38).\n")

    print("## 1. Unpowered modules — flip these\n")
    free = [f for f in o['power_on'] if not f['boost']]
    costly = [f for f in o['power_on'] if f['boost']]
    if free:
        print("**Boost-free** (upkeep in money/water/volatiles/metals only):\n")
        print("| Hab | Body | Module | +MC | Verdict | Upkeep/mo |")
        print("|---|---|---|---:|---|---|")
        for f in free[:n]:
            print(f"| {f['hab']} | {f['body'] or '—'} | {f['module']} | "
                  f"{f['mission_control'] or ''} | {f['verdict']} | {fmt_support(f['support_month'])} |")
        if len(free) > n:
            print(f"\n…and {len(free) - n} more boost-free flips (--top).")
    if costly:
        print(f"\n**Costs boost** ({len(costly)} modules) — screen against your boost cover "
              f"before flipping any (E37):\n")
        seen = {}
        for f in costly:
            seen.setdefault(f['module'], []).append(f)
        for mod, fs in sorted(seen.items(), key=lambda kv: -len(kv[1])):
            print(f"- **{len(fs)}× {mod}** — {fs[0]['boost']:g} boost/mo each "
                  f"({fs[0]['boost'] * len(fs):g}/mo total), upkeep {fmt_support(fs[0]['support_month'])}"
                  f"{', +%d MC each' % fs[0]['mission_control'] if fs[0]['mission_control'] else ''}")

    g = o['mc_module_gaps']
    print(f"\n## 2. Habs with no OpsCenter/CommandCenter\n")
    print(f"**{g['missing_built']} / {g['habs_total']}** have none built; "
          f"**{g['missing_after_current_builds']}** once current builds finish "
          f"(an OC→CC upgrade deletes the OC state at click time, E28 — those habs "
          f"read as 'missing' while the CC builds).\n")
    if g['buildable_now_t2']:
        print("**Buildable NOW (T2+, no MC module, nothing in flight)** — +4 MC each:\n")
        print("| Hab | Body | Est metals |")
        print("|---|---|---:|")
        for h in g['buildable_now_t2'][:n]:
            print(f"| {h['name']} | {h['body']} | ~{h.get('estimated_metal_cost', '?')} |")
    else:
        print("**Nothing to start** — every T2+ gap already has an OC/CC in flight.")
    t1 = g['need_core_first_t1']
    if t1:
        upgrading = [h for h in t1 if h.get('pending_core_status') == 'upgrading']
        founding = [h for h in t1 if h.get('pending_core_status') == 'founding']
        stable = [h for h in t1 if h not in upgrading and h not in founding]
        print(f"\n**T1 habs with no MC module ({len(t1)})** — OpsCenter needs a T2+ core:")
        for h in upgrading:
            print(f"- {h['name']} ({h['body']}) — core upgrade ALREADY in flight "
                  f"({h.get('pending_core_target')} → {str(h.get('pending_core_completion'))[:10]}), "
                  f"OpsCenter becomes buildable then. Nothing to do (E6).")
        for h in founding:
            print(f"- {h['name']} ({h['body']}) — still being FOUNDED, don't queue an upgrade yet.")
        for h in stable:
            print(f"- {h['name']} ({h['body']}) — stable T1: would need a core upgrade first "
                  f"(~{h.get('estimated_metal_cost', '?')} metals). Don't do it just for +4 MC (E6).")

    cc = o['oc_to_cc']
    print(f"\n## 3. OC→CC upgrades — {len(cc['candidates'])} candidates, "
          f"ranked by {'BUILD TIME' if args.sort == 'days' else 'METALS'}\n")
    if cc['unpowered_opscenters']:
        print(f"⚡ Power these OpsCenters first (+4 MC, free): "
              f"{', '.join(cc['unpowered_opscenters'])}\n")
    print("| # | Hab | Body | Est days | MC-days dark | Est metals | Power (+200 net) | Gate |")
    print("|---:|---|---|---:|---:|---:|---|---|")
    for i, c in enumerate(cc['candidates'][:n], 1):
        gate = ''
        if c['tier'] < 3:
            gate = f"T3 core ETA {c['t3core_eta']}" if c['t3core_eta'] else 'needs T3 core'
        elif c['incoming']:
            gate = f"speeds up: {c['incoming']}"
        print(f"| {i} | {c['hab']} | {c['loc']} | {c['days']} | {c['mc_days_dark']} "
              f"| {c['approx']}{c['metals']} | {c['power']} | {gate or '—'} |")
    print("\nEach click pays metals up front and takes the OpsCenter offline immediately (E24/E28).")

    mi = o['mines']
    print(f"\n## 4. Mines — upgrade before you expand\n")
    if mi['scarce_resources']:
        print(f"Scarce right now: **{', '.join(mi['scarce_resources'])}** — "
              f"upgrades feeding these are listed first.\n")
    print("### Tier upgrades — 0 MC\n")
    print("| Hab | Body | Gain/mo | Feeds scarce | Metals | Volatiles | Payback | Readiness |")
    print("|---|---|---|---|---:|---:|---:|---|")
    for u in mi['upgrades_0_mc'][:n]:
        gain = ', '.join(f"{k} +{v:g}" for k, v in sorted(
            u['gain_month'].items(), key=lambda kv: -kv[1]))
        pb = f"{u['payback_months']}mo" if u['payback_months'] else '—'
        print(f"| {u['hab']} | {u['body']} | {gain} | {', '.join(u['feeds_scarce']) or '—'} "
              f"| {'~' if not u['cost_known'] else ''}{u['metals_cost']} "
              f"| {u['volatiles_cost']:g} | {pb} | {u['ready']} |")
    if mi['bases_with_no_mine']:
        print(f"\n### Bases with no live mine — a NEW mine costs "
              f"{o['mission_control']['marginal_mc_next_new_mine']} MC on top of metals\n")
        print("| Hab | Body | T3 potential/mo | Metals | +MC | Note |")
        print("|---|---|---|---:|---:|---|")
        for s in mi['bases_with_no_mine'][:n]:
            pot = ', '.join(f"{k} {v:g}" for k, v in sorted(
                s['l3_potential'].items(), key=lambda kv: -kv[1]))
            note = []
            if s['destroyed_slots']:
                note.append(f"⚠ {s['destroyed_slots']} destroyed slot(s) — rebuild, verify in-game")
            if s['steps_to_L3'] > 1:
                note.append(f"{s['steps_to_L3']} tier steps; {s['ready']}")
            print(f"| {s['hab']} | {s['body']} | {pot} "
                  f"| {'~' if not s['cost_known'] else ''}{s['metals_cost_t2_t3']} "
                  f"| {s['mc_cost']} | {'; '.join(note) or s['ready']} |")
    print("\nNever cancel a mine in flight — materials are spent at click time and cancel "
          "refunds nothing (E24). The lever is declining to POWER it on completion.")


if __name__ == '__main__':
    main()
