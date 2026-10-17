# TI analyzer lessons — Economy: MC, CP-cap, mining, boost, water, habs & LEO

Part of the Seventh Councilor lessons library (see the repo `CLAUDE.md`). IDs permanent (`E1`…). Dates and worked numbers come from the reference
campaign (Resistance, 2026 start, Normal difficulty). Read before any MC / mining / resource /
hab-module recommendation. Canonical formulas: [REFERENCE](REFERENCE.md) § Mission Control vs
Earth CP-cap; the canonical mining-output formula lives in E20.

## E1 — Don't conflate MC (blue sphere) with Earth Control-Point cap (red shield)

Both are X/Y counters in adjacent top-bar positions. CP cap of e.g. 672/645 is not MC. Full
formulas + module table: [REFERENCE](REFERENCE.md).

## E2 — MC priority pips only matter in MAJORITY nations

MC priority shifts a nation's mix only when the faction holds majority. The extractor
partitions zero-MC-priority CPs into majority (actionable) vs minority (not a lever).

## E3 — Headline MC slack lies; check LATENT demand

The player suppresses demand to stay under cap (unpowered mines, idle shipyards, builds in
flight). True scarcity = visible slack − latent demand; read the extractor's "Latent MC demand"
subsection before declaring MC solved. (And per E25, don't trust the reconstructed slack at
all — the in-game top bar is truth.)

## E4 — The script's CP-cap input sum is PARTIAL

Orgs/councilors/tech effects aren't fully modeled (~30-40% short). The in-game CP tooltip is
ground truth.

## E5 — Account for module build cost BY LOCATION

OperationsCenter: base ~75 metals at Earth LEO, ~185 at Ganymede, ~375 at Io. On a
metal-constrained run, 4 LEO OpsCenters beat 1 on Io. The extractor ranks T2 habs by
approximate metal cost.

## E6 — T1→T2 upgrade advice: check the in-flight core FIRST

Three T1 flavors: upgrade already in progress (say nothing — wait), still being founded
(don't recommend upgrading yet), stable T1 (the only valid "needs upgrade" targets — and at
~300-600 metals + ~40d, almost never worth it just for +4 MC). Read in-flight
`TIHabModuleState` core entries before classifying.

**Classify cores from the TEMPLATES, not a hardcoded name list (fixed 2026-07-30).** The
extractor's core detector knew only T1 and T2 core names, so a hab upgrading straight to a
**ColonyCore/RingCore (tier 3)** fell through to "stable" and got re-recommended for the upgrade
it was already doing. On the 2033-11-01 save that mislabelled all three remaining T1 habs —
each had a ColonyCore in flight (2033-12-17 / 2033-12-27 / 2034-01-26). `load_core_module_tiers()`
now reads `dataName`/`tier` for every `*Core` module out of `TIHabModuleTemplate.json`, and any
in-flight core WITH a prior counts as 'upgrading' regardless of how many tiers it jumps. Whenever
a check enumerates template names by hand, ask what happens when the ladder gains a rung.

## E7 — "At cap" ≠ wasted; only OVER-cap modules are free to drop

At-cap means exactly enough modules; removing one loses 6% (or detection points). The
extractor partitions OVER / AT / UNDER and only surfaces over-cap modules as free decoms.
Detection values: ListeningPost +1 / ReconArray +2 / ArgusComplex +3 (same scale for
Xenology).

## E8 — Consider tier CONSOLIDATION when freeing LEO slots

Two T1s + researched T2 project → decom one T1, upgrade the other in place: −1 slot, same
bonus. Often the only slot-freeing move on at-cap stacks; the extractor detects these.

## E9 — LEO bonuses are buildout-rate multipliers, and OpsCenter is orthogonal

`LEOBonusMissionControl` accelerates how fast MC-priority CPs BUILD new Earth MC — it does NOT
multiply accumulated MC. And OperationsCenter is one-per-hab while bonus research centers are
multi-per-hab: they never compete for a slot. Treat "OpsCenter every LEO hab" and "stack bonus
modules on slot-rich habs" as independent programs.

## E10 — `EarthLEOOnly` builds vs LEO-only BONUSES

Climate / Listening Post / Recon Array / Argus / Sentinel can only be BUILT in LEO; most
research centers build anywhere but their LEO bonus applies only in LEO. All LEO bonuses cap
at 30% (detection +9 pts). Full module↔bonus table: [REFERENCE](REFERENCE.md) § LEO bonus
types.

## E11 — Use the "After pending" column for build advice

Recommend from projected (current + under-construction) headroom, or you'll re-suggest what
the user already started.

## E12 — Rank at-cap decommissions by CP priority weight

A LEO bonus amplifies the empire's CP weight on that axis: dropping 6% of Knowledge at weight
53 costs ~10× dropping 6% of Government at weight 5. Detection bonuses are NOT CP-amplified —
standalone value, don't treat weight-0 as cheap.

## E13 — Mine portfolio is an optimization, not "all on"

Mining-network MC cost grows superlinearly (quadratic — see E19); when MC-tight keep the
highest-VALUE mines on. The extractor scores mines by constrained-resource weights with
days-cover multipliers and emits lowest/top/unpowered/under-construction tables; use the swap
strategy (new high-value mine completes → power down the worst active). Subject to E18's
hard guard.

## E14 — MC overage is usually transient; smallest correction first

(a) The pending-modules pipeline (OpsCenters finishing) usually resolves overage in days —
check `Pending net change` first; if strongly positive, recommend at most a one-mine band-aid.
(b) **NEVER cancel under-construction mining bases to save MC** — every base is a future
OpsCenter host (net MC-positive later); cancellation trades a permanent asset for a one-time
pittance. Valid only if the body is about to be lost or the resource is at 365d+ cover.
(c) ONE mine off is usually the whole fix (marginal mine ≈ 4-10 MC via the quadratic);
recommend #1 from the lowest-value list, re-check next save. Multiple only if overage >~15 MC
AND pipeline flat/negative AND the user asked for speed.

## E15 — `TIHabSiteState.*_day` is the RAW GEOLOGICAL rate

Actual monthly income = `site_day × miningModifier(tier) × K[res]` — tier (Outpost 1.0 /
Automated 1.25 / Settlement 1.5 / Colony 2.0) and the faction-global mining bonus multiply it
2.5–3.7×. Raw rates stay valid for RANKING (bonus cancels); every ABSOLUTE number must use the
calibrated formula (E20). Calibrate K against one in-game module tooltip when in doubt —
tooltip is canonical.

## E16 — Boost deficits come from MODULE SUPPORT, not missing priority pips

**Production**: the Boost (`LaunchFacilities`) priority builds per-region infrastructure at
≈0.22–0.31 boost/YEAR per completion — decades to matter. Efficiency ranks CHN > USA > RUS
(IP × latitude). A nation's "Funding" is money, unrelated to boost.
**Consumption**: `supportMaterials_month` — Administration modules are the big sink (Node 1 /
Tower 4 / Complex 12 boost/mo), and their CP-cap benefit exists ONLY in Earth LEO
(`LEOControlPointCapacity`); elsewhere they pay full boost for a +2.5–10% hab-income
Efficiency multiplier that the community correctly calls a trap (the real mining lever is
tier upgrades). **Dead-tower triage**: a Tower on a hab whose mine isn't producing multiplies
~0 income — power those off first. **Hidden sink — shortage backfill**
(`GetBoostSubstitutedCost`): space builds lacking local materials silently pay Boost+Money
from Earth; shrinks as the space economy matures, so pace builds rather than fight it. Other
sinks: ship repair/resupply/ammo, scuttle crew-return, probes, STO launches, org `costBoost`.
Baseline boost is authored per-region (`baseBoostPerYear_dekatons`) + boost orgs; latitude
only penalizes the priority buildout. Efficiency-rule details: ×(1+value) per POWERED module
on the hab's GROSS income for money/inf/ops/exotics/research/mining resources — NOT
MC/Boost/Projects/Antimatter; stacks multiplicatively; not location-gated.
**Power OFF, don't DECOMMISSION** (decommission is irreversible-ish — see E24).
Use `boost_analysis.py`.

## E17 — "Where did my resource go?" → the Transactions ledger

Aggregate `TIFactionState.Transactions` by category over a window (`resource_flow.py`); a
stockpile crash is almost never income — it's a discrete spend. **For water the dominant sink
is FLEET RESUPPLY** (`ResupplyOperation` / `ResupplyAndRepairOperation`; the AndRepair variant
means combat damage — find the ships via `damagedSystems`/`damagedParts`). Case: water
13,300→41 in ~6 weeks — +116/day mining swamped by ~14,400 of resupply.

**WATER IS THE UNIVERSAL PROPELLANT FEEDSTOCK** — you pay `perTankPropellantMaterials.water`
(~0.65–1.0 per tank for nearly every drive; habs crack water into the propellant), NOT the
`propellant` TYPE; full refill ≈ `tanks × waterPerTank × 10`. Size water income to
operational TEMPO, not idle draw. Canonical per-drive table:
[Drives Refits and Logistics](../mechanics/Drives%20Refits%20and%20Logistics.md) §6.
(Tank MASS is a separate constant — 100 t/tank regardless of propellant; see the
LESSONS-ships core-formulas block.)

## E18 — NEVER power down a mine feeding a near-depleted resource

A 2032-11 shutdown ran water to zero. The extractor now weights water 5 / vol 4, applies a
<5d→4× cover tier, and hard-PROTECTS mines producing ≥0.5/d of a scarce resource (water <45d,
vol <30d, metals/fiss <15d, nobles <10d) — protected mines are excluded from drop lists. When
MC-deficit and the droppable list is empty, the answer is hab-side levers or a higher ceiling
(Command Center), not cutting a scarce-resource mine.

## E19 — MC decomposition: mining is the EXACT quadratic; habs is the residual

`missionControlUsage` is the game's exact used total (trust it). Mining cost = (active −
free)²/2 with free = Space-Mine-Freebies count (tooltip-verified: 18 = 6²/2 at 36 free) —
take it from the quadratic, never as a residual. Habs (which carry a dynamic per-hab term
beyond the static template values) = usage − ships − mining. The AVAILABLE side still
over-reads (see E25) — top bar is truth.

**Ships MC — the per-ship `missionControlConsumption` is a BASE value; two reductions apply**
(save-verified 2033-03-25: base 176 → game 142): (1) tech `Effect_TotalShipMissionControlCost`
= **×0.85 each** (stackable, faction-wide — read the stack count off `ShipMissionControlReduction`
in `TIEffectsState`); (2) a **Flag Bridge** utility module (`FlagBridge`, `specialModuleValue 0.8`,
rule `ReduceFleetMCConsumption`) = **×0.8 on its whole FLEET**. So `ship_mc = Σ_fleets ( fleet_base
× 0.85^techstacks × 0.8^has_FlagBridge )`; detect Flag Bridge via the design's
`moduleTemplateEntries[].moduleName == 'FlagBridge'`. Summing the raw field over-counts ~24%
(176 vs 142) and used to poison the ships/habs split. With this + the powered-filter fix (E25),
ships **and** habs both match the game (141/572 vs 142/571); nations was the last ~+7 error until
the per-CP split landed (E25, 2026-10-17).

## E20 — For ANY mine-output number, run `mine_completion_timeline.py`

Never quote the extractor's raw `site.*_day` as production (2.5–3.7× under-report; see E15).
The script derives K[res] from the save — `K = 30.44 × 1.15^(#Mining<Res>Bonus) × (1 + Σ
SpaceMiningBonus + Σ assigned-org miningBonus)` — verified <0.1% vs in-game tooltips, and
self-updates as tech/orgs change (the old hard-coded K silently drifted). It reports current
production AND the under-construction timeline. `cachedYearlyRevenue.<res>` is a stale cache —
never cite it. TODO: fold K-calibrated income into `extract_snapshot.py`'s mine tables.

## E21 — Module value = income − `supportMaterials_month`, netted against buffers

Never call a module "free". AdminTower is +1 MC but −30 money AND −4 boost/mo; a full
49-unpowered-module set would have been +38 MC / +1080 money but −143 boost −81 water −93
metals per month against single-digit-day buffers. **Unpowered modules are usually load-shed
ON PURPOSE** — not forgotten free wins. Before recommending powering anything, pull its
upkeep, net it, and check each cost line against days-cover; if it debits a <10-day resource,
leave it off. Standing decision in the reference campaign (2033-02): unpowered value modules
stay OFF until water/metals/boost recover. TODO: analyzer should emit net-of-upkeep.

## E22 — Farms are a first-order WATER lever [exact formula in E27]

Farms carry no template income; each ACTIVE (powered) Farm covers up to **600 crew** of a
hab's water life support, offsetting **600 × 0.0291667 = 17.5 water/month** (cap = hab crew,
so a small hab gets less). The old empirical "+19/Farm" (2033-02-28→03-01: 20 Farms swung
faction water −166 → +189/mo) was noise around the true 17.5. Never compute water from mining
complexes alone (that produced a multi-day phantom crisis). Exact per-hab/per-module formula
and the faction-level composition now live in **E27** + `lifesupport.py`; the in-game
water tooltip is still ground truth for the absolute net (it sums 8 terms, only one is habs).

## E23 — `cachedYearlyRevenue` is STALE; thin stock + positive income ≠ crisis

Read current rates from the in-game resource tooltip or compute from modules. Never call a
resource a crisis without confirming its income sign is negative. (The cache also runs ~2×
high for player research.)

## E24 — Construction pays ALL materials UP FRONT; there is no pause

Costs are deducted at click-time, not over the build. Under-construction modules are NOT an
ongoing drain; there is no pause; Decommission refunds nothing, costs boost (~1.5 crew evac)
and locks the slot 120 days. Never advise "pause/cancel builds to save resources" — the only
build-side lever is not STARTING new ones (a click at 0 stockpile imports from Earth on
boost).

## E25 — The reconstructed MC AVAILABLE/SLACK is unreliable; use the in-game tooltip

Only `missionControlUsage` and the mining quadratic are exact. The 2033-03-01 ground truth:
game slack +4, reconstructed slack +49 — which produced "you're ~5 over" advice when the
player was ~50 over. Root cause fixed (available summed UNPOWERED producers — the
powered-filter rule, P1.4); hab-produced is now exact. The last ≈ +7 (nations) was fixed
2026-10-17: a nation's regional MC is split **per control point** — each CP gets
`total // numControlPoints`, the remainder goes to the highest `positionInNation` CPs, and
crackdown (`benefitsDisabled`) CPs get nothing — so a majority holder does NOT get the whole
nation (save-verified 2006-07-16: per-CP split 22 = in-game 27 − HQ 2 − orgs 3; the
majority-nation sum gave 30). `extract_nation_mc_share()` is the one implementation; every
MC tool uses it. Even post-fix: to answer "am I over MC / by how much", read the in-game top
bar/tooltip or ask — don't quote the reconstruction's slack as fact.

## E26 (new 2026-07-04) — Over MC cap = your habs can be MUTINIED away (Control Space Asset)

Worked case (save-verified 2033-03): a T2 station in high lunar orbit flipped to the Academy
while the Academy owned ZERO ships. Mechanism: `ControlSpaceAsset` — a Persuasion-based
CONTESTED councilor mission ("persuade a ship or hab crew to join your faction"); no fleet,
no marines, no bombardment. Defense stack = flat 12 + CP-councilor Loyalty + Security
protection + hab size + population + defended-asset + ideological distance +
**`TIMissionModifier_DefenderMissionControlShortage`** — being over MC cap is an explicit
defense debuff. The flipped station was young (defense array and OpsCenter still under
construction → near-zero size/pop/defense modifiers) during a chronic overage window
(`history_MCCapOverageByDay`: +45–64 a month prior, +27 the days before the flip).
Operating rules: (a) treat any over-cap day as an active seizure window, not just an accident
risk (and 1.0.33+ also destroys modules while over); (b) young stations get their defense
module BEFORE economy modules when rivals' councilors roam; (c) counter-play — human-owned
habs can be taken BACK by marine assault (capture transfers between human factions) or a
reverse Control Space Asset; check first whether their success was a CRIT (grants temporary
mission resilience "until {date}").
Reach note: councilor travel is abstracted shuttle transport, so a FLEETLESS faction's
councilors reach anything in Earth–Luna space from Earth (and can stage from any hab they
own further out). Earth-orbit/lunar stations are permanently inside every rival councilor's
mission range; the defense stack and staying under MC cap are the only shields there.

## E27 (new 2026-07-05) — The EXACT water formula, and why a "water crash" is usually FLEET, not habs

Cracked from decompiled source (`TIHabState.GetNetCurrentMonthlyIncome`, `TIHabModuleTemplate.
MonthlySupportCost`/`MonthlyCrewSupportCost`, `TIFactionState` line ~3084, build 1.0.38) and
validated to the tooltip. Tool: **`lifesupport.py`** (per-hab, per-module).

**The water tooltip "daily income from our habs is X" IS the habs term.** Calibrated to the
2033-03 tooltips (229 +58.1/day; 230 reloaded +53.2/day). Two parts:
- **Production is EXACT.** Derived K=62.8 = `30.44 × 2.063` — and 2.063 = the tooltip's stated
  **"106% bonus to production"** exactly (`MiningWaterBonus` ×1.15² + `SpaceMiningBonus` +0.20 +
  org `miningBonus` +0.36). So there is **no missing water tech** and **no Administration
  adviser** — the reference campaign never advised a hab (`hab.advisingCouncilors` empty →
  multiplier 1.0). *An earlier draft blamed a ×1.156 "adviser"; WRONG — it was masking a
  production bug (core-upgrade habs zeroed out; see the core-upgrade trap below). Retracted
  per P1.*
- **Consumption (per-hab HABS-list water — EXACT, validated 2033-04-01):**
  `crew LS on EVERY okay module + material upkeep (powered only) − farm offset`. See formula box.

**Per-hab HAB water (the HABS-list water column — reproduced to 0.1):**
- **crew life support** = `template.crew × R` for **every okay module** (active, unpowered, AND
  building — at FULL template crew). `Σ template.crew` over okay modules = the hab **"population"**
  number the game shows. `R = 3.5 × 0.1 / 12 = 0.0291667` water/crew/mo.
- **material water upkeep** (`supportMaterials_month.water`) — **POWERED modules only**.
- **mine water production** — POWERED mines only (× K).
- **farm offset** = `min(600 × #poweredFarms, population) × R` — POWERED farms only.
- **hab net = production − (crewLS_all + material_powered − farmOffset)**.
- Validated on a 2,455-crew colony hab (2033-04-01): `crew 2455×R=71.6 + material 15 (5
  ResearchCampus×3; the building mining complex's 15 does NOT count) − farm 0 (3 farms
  unpowered) = −86.6`, EXACT — and 11 other habs matched to 0.1. Per-module tooltips confirm
  crew LS: Colony Core 125→3.6, Colony Mining Complex 200→5.8, Farm 5→0.15, CommandCenter
  1000→29.2, Farm 600-crew offset→17.5.
- **Earlier drafts were WRONG** and are retracted (P1): "state.crew (building→0/upgrade→prior)"
  and "crew on ACTIVE only". Ground truth: building/unpowered modules DO charge full crew LS.

**Faction "income from habs" tooltip = Σ(per-hab net), EXACT — no version gap, no adviser.**
2033-04-01: Σ per-hab = **+1793/mo = +58.91/day**, matching the tooltip's +58.9/day to the
decimal. The decompiled source is CURRENT and correct — crew LS on every okay module, per
`GetNetCurrentMonthlyIncome` → `GetDailyIncomeFromHabs` → `GeneralControlsController` tooltip.

**The core-upgrade trap (the bug behind every retracted theory above).** Habs whose CORE is
mid-UPGRADE (a completed `SettlementCore` being rebuilt into `ColonyCore`) have their only
core module at `constructionCompleted=false`; a `core_done = any(core && completed)` test
marks those habs inactive and zeroes their mines (~1,000+/mo of water dropped across several
habs, shrinking a ~+1800/mo hab sum to ~+31). Four successive theories (the ×1.156 "adviser",
crew-on-active-only, a "powered-crew faction number", a decompiled-vs-live version gap) were
epicycles around this one bug. **Fix:** read the stored `hab.anyCoreCompleted` bool (stays
True through a core upgrade) — with it, per-hab matches to 0.1 AND Σ = the tooltip exactly.
Rules: when a reconstruction is off by a lot, suspect the reconstruction's code and find the
missing rows before theorizing new game mechanics; sum the game's own displayed list as the
cross-check; prefer stored flags over ad-hoc re-derivation. Faction water is nominally 8
terms, but for water the habs term IS the whole displayed "income from habs".

**The "water crash" pattern (save-verified 2033-03).** Water income read +58.1/day →
−3.7/day → +53.8/day across three close saves; every structural theory was wrong. On
reloading the middle save the tooltip read +53.2/day, healthy — the −3.7 was a **TRANSIENT
DISPLAY GLITCH**, coinciding with water stock near zero (0.8) + a fleet
**ResupplyAndRepairOperation** (−22.5 water) in flight. The real hab-income change was −4.9/day
from powering off admin infrastructure for MC. **Rules: a scary instantaneous resource rate
at ~0 stock can be a display artifact — reload before diagnosing. When water income lurches
but hab inventory barely moved, look at fleets FIRST (in-flight resupply/repair — check
`resource_flow.py` categories `ResupplyOperation` / `ResupplyAndRepairOperation` and the
stock delta), not life support. Life support is slow and structural; it does not swing
60/day between two saves 5 hours apart.**

## E28 (new 2026-07-06) — Module upgrades take the PRIOR module OFFLINE; completion ΔMC = FULL new value

An in-place upgrade (e.g. OperationsCenter → CommandCenter) REPLACES the slot's
`TIHabModuleState` with a single new state (`constructionCompleted: false`,
`powered: false`, prior kept only as `priorModuleTemplateName`). The prior module
does NOT keep running during the build — its income/MC is already gone from the
top bar the moment the upgrade is clicked. So the gain on completion, measured
against the CURRENT top bar, is the FULL new-module value (+10 for a
CommandCenter), NOT new-minus-prior (+6). Do not report "+6 each, clean increments,
no dip" for OC→CC upgrades — the dip already happened at click time (save-verified
2033-04, 35 upgrades). Verified on a reference-campaign station (slot 3): exactly
one module state, the unpowered CommandCenter, no separate OpsCenter.
`module_completion_dates.py` encodes this. Same logic applies to any upgraded
module's income (research, money, mining tier): during the upgrade window the hab
runs WITHOUT the prior module's contribution.

**Amendment (2026-07-30, player call): therefore RANK upgrades by TIME, not by cost.**
The blackout runs for the whole build, so an OC→CC candidate's true price is
`days × 4` **MC-days** of lost Mission Control — and on most bodies the metals cost is
a flat 400, which discriminates nothing. `cc_upgrade_planner.py` now sorts by est-days
by default and prints an MC-days-dark column (`--sort metals` restores cheapest-first
for a metals-constrained save). Watch the interaction with construction modules: on the
2033-11-01 save the fastest candidates after two 96-day Callisto bases were six Mercury
bases (104-109 days, 3-11 construction modules each) at **933 metals** — time-first
ranking buys ~15% less blackout for 2.3× the metals, so read both columns before
clicking a batch. Same rule for any upgrade whose prior module was earning something.

## E29 (new 2026-07-06) — Mine shutdowns are CAPPED at the quadratic total; for a big overage, LEAD with that ceiling

When advising on an MC overage, compute `mining_cost = (active − 36)² / 2` FIRST and
state it as the hard ceiling on what mine shutdowns can recover. At 49 active mines
that ceiling was 84.5 MC against a ~100 overage — turning off ALL 13 chargeable mines
still left the player 28 MC over cap (2033-04-07, confirmed by in-game tooltip: 716/688,
"36 of 36 allowed mines with no cost"). The savings also collapse per mine (12.5 for
the 1st, 0.5 for the 13th), so the last few shutdowns destroy production for nothing.
RULE: when the overage exceeds ~70% of the mining quadratic total, say "mines cannot
close this gap alone" in the FIRST sentence — never bury the ceiling mid-answer under a
ranked shutdown list — and pair the mine list with the instant hab-side plan
(power ON unpowered OpsCenters/AdminTowers, power OFF ResearchCampuses at −1 MC / 60
research each) sized to the residual. The MC tooltip's "unpowered hab modules would
provide a net gain of N" line is the game's own statement of the hab-side headroom.

**The quadratic cuts BOTH ways — pace the re-powering.** Reference-campaign sequel, one
month later: with slack restored, the whole shut-down set was re-powered at once (plus
newly-completed mines) — at 58 active the mining network cost (58−36)²/2 = 242 MC and the
faction was ~226 MC over cap, with a destroyed module already on one station. At n mines
active each ADDITIONAL mine costs (n−36)+0.5 MC (~21 at n=58, vs ~12 at n=49), so re-power
incrementally, matching each mine (or two) to an Operations/Command Center completion —
`mine_shutdown_advisor.py --power-on` sequences this. Rule of thumb: past ~50 active mines
the marginal mine costs more MC than a CommandCenter produces (+10) — at that scale, MORE
mines need to be paired with mine-TIER upgrades on existing bases instead
(`mine_upgrade_planner.py`), which raise yield with zero extra network cost.

## E30 (2026-07-15; formerly duplicate-numbered E28) — Founding needs NO ship where you already have a hab+nanofactory in the same SUN-ORBITING system

An entire colony fleet was assigned to Jupiter's moons that needed no ships at all — bases
there could be founded directly thanks to nanofactories. Code-verified
(`TIFactionState.CanFoundHabFromHabAtLocation` / `MaxTierCanFoundAtLocation`):
found is legal iff SOME owned hab shares `location.GetSunOrbitingRelatedObject` (for a Jovian
moon that parent is **Jupiter**, i.e. any moon covers every other moon) AND that hab has an
ACTIVE module with a CanFoundTierN rule: **ConstructionModule→T1, Nanofactory→T2,
NanofacturingComplex→T3** (tier cap = best module in that system). `EligibleForFoundingBase` =
`Prospected(body) && !alienTerritory && CanExplore(body) && vacantHabSites>0` — **no fleet, no
outpost kit in the check.**
**Scoring rule:** colony/outpost-kit SHIPS are only worth spending on bodies whose sun-orbiting
system contains NO owned construction-module hab — in practice individual asteroids (each is
its own sun-orbiting object) and un-entered moon systems. Before assigning any colony ship,
compute the free-founding set first (2033-08: 24 systems free, incl. Jupiter at T2 → 26 vacant
Jovian sites needed zero ships; two kit-carrying colony ships were sitting IN Jupiter with useless
kits). Corollary: to raise a system's cap (Jupiter T2→T3) BUILD a NanofacturingComplex on any
hab in it — cheaper than shipping kits.

## E31 (new 2026-07-16; formerly duplicate-numbered E29) — CommandCenter requires a TIER-3 core; check hab tier before recommending OC→CC

Same pattern as the OpsCenter-needs-a-T2-core rule: a CommandCenter
only builds on a tier-3 hab (ColonyCore/RingCore). Verified 2033-08-05 save:
every built/queued CC sits on tier 3. A T2 station was mistakenly ranked
as the #2 upgrade target — not upgradeable yet. Before
recommending any OC→CC, read `hab.tier`; if <3, check for an in-flight
ColonyCore/RingCore (then it's "wait until <ETA>, then queue" — core upgrades
are cheap, ~27–85 metals / 45–60 d), else the core upgrade is a prerequisite
step. `cc_upgrade_planner.py` encodes this (T2 candidates sort last with 🔒
annotations).

## E32 (new 2026-07-16) — Resources are faction-POOLED: build location changes delivery, threat, and repair — not cost

There is one shared faction stockpile; a shipyard doesn't spend "local"
metals. So *where* you lay a hull is not a cost decision — a ship costs the
same at any yard. What build location actually changes:
- **Delivery point** — where the finished hull appears (and the ΔV/transit it
  then needs to reach the fight; check `transfer_eta.py` before committing).
- **Threat exposure** — a forward yard, especially one that `EnablesLocalFounding`
  (shipyard/construction hab), is a high-value target the alien AI weights
  heavily (mass^1.5, ×2 for construction habs — see
  [Alien Production Rebuilding and Targeting](../mechanics/Alien%20Production%20Rebuilding%20and%20Targeting.md)).
  Don't stand up an undefended forward yard next to an alien stronghold.
- **Repair/resupply access** — hulls repair and rearm at a friendly yard with a
  powered Shipyard; forward yards double as combat repair docks during a campaign.

Practical split: build **home-defense** hulls where they'll fight (they never
transit); build **expeditionary** hulls at the forward yards nearest the theater
(saves the long deploy) — accepting that those yards are contested and must be
defended or built behind a cleared front. The metals "crunch" that looks local
is really a single-pool flow-allocation problem (see
[Economy Markets and Loot](../mechanics/Economy%20Markets%20and%20Loot.md) — no market
buy-side exists), solved by queue priority and mine income, not by building
somewhere "cheaper."

## E33 (new 2026-07-18) — Pre-survey planning runs on mining-profile PRIORS; only RockyPlanetoidMine guarantees fissiles

Where do you send a prospector or survey-capable colony ship when every good body
is still unprospected? Not by reading `TIHabSiteState.*_day` — that's the hidden
truth and quoting it (or any ranking derived from it) is a cheat (P13). The
spoiler-safe basis is the **mining-profile prior**: each site's template
(`TIHabSiteTemplate.miningProfileName` → `TIMiningProfileTemplate`) carries
`<res>_mean / _width / _jump` fields, and those ARE the yield ranges the in-game
body panel shows you before a survey. Ranking unprospected bodies by priors
reveals nothing the game hides — it's the same information, aggregated. Tool:
`colony_planner.py --unprospected [--resource fissiles]`, transits via
`transfer_eta.py`.

What the fields mean, and the three rules that fall out:

- **Priors can be wrong BOTH ways — that's why surveying exists.** Reference
  campaign, a Kuiper plutino: one site displayed metals 80–120 pre-survey with a
  survey-true value of 158; its neighbor displayed 0–25 against a true 65. Treat
  prior sums as expectations for choosing a *destination*, never as income for
  planning a *budget*.
- **Only `RockyPlanetoidMine` has a guaranteed fissiles floor** (mean 2, min 2,
  jump 0.8 — the jump is a per-site jackpot chance) and it also carries the best
  outer-system metals (50±20) and nobles (15±5) priors. Fissiles prospecting
  therefore means rocky-planetoid clusters, and more vacant rocky sites = more
  jackpot rolls: a 4-rocky-site dwarf beats two 2-site bodies at equal distance.
- **Check coverage before dispatching.** `faction.intel[body] == 0.1` means a
  prospector or probe is already en route (P13) — and an in-transfer settler's
  destination is readable from `trajectory.destination` + `arrivalTime`
  (`resource_site_planner.py` prints both). The reference campaign once had a
  Kuiper target recommended to a settler whose sister ship was already 357 days
  into a transfer to that exact body.

Prospecting logistics that gate the whole plan: ship-based surveying requires the
`Prospector` specialModuleRule — in vanilla that's the **MobileSpaceScienceLab**
(200 t utility). A kit ship without it can only found ALREADY-prospected sites;
a Prospector+kit hull surveys on arrival and founds the same day, and keeps
surveying neighbors after its kit is spent. Probes to the Kuiper are
decades-slow (reference campaign: a plutino probe with a 23-year ETA), so for
deep targets the survey ship beats the probe by an order of magnitude.

## E34 (new 2026-07-19) — Power-audit before any power-on or upgrade recommendation; naive template sums get solar habs WRONG

Before recommending "power this module on" or "click this upgrade", check the hab's
power ledger — and compute it correctly:

- **Template `power` sums are wrong for solar habs, in BOTH directions.** Solar output
  scales with a location multiplier (Mercury surface ≈ ×3.3, Io ≈ ×0.018) and mirror
  stations can push a base's farms far above their rating (reference campaign: Mars
  farms rated 240 producing 543 each — a base that naive math called −200 deficit sat
  at +722 real surplus). The law and validated reconstruction:
  [Hab Power and Solar Output](../mechanics/Hab%20Power%20and%20Solar%20Output.md);
  tool: `hab_power_audit.py`.
- **Count idle generators as instant capacity.** A completed generator with
  `powered: false` switches on for free — the fix for a "short 150 power" Research
  University is often one click on a mothballed reactor, not a new build. The audit
  tool's verdicts distinguish POWER ON NOW / SWITCH IDLE GEN(S) ON / SHORT.
- **Upgrades need only NET headroom** — the prior module goes offline at click time,
  so OC→CC needs +200 (not +300) if the OC was powered. Budget the full figure only
  when the prior module was already dark.
- **The OC→CC power gate is now printed.** `cc_upgrade_planner.py` carries a
  `Power (+200 net)` column per candidate (`ok` / `idle gen` / `POWER SHORT n`) and sorts
  power-short candidates last — a CommandCenter you can't switch on is 400 metals of nothing.
  (2026-07-30: on the 2033-11-01 save, 4 of the cheapest 400-metal candidates were power-short
  and several more needed an idle reactor flipped.)
- **In-flight generators change the sequencing, not the verdict.** If a reactor lands
  before the upgrade completes, clicking both is safe — compare ETAs
  (`module_completion_dates.py`) instead of blocking the recommendation.
- The game silently auto-depowers modules when a hab goes into deficit (e.g. after a
  generator is destroyed) — a cluster of `powered: false` consumers on one hab is a
  deficit symptom worth diagnosing, not a player choice to preserve.

## E35 (2026-07-21, game 2033-09-27) — VOLATILES have a life-support drain just like water; Farms offset BOTH. Never quote gross mine output as "income"

Ground-truth catch (player's in-game Volatiles tooltip vs the analyst): tooltip **net volatiles
income 1,542/mo (50.7/day)**; the analyst had quoted **8,108/mo** — that was `mine_completion_
timeline.py` GROSS mine production. The ~6,566/mo gap is crew life-support + module upkeep. Two
cross-checks tie it down: the mine tool's derived global mining multiplier ×1.89 == the tooltip's
"89% bonus to production"; and gross − net == the consumption the tooltip warns about ("Volatiles
will be sent from Earth when production is insufficient at a Boost penalty").

Mechanic (verified — Farm localization + official wiki + Steam dev posts):
- **Crew life support consumes Water AND Volatiles at the SAME rate, ~0.35 t/crew/year each**
  (= the `3.5 × spaceResourceToTons 0.1 = 0.35 t/yr` water rate; `lifesupport.py --resource
  volatiles` now applies the SAME rate to volatiles). So the per-crew VOLATILES rate == the
  water rate: **R ≈ 0.02917 vol/crew/mo**.
- **Farm modules ("recycles matter into fresh food and breathable air") offset BOTH.** Per powered
  Farm: offset = min(600, spare population) × R ≈ **17.5 vol/mo** (same magnitude the mine tool
  prints for water, ~19/farm net). Farms defray the **crew** water+volatile upkeep only — NOT
  innate module upkeep (reactor farms still burn their 3–6 vol/mo).
- Consequence: **Farms are a volatiles-income lever, not "zero volatiles."** On the 2033-09-27
  save, 144 powered Farms were already offsetting ~+2,750 vol/mo; without them net would be
  ~−1,200/mo (importing from Earth at a boost penalty). 136 more Farms under construction add
  roughly another +2,600/mo of net volatiles as they finish — comparable to the 26 new mines.

Rules: (1) "Volatiles income" = NET (mines − crew LS − module upkeep) — the in-game Volatiles
tooltip is ground truth; `mine_completion_timeline.py` gives GROSS mine production only, never
quote it as income. (2) When counting a Farm's value, count its volatiles offset too, not just
water. (3) There is currently NO volatiles-life-support calculator (the water one has no volatiles
analog) — build one or trust the tooltip; do not reconstruct net volatiles from mine gross alone.

## E36 (2026-07-21) — INCOME (rate) vs STOCKPILE (level): construction spend hits the stockpile ONLY, never the income rate

Analyst error the player caught: said finishing construction makes "the up-front volatiles spend
that's sinking you disappear" while projecting INCOME. Wrong conflation. Two distinct axes:
- **Income (rate, the tooltip figure):** production − life-support − module upkeep. Changes ONLY
  when modules power on/off. **Construction cost NEVER appears here.**
- **Stockpile (level):** Δ = income − up-front construction draws (E24 lump-sums at build-click).
`resource_flow.py`'s "NET by resource" is the STOCKPILE change (income minus construction draws),
NOT income — e.g. volatiles NET −1,570/mo while income was +1,542/mo, the gap being ~−3,100/mo of
construction lump-sums. Rules: (1) never quote a net-stockpile-change as "income"; the in-game
resource tooltip's income line is the rate. (2) Stockpile relief from "finishing construction"
comes from not STARTING new builds (in-flight already paid) — it is independent of, and not caused
by, modules completing; module completion is what moves the INCOME line. Keep the two causes
separate. Extends E24 (construction pays up front) and E35 (income = NET production − LS − upkeep).

## E37 (2026-07-30, game 2033-11-01) — POWERABLE ≠ WORTH POWERING: screen every power-on candidate by `supportMaterials_month`, boost first

E34 answers "can this hab run the module". It does not answer "should you switch it on". The
second question is `supportMaterials_month`, and on a boost-tight save it flips the answer:

- Player's rejection of a correct-but-unwanted recommendation (2033-11-01, boost 55.4 stock vs
  123.9/mo income = 14d cover): *"Orbital Hospitals and Space Hotels are using my short boost.
  not flipping."* The audit had 7 unpowered OrbitalHospitals (1 boost/mo each) + 3 SpaceHotels
  (3 boost/mo each) = **16 boost/mo for +990 money/mo** — money was already 34.5K/mo, i.e. the
  return was in the resource that wasn't scarce, paid in the one that was.
- The spread across modules is large and NOT correlated with the payoff:
  **AdministrationTower** +1 MC for 30 money + **4 boost**/mo (worst on the board — 20 of them is
  80 boost/mo, ~⅔ of a whole boost income, for 20 MC); **OrbitalHospital** 1 boost + 5 water +
  3 vol; **SpaceHotel** 3 boost + 3 water + 2 vol; **CommandCenter** +10 MC for 100 money +
  10 vol + 10 metals + 5 nobles and **zero boost**; **Farm** — `supportMaterials_month` absent
  entirely, **no upkeep at all**.
- So the ranking of power-on candidates by MC or money alone is wrong. Rank by
  (payoff in a SCARCE resource) ÷ (upkeep in a SCARCE resource); a Farm and a CommandCenter can
  be flipped freely on a boost-starved save, an Administration Tower essentially never.

Rules: (1) `hab_power_audit.py` now prints each unpowered module's upkeep next to its verdict
(`[upkeep/mo: …]` / `[no upkeep]`) — quote it in the recommendation, never present a bare
"POWER ON NOW" list. (2) Check the upkeep resource against the player's days-cover, not against
its absolute size. (3) A boost-costing module whose only return is money/influence is a decline
on any save where money isn't the binding constraint.

## E38 (2026-07-30, game 2033-11-01) — UPGRADE before you EXPAND: a mine tier upgrade costs 0 MC, the next new mine costs the quadratic margin

Mining MC is `(active − 36)² / 2` (E-series quadratic, decompiled), so the marginal cost of one
more mine grows without bound while a **tier upgrade replaces the module in place and adds no
mine to the count — 0 extra MC**. Worked from the 2033-11-01 save (61 active, 27 building):

- mine #62 costs (62−36)²/2 − (61−36)²/2 = **+25.5 MC**; after the 27 in flight land (88 active),
  mine #89 costs **+52.5 MC**. The 27 mines already under construction add **~+1,040 MC** of
  demand against +146 MC of pending capacity — an MC hole of roughly −700.
- Meanwhile `mine_upgrade_planner.py` offered +264 water/mo for 480 metals (Bułak-Bałachovič,
  Callisto) and +181 metals +31 fissiles/mo for 412 metals (Cassius Dio) at **zero MC**.

Rules: (1) On any save with mines in flight, answer "should I build a new mine?" with the
quadratic margin first — it is usually the most expensive MC purchase available, and almost never
beats an upgrade. (2) Run `mine_upgrade_planner.py` before `colony_planner.py` /
`resource_site_planner.py` when the ask is "more of resource X". (3) Corollary to E24: never
"cancel" a low-value mine in flight — the materials are already spent and cancel refunds nothing;
the lever is declining to POWER it on completion (costs no MC, no upkeep, keeps the option).
`extract_snapshot.py`'s under-construction mine table said "consider canceling" until 2026-07-30;
it now says "leave UNPOWERED on completion". (4) **A "low value" test that ignores water and
volatiles is broken.** The same table judged under-construction mines on metals/nobles/fissiles
only, so on the 2033-11-01 save it told a volatiles-STARVED player to write off eleven mines —
including Leu Sapieha (Thebe, 4.22 vol/d) and Symon Budny (Amalthea, 4.08 vol/d), both 0.00
metals/d and both among the best volatiles sites they owned. It now reuses the same
`mine_protection()` scarcity rule the ACTIVE list uses, so a scarce-resource supplier is labelled
"power it on completion" and can never be called low value; the table also prints a Water+Vol/d
column. Whenever a tool ranks mines, check which resources its score actually reads.
