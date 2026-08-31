from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

from .models import PresentationPlan
from .parking import HORIZONTAL, ParkingPuzzleSpec, ParkingReplayTrace, ParkingRules
from .render import BACKGROUND, MUTED, PANEL, STRUCTURE, WHITE, _blend, _circle, _line, _rect, _text

RGB = tuple[int, int, int]
PARKING_VISUAL_ROLES = {
    "background": BACKGROUND,
    "panel": PANEL,
    "structure": STRUCTURE,
    "lot": (34, 46, 59),
    "target": (67, 217, 194),
    "blocker": (126, 148, 168),
    "blocker_edge": (176, 194, 210),
    "exit": (255, 209, 102),
    "focus": (255, 209, 102),
}
CELL_MARGIN = 9
EXIT_GAP_PX = 30


@dataclass(frozen=True)
class ParkingScene:
    puzzle: ParkingPuzzleSpec
    plan: PresentationPlan
    trace: ParkingReplayTrace
    moves: tuple[tuple[int, int], ...]
    exit_row: int
    semantic_bounds: tuple[int, int, int, int]


class ParkingSceneBuilder:
    def build(self, puzzle: ParkingPuzzleSpec, plan: PresentationPlan, trace: ParkingReplayTrace) -> ParkingScene:
        rules = ParkingRules()
        if not rules.is_goal(puzzle, trace.final):
            raise ValueError("parking scene requires a cleared final state")
        moves = tuple(
            (step.action.params["vehicle"][0], step.action.params["delta"][0])
            for step in trace.steps
        )
        if len(moves) < 2:
            raise ValueError("parking scene requires at least two moves")
        return ParkingScene(puzzle, plan, trace, moves, rules.exit_row(puzzle), (84, 88, 636, 666))


def alternate_parking_scene(scene: ParkingScene) -> ParkingScene:
    """Neutrality perturbation: cyclically shift the move order.

    A cyclic shift is used instead of a reversal because reversing can leave the
    same vehicle first and last, which would keep the reveal frame identical.
    On a minimal path no two consecutive moves share a vehicle, so the shifted
    first move always belongs to a different vehicle.
    """
    shifted = scene.moves[1:] + scene.moves[:1]
    alternate_plan = replace(scene.plan, logical_steps=tuple(scene.plan.logical_steps[1:] + scene.plan.logical_steps[:1]))
    return replace(scene, plan=alternate_plan, moves=shifted)


class ParkingRenderer:
    width = 720
    height = 720
    board_left = 120
    board_top = 126
    board_size = 480

    def cell_size(self, scene: ParkingScene) -> float:
        return self.board_size / scene.puzzle.width

    def position_snapshot_for_units(self, scene: ParkingScene, units: float) -> dict:
        offsets = [0.0] * len(scene.puzzle.vehicles)
        completed = 0
        for vehicle_id, delta in scene.moves:
            try:
                index = scene.puzzle.index_of(vehicle_id)
            except ValueError:
                continue
            span = abs(delta)
            if units >= span:
                offsets[index] += delta
                units -= span
                completed += 1
                continue
            sign = 1.0 if delta > 0 else -1.0
            offsets[index] += units * sign
            return {
                "offsets": tuple(offsets), "moving_vehicle": index,
                "moving_delta": delta, "completed_moves": completed,
            }
        return {"offsets": tuple(offsets), "moving_vehicle": None, "moving_delta": 0, "completed_moves": completed}

    def semantic_snapshot(self, scene: ParkingScene, frame: int) -> dict:
        timeline = scene.plan.timeline
        total_units = sum(abs(delta) for _, delta in scene.moves)
        if frame < timeline["reveal_start"]:
            return {
                "offsets": (0.0,) * len(scene.puzzle.vehicles), "moving_vehicle": None,
                "moving_delta": 0, "completed_moves": 0, "released": False,
            }
        if frame >= timeline["solve_end"] or total_units == 0:
            snapshot = self.position_snapshot_for_units(scene, float(total_units))
            return {**snapshot, "released": True}
        span = max(1, timeline["solve"] - 1)
        units = max(0.0, min(float(total_units), (frame - timeline["reveal_start"]) / span * total_units))
        return {**self.position_snapshot_for_units(scene, units), "released": False}

    def _vehicle_box(self, scene: ParkingScene, index: int, offset: float) -> tuple[float, float, float, float]:
        x, y, length, orientation, _ = scene.puzzle.vehicles[index]
        size = self.cell_size(scene)
        if orientation == HORIZONTAL:
            left = self.board_left + (x + offset) * size
            top = self.board_top + y * size
            return left, top, left + length * size, top + size
        left = self.board_left + x * size
        top = self.board_top + (y + offset) * size
        return left, top, left + size, top + length * size

    def _draw_vehicle(self, buf: bytearray, scene: ParkingScene, index: int, offset: float, fade: float, focus: bool) -> None:
        left, top, right, bottom = self._vehicle_box(scene, index, offset)
        is_target = scene.puzzle.vehicles[index][4] == scene.puzzle.target_id
        body = PARKING_VISUAL_ROLES["target"] if is_target else PARKING_VISUAL_ROLES["blocker"]
        edge = WHITE if is_target else PARKING_VISUAL_ROLES["blocker_edge"]
        x0, y0 = int(left) + CELL_MARGIN, int(top) + CELL_MARGIN
        x1, y1 = int(right) - CELL_MARGIN, int(bottom) - CELL_MARGIN
        corner = 6
        _rect(buf, self.width, self.height, x0 + corner, y0, x1 - corner, y1, _blend(body, fade))
        _rect(buf, self.width, self.height, x0, y0 + corner, x1, y1 - corner, _blend(body, fade))
        # Orientation axis: the shape itself, not colour alone, tells the axis.
        horizontal = scene.puzzle.vehicles[index][3] == HORIZONTAL
        if horizontal:
            middle = (y0 + y1) // 2
            _line(buf, self.width, self.height, (x0 + 12, middle), (x1 - 12, middle), _blend(edge, fade), 5)
        else:
            middle = (x0 + x1) // 2
            _line(buf, self.width, self.height, (middle, y0 + 12), (middle, y1 - 12), _blend(edge, fade), 5)
        if focus:
            _rect(buf, self.width, self.height, x0 - 5, y0 - 5, x1 + 5, y0 - 1, PARKING_VISUAL_ROLES["focus"])
            _rect(buf, self.width, self.height, x0 - 5, y1 + 1, x1 + 5, y1 + 5, PARKING_VISUAL_ROLES["focus"])
            _rect(buf, self.width, self.height, x0 - 5, y0 - 5, x0 - 1, y1 + 5, PARKING_VISUAL_ROLES["focus"])
            _rect(buf, self.width, self.height, x1 + 1, y0 - 5, x1 + 5, y1 + 5, PARKING_VISUAL_ROLES["focus"])

    def render_frame(self, scene: ParkingScene, frame: int) -> bytes:
        timeline = scene.plan.timeline
        if not 0 <= frame < timeline["total"]:
            raise ValueError("frame out of range")
        buf = bytearray(bytes(PARKING_VISUAL_ROLES["background"]) * self.width * self.height)
        appearance = max(1, timeline["appearance"])
        fade = min(1.0, frame / appearance)
        panel = _blend(PARKING_VISUAL_ROLES["panel"], fade)
        structure = _blend(PARKING_VISUAL_ROLES["structure"], fade)
        _rect(buf, self.width, self.height, 84, 88, 636, 666, panel)
        # Title is drawn without fade so frame zero is never blank.
        _text(buf, self.width, self.height, 102, 38, "GET THE CAR OUT", WHITE, 4)
        size = self.cell_size(scene)
        snapshot = self.semantic_snapshot(scene, frame)
        for y in range(scene.puzzle.height):
            for x in range(scene.puzzle.width):
                left = int(self.board_left + x * size)
                top = int(self.board_top + y * size)
                _rect(buf, self.width, self.height, left + 2, top + 2, int(left + size - 2), int(top + size - 2), _blend(PARKING_VISUAL_ROLES["lot"], fade))
        board_right = self.board_left + self.board_size
        board_bottom = self.board_top + self.board_size
        exit_top = self.board_top + scene.exit_row * size
        exit_bottom = exit_top + size
        _line(buf, self.width, self.height, (self.board_left, self.board_top), (board_right, self.board_top), structure, 5)
        _line(buf, self.width, self.height, (self.board_left, board_bottom), (board_right, board_bottom), structure, 5)
        _line(buf, self.width, self.height, (self.board_left, self.board_top), (self.board_left, board_bottom), structure, 5)
        _line(buf, self.width, self.height, (board_right, self.board_top), (board_right, exit_top + 4), structure, 5)
        _line(buf, self.width, self.height, (board_right, exit_bottom - 4), (board_right, board_bottom), structure, 5)
        # The east exit is a physical break in the rim, marked amber.
        _rect(buf, self.width, self.height, int(board_right) - 3, int(exit_top) + 4, int(board_right) + 22, int(exit_top) + 10, PARKING_VISUAL_ROLES["exit"])
        _rect(buf, self.width, self.height, int(board_right) - 3, int(exit_bottom) - 10, int(board_right) + 22, int(exit_bottom) - 4, PARKING_VISUAL_ROLES["exit"])
        target_index = scene.puzzle.index_of(scene.puzzle.target_id)
        for index in range(len(scene.puzzle.vehicles)):
            if index == target_index:
                continue
            self._draw_vehicle(buf, scene, index, snapshot["offsets"][index], fade, snapshot["moving_vehicle"] == index)
        self._draw_vehicle(buf, scene, target_index, snapshot["offsets"][target_index], fade, snapshot["moving_vehicle"] == target_index)
        _text(buf, self.width, self.height, 604, int(exit_top) + int(size / 2) - 10, "EXIT", PARKING_VISUAL_ROLES["exit"], 2)

        reveal, solve_end, result_end = timeline["reveal_start"], timeline["solve_end"], timeline["result_end"]
        if frame < reveal:
            phase = "THINK"
            progress = max(0.0, (frame - timeline["appearance"]) / max(1, timeline["thinking"]))
            _rect(buf, self.width, self.height, 120, 630, 600, 640, MUTED)
            _rect(buf, self.width, self.height, 120, 630, 120 + int(480 * min(1.0, progress)), 640, PARKING_VISUAL_ROLES["target"])
        elif frame < solve_end:
            phase = "SLIDE"
        elif frame < result_end:
            phase = "EXIT"
            pulse = 18 + int(9 * (0.5 + 0.5 * math.sin((frame - solve_end) * math.pi / 5)))
            _circle(buf, self.width, self.height, int(board_right) + 12, int((exit_top + exit_bottom) / 2), pulse, PARKING_VISUAL_ROLES["exit"], False, 4)
        else:
            phase = "CLEAR"
        label_width = len(phase) * 24 - 4
        _text(buf, self.width, self.height, (self.width - label_width) // 2, 674, phase, PARKING_VISUAL_ROLES["exit"] if phase in {"EXIT", "CLEAR"} else WHITE, 4)
        return bytes(buf)

    def render(self, scene: ParkingScene, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for frame in range(scene.plan.timeline["total"]):
            path = directory / f"frame_{frame:04d}.ppm"
            path.write_bytes(f"P6\n{self.width} {self.height}\n255\n".encode("ascii") + self.render_frame(scene, frame))
            paths.append(path)
        return paths
