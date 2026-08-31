from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

from .models import PresentationPlan
from .packing import PackingPuzzleSpec, PackingReplayTrace, PackingRules, shape_bbox
from .render import BACKGROUND, MUTED, PANEL, STRUCTURE, WHITE, _blend, _line, _rect, _text

RGB = tuple[int, int, int]
PACKING_VISUAL_ROLES = {
    "background": BACKGROUND,
    "panel": PANEL,
    "structure": STRUCTURE,
    "socket": (34, 46, 59),
    "socket_edge": (150, 172, 192),
    "piece": (126, 148, 168),
    "tray_piece_boundary": (42, 57, 72),
    "piece_edge": (176, 194, 210),
    "seated": (67, 217, 194),
    "focus": (255, 209, 102),
    "filled": (255, 209, 102),
}

HOLE_CELL_PX = 96
TRAY_CELL_PX = 60
TRAY_GAP_PX = 20
HOLE_BAND = (90, 474)
TRAY_TOP = 508
PIECE_MARGIN_HOLE = 5
PIECE_MARGIN_TRAY = 4
TRAY_BOUNDARY_PX = 7
PIECE_EDGE_PX = 3
PANEL_BOX = (60, 78, 660, 660)


@dataclass(frozen=True)
class PackingScene:
    puzzle: PackingPuzzleSpec
    plan: PresentationPlan
    trace: PackingReplayTrace
    moves: tuple[tuple[int, tuple[int, int]], ...]
    semantic_bounds: tuple[int, int, int, int]


class PackingSceneBuilder:
    def build(self, puzzle: PackingPuzzleSpec, plan: PresentationPlan, trace: PackingReplayTrace) -> PackingScene:
        rules = PackingRules()
        if not rules.is_goal(puzzle, trace.final):
            raise ValueError("packing scene requires a completely covered hole")
        moves = tuple(
            (puzzle.index_of(step.action.params["piece"][0]),
             (step.action.params["to"][0], step.action.params["to"][1]))
            for step in trace.steps
        )
        if len(moves) < 3:
            raise ValueError("packing scene requires at least three placements")
        return PackingScene(puzzle, plan, trace, moves, PANEL_BOX)


def alternate_packing_scene(scene: PackingScene) -> PackingScene:
    """Neutrality perturbation: cyclically shift the placement order.

    Every placement covers a different hole cell, so the shifted first
    placement always belongs to a different piece and lands somewhere else;
    the reveal frame, which focuses the piece being moved, therefore always
    changes.
    """
    shifted = scene.moves[1:] + scene.moves[:1]
    alternate_plan = replace(
        scene.plan, logical_steps=tuple(scene.plan.logical_steps[1:] + scene.plan.logical_steps[:1])
    )
    return replace(scene, plan=alternate_plan, moves=shifted)


class PackingRenderer:
    width = 720
    height = 720
    hole_cell = HOLE_CELL_PX
    tray_cell = TRAY_CELL_PX

    # ---------------- geometry ----------------

    def hole_origin(self, scene: PackingScene) -> tuple[float, float]:
        puzzle = scene.puzzle
        band_top, band_bottom = HOLE_BAND
        x = (self.width - puzzle.width * self.hole_cell) / 2
        y = band_top + ((band_bottom - band_top) - puzzle.height * self.hole_cell) / 2
        return x, y

    def tray_extent(self, scene: PackingScene) -> tuple[float, float, float, float]:
        shapes = [shape for _, shape in scene.puzzle.pieces]
        total = sum(shape_bbox(shape)[0] for shape in shapes) * self.tray_cell + TRAY_GAP_PX * (len(shapes) - 1)
        left = (self.width - total) / 2
        bottom = TRAY_TOP + max(shape_bbox(shape)[1] for shape in shapes) * self.tray_cell
        return left, TRAY_TOP, left + total, bottom

    def tray_origin(self, scene: PackingScene, index: int) -> tuple[float, float]:
        left = self.tray_extent(scene)[0]
        column = scene.puzzle.tray_slots[index][0]
        return left + column * self.tray_cell + index * TRAY_GAP_PX, TRAY_TOP

    def hole_target(self, scene: PackingScene, anchor: tuple[int, int]) -> tuple[float, float]:
        ox, oy = self.hole_origin(scene)
        return ox + anchor[0] * self.hole_cell, oy + anchor[1] * self.hole_cell

    # ---------------- semantics ----------------

    def placement_snapshot_for_units(self, scene: PackingScene, units: float) -> dict:
        total = len(scene.moves)
        units = max(0.0, min(float(total), units))
        seated = min(total, int(units))
        progress = units - seated
        if seated >= total:
            return {"seated": total, "moving_index": None, "moving_piece": None, "progress": 0.0}
        piece_index, anchor = scene.moves[seated]
        return {"seated": seated, "moving_index": seated, "moving_piece": piece_index,
                "moving_anchor": anchor, "progress": progress}

    def semantic_snapshot(self, scene: PackingScene, frame: int) -> dict:
        timeline = scene.plan.timeline
        total = len(scene.moves)
        if frame < timeline["reveal_start"]:
            # Pre-reveal frames never consult scene.moves.
            return {"seated": 0, "moving_index": None, "moving_piece": None, "progress": 0.0, "filled": False}
        if frame >= timeline["solve_end"]:
            return {**self.placement_snapshot_for_units(scene, float(total)), "filled": True}
        span = max(1, timeline["solve"] - 1)
        units = (frame - timeline["reveal_start"]) / span * total
        return {**self.placement_snapshot_for_units(scene, units), "filled": False}

    # ---------------- drawing ----------------

    def _draw_socket(self, buf: bytearray, scene: PackingScene, fade: float) -> None:
        ox, oy = self.hole_origin(scene)
        hole = scene.puzzle.hole()
        socket = _blend(PACKING_VISUAL_ROLES["socket"], fade)
        edge = _blend(PACKING_VISUAL_ROLES["socket_edge"], fade)
        for x, y in sorted(hole):
            left, top = int(ox + x * self.hole_cell), int(oy + y * self.hole_cell)
            right, bottom = left + self.hole_cell, top + self.hole_cell
            _rect(buf, self.width, self.height, left + 3, top + 3, right - 3, bottom - 3, socket)
        for x, y in sorted(hole):
            left, top = int(ox + x * self.hole_cell), int(oy + y * self.hole_cell)
            right, bottom = left + self.hole_cell, top + self.hole_cell
            if (x, y - 1) not in hole:
                _line(buf, self.width, self.height, (left, top), (right, top), edge, 5)
            if (x, y + 1) not in hole:
                _line(buf, self.width, self.height, (left, bottom), (right, bottom), edge, 5)
            if (x - 1, y) not in hole:
                _line(buf, self.width, self.height, (left, top), (left, bottom), edge, 5)
            if (x + 1, y) not in hole:
                _line(buf, self.width, self.height, (right, top), (right, bottom), edge, 5)

    def _draw_piece(
        self, buf: bytearray, shape, origin: tuple[float, float], cell: float,
        body: RGB, edge: RGB, margin: int, focus: bool = False,
        boundary: RGB | None = None,
    ) -> None:
        cells = set(shape)
        ox, oy = origin
        for x, y in sorted(cells):
            left, top = int(ox + x * cell), int(oy + y * cell)
            right, bottom = int(ox + (x + 1) * cell), int(oy + (y + 1) * cell)
            _rect(buf, self.width, self.height, left + margin, top + margin, right - margin, bottom - margin, body)
        # A drawn outline around the union tells the silhouette apart from the
        # grid behind it; shape, not colour alone, carries the identity. Tray
        # pieces use a dark, wider under-stroke plus this light edge so two
        # neighbouring, equally coloured pieces retain distinct silhouettes.
        segments = []
        for x, y in sorted(cells):
            left, top = int(ox + x * cell), int(oy + y * cell)
            right, bottom = int(ox + (x + 1) * cell), int(oy + (y + 1) * cell)
            if (x, y - 1) not in cells:
                segments.append(((left + margin, top + margin), (right - margin, top + margin)))
            if (x, y + 1) not in cells:
                segments.append(((left + margin, bottom - margin), (right - margin, bottom - margin)))
            if (x - 1, y) not in cells:
                segments.append(((left + margin, top + margin), (left + margin, bottom - margin)))
            if (x + 1, y) not in cells:
                segments.append(((right - margin, top + margin), (right - margin, bottom - margin)))
        if boundary is not None:
            for start, end in segments:
                _line(buf, self.width, self.height, start, end, boundary, TRAY_BOUNDARY_PX)
        for start, end in segments:
            _line(buf, self.width, self.height, start, end, edge, PIECE_EDGE_PX)
        if focus:
            width, height = shape_bbox(tuple(sorted(cells)))
            x0, y0 = int(ox) - 6, int(oy) - 6
            x1, y1 = int(ox + width * cell) + 6, int(oy + height * cell) + 6
            colour = PACKING_VISUAL_ROLES["focus"]
            _rect(buf, self.width, self.height, x0, y0, x1, y0 + 4, colour)
            _rect(buf, self.width, self.height, x0, y1 - 4, x1, y1, colour)
            _rect(buf, self.width, self.height, x0, y0, x0 + 4, y1, colour)
            _rect(buf, self.width, self.height, x1 - 4, y0, x1, y1, colour)

    def render_frame(self, scene: PackingScene, frame: int) -> bytes:
        timeline = scene.plan.timeline
        if not 0 <= frame < timeline["total"]:
            raise ValueError("frame out of range")
        buf = bytearray(bytes(PACKING_VISUAL_ROLES["background"]) * self.width * self.height)
        appearance = max(1, timeline["appearance"])
        fade = min(1.0, frame / appearance)
        _rect(buf, self.width, self.height, *PANEL_BOX, _blend(PACKING_VISUAL_ROLES["panel"], fade))
        # The title is drawn without fade so frame zero is never blank.
        _text(buf, self.width, self.height, 108, 34, "FILL THE HOLE", WHITE, 4)
        self._draw_socket(buf, scene, fade)
        snapshot = self.semantic_snapshot(scene, frame)
        seated = snapshot["seated"]
        moving = snapshot["moving_index"]
        drawn = set()
        for order in range(seated):
            piece_index, anchor = scene.moves[order]
            drawn.add(piece_index)
            _, shape = scene.puzzle.pieces[piece_index]
            self._draw_piece(
                buf, shape, self.hole_target(scene, anchor), self.hole_cell,
                _blend(PACKING_VISUAL_ROLES["seated"], fade), _blend(WHITE, fade), PIECE_MARGIN_HOLE,
            )
        for index, (_, shape) in enumerate(scene.puzzle.pieces):
            if index in drawn or (moving is not None and index == snapshot["moving_piece"]):
                continue
            self._draw_piece(
                buf, shape, self.tray_origin(scene, index), self.tray_cell,
                _blend(PACKING_VISUAL_ROLES["piece"], fade), _blend(PACKING_VISUAL_ROLES["piece_edge"], fade),
                PIECE_MARGIN_TRAY, boundary=_blend(PACKING_VISUAL_ROLES["tray_piece_boundary"], fade),
            )
        if moving is not None:
            piece_index = snapshot["moving_piece"]
            _, shape = scene.puzzle.pieces[piece_index]
            progress = snapshot["progress"]
            start = self.tray_origin(scene, piece_index)
            end = self.hole_target(scene, snapshot["moving_anchor"])
            origin = (start[0] + (end[0] - start[0]) * progress, start[1] + (end[1] - start[1]) * progress)
            cell = self.tray_cell + (self.hole_cell - self.tray_cell) * progress
            body = PACKING_VISUAL_ROLES["seated"] if progress > 0 else PACKING_VISUAL_ROLES["piece"]
            self._draw_piece(
                buf, shape, origin, cell, _blend(body, fade), _blend(WHITE, fade),
                PIECE_MARGIN_TRAY, focus=True,
                boundary=_blend(PACKING_VISUAL_ROLES["tray_piece_boundary"], fade),
            )

        reveal, solve_end, result_end = timeline["reveal_start"], timeline["solve_end"], timeline["result_end"]
        if frame < reveal:
            phase = "THINK"
            progress = max(0.0, (frame - timeline["appearance"]) / max(1, timeline["thinking"]))
            _rect(buf, self.width, self.height, 120, 636, 600, 646, MUTED)
            _rect(buf, self.width, self.height, 120, 636, 120 + int(480 * min(1.0, progress)), 646, PACKING_VISUAL_ROLES["seated"])
        elif frame < solve_end:
            phase = "PACK"
        elif frame < result_end:
            phase = "FULL"
            # A ring hugging the hole outline, not a circle across the board:
            # the sealed hole is what the result phase is about.
            ox, oy = self.hole_origin(scene)
            inset = 10 + int(6 * (0.5 + 0.5 * math.sin((frame - solve_end) * math.pi / 5)))
            x0, y0 = int(ox) - inset, int(oy) - inset
            x1 = int(ox + scene.puzzle.width * self.hole_cell) + inset
            y1 = int(oy + scene.puzzle.height * self.hole_cell) + inset
            ring = PACKING_VISUAL_ROLES["filled"]
            _rect(buf, self.width, self.height, x0, y0, x1, y0 + 5, ring)
            _rect(buf, self.width, self.height, x0, y1 - 5, x1, y1, ring)
            _rect(buf, self.width, self.height, x0, y0, x0 + 5, y1, ring)
            _rect(buf, self.width, self.height, x1 - 5, y0, x1, y1, ring)
        else:
            phase = "CLEAR"
        label_width = len(phase) * 24 - 4
        _text(
            buf, self.width, self.height, (self.width - label_width) // 2, 674, phase,
            PACKING_VISUAL_ROLES["filled"] if phase in {"FULL", "CLEAR"} else WHITE, 4,
        )
        return bytes(buf)

    def render(self, scene: PackingScene, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for frame in range(scene.plan.timeline["total"]):
            path = directory / f"frame_{frame:04d}.ppm"
            path.write_bytes(f"P6\n{self.width} {self.height}\n255\n".encode("ascii") + self.render_frame(scene, frame))
            paths.append(path)
        return paths
