# Game Design: Zero Button Game

## 1. Purpose and sources of truth

This document defines **what fits the Zero Button Game format** and how genres are evaluated and calibrated. [VISUAL_DESIGN.md](VISUAL_DESIGN.md) covers presentation; [README.md](README.md) covers generation and validation.

Runtime values and machine checks live in [presets/current](presets/current/), the genre plugins and solvers, [`protocol.py`](src/zero_button_game/protocol.py), [`pipeline.py`](src/zero_button_game/pipeline.py), [`sequence.py`](src/zero_button_game/sequence.py), and [tests](tests/). Update them with this document whenever a design change affects behavior.

## 2. Core format

Zero Button Game is a short prediction game that requires no viewer input. Each problem follows these canonical phases:

1. **appearance**: hide the board at `frame 0`, then briefly reveal the problem.
2. **thinking** (shown as `THINK`): hold the initial state still while the viewer predicts the outcome or procedure. An initial design guideline is about 4–8 seconds.
3. **solve**: after the reveal, automatically execute the deterministic correct operations (`Action`).
4. **result**: hold the successful state so completion is understandable without reading text.
5. **transition**: move to the next problem or finish. Three-problem sequences progress Easy → Medium → `target` (shown as HARD).

Thinking time runs from `frame 0` to `reveal_start`. The 4–8 second range is a design guideline, not a fixed rule; current calibrated values include 2.5 and 3.5 seconds. Consult presets and code for exact values. No phase requires viewer input.

## 3. Principles for fitting the format

Every new genre or major `ruleset` change must satisfy all of the following:

- **Intuitive goal**: explain in one sentence what must be done to succeed.
- **Short-horizon prediction**: infer the outcome or main procedure from the still image shown during `thinking`.
- **Short solution**: initially target about 2–5 typical operations.
- **Procedural dependency**: earlier operations change later choices or state. If operations commute, require enough combinatorial interaction and state the `equivalence_policy` explicitly.
- **Large state changes**: every operation clearly changes position, connectivity, illumination, outline, arrangement, or another focal property.
- **Traceable causality**: watching `solve` makes it possible to understand how one operation enabled the next.
- **Immediate success state**: success is recognizable within two seconds of `result`. `CLEAR` is supporting feedback, never the sole evidence.
- **Minimal legend**: show only what communicates the goal and primary operation, without leaking solution clues.
- **Determinism**: identical seeds, presets, and code produce identical problems, solutions, and metadata.
- **Defined solution identity**: either prove uniqueness or fix, alongside the `ruleset`, the `equivalence_policy` that defines when solutions are identical.

### Counting `Action` operations

Two to five operations is an **initial design guideline** for producing candidates that viewers can retain during `thinking` and follow during `solve`. It is neither a law nor a retroactive universal limit. Current examples above five operations, such as edge-by-edge movement in `maze` and the `target` band in `parking`, are checked separately for continuity of motion, causal readability, and total animation duration. Current values are defined by presets, solver-produced `Solution.actions`, and tests.

## 4. Designing difficulty

| Good difficulty | Poor difficulty |
|---|---|
| Operation order changes what becomes possible | Requires searching for many tiny differences |
| Presents a small number of plausible branches | Requires memorizing many clues or exception rules |
| Includes convincing near-miss alternatives | Operations cannot be inferred without instructions |
| Deepens dependencies at the same board size | Merely increases the board or object count |
| `solve` reveals why a prediction failed | Solver output looks like an arbitrary list of steps |

Do not define difficulty by operation count alone. Combine dependency depth, branching, plausible near misses, solver search nodes (`expanded_nodes`), and visual readability. A plugin-specific `difficulty_score` may select bands only within that genre; comparing scores between genres is prohibited.

- **Easy (`easy`)**: goal and operation are immediately recoverable, branching is limited, and the representative causal pattern can be learned.
- **Medium (`medium`)**: under the same `ruleset`, add at least one dependency level or plausible near miss so a greedy choice may fail.
- **target**: shown as HARD. Strengthen multiple dependencies, competing branches, or deeper near misses, but not through more explanation, fine-detail search, or a longer `solve`.

Candidate scans must show that several of operation count, dependency, branching, near misses, search nodes, and readability progress monotonically as intended across bands. Calibrate structure separately from thinking time. Some current structural bands are recorded as `uncalibrated-*`, and current thinking times are not calibrated for a general audience. Mosaic Shift's 4.0 / 6.0 / 8.0 seconds are uncalibrated initial values.

## 5. Designs to avoid and design precedents

Do not normally accept:

- Multi-constraint pencil puzzles that require many clues and exception rules.
- Long solutions that resemble solver output rather than visible state changes.
- One-operation puzzles without predictive depth.
- Physics simulations based on chance or continuous quantities.
- Games whose target cannot be inferred from the board.
- States or outcomes distinguished only by color.
- Completed forms that cannot be inferred from the initial view or a neutral legend.
- Cosmetic variants with the same operations, dependencies, and prediction target as an existing genre.

- **Rejected Tents / Trees-style designs**: visual search across many clues and local constraints makes the goal and main procedure hard to recover quickly.
- **Mirror Swap-style designs are insufficient**: a single swap can show a readable state change, but lacks order dependency and predictive depth.
- **Shortened Mosaic Shift**: representative six-move problems were too demanding to retain during `thinking` and follow during `solve`. The current standard uses shortest solutions of Easy 2 / Medium 3 / target 4 operations while preserving depth through cross-axis order dependency, a unique shortest solution, and enough misplaced tiles (`misplaced_tiles`).

## 6. The current seven genres

| Genre | Goal | Prediction during `thinking` | Primary operation | Why it fits | Main risk |
|---|---|---|---|---|---|
| `maze` | Reach GOAL from START | Route and branch choices | `traverse_edge` | A single route emerges continuously | Long edge sequences or fine visual search |
| `pipes` | Connect `source` to `sink` without leaks | Route, pieces, and rotation directions | `rotate_piece` | Rotation is large and flow confirms success | Dense connectors or multiple shortest routes |
| `parking` | Move the target car through the east exit | Blocker order and slide distances | `move_piece` | Each slide creates space for the next | Unnecessary vehicles or long sequences |
| `packing` | Fill the hole exactly with all pieces | Shape placement `anchor` values | `move_piece` | Each placement fills substantial space | Missing the no-rotation rule or mishandling identical pieces |
| `lights` | Turn on every cell | Set of cells to press | `toggle_cell` | Each press changes a cross | Commuting presses, color-only dependence, or trivial greediness |
| `fold` | Match the outline and cover each target cell exactly once with color | Creases, directions, and per-axis folds | `fold_along` | Outline shrinks and layers grow | Confusion with rotation or fold equivalence |
| `mosaic` | Restore a 3×3 emblem | Axis, line, cyclic direction, and order | `shift_line` | Whole-line motion exposes cross-axis order dependency | Independent-line fixes or unclear imagery |

Current equivalence examples include the pressed-cell set for `lights`; the multiset of shapes and anchors with identical pieces identified for `packing`; commuting cross-axis folds for `fold`; and the strictly ordered `Action` sequence for Mosaic Shift. Each genre module is authoritative for the exact string and version.

## 7. Generators, solvers, and acceptance evidence

Every genre satisfies [`protocol.py`](src/zero_button_game/protocol.py) and is registered in [`registry.py`](src/zero_button_game/registry.py). Process candidates in this order:

1. Generate deterministically from the seed and preset.
2. Validate the structure.
3. Find a shortest or canonical solution.
4. Replay the solution, including `Action.precondition`.
5. Calculate difficulty and apply reason-coded quality rejections.
6. Replay the presentation, then check neutrality, operation correspondence, success timing, and readability.

Define first whether “one solution” counts literal `Action` sequences or normalized routes, sets, equivalence classes, or covers. Version the `equivalence_policy` across solver, metadata, preset, and tests. If complete enumeration, rank (`gf2_rank`), or bounded exhaustive search cannot prove uniqueness, record the proof scope and limits. Do not count equivalent alternatives arbitrarily as distinct or merge distinct causal solutions merely because they look alike.

Solvers have finite node, state, depth, or equivalent limits. Reject limit overruns, unsolvable or non-unique candidates, invalid structures, illegal solutions, and quality failures with known reason codes. Fail closed on missing or extra preset JSON, type and range errors, and mismatches with the 20 fps grid. If the candidate limit does not yield enough accepted problems, raise `GenerationExhausted`; never substitute an uncertain candidate.

Retain `generation_seed_hex` derived from `master_seed` and `candidate_index`; `problem_sha256` / `solution_sha256`; preset ID, source path, and source-byte hash; solver ID/version, `optimality`, `cost`, operation count, and expanded nodes; uniqueness/equivalence proof and normalized-signature hash; difficulty metrics and `requested_band` / `accepted_band`; and results from replay, neutrality, rendering, and artifact validation.

Use `audit-quality` to compare `scanned`, `accepted`, `acceptance_rate`, `rejection_reasons`, candidate hashes, metric catalogs, and `audit_sha256` without rendering. Evaluate rejection bias and blinded accepted examples, not acceptance rate alone.

## 8. Human calibration

Human evaluation should be blinded: do not reveal source, solver output, title, or `CLEAR`. Record pre-reveal prediction, rule recovery, thinking time relative to `reveal_start`, ability to restate `solve` causality, and success recognition within two seconds of `result`.

Before integrating a new genre, run a title-hidden evaluation with at least five people. Require at least 80% (4/5) to recover the goal and primary operation, at least 80% to follow `solve` causality, and everyone to recognize success within two seconds. If two people infer the same wrong rule, revise and retest. If Easy / Medium / target do not separate as intended, retune dependency, branching, near misses, and presentation time as well as operation count.

Five participants only screens for serious comprehension defects; it does not establish general-audience calibration. Generalized claims require preserved target population, sample size, seeds, conditions, per-band results, and updated preset calibration metadata.

## 9. New-genre proposal checklist

- [ ] Explain the goal in one sentence and the primary operation in one word.
- [ ] Infer the completed state or success condition from the still initial state.
- [ ] Starting from 2–5 typical operations, demonstrate order dependency or interaction.
- [ ] Make each operation substantially change shape, position, connection, or outline, not just color.
- [ ] Keep `solve` short and prediction errors causally understandable.
- [ ] Prove a unique solution or a versioned `equivalence_policy`.
- [ ] Give generation, solving, and uniqueness checks finite limits.
- [ ] Separate Easy / Medium / target with multiple metrics.
- [ ] Keep pre-reveal information solution-independent and legends neutral.
- [ ] Test color vision, reduced-size display, `frame 0`, and `result`.
- [ ] Provide distinct causality, not a cosmetic reskin.
- [ ] Support deterministic fixtures, property, conformance, render, and integration tests.

## 10. Integration workflow

1. **Prototype** rules, operations, goal, equivalence, and representative seeds without rendering.
2. **Evaluate blind** whether a still problem communicates goal, operation, and prediction target.
3. **Audit logic** for generation, solving, replay, uniqueness, limits, metrics, and rejection reasons.
4. **Review rendering** in `VISUAL_DESIGN.md` for neutrality, animation, non-color cues, success timing, and safe area.
5. **Integrate** by synchronizing presets, plugin, registration, title/audio, schemas, tests, `README.md`, and both design documents, then generate and validate representatives.

Never change a `ruleset`, operation count, difficulty band, thinking time, equivalence relation, or acceptance condition by editing this document alone. Update all relevant implementation sources in the same change.
