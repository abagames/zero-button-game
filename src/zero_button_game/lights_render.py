from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

from .lights import (
    BOARD_HEIGHT, BOARD_WIDTH, LightsPuzzleSpec, LightsReplayTrace, LightsRules, cell_index, plus_cells,
)
from .models import PresentationPlan
from .render import BACKGROUND, MUTED, PANEL, WHITE, _blend, _circle, _line, _rect, _text

RGB = tuple[int, int, int]
Cell = tuple[int, int]

LIGHTS_VISUAL_ROLES = {
    "background": BACKGROUND,
    "panel": PANEL,
    "legend_panel": (25, 34, 45),
    "unlit": (34, 46, 59),
    "cell_edge": (150, 172, 192),
    "lit": (255, 209, 102),
    "cursor": (67, 217, 194),
    "focus": (67, 217, 194),
    "badge": (67, 217, 194),
    "badge_done": (41, 138, 126),
    "badge_text": (12, 18, 26),
    "pulse": (67, 217, 194),
    "goal_frame": (255, 209, 102),
}

CELL_PX = 96
BOARD_ORIGIN = (120, 96)
CELL_GAP = 5
CELL_EDGE_PX = 4
MINI_CELL_PX = 32
GOAL_TILE_ORIGIN = (96, 508)
RULE_TILE_ORIGIN = (300, 508)
RULE_TILE_ARROW_PX = 52
LEGEND_PAD_PX = 14
# Focus bracket: half-size in pixels at the start and at the end of a press.
# It closes in on the pressed cell instead of travelling between cells.
FOCUS_SPAN_START = 78
FOCUS_SPAN_END = 44
BADGE_SCALE = 3
BADGE_PAD_PX = 5
PROGRESS_BAR = (120, 626, 600, 636)
PANEL_BOX = (44, 70, 676, 660)
TITLE = "ALL LIGHTS ON"
TITLE_ORIGIN = (84, 30)
SAFE_AREA = (36, 36, 684, 684)
# The plus shown by the rule legend, in mini-tile coordinates.
LEGEND_PLUS = ((1, 1), (0, 1), (2, 1), (1, 0), (1, 2))


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


@dataclass(frozen=True)
class LightsScene:
    puzzle: LightsPuzzleSpec
    plan: PresentationPlan
    trace: LightsReplayTrace
    presses: tuple[Cell, ...]
    states: tuple[tuple[int, ...], ...]
    semantic_bounds: tuple[int, int, int, int]


class LightsSceneBuilder:
    def build(self, puzzle: LightsPuzzleSpec, plan: PresentationPlan, trace: LightsReplayTrace) -> LightsScene:
        rules = LightsRules()
        if not rules.is_goal(puzzle, trace.final):
            raise ValueError("lights scene requires a fully lit final board")
        presses = tuple(
            (step.action.params["cell"][0], step.action.params["cell"][1]) for step in trace.steps
        )
        if len(presses) < 2:
            raise ValueError("lights scene requires at least two presses")
        if len(set(presses)) != len(presses):
            raise ValueError("lights scene requires every press to be a distinct cell")
        states = (tuple(trace.initial.lights),) + tuple(tuple(step.after.lights) for step in trace.steps)
        return LightsScene(puzzle, plan, trace, presses, states, PANEL_BOX)


def alternate_lights_scene(scene: LightsScene) -> LightsScene:
    """Neutrality perturbation: cyclically shift the press order.

    Presses commute, so the shifted order is still a logically legal solution
    of the same puzzle. Every press is a distinct cell and there are at least
    two of them, so the shifted first press is always a different cell: the
    first indicated point is somewhere else and the reveal frame always changes.
    """
    shifted = scene.presses[1:] + scene.presses[:1]
    alternate_plan = replace(
        scene.plan, logical_steps=tuple(scene.plan.logical_steps[1:] + scene.plan.logical_steps[:1])
    )
    lights = list(scene.puzzle.initial)
    states = [tuple(lights)]
    for cell in shifted:
        for neighbour in plus_cells(scene.puzzle.width, scene.puzzle.height, cell):
            lights[cell_index(scene.puzzle.width, neighbour)] ^= 1
        states.append(tuple(lights))
    return replace(scene, plan=alternate_plan, presses=shifted, states=tuple(states))


class LightsRenderer:
    width = 720
    height = 720
    cell = CELL_PX

    # ---------------- geometry ----------------

    def board_origin(self, scene: LightsScene) -> tuple[int, int]:
        return BOARD_ORIGIN

    def board_extent(self, scene: LightsScene) -> tuple[int, int, int, int]:
        ox, oy = self.board_origin(scene)
        return ox, oy, ox + scene.puzzle.width * self.cell, oy + scene.puzzle.height * self.cell

    def cell_center(self, scene: LightsScene, cell: Cell) -> tuple[float, float]:
        ox, oy = self.board_origin(scene)
        return ox + (cell[0] + 0.5) * self.cell, oy + (cell[1] + 0.5) * self.cell

    def legend_geometry(self, scene: LightsScene) -> dict:
        """Legend layout. Depends on the puzzle only - never on the press set."""
        gx, gy = GOAL_TILE_ORIGIN
        rx, ry = RULE_TILE_ORIGIN
        span = 3 * MINI_CELL_PX
        rule_width = span + RULE_TILE_ARROW_PX + span
        return {
            "goal_tile": [gx, gy, gx + span, gy + span],
            "goal_panel": [gx - LEGEND_PAD_PX, gy - LEGEND_PAD_PX, gx + span + LEGEND_PAD_PX, gy + span + LEGEND_PAD_PX],
            "rule_before": [rx, ry, rx + span, ry + span],
            "rule_after": [rx + span + RULE_TILE_ARROW_PX, ry, rx + rule_width, ry + span],
            "rule_panel": [rx - LEGEND_PAD_PX, ry - LEGEND_PAD_PX, rx + rule_width + LEGEND_PAD_PX, ry + span + LEGEND_PAD_PX],
            "mini_cell_px": MINI_CELL_PX,
            "plus_cells": [list(item) for item in LEGEND_PLUS],
        }

    # ---------------- semantics ----------------

    def press_snapshot_for_units(self, scene: LightsScene, units: float) -> dict:
        """Board, focus marker and pulse state at a real-valued press position.

        The marker never travels: it is anchored on the cell being pressed and
        closes in on it, and every cell already pressed keeps a numbered badge.
        """
        total = len(scene.presses)
        units = max(0.0, min(float(total), units))
        pressed = min(total, int(units))
        progress = units - pressed
        if pressed >= total:
            return {
                "pressed": total, "press_index": None, "press_cell": None, "progress": 0.0,
                "lights": scene.states[total], "blend": {}, "focus": None, "focus_span": 0.0,
                "pulse_radius": 0.0,
            }
        cell = scene.presses[pressed]
        blend = {neighbour: progress for neighbour in plus_cells(scene.puzzle.width, scene.puzzle.height, cell)}
        return {
            "pressed": pressed, "press_index": pressed, "press_cell": cell, "progress": progress,
            "lights": scene.states[pressed], "blend": blend,
            "focus": self.cell_center(scene, cell),
            "focus_span": FOCUS_SPAN_START + (FOCUS_SPAN_END - FOCUS_SPAN_START) * progress,
            "pulse_radius": progress * self.cell,
        }

    def semantic_snapshot(self, scene: LightsScene, frame: int) -> dict:
        timeline = scene.plan.timeline
        total = len(scene.presses)
        if frame < timeline["reveal_start"]:
            # Pre-reveal frames never consult scene.presses or scene.states
            # beyond the puzzle's own initial board.
            return {
                "pressed": 0, "press_index": None, "press_cell": None, "progress": 0.0,
                "lights": tuple(scene.puzzle.initial), "blend": {}, "focus": None,
                "focus_span": 0.0, "pulse_radius": 0.0, "solved": False,
            }
        if frame >= timeline["solve_end"]:
            return {**self.press_snapshot_for_units(scene, float(total)), "solved": True}
        span = max(1, timeline["solve"] - 1)
        units = (frame - timeline["reveal_start"]) / span * total
        return {**self.press_snapshot_for_units(scene, units), "solved": False}

    def marker_signature(self, scene: LightsScene, frame: int) -> tuple:
        """Everything the point marker draws, quantised to whole pixels.

        This is the channel that carries the solve animation on its own now
        that nothing travels between cells: the focus bracket closes in, the
        cross pulse expands and the badge count grows, so the tuple changes on
        every single solve frame.
        """
        snapshot = self.semantic_snapshot(scene, frame)
        return (
            snapshot["pressed"], snapshot["press_index"], snapshot["press_cell"],
            int(round(snapshot["focus_span"])), int(round(snapshot["pulse_radius"])),
        )

    # ---------------- drawing ----------------

    def _draw_cell(self, buf: bytearray, left: int, top: int, size: int, colour: RGB, edge: RGB, gap: int, thickness: int) -> None:
        right, bottom = left + size, top + size
        _rect(buf, self.width, self.height, left + gap, top + gap, right - gap, bottom - gap, colour)
        _line(buf, self.width, self.height, (left + gap, top + gap), (right - gap, top + gap), edge, thickness)
        _line(buf, self.width, self.height, (left + gap, bottom - gap), (right - gap, bottom - gap), edge, thickness)
        _line(buf, self.width, self.height, (left + gap, top + gap), (left + gap, bottom - gap), edge, thickness)
        _line(buf, self.width, self.height, (right - gap, top + gap), (right - gap, bottom - gap), edge, thickness)

    def _draw_board(self, buf: bytearray, scene: LightsScene, snapshot: dict, fade: float) -> None:
        ox, oy = self.board_origin(scene)
        lights = snapshot["lights"]
        blend = snapshot.get("blend", {})
        edge = _blend(LIGHTS_VISUAL_ROLES["cell_edge"], fade)
        for y in range(scene.puzzle.height):
            for x in range(scene.puzzle.width):
                value = lights[y * scene.puzzle.width + x]
                base = LIGHTS_VISUAL_ROLES["lit"] if value else LIGHTS_VISUAL_ROLES["unlit"]
                target = LIGHTS_VISUAL_ROLES["unlit"] if value else LIGHTS_VISUAL_ROLES["lit"]
                colour = _mix(base, target, blend.get((x, y), 0.0))
                self._draw_cell(buf, ox + x * self.cell, oy + y * self.cell, self.cell,
                                _blend(colour, fade), edge, CELL_GAP, CELL_EDGE_PX)

    def _draw_mini(self, buf: bytearray, origin: tuple[int, int], lit: set[Cell], fade: float, press: Cell | None = None) -> None:
        ox, oy = origin
        edge = _blend(LIGHTS_VISUAL_ROLES["cell_edge"], fade)
        for y in range(3):
            for x in range(3):
                colour = LIGHTS_VISUAL_ROLES["lit"] if (x, y) in lit else LIGHTS_VISUAL_ROLES["unlit"]
                self._draw_cell(buf, ox + x * MINI_CELL_PX, oy + y * MINI_CELL_PX, MINI_CELL_PX,
                                _blend(colour, fade), edge, 2, 2)
        if press is not None:
            cx = ox + press[0] * MINI_CELL_PX + MINI_CELL_PX // 2
            cy = oy + press[1] * MINI_CELL_PX + MINI_CELL_PX // 2
            _circle(buf, self.width, self.height, cx, cy, MINI_CELL_PX // 4, _blend(LIGHTS_VISUAL_ROLES["cursor"], fade), True)
            _circle(buf, self.width, self.height, cx, cy, MINI_CELL_PX // 4 + 4, _blend(LIGHTS_VISUAL_ROLES["cursor"], fade), False, 3)

    def _draw_legend(self, buf: bytearray, scene: LightsScene, fade: float) -> None:
        """Rule and goal tiles. Reads the puzzle only, never the press set.

        Blind graders who were shown only pre-reveal frames recovered both the
        plus-toggle rule and the all-lit goal from these two tiles, and failed
        to recover the rule when they were removed; they are load-bearing, not
        decoration.
        """
        geometry = self.legend_geometry(scene)
        panel = _blend(LIGHTS_VISUAL_ROLES["legend_panel"], fade)
        _rect(buf, self.width, self.height, *geometry["goal_panel"], panel)
        _rect(buf, self.width, self.height, *geometry["rule_panel"], panel)
        self._draw_mini(buf, tuple(geometry["goal_tile"][:2]), {(x, y) for x in range(3) for y in range(3)}, fade)
        gx, gy, gx1, gy1 = geometry["goal_tile"]
        frame_colour = _blend(LIGHTS_VISUAL_ROLES["goal_frame"], fade)
        for x0, y0, x1, y1 in (
            (gx - LEGEND_PAD_PX, gy - LEGEND_PAD_PX, gx + 30, gy - LEGEND_PAD_PX + 5),
            (gx1 - 30, gy - LEGEND_PAD_PX, gx1 + LEGEND_PAD_PX, gy - LEGEND_PAD_PX + 5),
            (gx - LEGEND_PAD_PX, gy1 + LEGEND_PAD_PX - 5, gx + 30, gy1 + LEGEND_PAD_PX),
            (gx1 - 30, gy1 + LEGEND_PAD_PX - 5, gx1 + LEGEND_PAD_PX, gy1 + LEGEND_PAD_PX),
            (gx - LEGEND_PAD_PX, gy - LEGEND_PAD_PX, gx - LEGEND_PAD_PX + 5, gy + 30),
            (gx1 + LEGEND_PAD_PX - 5, gy - LEGEND_PAD_PX, gx1 + LEGEND_PAD_PX, gy + 30),
            (gx - LEGEND_PAD_PX, gy1 - 30, gx - LEGEND_PAD_PX + 5, gy1 + LEGEND_PAD_PX),
            (gx1 + LEGEND_PAD_PX - 5, gy1 - 30, gx1 + LEGEND_PAD_PX, gy1 + LEGEND_PAD_PX),
        ):
            _rect(buf, self.width, self.height, x0, y0, x1, y1, frame_colour)
        self._draw_mini(buf, tuple(geometry["rule_before"][:2]), set(), fade, press=(1, 1))
        self._draw_mini(buf, tuple(geometry["rule_after"][:2]), {tuple(item) for item in LEGEND_PLUS}, fade)
        before_right = geometry["rule_before"][2]
        arrow_y = geometry["rule_before"][1] + 3 * MINI_CELL_PX // 2
        tip = before_right + RULE_TILE_ARROW_PX - 10
        white = _blend(WHITE, fade)
        _line(buf, self.width, self.height, (before_right + 8, arrow_y), (tip, arrow_y), white, 5)
        _line(buf, self.width, self.height, (tip, arrow_y), (tip - 14, arrow_y - 12), white, 5)
        _line(buf, self.width, self.height, (tip, arrow_y), (tip - 14, arrow_y + 12), white, 5)

    def _badge_box(self, scene: LightsScene, cell: Cell, order: int) -> tuple[int, int, int, int]:
        """Box of the order badge pinned to a pressed cell's top-left corner."""
        label = str(order)
        text_width = 6 * BADGE_SCALE * len(label) - BADGE_SCALE
        text_height = 7 * BADGE_SCALE
        ox, oy = self.board_origin(scene)
        left = ox + cell[0] * self.cell + CELL_GAP + CELL_EDGE_PX + 4
        top = oy + cell[1] * self.cell + CELL_GAP + CELL_EDGE_PX + 4
        return left, top, left + text_width + 2 * BADGE_PAD_PX, top + text_height + 2 * BADGE_PAD_PX

    def _draw_badge(self, buf: bytearray, scene: LightsScene, cell: Cell, order: int, colour: RGB) -> None:
        x0, y0, x1, y1 = self._badge_box(scene, cell, order)
        _rect(buf, self.width, self.height, x0, y0, x1, y1, colour)
        _text(
            buf, self.width, self.height, x0 + BADGE_PAD_PX, y0 + BADGE_PAD_PX, str(order),
            LIGHTS_VISUAL_ROLES["badge_text"], BADGE_SCALE,
        )

    def _draw_marker_and_pulse(self, buf: bytearray, scene: LightsScene, snapshot: dict) -> None:
        """Point-by-point presentation: no travel, only anchored indications.

        Every cell already pressed keeps a dimmed numbered badge, so the whole
        press set is readable as an ordered list of points. The cell being
        pressed carries a bright badge, a bracket that closes in on it and the
        cross pulse of the toggle it triggers.
        """
        pressed = snapshot.get("pressed", 0)
        for order in range(pressed):
            self._draw_badge(buf, scene, scene.presses[order], order + 1, LIGHTS_VISUAL_ROLES["badge_done"])
        cell = snapshot.get("press_cell")
        if cell is None:
            return
        index = snapshot["press_index"]
        px, py = self.cell_center(scene, cell)
        colour = LIGHTS_VISUAL_ROLES["focus"]
        # Cross pulse: grows from the pressed cell out over its plus neighbours.
        radius = snapshot.get("pulse_radius", 0.0)
        if radius > 0:
            inner = max(40.0, radius * 0.45)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                _line(
                    buf, self.width, self.height,
                    (px + dx * inner, py + dy * inner), (px + dx * radius, py + dy * radius),
                    LIGHTS_VISUAL_ROLES["pulse"], 7,
                )
        # Focus bracket: four corners closing in on the pressed cell.
        span = snapshot.get("focus_span", 0.0)
        arm = max(10, int(round(span * 0.42)))
        cx, cy = int(round(px)), int(round(py))
        half = int(round(span))
        for sx in (-1, 1):
            for sy in (-1, 1):
                corner_x, corner_y = cx + sx * half, cy + sy * half
                _line(buf, self.width, self.height, (corner_x, corner_y), (corner_x - sx * arm, corner_y), colour, 7)
                _line(buf, self.width, self.height, (corner_x, corner_y), (corner_x, corner_y - sy * arm), colour, 7)
        _circle(buf, self.width, self.height, cx, cy, 13, colour, False, 5)
        _circle(buf, self.width, self.height, cx, cy, 6, WHITE, True)
        self._draw_badge(buf, scene, cell, index + 1, LIGHTS_VISUAL_ROLES["badge"])

    def render_frame(self, scene: LightsScene, frame: int) -> bytes:
        timeline = scene.plan.timeline
        if not 0 <= frame < timeline["total"]:
            raise ValueError("frame out of range")
        buf = bytearray(bytes(LIGHTS_VISUAL_ROLES["background"]) * self.width * self.height)
        appearance = max(1, timeline["appearance"])
        fade = min(1.0, frame / appearance)
        _rect(buf, self.width, self.height, *PANEL_BOX, _blend(LIGHTS_VISUAL_ROLES["panel"], fade))
        # The title is drawn without fade so frame zero is never blank.
        _text(buf, self.width, self.height, TITLE_ORIGIN[0], TITLE_ORIGIN[1], TITLE, WHITE, 4)
        snapshot = self.semantic_snapshot(scene, frame)
        self._draw_board(buf, scene, snapshot, fade)
        self._draw_legend(buf, scene, fade)
        self._draw_marker_and_pulse(buf, scene, snapshot)

        reveal, solve_end, result_end = timeline["reveal_start"], timeline["solve_end"], timeline["result_end"]
        if frame < reveal:
            phase = "THINK"
            progress = max(0.0, (frame - timeline["appearance"]) / max(1, timeline["thinking"]))
            x0, y0, x1, y1 = PROGRESS_BAR
            _rect(buf, self.width, self.height, x0, y0, x1, y1, MUTED)
            _rect(buf, self.width, self.height, x0, y0, x0 + int((x1 - x0) * min(1.0, progress)), y1,
                  LIGHTS_VISUAL_ROLES["cursor"])
        elif frame < solve_end:
            phase = "PRESS"
        elif frame < result_end:
            phase = "LIT"
            # A ring hugging the board outline: the fully lit board is what the
            # result phase is about.
            bx0, by0, bx1, by1 = self.board_extent(scene)
            inset = 10 + int(6 * (0.5 + 0.5 * math.sin((frame - solve_end) * math.pi / 5)))
            x0, y0, x1, y1 = bx0 - inset, by0 - inset, bx1 + inset, by1 + inset
            ring = LIGHTS_VISUAL_ROLES["lit"]
            _rect(buf, self.width, self.height, x0, y0, x1, y0 + 5, ring)
            _rect(buf, self.width, self.height, x0, y1 - 5, x1, y1, ring)
            _rect(buf, self.width, self.height, x0, y0, x0 + 5, y1, ring)
            _rect(buf, self.width, self.height, x1 - 5, y0, x1, y1, ring)
        else:
            phase = "CLEAR"
        label_width = len(phase) * 24 - 4
        _text(
            buf, self.width, self.height, (self.width - label_width) // 2, 674, phase,
            LIGHTS_VISUAL_ROLES["lit"] if phase in {"LIT", "CLEAR"} else WHITE, 4,
        )
        return bytes(buf)

    def render(self, scene: LightsScene, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for frame in range(scene.plan.timeline["total"]):
            path = directory / f"frame_{frame:04d}.ppm"
            path.write_bytes(f"P6\n{self.width} {self.height}\n255\n".encode("ascii") + self.render_frame(scene, frame))
            paths.append(path)
        return paths
