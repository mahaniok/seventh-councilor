# Setup

Three steps, ~2 minutes. You need Python 3.10+ and an installed copy of Terra Invicta.

## 0. The short way

```bash
python3 scripts/setup_campaign.py
```

Detects your faction, difficulty, research rate, and alien progression from your newest
save, writes `config.json`, and mirrors your game data — steps 1-2 below in one command.
Steps 1-3 are the manual equivalent (use them if you want a different save/campaign, or if
detection fails).

## 1. Create your config

```bash
cp config.example.json config.json
```

Edit `config.json`:

- `faction` — your faction's internal templateName:

  | Faction | templateName |
  |---|---|
  | The Resistance | `ResistCouncil` |
  | The Initiative | `ExploitCouncil` |
  | The Servants | `SubmitCouncil` |
  | The Protectorate | `AppeaseCouncil` |
  | The Academy | `CooperateCouncil` |
  | Humanity First | `DestroyCouncil` |
  | Project Exodus | `EscapeCouncil` |

- `research_rate_pct` — match your campaign-start setting; scripts use it to compute
  effective RP costs, so get it right.
- `alien_progression_pct`, `difficulty` — match your campaign-start settings. Scripts read
  the live values from your save; these copies are fallbacks and hints for the AI agent's
  reasoning (hate floor, event timing are keyed to them).
- `save_dir` / `game_install_dir` — leave `null` unless auto-detection fails (see below).

## 2. Mirror your game data

```bash
python3 scripts/sync_game_data.py
```

This builds the game's template JSONs and English localization from **your** install into
`scripts/templates/` and `scripts/localization/` (both gitignored). The analyzers use them to
translate internal names to the names you see in-game and to read module/drive/tech stats.

The mirror is built the way the game builds its own data — in **layers**, later ones
overriding earlier ones per `dataName`:

1. the base game (`TerraInvicta_Data/StreamingAssets/Templates`);
2. the DLC **scenario overlay** your campaign runs on (`config.json` → `"scenario"`:
   `"2003_Scenario"` for the Dark Skies 2003 start, `"Broken_Earth_Scenario"`, or `null`
   for the default 2026 start — `setup_campaign.py` detects it from the save). Scenario-only
   content lives *only* there: the 2003 start's `BS_*` techs, `Project_BS_*` projects and the
   `Effect_BS_*` penalties (e.g. ×0.5 control-point research). Without this layer those
   effects are invisible to the analyzers and research reads ~30% high;
3. the **mods** the game has enabled (`<install>/Mods/Enabled/*`; `config.json` → `"mods"`:
   `"enabled"`, a list of folder names, or `false`). Only template-file mods matter here —
   code (`.dll`) mods can't be mirrored, so their effects stay unmodelled.

`scripts/templates/_LAYERS.json` records which layers built each file. Check it when a
number disagrees with the game: a missing layer is the first suspect.

This repo intentionally ships no game data — it's Pavonis Interactive's. Mirroring locally
also means the data always matches *your* game version, scenario and mods.

**Re-run this after every game patch** and after enabling/disabling mods.

## 3. Verify

```bash
python3 scripts/extract_snapshot.py --newest --brief
```

You should get a summary of your latest save. If it can't find your saves or install, set
the paths explicitly in `config.json`:

| Platform | Typical save location |
|---|---|
| Windows | `C:\Users\<you>\Documents\My Games\TerraInvicta\Saves` (sometimes under `OneDrive\Documents`) |
| macOS (CrossOver) | `~/Documents/My Games/TerraInvicta/Saves` |
| Linux / Steam Deck (Proton) | `~/.local/share/Steam/steamapps/compatdata/1176470/pfx/drive_c/users/steamuser/Documents/My Games/TerraInvicta/Saves` |

| Platform | Typical install location |
|---|---|
| Windows | `C:\Program Files (x86)\Steam\steamapps\common\Terra Invicta` |
| macOS (CrossOver) | `~/Library/Application Support/CrossOver/Bottles/Steam/drive_c/Program Files (x86)/Steam/steamapps/common/Terra Invicta` |
| Linux | `~/.local/share/Steam/steamapps/common/Terra Invicta` |

## Using it with Claude Code

Open the repo folder in Claude Code and ask away — `CLAUDE.md` teaches the agent the whole
toolkit. Start with "analyze my newest save", then see [examples.md](examples.md) for the
full catalog of what you can ask.

The scripts are also plain CLIs — `python3 scripts/<name>.py --help` works without any agent.

## Optional: your campaign workspace

`campaign/` is a gitignored per-user folder for campaign-specific state the AI maintains for
you: a timeline log (`docs/campaign-log.md` format), your doctrine, ship-design calibration
readings, campaign-local lessons. See `campaign/README.md`. Nothing to set up — the
assistant creates files there as you work.

## Optional: generate reference pages

```bash
python3 scripts/generate_vault.py      # per-tech / per-project pages
python3 scripts/generate_modules.py    # ship-module reference pages
```

Output lands in `generated/` (gitignored — it's derived from your game data). Useful if you
browse the repo in Obsidian or want the agent to grep a tech tree that matches your exact
game version.

## Patch drift

Mechanics constants in `docs/lessons/` were verified against game build **1.0.38**
(mid-2026), mostly from decompiled game code. Patches occasionally change balance numbers
without notice. After a major patch: re-run `sync_game_data.py`, and treat calibrated
constants (armor mass coefficients, hate model, mining quadratic) as "verify before
trusting" until re-checked against tooltips.
