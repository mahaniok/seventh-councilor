# TI analyzer lessons — Research scoring & sequencing

Part of the Seventh Councilor lessons library (see the repo `CLAUDE.md`). IDs permanent
(`R1`…). Dates and worked numbers come from the
reference campaign (Resistance, 2026 start, Normal difficulty, research rate 200%, alien
progression 200%). **Read this whole file before scoring/ranking any techs or projects.**

## The scoring protocol — use persisted analysis, NEVER score from name alone

Scoring from names produces hallucinated mechanics ("Light IR Laser Cannon" is not an upgrade
— IR is the WEAKEST wavelength: IR < Green < UV).

**Step 1 — check existing analysis first.** Per-tech/per-project reference pages can be
generated into `generated/` from your own game data (`scripts/generate_vault.py`) and contain
raw template data. If you maintain per-project analysis notes and a note's frontmatter has
`analyzed:` + `analysis_score:`, reuse the vetted score (re-validating the strategic context
against the current save).

**Step 2 — un-analyzed projects get a deep analysis, persisted.**
1. Verify the actual mechanic from templates (`TIProjectTemplate.json`,
   `TIEffectTemplate.json`, and the module template the project unlocks).
2. Web-verify mechanics not explicit in templates (specialModuleRules flags, wavelength/damage
   questions) — read the official wiki through its MediaWiki `api.php`, or Wayback if the
   origin is unreachable (`docs/reaching-walled-sources.md`); Steam is a
   tiebreaker, not canon.
3. Compare against same-tier alternatives (side-by-side stats table).
4. Write the `## Analysis (real … · game …, score N/10 — TIER)` section into the project note
   with the frontmatter fields from [LESSONS-process](LESSONS-process.md) P7.
5. Cite sources (template path + any web link).

**Filenames** are friendlyNames, not dataNames — translate via `TIProjectTemplate.json` before
searching. **When asked to "score the open projects"**: pull the available list from the
extractor, split analyzed/unanalyzed, deep-analyze the gaps, present the ranked list noting
which scores were reused.

## R1 — Global techs cannot be swapped mid-research

You only pick at slot completion. Use the extractor's "Next global techs you can queue" for
slot-opening advice.

## R2 — Check what a project UNLOCKS before dropping/skipping it

`Project_GasCoreFissionReactorIV` ("Terawatt Gas Core I") gates Lodestar. Corollary
(2026-07-04): check what a reactor ladder TERMINATES in before slotting its rungs — Mirror
Cell / Electrostatic fusion ladders end in Reflex/Fusor drives (1–8 MN combat, 10× below
Lodestar): dead ends. **A fusion reactor project is only worth a slot if its ladder ends in a
drive worth mounting** — the ICF ladder (→ Helion Nova → Borane Nova → Protium Torch) is the
one that matters. Use `global_tech_tree_walk.py` (`--assume`, `--projects`) to walk 2-3 levels.
**Corollary 2026-07-08: "which arrives SOONEST" must be answered UNFILTERED** — value
baselines (beats-Lodestar / beats-Helicon) can hide a whole drive tier from an arrival-order
answer. `drive_upgrade_finder.py` § 3 is the unfiltered list; apply worth-it filters only
AFTER establishing order. A dead-end rung can still be the right cheap buy when its prereqs
are already committed spine spend (Triton Nova = +7k marginal at ICF I).

## R3 — Integrate alien-hate awareness into research recs

Check `alien_hate.next_masking_target`; the extractor tags hate-path globals 🛸. State the
floor reduction as ROI, don't double-recommend, and don't overweight the hate path when hate
is action-dominated (a −22 floor is noise under 400 above-floor) — see
[LESSONS-aliens](LESSONS-aliens.md) A1.

## R4 — Always check the Command Center chain when ranking globals

Project_CommandCenter upgrades every OpsCenter +4→+10 MC; with dozens built it's the biggest
one-shot MC unlock. The analyzer tags chain globals 🏛. Count built + under-construction
OpsCenters when valuing it. (Chain completed in the reference campaign 2032-12; keep the
pattern for future runs/chains.)

## R5 — In-game RP cost = template cost × 100 / `research_rate_pct`

The campaign's research-rate setting (from `config.json`) scales every RP cost. In the
reference campaign (Research Rate 200% → costs = template ÷ 2) this was verified exactly ×0.5
across many 2033 screenshots (DTF 50k→25k, Final Assault 25k→12.5k, GPA 40k→20k, Maglev
15k→7.5k…). Compute completion times from IN-GAME cost. (An early playthrough-note claim that
scaling is "not uniform ×0.5" predates these verifications and matched no observation since —
if a non-matching cost ever appears in-game, screenshot it and re-open.) Each campaign's rate
differs; verify against `config.json` per player.

## R6 — Cheap ≠ high impact: do the MARGINAL analysis

For any cap-raise or stackable +X: identify the affected entities, count how many actually
clip the cap / use the multiplier, compute per-RP impact, compare against projects touching
100% of the fleet/empire. Worked example: g-cap raises help only cap-clipped hulls, and each
successive +0.5g helps fewer ships (see [LESSONS-ships](LESSONS-ships.md) S11/S16 for the
current cap model and the hitscan caveat that demoted AccelPharm in 2033-02).

## R7 — Scan ALL THREE buckets and pull module stats

Available projects + paused-with-progress (`currentProjectProgress` off-slot — often the best
EV pick via sunk cost; Pan-Asian sat at 24%) + the auto-trigger queue. For every
weapon/armor/heat-sink/radiator/drive unlock, pull the actual module template and compute
per-ton benefit. Don't reuse stale prior scores without re-validating context. **Correction
(template-verified 2026-07)**: an old reading claimed Automated Outpost Core enables "founding
without councilor missions" — WRONG; founding never used councilors. It enables **uncrewed T1
cores** (crew 0, money support 0, MC −1 vs −2): cheap unmanned claim-staking.

## R8 — Score against the player's ACTUAL doctrine — and measure it from the save

Even a verified mechanic is worth 0 if the player never invokes it (no recruits → recruitment
XP is D; no coups → CoupBonus is D; no navy pips → Navy bonuses are D). But MEASURE, don't
assume from doctrine headlines (a "no military pips" assumption proved wrong — CHN/EUR ran
31-46%). Required queries: `availableCouncilors`+history, `controlPointPriorities` aggregated
per nation (mandatory before scoring any +priority project), full `shipDesigns` weapon mix,
`requiresNation` vs held CPs, recent `history_*` events, effect `contexts`. Sub-priorities are
distinct from parents (BuildArmy ≠ Military).

**Localization is mandatory in communication** — per a standing directive this covers GLOBAL
TECHS and abbreviations too: always use `TITechTemplate.en`
displayNames in prose and notes ("Inertial Plasma Confinement Techniques"/IPCT → **Inertial
Confinement Fusion (ICF)**; "Orbital Fighters" → **Exoatmospheric Fighters**). Template
friendlyNames ≠ in-game names for ~150 techs/projects. `generate_vault.py`
and `generate_modules.py` key page filenames, H1 titles, and all links on the localized
displayName (fallback to friendlyName when no loc entry, or when in-game names collide — the
seven faction "Talent Development" projects and the two "Hydra Direct Support Network"
channels keep their unique friendlyNames). Renamed pages carry the old friendlyName as an
`aliases:` frontmatter entry, so legacy links still resolve. dataNames are unchanged
everywhere (CLI examples like `--assume InertialPlasmaConfinementTechniques` stay as-is).
Template effect names are misleading legacy codes — always translate via
`localization/TIEffectTemplate.en` `Context.displayName.*`. The trap table:
`UpgradeArmyPriority` = **Build Navy**; `BuildSTOSquadronPriority` = **Build Exofighter**;
`LaunchFacilitiesPriority` = **Boost**; `SpaceDevPriority` = **Funding**;
`ControlPointMaintenance` = **Control Point Cap** (NOT money/influence upkeep — the
Transnational Management trap: its −120 effect is +120 CP cap). (Grep
`TIEffectTemplate.en` fresh each time rather than trusting a copied mapping table.)

## R9 — Pips can be tech-gated INERT

22 Build-Exofighter pips produced nothing until Orbital Fighters research
(`Effect_AllowBuildSTOFighters`). When scoring +priority projects, verify the enabling tech
per sub-priority; count only effective pips; surface parked pips to the user ("waiting on
tech X"). See also [LESSONS-politics](LESSONS-politics.md) C1 (post-founding inert pips).

## R10 — Fleet-wide effects score against ALL designs

Iterate every `shipDesigns[]` entry, build per-weapon-type counts, weight by deployed
instances. "The fleet has no lasers" was false (25+ laser ships) and mis-scored Precision
Focusing Software D instead of A.

## R11 — "Prereqs met" ≠ available: never gate a plan on an un-rolled project

Pending rolls carry `factionAvailableChance` (can be well under 100%: RMA 40% lifetime;
**Poseidon Torch 20% lifetime with a 10%/mo cap — an 80%-never lottery ticket** that
NONETHELESS ROLLED 2033-06-11 in the reference campaign: don't PLAN on these, but re-check the
menu each save because low-odds rolls do land) and slow
ramps (Hybrid Armor: 0 +5%/mo cap 35% → expect 5-8 months). Treat them as probabilistic
conditionals. Milestone gates are stricter still — [LESSONS-aliens](LESSONS-aliens.md) A3.

## R12 — Resolve dataNames before treating friendly names as separate things

"Defend the Earth" IS `Project_ResistVictory`. Friendly names get reused/translated; identity
lives in the dataName.

## R13 — The paused-projects table is NOT slot occupancy

Slots are 3 global + 3 project (read the weight table). Slotting something displaces
something — name the displacement (the zero-cost swap is whichever active project has 0 RP;
paused projects keep progress).

## R14 — Check weapon-bay stats vs what's already deployed

Anaconda/Rattler (Hypergolic) are strict DOWNGRADES from installed Hydrolox
(Copperhead/Viper): half damage, −38% EV. The real missile ladder runs through
AdvancedMissileWarfareDoctrine → Penetrators / Nerva / nuclear torpedoes. Never recommend a
bay whose numbers lose to something already on a design.

## R15 — The g-cap raiser chain (scoring view)

Four LifeScience projects, +0.5g each from base 3.0 (mechanics + current cap:
[LESSONS-ships](LESSONS-ships.md) S11). Score them by the marginal analysis (R6) AND the
enemy-weapon-mix check (S16) — cheapness alone is not a rank.

## R16 — Same-category concurrency IS penalized (×0.9 per extra slot)

Code-verified: `effective_category_bonus = SumCategoryModifiers(cat) ×
0.9^(active_slots_in_cat − 1)` (`TIFactionState.DistributedCategoryModifierValue`,
`categoryBonusPenaltyPerExtraSlot = 0.9`), counting globals AND projects together, applied to
ALL of that category's slots (`GetEffectiveResearch = points × (1 + distributed)`). The card's
+N% badge is the post-penalty value; the category tooltip prints "Bonus Distribution" when
penalized (2033-03-04: two Materials projects both +94% vs ~104% solo). Magnitude: only the
bonus portion — ≈5-8% throughput per stacked slot at the reference campaign's bonus levels.
**A one-category-at-a-time slotting rule has a real mechanical basis; slot advice must be
category-aware.** Related: `SumCategoryModifiers = Habs + Orgs + Traits + Investigations +
Fleets`; **Xenology += alienInvestigations/100**; SpaceScience gets fleet survey bonuses;
projects add `MultipleFacilitiesMultiplier` — CODE-VERIFIED 2026-07-07
(`TIFactionState.GetEffectiveResearch`, `TIGlobalConfig`): PROJECT slots (not globals) add
+5%/facility for the first 20, +3% for 21–40, +1% beyond 40, where facilities =
Σcouncilor-trait `incomeProjects` + (Σorg `projectCapacityGranted` − 1) + (Σhab-module
`incomeProjects` − 1). This is the ⚙ badge on project cards (in-game-verified 2033-05: +185% =
65 facilities). SkunkWorks=1, Foundry=2 per module — an upgrade is +1 facility. Also:
`researchBonusPerSlotInUse = 0.05` (the distribution bonus, R19). Science modules carry the
category techBonuses (T2 center +10%, T3 institute +25%, antimatter harvesters +10/25/50%
Energy) — census the categories against the critical path before building more (2033-02:
Energy was dead last at +60% raw while carrying the whole fusion spine; the fix was BUILDING
EnergyResearchCenters, not research).

## R17 — Verify before scoring: four worked corrections

Four scoring errors of one shape — reusing prior-ranking prose without template/code checks
(template-verified 2026-07; each also lives with its theme):
1. **Automated Outpost Core** ≠ councilor anything → uncrewed T1 cores (R7 correction).
2. **Marine Battalion Barracks** = hab DEFENSE; assault marines are ship modules
   ([LESSONS-ships](LESSONS-ships.md) S17).
3. **Defensive projects score against OBSERVED enemy activity** — zero Enthrall ops anywhere
   → Pherocyte Scanners 3, not 6 ([LESSONS-politics](LESSONS-politics.md) C11).
4. **Beams are hitscan** — g-cap raises don't dodge a laser-dominant enemy; SCV mobility
   clamps at 3g ([LESSONS-ships](LESSONS-ships.md) S16).
The general rule is R8's: verify the mechanism AND its invocation — by the player for
offensive value, by the enemy for defensive value.

## R18 — Assorted verified traps (from the old protocol's "past mistakes")

- **IR lasers**: weakest wavelength (IR < Green < UV) — not an upgrade over Green Arc.
- **"Increases the cap on X"**: first verify the cap is the BINDING constraint on the
  player's actual builds (Armor Struts re-scored 1/10 — nose caps weren't binding).
- **Refit verdicts must quantify**: `✓ saves N t/ship` / `⚠ HARMFUL (+N t dead mass)`. A
  within-family reactor "upgrade" can be actively harmful when the drive doesn't need the
  wattage (GC-III→IV on Pharos hulls = +561 t dead mass; the correct move was the combined
  Pharos→Lodestar + GC-III→IV refit).
- **`stackable` flag**: before calling two effects duplicates, check
  `TIEffectTemplate.json.stackable` — `Effect_MiningFissilesBonus` stacks ×1.15² (both
  projects compound); masking stacks 0.8^N.
- **Nation-formation projects**: check the granted regions/nations against the player's bloc
  (`requiresNation` + held CPs) before scoring.
- **"Drives are bad if you have Lodestar"** — role-fit first; high-EV drives win transit
  roles (and see S1 for the combat test).
- **Gun DPS must use the full SALVO cycle, not `cooldown_s` alone** (template-verified
  2026-07): coilguns carry `salvo_shots` + `intraSalvoCooldown_s` — Mk1 = 3-shot,
  Mk3 = **5-shot** salvos. Comparing single-shot cooldowns called Coil Mk3 a "parity
  sidegrade" of Rail Mk3 when it's actually a ~1.4–1.9× sustained-DPS upgrade in every
  mount (and Coil Mk1 a 4–5× downgrade when it's ~2–2.5×). Sustained = shots × KE /
  ((shots−1)·intra + cooldown); per-shot KE = 0.5·warheadMass·mv² matches the tooltip MJ
  exactly. Check `salvo_shots` on EVERY gun template before any DPS claim.

## R19 (2026-07-05, CORRECTED 2026-07-06) — Full research income IS reconstructable from the save

**The whole 🧪 tooltip reconstructs to ~0.1% from the save alone** — including the nations term
and per-nation research. `research_income.py` does it; validated vs saves 159 (500.1 vs tooltip
500.6/day) and 248 (561.6 vs 562.1/day).

**Column trap — do NOT "correct" this lesson back.** An earlier draft claimed the nations term
was "not reconstructable — per-region, unstored"; that reading was disproven. Its root cause:
the Nations screen's **Funding** column (`nation.historySpaceFunding[0]`, e.g. USA
762.9, CHN 809.8) was transcribed as per-nation "research". The actual research column reads
USA ~2.0K, CHN ~2.4K — which the `research_month` getter reproduces exactly. Before concluding
a reconstruction is broken, confirm WHICH column/field the ground-truth number is — re-read
the source screenshot/tooltip, don't trust a transcription. (`historyResearch[0]` is the
recorded research_month, fine to use.)

**The verified chain (all from the current build's decompiled source + wiki §Research; `research_income.py`):**
- `research_month(nation)` = `(pop_Mn × gdpTerm × edu·min(edu,12) × max(dem,1)^(1/6) × 0.0075
  + min(pop/5000, numCP+edu+dem/2)) × (1.25−|coh−5|/10) × (1−unrest²/100) × (1+adviserSciBonus)`,
  `gdpTerm(pc)= (pc/15000)^0.6 if pc≤30000 else 1.5157166+0.90943·(ln(pc/15000)−ln2)`. Inputs from
  save: `pop_Mn`=Σ region populations, `perCapGDP`=GDP/(pop_Mn·1e6), edu/dem/coh/unrest raw,
  `adviserSciBonus`=Σᵢ effSci_i/100/(i+1) over advisers desc by effective Science.
- **player research from a nation** = `research_month × ksBonus × cpResearchMult / numCP × myCPs`
  (CPs with `benefitsDisabled` excluded). `ksBonus`=1.05 iff you own that nation's KnowledgeSector
  CP; `cpResearchMult` = Π faction `ControlPointResearch` effects (two `Effect_CPResearch10` → ×1.21
  in the reference campaign). The "N from nations" tooltip line = Σ over your CPs.
- **councilors** = per councilor `(Σ org.incomeResearch_month + Σ trait.incomeResearch) ×
  (1+effScience/100)`, effScience = base + Σ org.science + trait mods.
- **habs** = Σ powered+completed player module `incomeResearch_month`.
- **unused MC** = `max(0, maxMC−usedMC) × 0.075/day` (shows as a tooltip line only when under cap).
- **distribution bonus** = `0.05 × (# researchWeights slots in use)` (4 base + org/hab slots when
  unlocked → 25% at 5 slots, 30% at 6). **TOTAL = (HQ+councilors+nations+habs+unusedMC)×(1+bonus).**
- Rival factions: same chain (`--all-factions`), but their nations/councilors are partly
  intel-hidden, so rival totals are ~5-7% estimates vs the Intel→Factions flask.
- `Transactions."Daily Income"` latest Research entry = the BOOKED daily research; it's ~booked =
  total/(1+bonus) minus same-day drift, so **use the reconstructed tooltip total, not the booked
  ledger**, for "total research". `cachedYearlyRevenue.Research` is stale — never use.
- **Operating rule:** `research_income.py <save> [--all-factions]` fills the research-trend
  tracking table entirely from the save (Total/Nations/per-nation/Habs/Redist/HQ+Counc + rival
  estimates). No screen reads needed.

## R20 (2026-07-07) — Fusion globals are scored as reactor+drive PAIRS, on three axes

A 2033-05 ranking scored Tokamaks 3/C− from the drive table alone ("dominated rung-for-rung
by ICF") — per-thruster thrust is only one axis, and the claim was false on the other two.
A fusion global's value = the best PAIR its ladder produces:

1. **Per-mass thrust** — combat MN / (reactor t + radiator t) per thruster (S14; reactor
   stats from `TIPowerPlantTemplate.json` — Tokamaks are the lightest/cleanest line at
   every tier, ICF the dirtiest early). Per-mass, Tokamak pairs beat ICF pairs at every
   shared fuel tier; **no first-gen fusion lantern beats Lodestar (~0.23 MN/t) in
   combat-per-mass** — early fusion buys EV/reach, not combat.
2. **Per-hull absolute thrust** — 6-thruster cap means capitals need big per-thruster
   numbers; this is ICF's real (and only) dominance axis.
3. **Unlock odds** (R11) — the DRIVE project's `factionAvailableChance` and roll cap:
   Helion Nova Lantern is 75%-avail (25% *never*) · roll≤50; all terminal torches are
   50%-avail lottery tickets; **Deuteron Torus is the only guaranteed fusion warship
   drive**. A cheap second ladder is lottery insurance (both-ladders P(no DHe3 drive)
   ≈6% vs 25% ICF-only). Also count fuel logistics: Helion pairs need He3 infrastructure;
   Deuteron/Protium Torus run on plain hydrogen.

Method: prereq-closure per drive project from the save's completed sets (globals + project
RP separately), pair each drive with its required reactor rung, compute all three axes
before any "ladder X dominates Y" claim. **`scripts/fusion_ladder_planner.py` runs the whole
method against your save** — one row per pair, all three axes plus He3/propellant flags;
`docs/tech-analysis.md` § "Global techs" keeps a worked reference-campaign example.

## R21 (2026-07-07) — Check FACTION LOCKS on a gateway's downstream before crediting the gate

"Project X gates Y" is worth nothing if Y is faction-locked away. Worked case: Pherocyte
Resistance → Hydra Biowarfare → **Kill the Hive (`factionPrereq: DestroyCouncil` — HF only)**;
Pherocyte Mastery → **Enslave the Masters (Initiative only)**; A Permanent Peace (Cooperate
only). For the Resistance the whole branch collapses to councilor-buff garnish. Rule: when
walking unlock trees, read `factionPrereq`/`factionsAllowed` on every downstream node —
victory-project prereqs (`Project_*Victory`) are ALWAYS faction-locked. Also from the same
triage: Pherocyte Inoculations needs the SPECIMEN quartet (Griffin/Salamander Interrogation +
WarDog/Megafauna Necropsy) + TransformPhages, not the pherocyte-defense quartet.

## R22 (2026-07-07) — Field semantics: enumerate ALL code consumers + localization BEFORE a verdict

Three mis-calls, one root cause — asserting a field's meaning from its name or its
FIRST code hit:
1. `incomeProjects` called "a boolean flag" off one `>0` check — a second consumer
   (`MultipleFacilitiesMultiplier`, R16) sums it into project-slot research speed.
2. `ControlPointMaintenance` read as money upkeep — localization says "Control Point Cap".
3. Pherocyte Resistance's gates credited without faction-lock checks (R21).
Protocol: (a) translate every `Context.*`/effect name via the localization files FIRST (R8);
(b) grep the decompiled repo for ALL consumers of the field, not the first one; (c) only then
score. When the player pushes back on a mechanic, assume the missing consumer exists and find it.

## R23 (2026-07-13) — Income claims come from research_income.py, not the extractor's breakdown

On the same 2033-06-17 save the extractor's "Research income breakdown" section reconstructed
13,896/mo while the canonical `research_income.py` (R19: reproduces the in-game 🧪 tooltip to
~0.1%) computed **16,898/mo** — the extractor under-counted the Nations term (missing advise/
KS multipliers) and mislabeled the distribution bonus (+21% shown where the real figure is
+30% at 6 slots). A −21% phantom "income drop" nearly entered the campaign's trend table.
Rule: the extractor's income section is for orientation only; **any research-income number
that gets written down (trend tables, trend claims, ETA math) is generated by
`research_income.py`**, whose output even prints a paste-ready trend-table row.
(2026-10-17: the extractor now overlays `research_income.SaveResearch` on its own section
whenever the templates mirror is present — the table says "exact" when it did — so the two no
longer disagree; the heuristic remains only as the no-templates fallback.)

## R24 (2026-07-16, game 2033-08-22) — Steal Project = project COPY + a research CATCH-UP payout that scales with the TARGET's lead

A rival's overnight ~6k jump on one global tech is not an income change — check the
Transactions ledger for a `Steal Project` entry before theorizing. Code-verified
(`TIMissionEffect_StealProject.cs`), the mission on success pays the thief TWICE:

1. **Project copy** (via `PromptStealProject`): thief picks one of the target's faction
   projects — even one they MISSED the availability roll on (it moves `missedProjects` →
   `availableProjectNames`). The target loses NOTHING (no progress drop, no negative TX).
2. **Research catch-up**: `RP = 10 × max(0, target_daily_research − thief_daily_research)`;
   crit success = `(gap + 25) × 2 × 10`. Added as raw Research ("Steal Project" TX
   category), then flows through the thief's researchWeights (empty project slots → all
   into their weighted global slots) and lands ×(1 + category bonus).

Worked case (reference campaign, 2033-08-21 02:12): an HF councilor stealing from the
Resistance's highest-output (high-Persuasion) councilor. Gap 457.5 − 51.6 ≈ 405.9/day → ×10 = 4,059 ≈ ledger 4,051 (plain success).
HF weights [0,2,0,2,2,2] with idle project slots → the entire chunk hit Ultracapacitors:
+6,139 = 4,051 × ~1.52 category bonus. They also copied Project_MolecularBenefication.

Implications: (a) the research LEADER is the highest-value target in the game — every
successful steal hands a laggard ~10 days of the income GAP, so guard visible councilors
(Celebrities on Public Campaign) with Security; (b) when diagnosing any faction's sudden
per-tech jump: diff `techProgress.factionContributions` across daily saves, then grep the
thief's `Transactions` — the category string names the mechanic; (c) the victim-side save
shows no loss, so don't hunt for one.

## R25 (2026-07-13) — Unlock projects have a TIER CEILING: check whether your economy still founds that tier

A project that unlocks a new hab core (or any founding kit) is only worth what the habs it
founds can become. Template check before scoring: the core's `tier`, and whether ANY module
lists it in `upgradesFromName`. The automated cores are the worked trap: `AutomatedPlatformCore`
and `AutomatedOutpostCore` are tier-1 `coreModule`s that **nothing upgrades from** — the crewed
ladder is OutpostCore→SettlementCore→ColonyCore / PlatformCore→OrbitalCore→RingCore, and
`OrbitalCore.upgradesFromName = PlatformCore` (crewed) only. An automated hab is a permanent
tier-1 dead end: −1 MC and zero crew, but it can never host tier-2+ modules (Operations
Centers, the big mines' upgrade path, research institutes).

Consequence for scoring: automated cores are situational — real value only when you are
founding WIDE tier-1 sprawl (remote belt claims, quick resource grabs you may abandon). For a
tall economy that upgrades every mining hab to tier 2–3, the project unlocks nothing you will
ever build, whatever its price tag. The reference campaign carried a stale 8/10 "A, research
before any mining buildout" score for Automated Platform Core into 2033, when the economy had
long since stopped founding tier-1 platforms — re-scored 3/10 on player pushback. Generalize:
for ANY "unlocks a buildable thing" project, the question is not "is the thing good?" but "does
your build pipeline still have a slot where this thing would go?" (same family as R6 marginal
analysis and R18's cap-raiser check).

## R26 (2026-07-13) — Persisted scores DECAY: sweep the lessons library for every project you are about to recommend

Step 1 of the scoring protocol says reuse persisted analysis — but a stored score is only as
current as its `analyzed:` date, and a LESSON written after that date can invalidate it
outright. Before presenting any ranked list, run each recommendation through the lessons files
one more time (grep by project name and by mechanism) and re-validate scores whose analysis
predates the newest relevant lesson. Two reference-campaign worked cases: (a) Pherocyte
Resistance carried 8/10 "A — strong unlock chain" from an early analysis; R21 (faction locks
on downstream nodes), written five weeks later, collapsed the entire chain for the campaign's
faction and the honest score was 6/10 with the unlocks worth zero. (b) A hate-floor masking
project scored well pre-war; once the campaign entered permanent Total War with a kill-driven
hate accumulator ~250 points above the floor ([LESSONS-aliens](LESSONS-aliens.md) A4), the
floor reduction became a literal no-op — the stored score survived two ranking passes before
the player caught it. The protocol is cheap: scores are reused, but every reused score gets a
"has any lesson newer than this analysis touched this project or its mechanism?" check before
it reaches the player.

## R27 (2026-07-20, game 2033-09-16) — Re-audit standing ladder recommendations when a LOTTERY LANDS or the victory horizon COLLAPSES; insurance stops being worth premiums once the insured risk is gone

The fusion-ladder advice ("fund ICF/Hybrid as lottery insurance", R20) was correct while the
player's only good drives were fission and every strong fusion drive was a probabilistic roll.
Then two state changes silently invalidated it: (a) **Poseidon Torch — a 20%-lifetime, R11
"80%-never" project — actually rolled and reached 65% progress**, closing the transit role
outright; (b) the **victory chain hit 5/5**, collapsing the horizon that deep (85k–200k RP,
50%-avail) ladder payoffs needed. The stale "sink RP into ICF" advice survived into a later
session and the player caught the contradiction.

Rules:
1. **Insurance logic is state-dependent**: a second-ladder recommendation exists to cover the
   risk of ending with no good drive in a role. When a lottery LANDS (or any superior option
   becomes researched/in-hand), re-run the ladder ranking from scratch — R26's decay sweep
   applies to ROLE COVERAGE, not just per-project scores.
2. **Horizon check**: before recommending any multi-rung ladder, estimate rungs×RP against the
   campaign's remaining life (victory-chain state is the proxy). A ladder that cannot mature
   before the end is a 0 regardless of its terminal drive.
3. **Sunk globals locked in a slot are FREE OPTIONS, not commitments** (globals can't be
   swapped mid-slot — rule 7): let them finish, note that completed prereqs make later
   availability rolls happen passively at zero cost, and do NOT let "we already have the
   global" pull project RP toward the dead ladder (sunk-cost direction check).
4. When the player says "didn't you tell me X before?" — reconstruct WHICH state change flipped
   the verdict and say so explicitly; if none did, the earlier advice was simply wrong (own it).

## R28 (2033-10-28; authored 2026-07-21) — Before planning an upgrade path, find the GATE — and remember tech-reachable ≠ usable

Two failure modes when a player asks "what's the next X I can get?".

**(1) A whole tier is often walled behind ONE or TWO expensive globals.** Quoting the cheapest
item that beats the baseline hides the wall. 2033-10-28: every fusion torch that beats Lodestar
(1,320 MN) — Borane 1,814 / Protium Nova 2,376 / Protium Converter 3,514 — needs BOTH
`TerawattFusionReactors` (50,000 in-game RP) and `AneutronicFusion` (37,500). Those two globals are
87,500 of Borane's 282,500 total; **naming the gate reframes the whole decision** from "which drive"
to "am I buying this tier at all?" Run
`drive_upgrade_finder.py --exclude <techA>,<techB>` — it prints a **CEILING WITHOUT** section and
marks every blocked candidate ⛔, so the answer to "what's my ceiling without paying for X?" is one
command. When the answer is "nothing", say so plainly: *the drive you already fly IS the ceiling.*

**(2) Tech-reachable ≠ usable — exotic PROPELLANT is a second, independent gate.** The two drives
that dodged the fusion wall above (Pion Torch, Advanced Antimatter Plasma Core) both burn
**antimatter per tank**, which the player did not produce, so they were unusable at any RP price —
and both cost MORE than the blocked option anyway. Always read `perTankPropellantMaterials` next to
the prereq closure; the finder now flags `⚠ NEEDS ANTIMATTER fuel` / `⚠ needs exotics fuel`.
Related: Helion drives cost 1 fissile/tank unless the He3 Mine is built (LESSONS-ships S20).

**Corollary — check whether the upgrade is even USED.** Score the gate against the bio-g cap first:
if the current drive already exceeds the mass-at-cap of anything you build (Lodestar x6 carries
33,639 t at 4.0 g vs a ~26 kt largest design), extra combat thrust buys **nothing** and the real
purchase is EV/reach. A 282,500-RP "combat upgrade" that changes no combat outcome is the most
expensive kind of mistake. Note the budget SHRINKS as the g-cap rises (29,901 t at 4.5 g), so
raising the cap is what makes a stronger drive worth buying.

## R29 (2026-10-17; in-game 2006-07-16, build 1.0.63) — Councilor research no longer scales with Science; the save version picks the formula

R19's councilor term — `(Σ org.incomeResearch_month + Σ trait.incomeResearch) × (1 + effective
Science/100)` — was code-verified on 1.0.39. On a **1.0.63** save (2003 scenario, Brutal) the 🧪
tooltip matches the **UNSCALED** sum exactly: 4 councilors' org+trait research = 454/mo flat; the
scaled form gave 535/mo and the total over-read the tooltip by 11% (906 vs 813.6/mo). With the flat
term the reconstruction hit 813/mo (0.1%) — and the Transactions ledger's booked research
(23.57/day = base before the distribution bonus) agrees to the same precision, so this is the
formula change, not a mis-read elsewhere.

`research_income.py` defaults on `TIGlobalValuesState.latestSaveVersion`
(`councilor_science_scales = version < 1.0.63`) and then **lets the ledger arbitrate**: it
computes both models and switches to the other one when it lands clearly closer (>1% of the base)
to the faction's booked research, printing `councilor model: … LEDGER-CALIBRATED` when it does.
Evidence so far: a **1.0.62 base-game** save (HF, 2045-12) matches SCALED to 0.4%; the
**1.0.63 2003-scenario** save matches FLAT to 0.1% — build or scenario, not yet separable, and the
self-calibration makes the default a hint rather than a dependency. **Pin it here when a 1.0.63
base-game save (or a 1.0.62 2003 save) shows up.** Science still matters for research through
nations (adviser bonus inside `research_month`) — only the org-income multiplier is gone.

Rival research: quote the **ledger-anchored** figure (`booked_day × (1 + distribution)` — it IS
what their Intel screen shows, one day stale); the formula reconstruction over-reads rivals by
5–20% on the 2003 save for reasons not yet isolated and is kept as the explanation, not the value.
Both `research_income.py --all-factions` and the extractor's faction table now do this.

Diagnostic that found it (reuse it): when the total is off, compare the reconstruction's
pre-distribution base with the ledger's `Daily Income` Research entry (ground truth, excludes the
distribution bonus); the gap is a single term — here it equalled Σ org research × Science/100 to
the unit. Related: P18 (the other half of the same 30% over-read was a missing DLC template layer).
