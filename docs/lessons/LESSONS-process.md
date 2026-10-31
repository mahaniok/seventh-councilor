# TI analyzer lessons — Process, parsing & provenance

Part of the Seventh Councilor lessons library (see the repo `CLAUDE.md`). Lesson IDs are
permanent (`P1`…). Dates and worked numbers come from the **reference campaign** these lessons
were battle-tested on (Resistance, 2026 start, Normal difficulty, research rate 200%, alien
progression 200%). Read this file before any session that parses saves, compares snapshots, or
writes analysis notes.

## P1 (meta-principle) — The save / in-game tooltip is GROUND TRUTH; your reconstruction is a hypothesis

A derived / cached / reconstructed number must never be trusted over the game's own displayed
number (save-verified 2026-07, repeated two-save diffs). Recurring failure modes of this shape:
(a) stale `cachedYearlyRevenue` quoted as a *current* rate;
(b) raw `site.*_day` quoted as production when actual = ×tier×bonus; (c) per-module sums (MC,
water, upkeep) ignoring `powered`; (d) an entire module class ignored — e.g. Farms — turning a
healthy water balance into a phantom "water crisis"; (e) reconstructed MC-available quoted as
slack when it over-read ~45; (f) a metals crisis invented from a stale stock/income ratio.

**Hard rules, apply every time:**
1. Before any numeric claim, ask "does a tooltip / screenshot / save field show this directly?"
   If yes, use THAT. Present reconstructions as *estimates*, loudly caveated — never as fact.
2. Use the existing tool before re-deriving (`mine_completion_timeline.py` already does mine
   output correctly). Check what scripts exist first.
3. When the player says the save contradicts you, STOP defending — verify immediately. Diff two
   saves, read the field, find the missing module class.
4. Every per-module resource sum filters `powered`, not just `constructionCompleted` —
   unpowered modules neither produce nor consume anything.
5. `cachedYearlyRevenue` is stale for everything — never cite it for a current rate or crisis.
6. After editing any lessons file or script, VERIFY the edit actually persisted (re-open the
   file and grep for your change). Cloud-sync tooling can silently revert working-tree files
   mid-session, and parallel sessions sharing the same checkout overwrite each other's edits —
   after any lesson/script edit, grep the file for your change before trusting it persisted,
   and let ONE session own the docs/scripts at a time.

## P2 — Parse to exact JSON paths; every save fact carries provenance

Never substring-search a raw-text window of the save: a fixed-size text window after one key
can overrun into the adjacent array (a 20k-char window after `finishedTechsNames` overran into
`techProgress` and misreported a just-queued tech at 78 RP as FINISHED), and a fact recorded
without its source file cannot be audited when two saves are in play (save-verified 2026-06).
Rules: `json.load` (or the extractor's helpers) + exact key paths
(`TIGlobalResearchState[0].Value.finishedTechsNames` etc.); when two saves are in play (named
save vs ExitSave), STATE which file every fact came from; remember `currentProjectProgress`
contains weighted AND paused entries — entry-count is not slot-count. **And never type a
dataName from memory for membership tests — Pavonis ships TYPO'D dataNames**
(`Project_SubsurfaceRadiatonAnalysis`, sic) — pull the exact string from the template first,
or a correctly-spelled check silently returns "not researched" (save-verified 2026-07).

## P3 — Saves come in TWO formats: `.json` AND `.gz`

Both coexist in the save directory; older saves are typically `.gz`. The analyzer's `load_save`
sniffs gzip magic bytes (0x1f 0x8b) and handles both. Don't `ls *.json` and conclude "no saves
exist for year X" — also glob `*.gz` (hundreds of `.gz` saves can cover the "missing" years).
Both formats can carry a **UTF-8 BOM** that a plain `utf-8` decode rejects with
"Unexpected UTF-8 BOM" — always decode with `encoding='utf-8-sig'` (harmless when no BOM);
`load_save` already does, so use it instead of ad-hoc `json.load(open(...))`.

## P4 — Check `TIGlobalValuesState.latestSaveVersion` across real-world gaps

A TI patch may ship between saves and change formulas. Transitions seen in the reference
campaign: 1.0.28 (2028-29) → 1.0.29 (2030-early 31) → 1.0.32 (mid-31→) → 1.0.38 (2033-01,
skipping 1.0.33–1.0.37). When a between-save change looks impossible: check (a) save versions
match, (b) file mtimes — a weeks-long real-world gap suggests a patch. Don't pin behavior
changes on in-game events if a patch could explain them. (The Oct-2031 cohesion crash WAS
in-game — both saves 1.0.32, 4 real-world minutes apart — but the verification matters.)

## P5 — Ship CLASS names ≠ instance names ≠ template IDs

- `ship.displayName` → the individual ship's name (e.g. "Resolute" — invented example).
- `ship.templateName` → internal design ID (`playerShipTemplate70`) — never user-facing.
- `faction.shipDesigns[].dataName == ship.templateName`, and that design's `_displayName` →
  **the class name** (e.g. "Falcon-2" — invented example) — what the player calls it.

Always resolve `templateName` through `shipDesigns` before presenting fleet inventories.
Also: design entries key weapons as `moduleName`; runtime `hullWeapons`/`noseWeapons` arrays on
ship state use `moduleTemplateName` — filter on only one and you miss half the data.
**FLEET names are per-faction**: `TISpaceFleetState.displayNameByFaction` maps faction id →
name (the player's custom name; rivals see procedural Romeo-### names);
top-level `displayName` is usually EMPTY — reading it makes every named player fleet show as
"(unnamed)" and a direct name search fail (save-verified 2026-07). Resolve via the
player's faction id first.

## P6 — When a file is "missing", search the repo BEFORE claiming hallucination

This repo is the single source of truth for scripts, reference docs, and lessons. An older or
per-machine copy of the runbook may reference files that have since moved. Search `docs/`,
`scripts/`, and `generated/` before concluding a referenced file doesn't exist. Known failure
modes: a correct file reference dismissed as "hallucinated"; stray copies of scripts edited
outside the canonical location for weeks while the canonical copy drifted.
**Scripts have exactly one edit site: `scripts/` in this repo.**

## P7 — Analysis-note frontmatter: two namespaces, both dated

If you keep dated analysis notes alongside this repo (playthrough/operational notes and
per-tech analysis notes), two conventions coexist — match the file you're editing, don't
invent new fields:

- **Playthrough / operational notes**: `snapshot_date` (real-world) + `in_game_date`.
  Re-evaluations: `re_evaluated_snapshot_date` / `re_evaluated_in_game_date` +
  `prev_snapshot_date` / `prev_in_game_date`.
- **Tech-analysis notes** (per-project/per-tech): `analyzed_real_date` + `analyzed_game_date`
  + legacy alias `analyzed:` + `analysis_score` + `analysis_tier`. Re-scores:
  `re_evaluated_real_date` / `re_evaluated_game_date` + `prev_analyzed_*`.

Both REQUIRE both dates: the real-world date answers "has the game/meta shifted since?", the
in-game date answers "does this still apply to the current game state?". Section header format:
`## Analysis (real YYYY-MM-DD · game YYYY-MM-DD, score N/10 — TIER tier)`.
Process rule: before creating any frontmatter field, check the file's existing convention.

## P8 (new, 2026-07-04) — Position ≠ availability: check trajectory and ΔV before assigning a ship

A fleet's `globalPosition` says where it is, not whether it can go anywhere: check
`trajectory` (committed, generally non-cancellable transfers — destination + `arrivalTime`)
and `currentDeltaV_kps` / `currentMaxDeltaV_kps` before putting a ship in an operation plan.
Two worked failure modes (save-verified 2026-07, a Ceres expedition plan): a battle line with
full-tank ΔV 7–12 kps ordered to "just transit to Ceres" (that ΔV = multi-year transfers —
a low-ΔV line is home-defense by construction), and a monitor counted as departing Vesta that
was mid-transfer to 8 Flora, non-cancellable, ~14 months from Ceres. For any expedition:
per-ship full-tank ΔV, current propellant state (a fleet can sit dry after a water shortage),
current phase distance from save positions, and the in-game transfer planner as ground truth.

## P9 (2026-07-07) — Transit times: the in-game designer/planner beats the coast estimator

The `d/(0.75×ΔV/2)` coast estimate mis-sized a Helicon courier leg by ~35% (estimated ~2
months; the designer's Example Transfer read 5.44 weeks and the actual plotted transfer 6
weeks). Low-thrust high-EV couriers are ACCELERATION-limited (brachistochrone ~2√(d/a)), and
phase geometry moves the answer again. Rule: use the coast estimate only to rank options;
before committing any councilor/fleet timeline, read the designer's Example Transfer or plot
the real transfer in-game. **Second worked example (designer-verified 2026-07): hand-math
called Dusty Plasma "accel-crushed by reactor mass" for Kuiper runs — the designer showed the
DPD build is 3× LIGHTER (2 tanks vs 28, water 9.7 vs 260.6) with near-equal cruise accel
(2.7 vs 3.2 mg) and 643 vs 401 kps ΔV → Quaoar 50.7 vs 60.9 weeks, DPD WINS. Never rank drive
variants by mental mass budgets — build both in the designer and read Example Transfer.**
Corollary: building a courier AT the departure body can beat recalling a faster ship from
elsewhere — compare build+one-leg vs fetch+deliver.

## P10 (2026-07-13) — Victory-condition status: query the save directly; extractor doesn't cover it

`extract_snapshot.py` reports the victory PROJECT chain (5/5 done) but not the live
Close-the-Gate legs — on 2033-06-17 "chain COMPLETE" coexisted with 8/8 kill-list bases
standing and 30/30 alien fleets over the 4,000-SCV bar. The three legs are cheap direct save
queries (validated: the alien fortress base’s SCV 43,687 exact match to the independent assault recon):
(a) Earth leg — count regions whose `nation` = Alien Nation; (b) bases leg — alien habs with
`habType=Base && tier≥3 && anyCoreCompleted` on the surveyed set (HQ exempt); (c) fleets leg —
per-fleet Σ of each ship design's `_unnormalizedCombatValue` (cached design value; the LIVE
check in code also discounts damage/ammo/fuel — see
[Alien Production Rebuilding and Targeting](../mechanics/Alien%20Production%20Rebuilding%20and%20Targeting.md)
§3). Reusable implementation: `scripts/ops_query.py` (fleets+transits, councilor missions,
yards, construction, alien OOB). TODO: fold a "Close the Gate status" section into the
extractor proper.

## P11 (2026-07-14) — NEVER assert asset ownership without reading the faction field

A station at 7 Iris was called "our base" and a write-off recommendation built on it — it
belonged to **Humanity First** (`DestroyCouncil`) (save-verified 2026-07). Root cause: an
alien attack transit toward the body was reflexively interpreted as "attack on us"; the hab
query printed displayName/tier but the faction field was never read. The aliens war on ALL
human factions — a large share of their raids target rivals, and those raids are *good* news.
Rule: any claim of the form "our X" / "your X" about a hab, fleet, org, or councilor requires
the save's `faction` field read in the same query that found the object. Same failure family
as R21/R22 (asserting from first hit) — ownership is a field, not an inference.

## P12 (2026-07-14) — Ships never vanish: check `isRefit` in the shipyard queues before narrating losses

Two missile monitors that seemingly "left the fleet" in an 18→11 restructuring had in fact
gone to refit. Ground truth in the same save: `nShipyardQueues` orders carry `isRefit`,
`refit_originalShipDesignTemplateName`, and `originalSpaceShipState` — 7 of the 11 orders at
the Ceres yard were refits, only 4 were new builds. **Ships under refit leave their fleet
roster and reappear when done** — a fleet-count drop between saves means
detach/refit/transfer until proven otherwise; conclude "lost/scrapped" only from combat
records or a genuine disappearance from `TISpaceShipState`.
Bonus fact worth reusing: **refit cost = module delta only and it is TINY** (a
monitor missile-bay refit: ~3 metals + 18 water, 8–18 days; a battlecruiser refit:
~0.3 metals, 4 days) — refitting legacy Copperhead-missile ships to nuke bays is nearly free
compared to new hulls. `ops_query.py` Q4 prints REFIT-of-X vs new-build so this can't be
misread. Related: P11 (read the field, don't infer),
[Drives Refits and Logistics](../mechanics/Drives%20Refits%20and%20Logistics.md)
(refit legality).

## P13 (2026-07-15; formerly duplicate-numbered P10) — Prospected/survey knowledge IS in the save: `faction.intel[body]`

**Resolves the long-standing "survey-knowledge field not located" TODO** (resource_site_planner
banner, the runbook's no-spoilers note). Code-verified `TIFactionState.cs:9795`:
`Prospected(spaceBody) => GetIntel(spaceBody) >= 1f`; `Prospected(habSite) =>
Prospected(habSite.parentBody)`. Values: **0.1 = prospector/probe EN ROUTE**
(`LaunchProspector` sets 0.1), **1.0 = PROSPECTED** (`ProspectSpaceBody` sets 1.0).
Read it: `faction.intel` is a Key/Value list — `{key(e['Key']): e['Value']}`, keys are
TISpaceBodyState ids. Validated 2033-08 against an in-game check: 87 Sylvia /
511 Davida / 1172 Äneas all intel 1.0 = prospected ✓ (207 of 373 bodies prospected).
**Spoiler rule now precise**: quoting `*_day` yields is legitimate iff `intel[parentBody] >= 1.0`;
below that the player only has a range prior — still redact. Colonization needs Prospected
(`CanFoundColony => Prospected && EligibleforColonization && vacantHabSites.Count > 0`).

## P14 (2026-07-15; formerly duplicate-numbered P11) — Link habs to bodies via `habSite.parentBody`, NEVER by nearest position

A position-proximity match ("hab within 0.05 AU of body X") silently MISSED a station on
87 Sylvia and reported the body as free real estate (save-verified 2026-07).
Correct chain: `hab.habSite -> TIHabSiteState -> parentBody -> TISpaceBodyState`,
and inversely `site.hab is None` = vacant site. Same for site→body (`site.parentBody` is a
direct field). Position matching also collapses whole moon systems (all Saturn moons are
<0.07 AU apart, so one alien fleet at Titan registers as "at" all ten — true for THREAT
purposes, useless for occupancy).

## P15 (2026-07-18) — Editing a save is byte-surgery, NOT re-serialization

Reading a save is `json.load`; WRITING one is not `json.dump`. TI saves are pretty-printed with
**CRLF** line endings, **4-space** indentation, all non-ASCII **escaped as `\uXXXX`** (the file
is pure ASCII on disk), and — critically — they carry non-standard **`Infinity` / `-Infinity`**
tokens that strict JSON forbids. A full `json.load` → `json.dump` round-trip silently rewrites
every line (CRLF→LF), can reformat those Infinity tokens and float reprs, and turns a two-value
edit into a diff nobody can review. Do a **surgical byte edit** instead (save-verified on the
reference campaign, 2033-07-08, a ~94 MB save):

1. Work in **binary** (`open(..., 'rb'/'wb')`). Find the `gamestates` section you need and,
   inside it, the exact array to change. **Anchor the section on the header form `"<FullType>": [`,
   NOT the bare type string** — the type name also appears as a `$type` VALUE inside objects, and
   its first occurrence in the file is usually one of those, not the section header (this bit once,
   silently selecting the wrong array).
2. **Clone existing bytes.** To append an element, copy the last sibling element's exact bytes
   (indentation and all) and substitute the value; build indentation from scratch only for a
   fresh/empty `[]`, deriving the indent from the field-label line by counting leading SPACES
   only (not the label text — an off-by-one there over-indents the whole new block).
3. **Always back up first — non-negotiable, and the SCRIPT does it, not the player.** Before
   writing, copy the untouched original to a backup: either a timestamped `.BACKUP-*` every run
   (`ti_war_editor.py`), or a one-time pristine-original `.bak-*` that idempotent re-runs
   preserve. Never assume the player
   already has a copy, and don't add a flag that skips the backup. Then write to a temp file and
   **validate before replacing the original**: re-parse the result and deep-compare it to an
   independently-built expected model (the surest check — formatting-independent), and assert the
   CRLF count, ASCII-decodability, and Infinity-token count are all unchanged. Abort and keep the
   original on any mismatch — the backup is the last line of defence when validation can't catch
   an in-game incompatibility.

This is the general recipe for ANY save mutation; `scripts/ti_war_editor.py` is the worked
implementation (the war data model it edits is [LESSONS-politics](LESSONS-politics.md) C13).
Same discipline as the rest of this file: a mutated save you haven't re-parsed and diffed is a
hypothesis, not a fact (P1). These tools are opt-in and player-initiated — never mutate a save
as a side effect of an analysis request.

## P16 (2033-09-01 in-game; authored 2026-07-19) — Save history arrays are NEWEST-FIRST; verify slice direction before labeling "recent"

Every `history*` array in the save is stored **newest-first**: index `[0]` is today / the current
month, higher indices are older. This holds for `historyResearch`, `historyGDP`,
`historyPopulation`, `historySpaceFunding`, AND `history_MCCapOverageByDay`. So the recent window
is the **head** `arr[:N]`, never the tail `arr[-N:]`.

**The bug this class caused (fixed 2033-09-01).** `extract_snapshot.py` read
`overage_hist[-7:]` (the OLDEST 7 days), labeled it "last week", and printed a month-old spike
(peak 74) as if it were live — which produced the flatly-wrong conclusion that the player was
~74 MC over cap. Ground truth: their most recent 7 days were all 0 overage (blue MC sphere
916/986 = 70 under cap). `mine_shutdown_advisor.py` read the same field correctly (`[:10]`,
labeled "newest first"), so the two tools disagreed — **when two tools disagree on a
history-derived number, the one treating `[0]` as newest is right.**

**How to apply:** (1) any time you slice a `history*` array for a "recent"/"last week"/"trend"
figure, use `[:N]` and state the direction in the label; (2) if you're computing a delta or
"current" value, use `[0]`, never `[-1]` (an existing correct pattern — `historyResearch[0]` is
current-month research); (3) when a reconstructed "you're over/under cap" number contradicts the
in-game top bar, suspect a reversed history slice before theorizing a mechanic (P1). The extractor
now prints overage as `NEWEST-FIRST` with a guard that says "NOT currently over MC cap" when the
recent head is ≈0 despite an old in-window spike. See also [REFERENCE](REFERENCE.md) § Nation
history arrays.

## P17 (2026-07-20) — Present incomes in ONE unit that matches the player's in-game setting; NEVER yearly, never mixed

Terra Invicta shows incomes in a single unit set by the Gameplay option **"Show monthly incomes"**
(monthly when ON — the common case; daily when OFF) — the game NEVER shows yearly. Match it, and
never mix units in one answer or one table (a table with `/yr`, `/mo`, and `/day` at once is the
canonical "sloppy" tell a player will call out).

**Source of truth:** `config.json` → `income_display` (`"month"` default | `"day"`), written by
`setup_campaign.py` and read by the scripts. In `extract_snapshot.py`: `income_unit()` /
`per_unit(yearly)` / `unit_suffix()`; `nation_report.py` converts `spaceFunding_year` → per-month.
Helpers default to `"month"` when the key is absent, so it works for every campaign out of the box.

**Rules:**
1. Pick ONE unit for a report and hold it throughout.
2. **NEVER display a yearly income.** Raw annual save fields (`cachedYearlyRevenue.*`,
   `spaceFunding_year`) are internal — convert with ÷12 (monthly) or ÷365.25 (daily) before showing.
3. `history*[0]` heads are stored **per-MONTH** (P16) — label them `/mo`, not `/day`
   (`nation_report`'s old "Research/day [0]" was a mislabel; the value was monthly).
4. When a tool emits a different unit than the player's setting, CONVERT in your reply — don't pass
   the raw unit through.
5. Terminology travels with this: use the localized in-game label, never the internal field name
   (say **"Funding"**, not "space funding"/`spaceFunding_year`; translate through localization).

## P18 (2026-10-17; in-game 2006-07-16) — Game data is LAYERED: base + DLC scenario + mods. A template the mirror lacks reads as "no effect"

The game does not load `StreamingAssets/Templates` alone. Dark Skies ships its scenarios as
**template overlays** — `<install>/DLC_Content/DarkSkies/2003_Scenario/Templates/` (and
`Broken_Earth_Scenario/`) — merged on top of the base per `dataName`, and every enabled mod
(`<install>/Mods/Enabled/*/TI*Template.json`) is merged after that. A mirror built from the
base folder alone is silently wrong for such a campaign, and the failure is **quiet**: the
save references effect/tech/project names the mirror has never heard of, and every lookup
falls back to a default ("Additive 0", "no effect", "unknown project").

Worked case (2003-scenario save, Brutal, build 1.0.63): the faction carried
`Effect_BS_CPResearchPenalty` (Multiplicative **×0.5** on ControlPointResearch) — defined ONLY
in the 2003 overlay. `research_income.py` defaulted it to ×1.0 and read research 1,070/mo vs
the tooltip's 813.6; the extractor's CP-cap stack (`Effect_BS_CPMaintenancePenalty` +40,
`…MakingHistoryA` −60) was parsed from the digits in the names (0 / 0) instead of the template
values; `BS_*` techs and `Project_BS_*` projects had no friendly names. Nothing errored.

**Rules:**
1. `sync_game_data.py` builds the mirror from the three layers (config `scenario` + `mods`;
   `setup_campaign.py` detects the scenario from the `BS_`/`BSBE_` prefixes in the save's
   research state). Provenance per file: `scripts/templates/_LAYERS.json`. Re-sync after a
   patch AND after changing mods.
2. When a reconstructed number disagrees with the game, the first suspect is a missing layer —
   check `_LAYERS.json` and whether the save applies effect names the templates don't define.
   `research_income.py` now WARNS on stderr for unknown ControlPointResearch effects; never
   let an unknown effect default silently in new code — warn.
3. Value effects from the template (`operation` + `value`), never from digits in the name
   (`Effect_ControlPointMaintenanceBonus160` is −120; un-numbered effects are not 0).
4. `TIGlobalConfig.json` (base and overlays) is JSON-with-comments (`//`, `/* */`) and a
   single-dict file — merge it KEY-wise, never per-dataName (an overlay carries only the keys
   it changes).
5. Only template-file mods can be mirrored; `.dll` mods change code and stay unmodelled — list
   them in the campaign notes so a persistent discrepancy can be attributed.
Related: E25 (per-CP MC split), R29 (councilor research no longer scales with Science on 1.0.63).
