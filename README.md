# Zero Button Game

Zero Button Game is a production environment for generating and validating deterministic puzzle GIFs/MP4s for maze, pipes, parking, packing, lights, fold, and mosaic, plus three-problem Easy → Medium → Hard MP4s with optional audio. The CLI, generation pipeline, and metadata use the internal bands `easy` / `medium` / `target`; only the sequence's viewer-facing display renders `target` as HARD.

## Quick start

### 1. Requirements

- CPython 3.12 or later (validated with 3.12.3)
- FFmpeg / ffprobe (validated with 6.1.1, including `libx264`)
- gifsicle (validated with 1.94)
- No external Python package dependencies. Runtime checks cover system tools such as FFmpeg, ffprobe, ImageMagick, and gifsicle.

Run the following commands from the project root:

```bash
python3 --version
ffmpeg -version
ffprobe -version
gifsicle --version
PYTHONPATH=src python3 -m zero_button_game --help
```

`PYTHONPATH=src` adds the uninstalled `src/` tree to Python's import path. `-m` is Python's standard option for running the package's [`__main__.py`](src/zero_button_game/__main__.py) as a module.

The old `PYTHONPATH=src python3 -m puzzle_gif` CLI has been replaced by `PYTHONPATH=src python3 -m zero_button_game`. No alias, shim, or re-export is provided for the old package.

### 2. Generate representative sequences for all seven genres

Choose a new `--output` directory that does not overlap existing artifacts:

```bash
PYTHONPATH=src python3 -m zero_button_game generate-representatives \
  --seed 20260828 --max-candidates 500 --audio on \
  --output output/my-representatives-2026-08-28
```

This creates a three-problem MP4 for each genre and a collection-root `manifest.json`.

### 3. Generate one genre sequence

```bash
PYTHONPATH=src python3 -m zero_button_game generate-sequence \
  --type pipes --seed 20260828 --max-candidates 500 --audio on \
  --output output/my-pipes-sequence-2026-08-28
```

`--type` accepts `maze`, `pipes`, `parking`, `packing`, `lights`, `fold`, or `mosaic`. Each sequence generates `easy` → `medium` → `target`, displayed as `EASY` → `MEDIUM` → `HARD`.

### 4. Generate a standalone artifact

```bash
PYTHONPATH=src python3 -m zero_button_game generate \
  --type lights --difficulty target --seed 8521 \
  --output output/my-lights-single-2026-08-28
```

The default is `--format gif,mp4`; use `--format gif` or `--format mp4` to limit output. Every genre supports `easy`, `medium`, and `target`.

### Audit logic-only quality candidates

```bash
PYTHONPATH=src python3 -m zero_button_game audit-quality \
  --type parking --difficulty easy --seed 20260822 --candidates 20
```

`audit-quality` runs the normal generation and quality checks without rendering or writing files. It prints deterministic JSON with acceptance rates, rejection reasons, metric catalogs, candidate and preset hashes, and `audit_sha256`. Identical preset bytes, type, band, seed, and candidate count produce identical results and hashes. See [`protocol.py`](src/zero_button_game/protocol.py) for the plugin contract.

### 5. Select audio

Audio applies to sequences:

- `--audio on`: MP4 with AAC audio containing four cue layers for countdown, operations, completion, and problem transitions.
- `--audio off`, or omitted: backward-compatible silent MP4 with no audio stream.

Standalone `generate` creates silent GIF/MP4 artifacts. [`sequence.py`](src/zero_button_game/sequence.py) is the implementation source of truth for sequence audio, timelines, and validation.

### 6. Validate

```bash
# Seven-genre collection or one genre sequence
PYTHONPATH=src python3 -m zero_button_game validate-sequence \
  output/my-representatives-2026-08-28

# Standalone run or single instance
PYTHONPATH=src python3 -m zero_button_game validate \
  output/my-lights-single-2026-08-28 --strict
```

Validation failures exit nonzero. Given a collection root, `validate-sequence` validates every sequence below it; given a sequence directory, it validates that sequence. `validate --strict` recursively validates every instance in a run.

### 7. Inspect output

Seven-genre generation:

```text
<collection>/
  manifest.json
  <puzzle-type>/<sequence-id>/
    sequence.mp4
    sequence.json
    validation.json
    components/<band>/<puzzle-type>/<instance-id>/...
```

Standalone generation:

```text
<run>/<puzzle-type>/<instance-id>/
  problem.json
  solution.json
  presentation.json
  metadata.json
  validation.json
  animation.gif            # when --format includes gif
  preview.mp4              # when --format includes mp4
  contact_sheet.png        # distributable; pre-reveal only
  contact_sheet_full.png   # review copy containing the solution
  keyframes/               # distributable; pre-reveal only
  keyframes_full/          # review copy containing the solution
```

Sequence components retain the complete standalone artifact set, but temporary PPM frames used for composition are removed after success. `sequence.json` records per-band seeds, instances, presets, thinking times, section frames, component hashes, presentation/audio data, and the final MP4 hash.

### 8. Modify preset JSON

Runtime presets and standard thinking times live in [presets/current](presets/current/); shared timeline contracts live in [presets/shared](presets/shared/). See [presets/README.md](presets/README.md) for catalog and naming rules. Normally, edit only `current/`.

```bash
PYTHONPATH=src python3 -c \
  'from zero_button_game.preset_loader import PresetLoader; print(PresetLoader().audit_catalog())'

PYTHONPATH=src python3 -m unittest discover \
  -s tests -p 'test_presets.py' -v
```

The loader rereads JSON on every use and records the source-byte SHA-256 in standalone and sequence metadata. It fails closed on catalog, identity, field, range, and 20 fps grid errors.

## Major constraints

- **Never overwrite existing `output/`.** An identical instance produces `OUTPUT_CONFLICT` by default. Reproducibility checks must use a separate new output. Standalone `--force` moves an existing instance to `output/superseded/`, but a new run is recommended. Sequences have no `--force`.
- **Nonstandard thinking time is a comparison condition.** `--thinking-time` accepts 2.5–20.0 seconds on the 20 fps grid for any plugin/band. Values differing from JSON standards are recorded as `comparison-override-not-standard`; omit the override for canonical generation.
- **Thinking times are not calibrated for a general audience.** The table below marks uncalibrated values. Sequence pacing, titles, countdown, HARD comprehension, volume, and cue distinctness also remain unevaluated with general users; see [GAME_DESIGN.md](GAME_DESIGN.md) for calibration requirements.
- **Difficulty scores cannot be compared across genres.** Each plugin defines its own formula and range.
- **`_full` artifacts contain solutions.** For publication and evaluation, normally use `contact_sheet.png` / `keyframes/`; do not distribute `*_full`.
- **All of `output/` is excluded from Git.** This covers media, metadata, manifests, JSON/JSONL, and temporary staging. Reproducibility comes from seeds, presets, code, and outputs such as `audit-quality` stdout.
- `--keep-frames` is for investigation. A 720×720 RGB PPM is about 1.5 MiB per frame, so normally omit it.

## Current standards

| Genre                   | Current preset                                          | Standalone | Three-problem sequence   |                    Thinking time Easy / Medium / Hard (seconds) |
| ----------------------- | ------------------------------------------------------- | ---------- | ------------------------ | --------------------------------------------------------------: |
| `maze`                  | [`maze-{easy,medium,target}.json`](presets/current/)    | GIF / MP4  | MP4 (with/without audio) |                                                 2.5 / 2.5 / 3.5 |
| `pipes`                 | [`pipes-{easy,medium,target}.json`](presets/current/)   | GIF / MP4  | MP4 (with/without audio) |                                                 4.0 / 6.0 / 8.0 |
| `parking`               | [`parking-{easy,medium,target}.json`](presets/current/) | GIF / MP4  | MP4 (with/without audio) |                                                 4.0 / 4.0 / 8.0 |
| `packing`               | [`packing-{easy,medium,target}.json`](presets/current/) | GIF / MP4  | MP4 (with/without audio) |                                                 4.0 / 4.0 / 8.0 |
| `lights`                | [`lights-{easy,medium,target}.json`](presets/current/)  | GIF / MP4  | MP4 (with/without audio) | 4.0 / 6.0 (uncalibrated candidate) / 8.0 (existing calibration) |
| `fold`                  | [`fold-{easy,medium,target}.json`](presets/current/)    | GIF / MP4  | MP4 (with/without audio) |                                                 4.0 / 6.0 / 6.0 |
| `mosaic` (MOSAIC SHIFT) | [`mosaic-{easy,medium,target}.json`](presets/current/)  | GIF / MP4  | MP4 (with/without audio) |                   4.0 / 6.0 / 8.0 (uncalibrated initial values) |

Internal bands are `easy` / `medium` / `target`; only viewer-facing labels are `EASY` / `MEDIUM` / `HARD`. Standalone output supports `--format gif`, `--format mp4`, or `--format gif,mp4`; three-problem sequences are MP4-only and support `--audio on` / `--audio off`.

Thinking time runs from `frame 0` to `reveal_start`. [src/zero_button_game](src/zero_button_game/) and [presets/current](presets/current/) are authoritative for each plugin's ruleset, `Action`, score range, and calibration metadata. See [VISUAL_DESIGN.md](VISUAL_DESIGN.md) for color, layout, and visual acceptance requirements.

Mosaic Shift restores a 3×3 emblem with cyclic row and column shifts. See [GAME_DESIGN.md](GAME_DESIGN.md) for difficulty and solution rules and [VISUAL_DESIGN.md](VISUAL_DESIGN.md) for presentation requirements.

## Documentation map

| Document                             | Role                                               |
| ------------------------------------ | -------------------------------------------------- |
| This README                          | User entry point for generation and validation     |
| [GAME_DESIGN.md](GAME_DESIGN.md)     | Genre fit, difficulty, acceptance, and calibration |
| [VISUAL_DESIGN.md](VISUAL_DESIGN.md) | Current visual specification                       |

## Development checks

```bash
PYTHONPATH=src python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_markdown_links.py
```

Before adding a plugin, review the boundaries in [`protocol.py`](src/zero_button_game/protocol.py), [`registry.py`](src/zero_button_game/registry.py), and [`pipeline.py`](src/zero_button_game/pipeline.py).
