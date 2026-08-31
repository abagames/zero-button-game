from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

from .models import PresentationPlan
from .pipes import DIRECTIONS, PipePuzzleSpec, PipeReplayTrace, PipeRules
from .render import BACKGROUND, MUTED, PANEL, STRUCTURE, WHITE, _blend, _circle, _diamond, _line, _rect, _text

RGB = tuple[int, int, int]
PIPE_VISUAL_ROLES = {
    "background": BACKGROUND,
    "panel": PANEL,
    "structure": STRUCTURE,
    "source": (67, 217, 194),
    "goal": (255, 209, 102),
    "flow_outer": (67, 217, 194),
    "flow_inner": (245, 248, 250),
    "rotation_focus": (255, 209, 102),
}


@dataclass(frozen=True)
class PipeScene:
    puzzle: PipePuzzleSpec
    plan: PresentationPlan
    trace: PipeReplayTrace
    flow_order: tuple[tuple[int, int], ...]
    semantic_bounds: tuple[int, int, int, int]


class PipeSceneBuilder:
    def build(self, puzzle: PipePuzzleSpec, plan: PresentationPlan, trace: PipeReplayTrace) -> PipeScene:
        rules = PipeRules()
        if not rules.is_goal(puzzle, trace.final):
            raise ValueError("pipes scene requires a solved final state")
        order = rules.connected_path(puzzle, trace.final)
        if not order or order[-1] != puzzle.sink:
            raise ValueError("flow path does not reach GOAL")
        return PipeScene(puzzle, plan, trace, order, (84, 88, 636, 666))


def alternate_pipe_scene(scene: PipeScene) -> PipeScene:
    # Only used by the pre-reveal neutrality oracle. The initial puzzle is the
    # same while the solution-dependent ordering is deliberately perturbed.
    alternate_plan = replace(scene.plan, logical_steps=tuple(reversed(scene.plan.logical_steps)))
    return replace(scene, plan=alternate_plan, flow_order=tuple(reversed(scene.flow_order)))


class PipeRenderer:
    width = 720
    height = 720
    board_left = 120
    board_top = 126
    board_size = 480

    def _center(self, scene: PipeScene, cell: tuple[int, int]) -> tuple[float, float]:
        size = self.board_size / scene.puzzle.width
        return self.board_left + (cell[0] + 0.5) * size, self.board_top + (cell[1] + 0.5) * size

    def _rotation_snapshot(self, scene: PipeScene, frame: int) -> dict:
        timeline = scene.plan.timeline
        if frame < timeline["reveal_start"]:
            return {
                "rotations":(0,) * len(scene.puzzle.initial_masks),
                "current_piece":None, "current_delta_degrees":0.0,
                "completed_actions":0,
            }
        actions = scene.plan.logical_steps
        total_units = sum(abs(action.params["quarter_turns"][0]) for action in actions)
        if frame >= timeline["solve_end"] or total_units == 0:
            return self.rotation_snapshot_for_units(scene, float(total_units))
        fraction = (frame - timeline["reveal_start"]) / max(1, timeline["solve"])
        units = max(0.0, min(float(total_units), fraction * total_units))
        return self.rotation_snapshot_for_units(scene, units)

    def rotation_snapshot_for_units(self, scene: PipeScene, units: float) -> dict:
        rotations = [0] * len(scene.puzzle.initial_masks)
        actions = scene.plan.logical_steps
        completed = 0
        for action in actions:
            turns = action.params["quarter_turns"][0]
            duration = abs(turns)
            index = action.params["cell"][1] * scene.puzzle.width + action.params["cell"][0]
            if units >= duration:
                rotations[index] = (rotations[index] + turns) % 4
                units -= duration
                completed += 1
                continue
            return {
                "rotations":tuple(rotations), "current_piece":index,
                "current_delta_degrees":units * 90.0 * (1 if turns > 0 else -1), "completed_actions":completed,
            }
        return {"rotations":tuple(rotations),"current_piece":None,"current_delta_degrees":0.0,"completed_actions":completed}

    def _flow_fraction(self, scene: PipeScene, frame: int) -> float:
        timeline = scene.plan.timeline
        if frame < timeline["solve_end"]:
            return 0.0
        return min(1.0, (frame - timeline["solve_end"] + 1) / max(1, timeline["result"]))

    def semantic_snapshot(self, scene: PipeScene, frame: int) -> dict:
        rotation = self._rotation_snapshot(scene, frame)
        flow = self._flow_fraction(scene, frame)
        reached_count = min(len(scene.flow_order), int(math.ceil(flow * len(scene.flow_order)))) if flow > 0 else 0
        return {
            **rotation, "flow_fraction":flow,
            "flow_reached":scene.flow_order[:reached_count],
            "flow_goal_reached":scene.puzzle.sink in scene.flow_order[:reached_count],
        }

    def _draw_connector(
        self, buf: bytearray, scene: PipeScene, cell: tuple[int, int], mask: int,
        angle_degrees: float, color: RGB, thickness: int,
    ) -> None:
        cx, cy = self._center(scene, cell)
        size = self.board_size / scene.puzzle.width
        length = size * 0.43
        angle = math.radians(angle_degrees)
        cosine, sine = math.cos(angle), math.sin(angle)
        vectors = {1:(0.0,-1.0),2:(1.0,0.0),4:(0.0,1.0),8:(-1.0,0.0)}
        for bit, (vx, vy) in vectors.items():
            if mask & bit:
                rx, ry = vx * cosine - vy * sine, vx * sine + vy * cosine
                _line(buf, self.width, self.height, (cx, cy), (cx + rx * length, cy + ry * length), color, thickness)
        _circle(buf, self.width, self.height, int(cx), int(cy), max(5, thickness // 2), color, True)

    def render_frame(self, scene: PipeScene, frame: int) -> bytes:
        timeline = scene.plan.timeline
        if not 0 <= frame < timeline["total"]:
            raise ValueError("frame out of range")
        buf = bytearray(bytes(PIPE_VISUAL_ROLES["background"]) * self.width * self.height)
        appearance = max(1, timeline["appearance"])
        fade = min(1.0, frame / appearance)
        panel = _blend(PIPE_VISUAL_ROLES["panel"], fade)
        structure = _blend(PIPE_VISUAL_ROLES["structure"], fade)
        _rect(buf, self.width, self.height, 84, 88, 636, 666, panel)
        _text(buf, self.width, self.height, 102, 38, "CONNECT THE PIPES", WHITE, 4)
        size = self.board_size / scene.puzzle.width
        snapshot = self.semantic_snapshot(scene, frame)
        for y in range(scene.puzzle.height):
            for x in range(scene.puzzle.width):
                left = int(self.board_left + x * size)
                top = int(self.board_top + y * size)
                _rect(buf, self.width, self.height, left + 4, top + 4, int(left + size - 4), int(top + size - 4), _blend((34, 46, 59), fade))
                _line(buf, self.width, self.height, (left, top), (left + size, top), _blend(MUTED, fade), 2)
                _line(buf, self.width, self.height, (left, top), (left, top + size), _blend(MUTED, fade), 2)
                index = y * scene.puzzle.width + x
                angle = snapshot["rotations"][index] * 90.0
                if snapshot["current_piece"] == index:
                    angle += snapshot["current_delta_degrees"]
                self._draw_connector(buf, scene, (x, y), scene.puzzle.initial_masks[index], angle, structure, 16)
                if snapshot["current_piece"] == index:
                    cx, cy = self._center(scene, (x, y))
                    _circle(buf, self.width, self.height, int(cx), int(cy), int(size * 0.39), PIPE_VISUAL_ROLES["rotation_focus"], False, 4)
                    tick_angle = math.radians(angle - 25)
                    a = (cx + math.cos(tick_angle) * size * 0.31, cy + math.sin(tick_angle) * size * 0.31)
                    b = (cx + math.cos(tick_angle) * size * 0.42, cy + math.sin(tick_angle) * size * 0.42)
                    _line(buf, self.width, self.height, a, b, WHITE, 5)
        # Flow is a non-state-mutating double stroke along only the canonical
        # START -> GOAL path. Dangling distractor arms never receive fluid.
        reached = set(snapshot["flow_reached"])
        final_rotations = scene.trace.final.rotations
        for cell in snapshot["flow_reached"]:
            index = cell[1] * scene.puzzle.width + cell[0]
            cx, cy = self._center(scene, cell)
            neighbors = []
            position = scene.flow_order.index(cell)
            if position:
                neighbors.append(scene.flow_order[position - 1])
            if position + 1 < len(scene.flow_order) and scene.flow_order[position + 1] in reached:
                neighbors.append(scene.flow_order[position + 1])
            for neighbor in neighbors:
                nx, ny = self._center(scene, neighbor)
                midpoint = ((cx + nx) / 2, (cy + ny) / 2)
                _line(buf, self.width, self.height, (cx, cy), midpoint, PIPE_VISUAL_ROLES["flow_outer"], 12)
                _line(buf, self.width, self.height, (cx, cy), midpoint, PIPE_VISUAL_ROLES["flow_inner"], 5)
            _circle(buf, self.width, self.height, int(cx), int(cy), 6, PIPE_VISUAL_ROLES["flow_inner"], True)
        if reached:
            front = scene.flow_order[len(reached) - 1]
            fx, fy = self._center(scene, front)
            _circle(buf, self.width, self.height, int(fx), int(fy), 11, WHITE, True)
            _circle(buf, self.width, self.height, int(fx), int(fy), 18, PIPE_VISUAL_ROLES["flow_outer"], False, 4)

        sx, sy = self._center(scene, scene.puzzle.source)
        gx, gy = self._center(scene, scene.puzzle.sink)
        _circle(buf, self.width, self.height, int(sx), int(sy), 24, PIPE_VISUAL_ROLES["source"], False, 5)
        _line(buf, self.width, self.height, (sx - 17, sy), (sx + 17, sy), WHITE, 5)
        _line(buf, self.width, self.height, (sx + 17, sy), (sx + 5, sy - 10), WHITE, 5)
        _line(buf, self.width, self.height, (sx + 17, sy), (sx + 5, sy + 10), WHITE, 5)
        _diamond(buf, self.width, self.height, int(gx), int(gy), 25, PIPE_VISUAL_ROLES["goal"], 5)
        _circle(buf, self.width, self.height, int(gx), int(gy), 7, WHITE, True)
        _text(buf, self.width, self.height, 88, 616, "START", PIPE_VISUAL_ROLES["source"], 3)
        _text(buf, self.width, self.height, 528, 616, "GOAL", PIPE_VISUAL_ROLES["goal"], 3)

        reveal, solve_end, result_end = timeline["reveal_start"], timeline["solve_end"], timeline["result_end"]
        if frame < reveal:
            phase = "THINK"
            progress = max(0.0, (frame - timeline["appearance"]) / max(1, timeline["thinking"]))
            _rect(buf, self.width, self.height, 120, 650, 600, 660, MUTED)
            _rect(buf, self.width, self.height, 120, 650, 120 + int(480 * min(1.0, progress)), 660, PIPE_VISUAL_ROLES["source"])
        elif frame < solve_end:
            phase = "ROTATE"
        elif frame < result_end:
            phase = "FLOW"
        else:
            phase = "CLEAR"
        label_width = len(phase) * 24 - 4
        _text(buf, self.width, self.height, (self.width - label_width) // 2, 674, phase, PIPE_VISUAL_ROLES["goal"] if phase == "CLEAR" else WHITE, 4)
        return bytes(buf)

    def render(self, scene: PipeScene, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for frame in range(scene.plan.timeline["total"]):
            path = directory / f"frame_{frame:04d}.ppm"
            path.write_bytes(f"P6\n{self.width} {self.height}\n255\n".encode("ascii") + self.render_frame(scene, frame))
            paths.append(path)
        return paths
