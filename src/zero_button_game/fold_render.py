from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

from .fold import (
    COLOURED, MIN_FOLD_COUNT, NO_PAPER, FoldPuzzleSpec, FoldReplayTrace, FoldRules, FoldState,
    action_fold, actions_for_folds, fold_result_extent, fold_state, split_folds,
)
from .models import PresentationPlan
from .render import BACKGROUND, MUTED, PANEL, WHITE, _blend, _line, _quad, _rect, _text

RGB = tuple[int, int, int]
Cell = tuple[int, int]
Fold = tuple[int, int, int]

FOLD_VISUAL_ROLES = {
    "background": BACKGROUND,
    "panel": PANEL,
    "legend_panel": (25, 34, 45),
    "board": (20, 27, 36),
    "paper": (78, 96, 116),
    "paper_edge": (168, 188, 206),
    "colour": (67, 217, 194),
    "stack_shadow": (12, 17, 24),
    "stack_edge": (110, 132, 152),
    "crease": (245, 248, 250),
    "target": (255, 209, 102),
    "progress": (67, 217, 194),
}

CELL_PX = 68
BOARD_ORIGIN = (156, 74)
CELL_GAP = 3
STACK_INSET_PX = 5
MAX_DRAWN_STACK_STEPS = 3
MINI_CELL_PX = 38
GOAL_TILE_ORIGIN = (148, 552)
RULE_TILE_ORIGIN = (340, 552)
RULE_ARROW_PX = 30
LEGEND_PAD_PX = 14
LEGEND_ARC_PX = 44
PROGRESS_BAR = (156, 58, 564, 68)
PANEL_BOX = (118, 56, 600, 656)
TITLE = "FOLD TO FILL THE BOX"
TITLE_ORIGIN = (120, 22)
PHASE_BASELINE = 672
SAFE_AREA = (36, 36, 684, 684)
LIFT_GAIN = 0.09
STANDING_FLAP_PX = 18
MIN_CELL_PX = 64
MIN_MINI_CELL_PX = 32


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def visible_value(state: FoldState, cell: Cell) -> int:
    """What a cell shows: the union of its stack, which is what the goal asks for.

    The goal predicate is "some layer under this cell is coloured", so the face
    is painted from that same union rather than from the topmost layer. Painting
    the topmost layer instead would leave the solved target mostly grey - the
    success frame would contradict the success condition. The stack *depth* is
    still shown, as nested inner outlines rather than as a shade.
    """
    found = NO_PAPER
    for layer in state.layers:
        value = state.value_at(layer, cell)
        if value == COLOURED:
            return COLOURED
        if value != NO_PAPER:
            found = value
    return found


@dataclass(frozen=True)
class FoldScene:
    puzzle: FoldPuzzleSpec
    plan: PresentationPlan
    trace: FoldReplayTrace
    folds: tuple[Fold, ...]
    states: tuple[FoldState, ...]
    semantic_bounds: tuple[int, int, int, int]


class FoldSceneBuilder:
    def build(self, puzzle: FoldPuzzleSpec, plan: PresentationPlan, trace: FoldReplayTrace) -> FoldScene:
        rules = FoldRules()
        if not rules.is_goal(puzzle, trace.final):
            raise ValueError("fold scene requires a final sheet that fills the target")
        folds = tuple(action_fold(step.action) for step in trace.steps)
        if len(folds) < MIN_FOLD_COUNT:
            raise ValueError("fold scene requires at least two folds")
        vertical, horizontal = split_folds(folds)
        if not vertical or not horizontal:
            raise ValueError("fold scene requires folds on both axes")
        states = (trace.initial,) + tuple(step.after for step in trace.steps)
        return FoldScene(puzzle, plan, trace, folds, states, PANEL_BOX)


def alternate_fold_scene(scene: FoldScene) -> FoldScene:
    """Neutrality perturbation: fold the horizontal creases before the vertical ones.

    Folds on different axes commute, so the swapped grouping is a genuinely
    legal solution of the same puzzle rather than a corrupted one, and every
    scene the builder accepts uses both axes, so the first crease really does
    change and the reveal frame always differs.
    """
    vertical, horizontal = split_folds(scene.folds)
    folds = tuple((1, line, direction) for line, direction in horizontal) + tuple(
        (0, line, direction) for line, direction in vertical
    )
    rules = FoldRules()
    actions = actions_for_folds(scene.puzzle, folds, rules)
    state = rules.initial_state(scene.puzzle)
    states = [state]
    for axis, line, direction in folds:
        state = fold_state(state, axis, line, direction)
        states.append(state)
    alternate_plan = replace(scene.plan, logical_steps=actions)
    return replace(scene, plan=alternate_plan, folds=folds, states=tuple(states))


class FoldRenderer:
    width = 720
    height = 720
    cell = CELL_PX

    # ---------------- geometry ----------------

    def board_origin(self, scene: FoldScene) -> tuple[int, int]:
        return BOARD_ORIGIN

    def board_extent(self, scene: FoldScene) -> tuple[int, int, int, int]:
        ox, oy = self.board_origin(scene)
        return ox, oy, ox + scene.puzzle.width * self.cell, oy + scene.puzzle.height * self.cell

    def world(self, scene: FoldScene, gx: float, gy: float) -> tuple[float, float]:
        ox, oy = self.board_origin(scene)
        return ox + gx * self.cell, oy + gy * self.cell

    def target_box(self, scene: FoldScene) -> tuple[float, float, float, float]:
        tx0, ty0, tx1, ty1 = scene.puzzle.target
        left, top = self.world(scene, tx0, ty0)
        right, bottom = self.world(scene, tx1, ty1)
        return left, top, right, bottom

    def legend_geometry(self, scene: FoldScene) -> dict:
        """Legend layout. Depends on the puzzle only - never on the fold class."""
        gx, gy = GOAL_TILE_ORIGIN
        rx, ry = RULE_TILE_ORIGIN
        goal_w, goal_h = 3 * MINI_CELL_PX, 2 * MINI_CELL_PX
        before_w = 2 * MINI_CELL_PX
        mid_w = MINI_CELL_PX + STANDING_FLAP_PX
        after_w = MINI_CELL_PX
        mid_x = rx + before_w + RULE_ARROW_PX
        after_x = mid_x + mid_w + RULE_ARROW_PX
        rule_w = before_w + RULE_ARROW_PX + mid_w + RULE_ARROW_PX + after_w
        return {
            "goal_tile": [gx, gy, gx + goal_w, gy + goal_h],
            "goal_panel": [gx - LEGEND_PAD_PX, gy - LEGEND_ARC_PX, gx + goal_w + LEGEND_PAD_PX, gy + goal_h + LEGEND_PAD_PX],
            "rule_before": [rx, ry, rx + before_w, ry + goal_h],
            "rule_mid": [mid_x, ry, mid_x + mid_w, ry + goal_h],
            "rule_after": [after_x, ry, after_x + after_w, ry + goal_h],
            "rule_panel": [rx - LEGEND_PAD_PX, ry - LEGEND_ARC_PX, rx + rule_w + LEGEND_PAD_PX, ry + goal_h + LEGEND_PAD_PX],
            "mini_cell_px": MINI_CELL_PX,
        }

    # ---------------- semantics ----------------

    def fold_snapshot_for_units(self, scene: FoldScene, units: float) -> dict:
        """Sheet, crease and flap angle at a real-valued fold position.

        ``units`` runs from 0 to ``len(scene.folds)``; the fractional part is
        the flap's rotation from flat (0) to landed (pi).
        """
        total = len(scene.folds)
        units = max(0.0, min(float(total), units))
        completed = min(total, int(units))
        progress = units - completed
        if completed >= total:
            return {
                "units": float(total), "fold_index": None, "fold": None, "progress": 0.0,
                "angle": 0.0, "state": scene.states[total], "extent": scene.states[total].extent,
            }
        return {
            "units": units, "fold_index": completed, "fold": scene.folds[completed],
            "progress": progress, "angle": progress * math.pi,
            "state": scene.states[completed], "extent": scene.states[completed].extent,
        }

    def semantic_snapshot(self, scene: FoldScene, frame: int) -> dict:
        timeline = scene.plan.timeline
        total = len(scene.folds)
        if frame < timeline["reveal_start"]:
            # Pre-reveal frames read the flat sheet only; they never consult
            # scene.folds or any state past the initial one.
            return {
                "units": 0.0, "fold_index": None, "fold": None, "progress": 0.0,
                "angle": 0.0, "state": scene.states[0], "extent": scene.states[0].extent,
                "solved": False,
            }
        if frame >= timeline["solve_end"]:
            return {**self.fold_snapshot_for_units(scene, float(total)), "solved": True}
        span = max(1, timeline["solve"])
        units = (frame - timeline["reveal_start"] + 1) / span * total
        return {**self.fold_snapshot_for_units(scene, units), "solved": False}

    def fold_units(self, scene: FoldScene, frame: int) -> float:
        return self.semantic_snapshot(scene, frame)["units"]

    # ---------------- drawing ----------------

    def _cell_colour(self, value: int) -> RGB:
        return FOLD_VISUAL_ROLES["colour"] if value == COLOURED else FOLD_VISUAL_ROLES["paper"]

    def _outline(self, buf: bytearray, box: tuple[float, float, float, float], colour: RGB, thickness: int) -> None:
        x0, y0, x1, y1 = box
        for a, b in (((x0, y0), (x1, y0)), ((x0, y1), (x1, y1)), ((x0, y0), (x0, y1)), ((x1, y0), (x1, y1))):
            _line(buf, self.width, self.height, a, b, colour, thickness)

    def _draw_stacked_cell(self, buf: bytearray, left: float, top: float, size: float, depth: int, value: int, fade: float, gap: int = CELL_GAP, inset: int = STACK_INSET_PX) -> None:
        """One resting cell.

        The layer count is drawn as nested inner outlines - a stack seen from
        directly above - so depth never rides on colour alone and never spills
        into the neighbouring cell.
        """
        _rect(buf, self.width, self.height, int(left + gap), int(top + gap),
              int(left + size - gap), int(top + size - gap), _blend(self._cell_colour(value), fade))
        edge = _blend(FOLD_VISUAL_ROLES["paper_edge"], fade)
        box = (left + gap, top + gap, left + size - gap, top + size - gap)
        self._outline(buf, box, edge, 3)
        steps = max(0, min(MAX_DRAWN_STACK_STEPS, depth - 1))
        stack_edge = _blend(FOLD_VISUAL_ROLES["stack_edge"], fade)
        for index in range(1, steps + 1):
            shrink = index * inset
            self._outline(
                buf,
                (box[0] + shrink, box[1] + shrink, box[2] - shrink, box[3] - shrink),
                stack_edge, 2,
            )

    def _draw_sheet(self, buf: bytearray, scene: FoldScene, state: FoldState, fade: float, skip: tuple[int, int, int] | None = None) -> None:
        x0, y0, x1, y1 = state.extent
        for y in range(y0, y1):
            for x in range(x0, x1):
                if skip is not None:
                    axis, lo, hi = skip
                    coordinate = x if axis == 0 else y
                    if lo <= coordinate < hi:
                        continue
                left, top = self.world(scene, x, y)
                self._draw_stacked_cell(buf, left, top, self.cell, state.depth_at((x, y)), visible_value(state, (x, y)), fade)

    def _flap_point(self, scene: FoldScene, fold: Fold, angle: float, gx: float, gy: float, span: tuple[float, float], reach: float) -> tuple[float, float]:
        axis, line, _ = fold
        cosine, sine = math.cos(angle), math.sin(angle)
        centre = (span[0] + span[1]) / 2
        if axis == 0:
            offset = gx - line
            lift = abs(offset) * sine
            scale = 1.0 + LIFT_GAIN * (lift / max(1e-6, reach))
            px, _ = self.world(scene, line + offset * cosine, 0)
            _, py = self.world(scene, 0, centre + (gy - centre) * scale)
            return px, py
        offset = gy - line
        lift = abs(offset) * sine
        scale = 1.0 + LIFT_GAIN * (lift / max(1e-6, reach))
        _, py = self.world(scene, 0, line + offset * cosine)
        px, _ = self.world(scene, centre + (gx - centre) * scale, 0)
        return px, py

    def _draw_flap(self, buf: bytearray, scene: FoldScene, snapshot: dict) -> None:
        fold = snapshot["fold"]
        if fold is None:
            return
        axis, line, direction = fold
        state = snapshot["state"]
        angle = snapshot["angle"]
        x0, y0, x1, y1 = state.extent
        lo, hi = (x0, x1) if axis == 0 else (y0, y1)
        stay_lo, stay_hi = fold_result_extent(lo, hi, line, direction)
        moving = [c for c in range(lo, hi) if not stay_lo <= c < stay_hi]
        if not moving:
            return
        span = (y0, y1) if axis == 0 else (x0, x1)
        reach = max(abs(c - line) for c in moving) + 1
        # Far cells first so nearer flap cells overlap them correctly.
        order = sorted(moving, key=lambda c: -abs(c - line))
        cross = range(y0, y1) if axis == 0 else range(x0, x1)
        for coordinate in order:
            for other in cross:
                cell = (coordinate, other) if axis == 0 else (other, coordinate)
                value = visible_value(state, cell)
                if value == NO_PAPER:
                    continue
                gx, gy = cell
                corners = tuple(
                    self._flap_point(scene, fold, angle, gx + dx, gy + dy, span, reach)
                    for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))
                )
                _quad(buf, self.width, self.height, corners, self._cell_colour(value))
                for a, b in zip(corners, corners[1:] + corners[:1]):
                    _line(buf, self.width, self.height, a, b, FOLD_VISUAL_ROLES["paper_edge"], 3)
        if axis == 0:
            a = self.world(scene, line, y0)
            b = self.world(scene, line, y1)
        else:
            a = self.world(scene, x0, line)
            b = self.world(scene, x1, line)
        _line(buf, self.width, self.height, a, b, FOLD_VISUAL_ROLES["crease"], 5)

    def _dashed_rect(self, buf: bytearray, box: tuple[float, float, float, float], colour: RGB, thickness: int = 5, dash: int = 14, gap: int = 10) -> None:
        left, top, right, bottom = box

        def run(a: float, b: float, fixed: float, horizontal: bool) -> None:
            position = a
            while position < b:
                stop = min(b, position + dash)
                if horizontal:
                    _line(buf, self.width, self.height, (position, fixed), (stop, fixed), colour, thickness)
                else:
                    _line(buf, self.width, self.height, (fixed, position), (fixed, stop), colour, thickness)
                position = stop + gap
        run(left, right, top, True)
        run(left, right, bottom, True)
        run(top, bottom, left, False)
        run(top, bottom, right, False)

    def _draw_mini_cell(self, buf: bytearray, left: int, top: int, size: int, value: int, fade: float, depth: int = 1) -> None:
        self._draw_stacked_cell(buf, left, top, size, depth, value, fade, gap=2, inset=4)

    def _arc_arrow(self, buf: bytearray, cx: float, cy: float, radius: float, colour: RGB) -> None:
        """Half turn drawn right-to-left above a tile; the font has no arc glyph."""
        points = []
        segments = 18
        for index in range(segments + 1):
            theta = math.pi * index / segments
            points.append((cx + radius * math.cos(theta), cy - radius * math.sin(theta) * 0.72))
        for a, b in zip(points, points[1:]):
            _line(buf, self.width, self.height, a, b, colour, 4)
        tip = points[-1]
        before = points[-3]
        dx, dy = tip[0] - before[0], tip[1] - before[1]
        length = max(1e-6, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        for sign in (1, -1):
            _line(
                buf, self.width, self.height, tip,
                (tip[0] - ux * 12 - sign * uy * 9, tip[1] - uy * 12 + sign * ux * 9),
                colour, 4,
            )

    def _draw_legend(self, buf: bytearray, scene: FoldScene, fade: float) -> None:
        """Goal tile and fold-rule tile. Reads the puzzle only, never the folds."""
        geometry = self.legend_geometry(scene)
        panel = _blend(FOLD_VISUAL_ROLES["legend_panel"], fade)
        _rect(buf, self.width, self.height, *geometry["goal_panel"], panel)
        _rect(buf, self.width, self.height, *geometry["rule_panel"], panel)
        gx, gy, gx1, gy1 = geometry["goal_tile"]
        white = _blend(WHITE, fade)
        _text(buf, self.width, self.height, (gx + gx1) // 2 - 34, gy - LEGEND_ARC_PX + 4, "GOAL",
              _blend(FOLD_VISUAL_ROLES["target"], fade), 3)
        for row in range(2):
            for column in range(3):
                self._draw_mini_cell(buf, gx + column * MINI_CELL_PX, gy + row * MINI_CELL_PX, MINI_CELL_PX, COLOURED, fade, 2)
        self._dashed_rect(buf, (gx - 6, gy - 6, gx1 + 6, gy1 + 6), _blend(FOLD_VISUAL_ROLES["target"], fade), 4, 10, 7)
        rx, ry, rx1, ry1 = geometry["rule_before"]
        _text(buf, self.width, self.height, geometry["rule_mid"][0] - 10, ry - LEGEND_ARC_PX + 4, "FOLD", white, 3)
        crease = _blend(FOLD_VISUAL_ROLES["crease"], fade)
        for row in range(2):
            self._draw_mini_cell(buf, rx, ry + row * MINI_CELL_PX, MINI_CELL_PX, 1, fade)
            self._draw_mini_cell(buf, rx + MINI_CELL_PX, ry + row * MINI_CELL_PX, MINI_CELL_PX, COLOURED, fade)
        _line(buf, self.width, self.height, (rx + MINI_CELL_PX, ry), (rx + MINI_CELL_PX, ry1), crease, 3)
        self._arc_arrow(buf, rx + MINI_CELL_PX, ry - 6, MINI_CELL_PX, _blend(WHITE, fade))
        # Middle stage: the same flap caught standing on its crease. Without it
        # a blind grader read the arc as a rotation rather than a fold.
        mx, my, _, my1 = geometry["rule_mid"]
        for row in range(2):
            self._draw_mini_cell(buf, mx, my + row * MINI_CELL_PX, MINI_CELL_PX, 1, fade)
        hinge = mx + MINI_CELL_PX
        _quad(
            buf, self.width, self.height,
            ((hinge, my + 4), (hinge + STANDING_FLAP_PX, my - 6),
             (hinge + STANDING_FLAP_PX, my1 + 6), (hinge, my1 - 4)),
            _blend(FOLD_VISUAL_ROLES["colour"], fade),
        )
        _line(buf, self.width, self.height, (hinge, my), (hinge, my1), crease, 3)
        ax, ay = geometry["rule_after"][0], geometry["rule_after"][1]
        for row in range(2):
            self._draw_mini_cell(buf, ax, ay + row * MINI_CELL_PX, MINI_CELL_PX, COLOURED, fade, 2)
        arrow_y = (ry + ry1) / 2
        for start, end in ((rx1, mx), (geometry["rule_mid"][2], ax)):
            _line(buf, self.width, self.height, (start + 6, arrow_y), (end - 6, arrow_y), white, 4)
            _line(buf, self.width, self.height, (end - 6, arrow_y), (end - 16, arrow_y - 8), white, 4)
            _line(buf, self.width, self.height, (end - 6, arrow_y), (end - 16, arrow_y + 8), white, 4)

    def render_frame(self, scene: FoldScene, frame: int) -> bytes:
        timeline = scene.plan.timeline
        if not 0 <= frame < timeline["total"]:
            raise ValueError("frame out of range")
        buf = bytearray(bytes(FOLD_VISUAL_ROLES["background"]) * self.width * self.height)
        appearance = max(1, timeline["appearance"])
        fade = min(1.0, frame / appearance)
        _rect(buf, self.width, self.height, *PANEL_BOX, _blend(FOLD_VISUAL_ROLES["panel"], fade))
        # The title is drawn without fade so frame zero is never blank.
        _text(buf, self.width, self.height, TITLE_ORIGIN[0], TITLE_ORIGIN[1], TITLE, WHITE, 4)
        bx0, by0, bx1, by1 = self.board_extent(scene)
        _rect(buf, self.width, self.height, bx0, by0, bx1, by1, _blend(FOLD_VISUAL_ROLES["board"], fade))
        snapshot = self.semantic_snapshot(scene, frame)
        state = snapshot["state"]
        fold = snapshot["fold"]
        if fold is None:
            self._draw_sheet(buf, scene, state, fade)
        else:
            axis, line, direction = fold
            x0, y0, x1, y1 = state.extent
            lo, hi = (x0, x1) if axis == 0 else (y0, y1)
            stay = fold_result_extent(lo, hi, line, direction)
            moving_lo, moving_hi = (stay[1], hi) if stay[0] == lo else (lo, stay[0])
            self._draw_sheet(buf, scene, state, fade, skip=(axis, moving_lo, moving_hi))
            self._draw_flap(buf, scene, snapshot)
        self._dashed_rect(buf, self.target_box(scene), _blend(FOLD_VISUAL_ROLES["target"], fade))
        self._draw_legend(buf, scene, fade)

        reveal, solve_end, result_end = timeline["reveal_start"], timeline["solve_end"], timeline["result_end"]
        if frame < reveal:
            phase = "THINK"
            progress = max(0.0, (frame - timeline["appearance"]) / max(1, timeline["thinking"]))
            px0, py0, px1, py1 = PROGRESS_BAR
            _rect(buf, self.width, self.height, px0, py0, px1, py1, MUTED)
            _rect(buf, self.width, self.height, px0, py0, px0 + int((px1 - px0) * min(1.0, progress)), py1,
                  FOLD_VISUAL_ROLES["progress"])
        elif frame < solve_end:
            phase = "FOLD"
        elif frame < result_end:
            phase = "FULL"
            left, top, right, bottom = self.target_box(scene)
            inset = 10 + int(6 * (0.5 + 0.5 * math.sin((frame - solve_end) * math.pi / 5)))
            ring = FOLD_VISUAL_ROLES["target"]
            x0, y0, x1, y1 = left - inset, top - inset, right + inset, bottom + inset
            _rect(buf, self.width, self.height, int(x0), int(y0), int(x1), int(y0) + 5, ring)
            _rect(buf, self.width, self.height, int(x0), int(y1) - 5, int(x1), int(y1), ring)
            _rect(buf, self.width, self.height, int(x0), int(y0), int(x0) + 5, int(y1), ring)
            _rect(buf, self.width, self.height, int(x1) - 5, int(y0), int(x1), int(y1), ring)
        else:
            phase = "CLEAR"
        label_width = len(phase) * 24 - 4
        _text(
            buf, self.width, self.height, (self.width - label_width) // 2, PHASE_BASELINE, phase,
            FOLD_VISUAL_ROLES["target"] if phase in {"FULL", "CLEAR"} else WHITE, 4,
        )
        return bytes(buf)

    def render(self, scene: FoldScene, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for frame in range(scene.plan.timeline["total"]):
            path = directory / f"frame_{frame:04d}.ppm"
            path.write_bytes(f"P6\n{self.width} {self.height}\n255\n".encode("ascii") + self.render_frame(scene, frame))
            paths.append(path)
        return paths
