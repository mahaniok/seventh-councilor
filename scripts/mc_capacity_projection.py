#!/usr/bin/env python3
"""Project MC CAPACITY growth from under-construction modules; find when it
crosses a target.

Usage:
    python3 mc_capacity_projection.py [save.json|gz] [--faction X]
        [--target 1000]

Computes current MC available via extract_snapshot's calibrated four-source
reconstruction (HQ + councilors + nations as the per-CP split + POWERED positive-MC
hab modules, lesson E25), then walks every
pending positive-MC module by completionDate and prints:
  - monthly capacity-gain rollup with running capacity
  - the date capacity crosses --target (with the ±7 caveat)
  - final capacity when all in-flight producers complete

NOTE this is CAPACITY only. Usage grows too (mines/ships/consumer modules in
flight) — for slack projections check extract_snapshot's "Pending net change".
"""
import argparse

from extract_snapshot import (
    load_save, get_factions, faction_id_by_template, extract_hab_inventory,
    extract_mc_latent_demand, extract_mc_full, extract_control_points,
    kv_items, get_game_date,
)
from module_completion_dates import load_mc_table


def pending_producer_events(gs, faction_id, mc_table):
    """Date-sorted (date, gain, module, hab) for under-construction
    positive-MC modules. The prior module of an upgrade is already offline
    (lesson E28), so each event adds the FULL new-module MC."""
    habs = {k: v for k, v in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabState')}
    sec2hab = {}
    for hv in habs.values():
        for s in hv.get('sectors', []):
            sec2hab[s['value']] = hv
    events = []
    for _, mv in kv_items(gs, 'PavonisInteractive.TerraInvicta.TIHabModuleState'):
        if mv.get('constructionCompleted') or mv.get('destroyed'):
            continue
        hab = sec2hab.get((mv.get('sector') or {}).get('value'))
        if not hab or (hab.get('faction') or {}).get('value') != faction_id:
            continue
        gain = mc_table.get(mv.get('templateName'), 0)
        if gain > 0:
            events.append((str(mv.get('completionDate'))[:10], gain,
                           mv.get('templateName'), str(hab.get('displayName'))))
    events.sort()
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('save', nargs='?', default=None,
                    help='save file; omitted = newest save auto-detected')
    ap.add_argument('--faction', default=None)
    ap.add_argument('--target', type=float, default=1000)
    args = ap.parse_args()
    if not args.save:
        from ti_config import newest_save
        args.save = newest_save()
        if not args.save:
            raise SystemExit("No save given and none auto-found — pass a save path.")
        print(f"(using newest save: {args.save})")
    from ti_config import require_faction
    args.faction = require_faction(args.faction)

    gs = load_save(args.save)['gamestates']
    factions = get_factions(gs)
    fid = faction_id_by_template(factions, args.faction)
    hab_inv = extract_hab_inventory(gs, fid)
    latent = extract_mc_latent_demand(gs, fid)
    mc = extract_mc_full(gs, fid, hab_inv, factions,
                         mining_cost=latent['mine_network_cost'])
    from extract_snapshot import extract_nation_mc_share
    mc['mc_nations'] = extract_nation_mc_share(gs, fid)   # per-CP split = the tooltip's nations line
    available = (mc['mc_hq'] + mc['mc_councilors'] + mc['mc_nations']
                 + mc['mc_hab_produced'])
    used = mc['mc_used_reported']

    game_date = get_game_date(gs)
    if isinstance(game_date, tuple):
        game_date = game_date[0]
    print(f"Game date {game_date} — capacity {available:.0f} "
          f"(reconstruction; top bar is truth), used {used:.0f}, "
          f"slack {available - used:+.0f}")

    mc_table = load_mc_table()
    events = pending_producer_events(gs, fid, mc_table)
    if not events:
        print("No positive-MC modules under construction.")
        return

    cum = available
    crossed = None
    by_month = {}
    for d, g, name, habn in events:
        cum += g
        by_month.setdefault(d[:7], [0, 0])
        by_month[d[:7]][0] += g
        if crossed is None and cum >= args.target:
            crossed = (d, name, habn, cum)

    print(f"\n{len(events)} pending producer modules, "
          f"+{sum(e[1] for e in events):.0f} total → final capacity "
          f"{available + sum(e[1] for e in events):.0f}")
    print("\n| Month | ΔMC | Capacity at month end |")
    print("|---|---:|---:|")
    running = available
    for mo in sorted(by_month):
        running += by_month[mo][0]
        print(f"| {mo} | +{by_month[mo][0]:.0f} | {running:.0f} |")

    if crossed:
        d, name, habn, c = crossed
        print(f"\nCapacity crosses {args.target:.0f} on {d} "
              f"({name} at {habn} → {c:.0f}). "
              f"±7 MC reconstruction error can shift this to the next completion.")
    else:
        print(f"\nCapacity never reaches {args.target:.0f} with current builds "
              f"(final {cum:.0f}).")


if __name__ == '__main__':
    main()
