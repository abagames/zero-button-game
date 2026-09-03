# Visual Design: Zero Button Game

This document defines the visual design for pipes, lights, fold, parking, packing, maze, and mosaic: palettes, layout, safe areas, readability limits, `frame 0`, feedback, and acceptance criteria.

Common contract for every genre: a 720×720 canvas; safe area (36, 36, 684, 684); procedural RGB24 rendering with no external bitmap assets; no board at `frame 0` (fade `min(1.0, frame / appearance)`); and a title that is not faded.

Machine-checked dimensions, colors, and readability limits live in each `src/zero_button_game/*_render.py` file (`render.py` for maze) and in `registry.py` under `visual_contract` / `render_contract_checks`. Keep code, checks, and this document synchronized.

## 1. pipes

**Concept-derived visual tags**: `render-crisp-modular-plumbing`, `motionviz-mechanical-quarter-turn`, `motionviz-traveling-pressure-pulse`

### 1.1 Visual concept

A quiet piping diagram becomes a single illuminated circuit through mechanical quarter-turns. Connector geometry takes priority over decoration so the problem, operation, and successful connection remain distinguishable without text.

### 1.2 Color palette

| Role | Color | Hex | Usage |
|---|---|---|---|
| Background | near-black navy | `#10151D` | Outer area and largest surface |
| Pipe structure | pale steel | `#D8E2EA` | Thick unconnected pipe strokes |
| Source / flow outer | aqua | `#43D9C2` | START marker, flow outline, front ring |
| Goal / rotation focus | amber | `#FFD166` | GOAL diamond, target ring/tick |
| Flow core | white | `#F5F8FA` | Inner flow stroke and front dot |

`pipes_render.PIPE_VISUAL_ROLES` is authoritative; renderer and checks share those values.

### 1.3 Rendering, layout, and feedback

- Cells are 160px in 3×3 and 120px in 4×4; strict minimum 96px.
- Connectors use 16px steel strokes and a central hub; elbow, straight, and junction pieces are identifiable by outline.
- START is an aqua circle plus white direction arrow; GOAL is an amber diamond plus white dot.
- During rotation, connector geometry turns continuously under an amber ring and outward tick.
- Flow uses an aqua 12px outer stroke, white 5px core, and a moving white-dot/aqua-ring front.
- A 480px board sits in a centered 552×578 panel on the fixed canvas. Grid boundaries are muted 2px lines; title, phase, and START/GOAL labels sit outside the board.
- At `frame 0`, panel, cells, grid, and connectors match the background. Title, START/GOAL markers and labels, THINK track, and phase text remain visible. This prevents an indefinitely displayed thumbnail from defeating time-based difficulty.
- Before `reveal_start`, render only scrambled connectors with no solution-dependent highlight. Rotations animate continuously. After the final connection, focus disappears and flow travels only along the canonical START→GOAL route. Hold that flow and combine the GOAL diamond with `CLEAR`.

Keep the white-core aqua flow visually dominant. Use only the START arrow and GOAL diamond as familiar symbols; avoid textures, particles, and effects outside the active route.

### 1.4 Asset handoff and acceptance

Keep the grid, connectors, markers, ring/tick, flow, and bitmap font procedural. Any future asset treatment must share connector centerlines and canonical masks with Rules; image appearance must never determine connectivity.

Acceptance requires:

- Identical alternate-solution pixel hashes before `reveal_start`, with a difference at `reveal_start`.
- Cells ≥96px, connectors ≥16px, and flow cores ≥5px.
- Rotation snapshots that match `Action`.
- Flow that begins only after solving and ends on the canonical START→GOAL path.
- No flow in unused distractor connectors.

## 2. lights

**Sources of truth**: `src/zero_button_game/lights_render.py` and `registry.LightsPlugin.visual_contract` / `render_contract_checks`.

### 2.1 Visual concept and layout

Focus closes on a pressed cell, a cross expands from it, and numbered badges preserve press order until the whole board lights. Because a light has only two states, focus brackets, badges, and the cross pulse communicate the action and its order; cell color communicates the result.

| Element | Constant | Value |
|---|---|---|
| Canvas | `LightsRenderer.width/height` | 720×720 |
| Panel | `PANEL_BOX` | (44, 70, 676, 660), also `semantic_bounds` |
| Cell | `CELL_PX` | 96px, gap 5, edge 4 |
| Board | `BOARD_ORIGIN` (120, 96), 5×4 | 480×384px, x 120–600 / y 96–480 |
| Goal tile | `GOAL_TILE_ORIGIN` (96, 508), 3×3×32 | 96px square; panel (82, 494, 206, 622) |
| Rule tile | `RULE_TILE_ORIGIN` (300, 508) | two 3×3 tiles plus 52px arrow; width 244px; panel (286, 494, 558, 622) |
| Progress bar | `PROGRESS_BAR` | (120, 626, 600, 636) |
| Title | `TITLE_ORIGIN` | (84, 30), scale 4 |
| Phase text | — | centered at y 674 |

The safe-area margins are board left/top/right 84/60/84px and legend panels left/right/bottom 46/126/62px. Checks require the board and both legend panels inside the safe area, legends below the board, and `MINI_CELL_PX` ≥28px.

**Rationale.** A side legend needs 272px, leaving only 376px for five 75px cells, below the 96px minimum. An upper legend fits but places instructions before the puzzle in the visual flow. The adopted layout therefore keeps the board at y 96 and a single legend row at y 508, followed by progress at y 626 and phase text at y 674.

### 2.2 Neutral legend, text, and palette

The legend depends only on the problem. The goal tile is an all-lit 3×3 miniature with amber corner marks. The rule tile shows an unlit miniature with a cursor ring on (1,1), an arrow, then the plus-shaped five lit cells (`LEGEND_PLUS`). `alternate_lights_scene`, `legend_geometry()`, pre-reveal snapshots, and `legend_solution_dependent: False` enforce neutrality.

The title is `ALL LIGHTS ON`. The former `LIGHTS OUT` contradicted the goal and was read as “turn everything off”; a follow-up readability measurement recovered the correct goal, rule, and risk after the change. Phase text is `THINK / PRESS / LIT / CLEAR`; `LIT` and `CLEAR` are amber. The bundled bitmap font supports uppercase letters, digits, and `- . :`, but not `J` or `Q`; `_text()` applies `upper()`.

| Role | Value | Usage |
|---|---|---|
| Background | `#10151D` | Outer area |
| Panel / legend panel | `render.PANEL` / (25, 34, 45) | Board and legend surfaces |
| Unlit / lit | (34, 46, 59) / (255, 209, 102) | Cell state; lit also marks goal, text, and result ring |
| Cell edge | (150, 172, 192) | 4px outline |
| Cursor / progress | (67, 217, 194) | Rule ring and THINK bar |
| Focus / badge | aqua / dim aqua | Current and completed press markers |
| Pulse | (67, 217, 194) | 7px expanding cross |

`LIGHTS_VISUAL_ROLES` is authoritative. `state_change_not_color_only: False` is explicit because binary light state has no inherent shape change. Shape-based support comes from brackets closing from half-width 78px to 44px, persistent numbered badges, a cross pulse expanding from `max(40, radius*0.45)` to `progress * 96`, and color cross-fades limited to the affected plus five cells.

### 2.3 Timeline and acceptance

At `frame 0`, panel, board, and legends are hidden; title, THINK track, and phase remain. Before reveal, show only `puzzle.initial` and a neutral progress bar. During PRESS, animate fixed-cell brackets, pulse, cross-fade, and badges. During LIT, hold the all-lit board and all badges under an amber ring breathing from 10–16px. During CLEAR, retain both state and badges.

`validation.py` requires solve motion at least every four frames; `press_marker_animates_every_frame` checks every consecutive frame.

Acceptance requires:

- Matching pre-reveal alternate hashes and a different reveal frame.
- Cells ≥96px and mini-cells ≥28px.
- Neutral legends inside the safe area.
- Rendering that matches `toggle_cell`.
- Badges that recover press order at every band's LIT/CLEAR boundaries.
- A board that first becomes fully lit after the last press.
- A unique GF(2) press set (nullity 0).
- `board_lit` and `rule_legend` cues with `state_mutation: False`; the legend must also be solution-independent.

## 3. fold

**Sources of truth**: `fold.FOLD_RULESET` (`fold-to-target-exact-v1`), `src/zero_button_game/fold.py`, `fold_render.py`, and `registry.FoldPlugin.visual_contract` / `render_contract_checks`.

### 3.1 Visual concept and layout

A partially colored sheet shrinks through folds until its outline matches a dashed box and every target cell is covered exactly once by color. Colored overlap is forbidden and the number of colored cells equals the target area. Shape carries state: strictly decreasing outline, nested layer contours, and real-angle flap rotation.

The paper occupies a centered board using cells ≥68px (contract minimum 64px); legend mini-cells are ≥38px (minimum 32px). Board, both legend panels, and `PANEL_BOX` remain within the safe area. The below-board rule legend shows three stages—before the fold, a flap upright on the crease, and after the fold—and uses `GOAL` and `FOLD` labels.

**Evidence.** A two-stage “tile → arc arrow → tile” legend was mistaken for rotation. A title-hidden follow-up recovered the goal and operation after the three-stage revision. Each round had one participant, so the result does not generalize to a population; no primary record exists under `studies/`.

| Role | Value | Usage |
|---|---|---|
| Background / panel | `render.BACKGROUND` / `render.PANEL` | Outer and support surfaces |
| Legend panel / board | (25, 34, 45) / (20, 27, 36) | Legend and paper surface |
| Paper / edge | (78, 96, 116) / (168, 188, 206) | Blank cells and outline |
| Colour | (67, 217, 194) | Colored cells and upright flap |
| Stack shadow / edge | (12, 17, 24) / (110, 132, 152) | Nested depth contours, up to `MAX_DRAWN_STACK_STEPS` 3 |
| Crease | (245, 248, 250) | Fold line |
| Target | (255, 209, 102) | Dashed goal, `GOAL`, FULL ring/text |
| Progress | (67, 217, 194) | THINK bar |

`FOLD_VISUAL_ROLES` is authoritative. `state_change_not_color_only: True` rests on strictly decreasing area, 5px inset nested outlines in `_draw_stacked_cell`, and monotonic real-angle flap motion from `_flap_point` with `LIFT_GAIN` 0.09. Layer order itself is not visible; `visible_value` renders the union of colored layers because the goal checks exactly one colored projection per target cell. `FoldState` hashing still includes layer order, tested by `FoldLayerOrderTests` with an asymmetric 4×1 strip.

An earlier top-layer-only renderer colored only two of six goal cells, contradicting successful exact coverage. Rendering the union of layers fixed it.

### 3.2 Timeline and acceptance

Standard THINK time is 4.0 / 6.0 / 6.0 seconds for easy / medium / target. It is one evaluator's current within-subject standard from round 5, not a general-audience optimization. Keep `--thinking-time` for comparisons. Revisit timing if multi-seed evidence repeatedly shows target too short or medium too generous, if band separation fails in use, if generation/fold count/presentation changes, or before claiming general-audience fitness.

At `frame 0`, only `FOLD TO FILL THE BOX` remains unfaded. During THINK, show flat paper, dashed target, neutral legend, and progress. During FOLD, replace the moving side with a rotating flap while outline shrinks and layers grow. During FULL, breathe an amber ring from 10–16px; during CLEAR, change the phase text.

Acceptance requires:

- Matching pre-reveal alternate hashes and a different reveal frame.
- Passing dimension and safe-area checks.
- Solution-independent legend and target geometry.
- Rendering that matches `fold_along`, with flap angle advancing every frame.
- A target that first fills after the last fold.
- A fold class proven unique by complete enumeration.
- `rule_legend` and `target_outline` with `state_mutation: False` and `solution_dependent: False`.

## 4. parking

**Sources of truth**: `src/zero_button_game/parking_render.py` and `registry.ParkingPlugin.visual_contract` / `render_contract_checks`.

### 4.1 Concept, layout, and motion

Slide cars along their axes in a crowded lot and release the aqua target through the amber opening on the east. A 480px board begins at (120, 126): cells are 96px for 5×5 or 80px for 6×6. The panel / `semantic_bounds` is (84, 88, 636, 666), leaving safe-area margins 48px left/right, 52px top, and 18px bottom. With `CELL_MARGIN = 9`, vehicle short sides are 78px / 62px, above the 48px minimum. The east opening measures `cell - 8`: 88px / 72px, above the 24px minimum.

The target is aqua, blockers blue-gray, and the exit/current focus amber, on `#10151D` with `#D8E2EA` structure. Rounded vehicles carry a white line along their long axis, so shape and line direction communicate movement independently of color. `state_change_not_color_only: True`. Interpolate each `move_piece` continuously; set `released: True` only after the final slide. Result feedback is an amber ring centered on the exit; `CLEAR` is supporting text. At `frame 0`, title `GET THE CAR OUT`, THINK track, phase, and the neutral exit label remain while board and vehicles are hidden.

Acceptance requires:

- Cells ≥72px, vehicle short sides ≥48px, and an exit ≥24px.
- Continuous motion matching every `move_piece`.
- Release only after the final slide.
- Matching pre-reveal hashes against a cyclically shifted move order and a different reveal frame.
- Identification independent of color through body shape, axis line, and the east opening.

## 5. packing

**Sources of truth**: `src/zero_button_game/packing_render.py`, `packing.MAX_TRAY_WIDTH_CELLS`, and `registry.PackingPlugin.visual_contract` / `render_contract_checks`.

### 5.1 Concept, layout, and motion

Move fixed-orientation pieces from a lower tray into the dark upper hole to seal it without gaps. The hole is centered in `HOLE_BAND = (90, 474)` with 96px cells, up to 4×4 (384px, x 168–552 / y 90–474). The tray begins at y=508 with 60px cells and 20px gaps. Piece bounding-box widths total at most nine cells, so four pieces use at most `60 × 9 + 20 × 3 = 600px`, x 60–660. Panel / `semantic_bounds` is (60, 78, 660, 660), with safe-area margins of 24px left, 42px top, 24px right, and 24px bottom.

Hole piece bodies are `96 - 2×5 = 86px`; tray bodies are `60 - 2×4 = 52px`. Contract minima are hole cells 96px, tray cells 54px, and piece bodies 48px. `tray_extent()` and actual hole dimensions are checked against the safe area.

**Rationale.** A uniform 84px layout reaches about 696px at eight cells plus gaps, while seven cells cannot hold four pieces. A side-by-side vertical layout exceeds 520px in height and moves the hole off center. The adopted design places the hole above the tray and uses separate cell scales.

The empty socket is dark with a light outline; unplaced pieces are blue-gray, seated pieces aqua, and active focus/completion ring amber. Only piece exteriors receive a 3px outline, with internal cell boundaries removed. During `solve`, pieces move from tray to hole `anchor` while scaling 60px→96px, without rotation or reflection. Exact cover completes only after the last placement, followed by a breathing amber ring. At `frame 0`, only `FILL THE HOLE`, THINK track, and phase remain visible.

Acceptance requires:

- Hole cells ≥96px, tray cells ≥54px, and piece bodies ≥48px.
- The hole and `tray_extent()` inside the safe area, with the hole above the tray.
- Rendered piece, order, and anchor matching `move_piece`.
- Exact cover only after the final placement.
- Matching pre-reveal hashes against cyclic placement order and a different reveal frame.
- `state_change_not_color_only: True`, supported by position, outline, and continuous scale.

## 6. maze

**Sources of truth**: `src/zero_button_game/render.py` and `registry.MazePlugin.visual_contract` / `render_contract_checks`.

### 6.1 Concept, layout, and motion

An aqua trace grows through a quiet walled maze from START to an amber diamond GOAL. The 540px board begins at (90, 108); panel is (72, 90, 648, 666), and `semantic_bounds` is (78, 96, 642, 660), leaving safe-area margins 42px left/right, 60px top, and 24px bottom. Grids range 5×5–9×9, so cells are 108–60px against a 54px minimum. Walls are 5px and the completed path has a 7px minimum.

Walls are pale steel, START an aqua double circle, and GOAL an amber diamond. Before reveal, render walls and markers only, never the path. After reveal, extend a 10px aqua line with a white tip along the canonical path, then switch to a 7px amber completed line. At `frame 0`, only `SOLVE THE MAZE`, THINK track, and phase remain. Avoid decorative motion; the board itself carries causality.

Acceptance requires:

- Cells ≥54px, 5px walls, and a path ≥7px.
- Pre-reveal hashes matching a reversed-path alternate and a different reveal frame.
- START circles and GOAL diamonds identifiable without color.
- Success visible without text as the amber path joins START and GOAL.

## 7. mosaic (MOSAIC SHIFT)

**Sources of truth**: `src/zero_button_game/mosaic_render.py` and `registry.MosaicPlugin.visual_contract` / `render_contract_checks`.

### 7.1 Concept, layout, and motion

Restore a fragmented emblem by cyclically shifting an entire row or column of a 3×3 board. The board begins at (120, 112), measures 480px, and uses 160px cells. Panel / `semantic_bounds` is (72, 78, 648, 656), leaving safe-area margins 36px left/right, 42px top, and 28px bottom. The quality-controlled procedural vocabulary is `halo-diamond`, `four-petal-star`, and `shield-knot`; no external bitmap assets are used.

Tiles are blue-gray, primary emblem lines aqua, and secondary lines/active focus amber, with 3px seams. Emblems use strokes at least 12px wide and combine ring, diamond, petal, and shield contours. `state_change_not_color_only: True` because completion is legible through line continuity, outline, and symmetry. During `solve`, interpolate the entire active line and draw off-board fragments simultaneously entering from the opposite side to show wrap-around. A 5px focus outline and arrow communicate axis, line, and direction.

At `frame 0`, hide the board and retain the title. THINK uses only the initial board and neutral progress, never `Action` order. `CLEAR` and the completion ring appear only from `solve_end`; the side-closing transition never creates a fully blank frame.

### 7.2 Difficulty, quality, and acceptance

Current shortest `Action` counts are Easy 2 / Medium 3 / target 4. The solver uses bounded BFS to depth 8 with a 362,880-node budget and records shortest depth, exact shortest-path count, and expanded nodes. Under `mosaic-exact-action-order-v1`, commuting orders are distinct paths, so accepted candidates require exactly one shortest path. Reject single-axis cases, nonintersecting independent-line fixes, too few misplaced fragments, and already-complete boards.

**Rationale.** The original 2–3 / 3–5 / 5–8 ranges were too demanding for short prediction; representative Medium 4 and target 6 problems were reduced to 2 / 3 / 4.

**Calibration limit.** Thinking times remain 4.0 / 6.0 / 8.0 seconds and have not been evaluated with people since the operation counts were reduced.

Acceptance requires:

- A 720×720 canvas, 160px cells (minimum 144px), and board/panel inside the safe area.
- Matching pre-reveal hashes against a cyclically reordered alternate and a different reveal frame.
- `shift_line` axis, line, delta, boundary states, and wrap interpolation matching semantic snapshots.
- Exactly one shortest path under bounded BFS, with intersecting axes and no independent-line repair.
- `solved: False` before the final shift and `solved: True` / `CLEAR` only from `solve_end`.
- `emblem_complete` with `state_mutation: False`.
