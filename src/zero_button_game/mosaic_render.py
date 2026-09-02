from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

from .models import PresentationPlan
from .mosaic import MosaicPuzzleSpec, MosaicReplayTrace, MosaicRules, action_signature
from .render import BACKGROUND, GOAL, MUTED, PANEL, STRUCTURE, WHITE, _blend, _line, _rect, _text

RGB = tuple[int, int, int]
MOSAIC_VISUAL_ROLES = {
    "tile": (35, 47, 60), "tile_edge": (101, 121, 138),
    "primary": (67, 217, 194), "secondary": (255, 209, 102),
    "focus": (255, 209, 102), "shadow": (9, 13, 19),
}
PANEL_BOX = (72, 78, 648, 656)
SAFE_AREA = (36, 36, 684, 684)
BOARD_LEFT = 120
BOARD_TOP = 112
CELL_PX = 160
BOARD_PX = CELL_PX * 3
MIN_CELL_PX = 144
MIN_STROKE_PX = 12
PROGRESS_BAR = (128, 622, 592, 632)


@dataclass(frozen=True)
class MosaicScene:
    puzzle: MosaicPuzzleSpec
    plan: PresentationPlan
    trace: MosaicReplayTrace
    actions: tuple[tuple[str, int, int], ...]
    states: tuple[tuple[int, ...], ...]
    semantic_bounds: tuple[int, int, int, int]


class MosaicSceneBuilder:
    def build(self, puzzle: MosaicPuzzleSpec, plan: PresentationPlan, trace: MosaicReplayTrace) -> MosaicScene:
        rules = MosaicRules()
        if puzzle.size != 3:
            raise ValueError("mosaic renderer requires a 3x3 board")
        if not rules.is_goal(puzzle, trace.final):
            raise ValueError("mosaic scene requires a completed emblem")
        actions = tuple(action_signature(step.action) for step in trace.steps)
        if not 2 <= len(actions) <= 8:
            raise ValueError("mosaic movie requires two to eight shifts")
        states = (trace.initial.tiles,) + tuple(step.after.tiles for step in trace.steps)
        return MosaicScene(puzzle, plan, trace, actions, states, PANEL_BOX)


def alternate_mosaic_scene(scene: MosaicScene) -> MosaicScene:
    """Perturb solve data while preserving every problem-only pre-reveal pixel."""
    return replace(scene, actions=scene.actions[1:] + scene.actions[:1])


class MosaicRenderer:
    width = 720
    height = 720
    cell = CELL_PX
    board_left = BOARD_LEFT
    board_top = BOARD_TOP
    board_size = BOARD_PX

    def __init__(self) -> None:
        self._tiles: dict[tuple[str, int, int], bytes] = {}
        for art_name in ("halo-diamond", "four-petal-star", "shield-knot"):
            for fade_step in range(9):
                for tile_id in range(9):
                    self._tiles[(art_name, fade_step, tile_id)] = self._build_tile(art_name, tile_id, fade_step / 8)

    @staticmethod
    def _mix(a: RGB, b: RGB, factor: float) -> RGB:
        factor = max(0.0, min(1.0, factor))
        return tuple(int(a[i] + (b[i] - a[i]) * factor) for i in range(3))  # type: ignore[return-value]

    @staticmethod
    def _emblem_pixel(art_name: str, dx: float, dy: float) -> int:
        """0 background, 1 aqua primary, 2 amber secondary; geometry is thick."""
        radius = math.hypot(dx, dy)
        manhattan = abs(dx) + abs(dy)
        if art_name == "halo-diamond":
            if 138 <= radius <= 170:
                return 1
            if 72 <= manhattan <= 108 or manhattan <= 30:
                return 2
            if abs(dx) <= 9 or abs(dy) <= 9:
                return 1
        elif art_name == "four-petal-star":
            horizontal = ((dx - 72) / 88) ** 2 + (dy / 52) ** 2 <= 1 or ((dx + 72) / 88) ** 2 + (dy / 52) ** 2 <= 1
            vertical = (dx / 52) ** 2 + ((dy - 72) / 88) ** 2 <= 1 or (dx / 52) ** 2 + ((dy + 72) / 88) ** 2 <= 1
            if horizontal or vertical:
                return 1
            if manhattan <= 62 or 142 <= radius <= 164:
                return 2
        else:  # shield-knot
            shield = abs(dx) <= 142 and -150 <= dy <= 86 + 0.48 * abs(dx)
            inner = abs(dx) <= 112 and -120 <= dy <= 54 + 0.48 * abs(dx)
            if shield and not inner:
                return 1
            if abs(dx - dy) <= 11 or abs(dx + dy) <= 11:
                if radius <= 138:
                    return 2
            if radius <= 30:
                return 1
        return 0

    def _build_tile(self, art_name: str, tile_id: int, fade: float) -> bytes:
        target_x, target_y = tile_id % 3, tile_id // 3
        base = self._mix(BACKGROUND, MOSAIC_VISUAL_ROLES["tile"], fade)
        edge = self._mix(BACKGROUND, MOSAIC_VISUAL_ROLES["tile_edge"], fade)
        primary = self._mix(BACKGROUND, MOSAIC_VISUAL_ROLES["primary"], fade)
        secondary = self._mix(BACKGROUND, MOSAIC_VISUAL_ROLES["secondary"], fade)
        data = bytearray(bytes(base) * self.cell * self.cell)
        centre = self.board_size / 2
        for py in range(self.cell):
            gy = target_y * self.cell + py + 0.5
            for px in range(self.cell):
                gx = target_x * self.cell + px + 0.5
                role = self._emblem_pixel(art_name, gx - centre, gy - centre)
                if role:
                    offset = (py * self.cell + px) * 3
                    data[offset:offset + 3] = bytes(primary if role == 1 else secondary)
        # Three-pixel seams make cyclic fragments and wrap-around readable.
        for inset in range(3):
            for x in range(inset, self.cell - inset):
                for y in (inset, self.cell - 1 - inset):
                    offset = (y * self.cell + x) * 3
                    data[offset:offset + 3] = bytes(edge)
            for y in range(inset, self.cell - inset):
                for x in (inset, self.cell - 1 - inset):
                    offset = (y * self.cell + x) * 3
                    data[offset:offset + 3] = bytes(edge)
        return bytes(data)

    def _paste_tile(self, buf: bytearray, scene: MosaicScene, tile_id: int, left: int, top: int, fade_step: int) -> None:
        right = self.board_left + self.board_size
        bottom = self.board_top + self.board_size
        x0, y0 = max(left, self.board_left), max(top, self.board_top)
        x1, y1 = min(left + self.cell, right), min(top + self.cell, bottom)
        if x0 >= x1 or y0 >= y1:
            return
        tile = self._tiles[(scene.puzzle.art_name, fade_step, tile_id)]
        length = (x1 - x0) * 3
        source_x = x0 - left
        for y in range(y0, y1):
            source_y = y - top
            source = (source_y * self.cell + source_x) * 3
            destination = (y * self.width + x0) * 3
            buf[destination:destination + length] = tile[source:source + length]

    def shift_snapshot_for_units(self, scene: MosaicScene, units: float) -> dict:
        total = len(scene.actions)
        units = max(0.0, min(float(total), units))
        completed = min(total, int(units))
        progress = units - completed
        if completed >= total:
            return {"completed": total, "action_index": None, "axis": None, "line": None, "delta": None, "progress": 0.0, "tiles": scene.states[total]}
        axis, line, delta = scene.actions[completed]
        return {"completed": completed, "action_index": completed, "axis": axis, "line": line, "delta": delta, "progress": progress, "tiles": scene.states[completed]}

    def semantic_snapshot(self, scene: MosaicScene, frame: int) -> dict:
        timeline = scene.plan.timeline
        if frame < timeline["reveal_start"]:
            return {"completed": 0, "action_index": None, "axis": None, "line": None, "delta": None, "progress": 0.0, "tiles": scene.puzzle.initial_tiles, "solved": False}
        if frame >= timeline["solve_end"]:
            return {**self.shift_snapshot_for_units(scene, float(len(scene.actions))), "solved": True}
        units = (frame - timeline["reveal_start"] + 1) / max(1, timeline["solve"]) * len(scene.actions)
        return {**self.shift_snapshot_for_units(scene, units), "solved": False}

    def _draw_board(self, buf: bytearray, scene: MosaicScene, snapshot: dict, fade_step: int) -> None:
        tiles = snapshot["tiles"]
        axis, line = snapshot["axis"], snapshot["line"]
        progress, delta = snapshot["progress"], snapshot["delta"]
        _rect(buf, self.width, self.height, self.board_left - 8, self.board_top - 8, self.board_left + self.board_size + 8, self.board_top + self.board_size + 8, _blend(STRUCTURE, fade_step / 8))
        _rect(buf, self.width, self.height, self.board_left, self.board_top, self.board_left + self.board_size, self.board_top + self.board_size, MOSAIC_VISUAL_ROLES["shadow"])
        for y in range(3):
            for x in range(3):
                if (axis == "row" and y == line) or (axis == "col" and x == line):
                    continue
                self._paste_tile(buf, scene, tiles[y * 3 + x], self.board_left + x * self.cell, self.board_top + y * self.cell, fade_step)
        if axis is None:
            return
        eased = progress * progress * (3 - 2 * progress)
        travel = int(round(delta * self.cell * eased))
        for index in range(3):
            x, y = (index, line) if axis == "row" else (line, index)
            base_left = self.board_left + x * self.cell
            base_top = self.board_top + y * self.cell
            for wrap in (-self.board_size, 0, self.board_size):
                left = base_left + (travel + wrap if axis == "row" else 0)
                top = base_top + (travel + wrap if axis == "col" else 0)
                self._paste_tile(buf, scene, tiles[y * 3 + x], left, top, fade_step)
        focus = MOSAIC_VISUAL_ROLES["focus"]
        if axis == "row":
            x0, y0 = self.board_left - 6, self.board_top + line * self.cell - 6
            x1, y1 = self.board_left + self.board_size + 6, y0 + self.cell + 12
        else:
            x0, y0 = self.board_left + line * self.cell - 6, self.board_top - 6
            x1, y1 = x0 + self.cell + 12, self.board_top + self.board_size + 6
        for a, b in (((x0, y0), (x1, y0)), ((x0, y1), (x1, y1)), ((x0, y0), (x0, y1)), ((x1, y0), (x1, y1))):
            _line(buf, self.width, self.height, a, b, focus, 5)
        if axis == "row":
            tip_x, tip_y = (x1 + 10 if delta > 0 else x0 - 10), (y0 + y1) / 2
            _line(buf, self.width, self.height, (tip_x - delta * 14, tip_y - 10), (tip_x, tip_y), focus, 5)
            _line(buf, self.width, self.height, (tip_x - delta * 14, tip_y + 10), (tip_x, tip_y), focus, 5)
        else:
            tip_x, tip_y = (x0 + x1) / 2, (y1 + 10 if delta > 0 else y0 - 10)
            _line(buf, self.width, self.height, (tip_x - 10, tip_y - delta * 14), (tip_x, tip_y), focus, 5)
            _line(buf, self.width, self.height, (tip_x + 10, tip_y - delta * 14), (tip_x, tip_y), focus, 5)

    def render_frame(self, scene: MosaicScene, frame: int) -> bytes:
        timeline = scene.plan.timeline
        if not 0 <= frame < timeline["total"]:
            raise ValueError("frame out of range")
        buf = bytearray(bytes(BACKGROUND) * self.width * self.height)
        fade_step = min(8, int(round(8 * min(1.0, frame / max(1, timeline["appearance"])))))
        _rect(buf, self.width, self.height, *PANEL_BOX, _blend(PANEL, fade_step / 8))
        _text(buf, self.width, self.height, 120, 34, "RESTORE THE EMBLEM", WHITE, 4)
        snapshot = self.semantic_snapshot(scene, frame)
        self._draw_board(buf, scene, snapshot, fade_step)
        reveal, solve_end, result_end = timeline["reveal_start"], timeline["solve_end"], timeline["result_end"]
        if frame < reveal:
            phase = "THINK"
            progress = max(0.0, (frame - timeline["appearance"]) / max(1, timeline["thinking"]))
            _rect(buf, self.width, self.height, *PROGRESS_BAR, MUTED)
            _rect(buf, self.width, self.height, PROGRESS_BAR[0], PROGRESS_BAR[1], PROGRESS_BAR[0] + int((PROGRESS_BAR[2] - PROGRESS_BAR[0]) * min(1.0, progress)), PROGRESS_BAR[3], MOSAIC_VISUAL_ROLES["primary"])
        elif frame < solve_end:
            phase = "SHIFT"
        else:
            phase = "CLEAR"
            pulse = 8 + int(5 * (0.5 + 0.5 * math.sin((frame - solve_end) * math.pi / 5)))
            x0, y0 = self.board_left - pulse, self.board_top - pulse
            x1, y1 = self.board_left + self.board_size + pulse, self.board_top + self.board_size + pulse
            for a, b in (((x0, y0), (x1, y0)), ((x0, y1), (x1, y1)), ((x0, y0), (x0, y1)), ((x1, y0), (x1, y1))):
                _line(buf, self.width, self.height, a, b, GOAL, 5)
        label_width = len(phase) * 24 - 4
        _text(buf, self.width, self.height, (self.width - label_width) // 2, 674, phase, GOAL if phase == "CLEAR" else WHITE, 4)
        if frame >= result_end:
            transition = (frame - result_end + 1) / max(1, timeline["transition"])
            cover = int((self.width / 2 - 20) * min(1.0, transition))
            _rect(buf, self.width, self.height, 0, 0, cover, self.height, BACKGROUND)
            _rect(buf, self.width, self.height, self.width - cover, 0, self.width, self.height, BACKGROUND)
        return bytes(buf)

    def render(self, scene: MosaicScene, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        header = f"P6\n{self.width} {self.height}\n255\n".encode("ascii")
        paths = []
        for frame in range(scene.plan.timeline["total"]):
            path = directory / f"frame_{frame:04d}.ppm"
            path.write_bytes(header + self.render_frame(scene, frame))
            paths.append(path)
        return paths
