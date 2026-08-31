from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .maze import normalized_edge
from .models import PresentationPlan, PuzzleSpec, ReplayTrace

RGB = tuple[int, int, int]
BACKGROUND: RGB = (16, 21, 29)
PANEL: RGB = (25, 34, 45)
STRUCTURE: RGB = (216, 226, 234)
MUTED: RGB = (87, 105, 121)
ACTOR: RGB = (67, 217, 194)
GOAL: RGB = (255, 209, 102)
WHITE: RGB = (245, 248, 250)

FONT = {
    "A": ("01110","10001","10001","11111","10001","10001","10001"),
    "B": ("11110","10001","10001","11110","10001","10001","11110"),
    "C": ("01111","10000","10000","10000","10000","10000","01111"),
    "D": ("11110","10001","10001","10001","10001","10001","11110"),
    "E": ("11111","10000","10000","11110","10000","10000","11111"),
    "F": ("11111","10000","10000","11110","10000","10000","10000"),
    "G": ("01110","10001","10000","10111","10001","10001","01110"),
    "H": ("10001","10001","10001","11111","10001","10001","10001"),
    "I": ("11111","00100","00100","00100","00100","00100","11111"),
    "K": ("10001","10010","10100","11000","10100","10010","10001"),
    "L": ("10000","10000","10000","10000","10000","10000","11111"),
    "M": ("10001","11011","10101","10101","10001","10001","10001"),
    "N": ("10001","11001","10101","10011","10001","10001","10001"),
    "O": ("01110","10001","10001","10001","10001","10001","01110"),
    "P": ("11110","10001","10001","11110","10000","10000","10000"),
    "R": ("11110","10001","10001","11110","10100","10010","10001"),
    "S": ("01111","10000","10000","01110","00001","00001","11110"),
    "T": ("11111","00100","00100","00100","00100","00100","00100"),
    "U": ("10001","10001","10001","10001","10001","10001","01110"),
    "V": ("10001","10001","10001","10001","10001","01010","00100"),
    "W": ("10001","10001","10001","10101","10101","10101","01010"),
    "X": ("10001","10001","01010","00100","01010","10001","10001"),
    "Y": ("10001","10001","01010","00100","00100","00100","00100"),
    "Z": ("11111","00001","00010","00100","01000","10000","11111"),
    "0": ("01110","10011","10101","10101","11001","10001","01110"),
    "1": ("00100","01100","00100","00100","00100","00100","01110"),
    "2": ("01110","10001","00001","00010","00100","01000","11111"),
    "3": ("11110","00001","00001","01110","00001","00001","11110"),
    "4": ("00010","00110","01010","10010","11111","00010","00010"),
    "5": ("11111","10000","10000","11110","00001","00001","11110"),
    "6": ("01110","10000","10000","11110","10001","10001","01110"),
    "7": ("11111","00001","00010","00100","01000","01000","01000"),
    "8": ("01110","10001","10001","01110","10001","10001","01110"),
    "9": ("01110","10001","10001","01111","00001","00001","01110"),
    "-": ("00000","00000","00000","11111","00000","00000","00000"),
    ">": ("00000","10000","01000","00100","01000","10000","00000"),
    ".": ("00000","00000","00000","00000","00000","00110","00110"),
    ":": ("00000","00110","00110","00000","00110","00110","00000"),
    "/": ("00001","00010","00100","01000","10000","00000","00000"),
    " ": ("00000",) * 7,
}


@dataclass(frozen=True)
class MazeScene:
    puzzle: PuzzleSpec
    plan: PresentationPlan
    path: tuple[tuple[int, int], ...]
    semantic_bounds: tuple[int, int, int, int]


class MazeSceneBuilder:
    def build(self, puzzle: PuzzleSpec, plan: PresentationPlan, trace: ReplayTrace) -> MazeScene:
        path = (trace.initial.current,) + tuple(step.after.current for step in trace.steps)
        return MazeScene(puzzle, plan, path, (78, 96, 642, 660))


def _set_pixel(buf: bytearray, width: int, height: int, x: int, y: int, color: RGB) -> None:
    if 0 <= x < width and 0 <= y < height:
        offset = (y * width + x) * 3
        buf[offset:offset + 3] = bytes(color)


def _rect(buf: bytearray, width: int, height: int, x0: int, y0: int, x1: int, y1: int, color: RGB) -> None:
    x0, x1 = max(0, x0), min(width, x1)
    y0, y1 = max(0, y0), min(height, y1)
    row = bytes(color) * max(0, x1 - x0)
    for y in range(y0, y1):
        offset = (y * width + x0) * 3
        buf[offset:offset + len(row)] = row


def _line(buf: bytearray, width: int, height: int, a: tuple[float, float], b: tuple[float, float], color: RGB, thickness: int = 1) -> None:
    x0, y0 = int(round(a[0])), int(round(a[1]))
    x1, y1 = int(round(b[0])), int(round(b[1]))
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    radius = thickness // 2
    while True:
        _rect(buf, width, height, x0 - radius, y0 - radius, x0 + radius + 1, y0 + radius + 1, color)
        if x0 == x1 and y0 == y1:
            break
        twice = 2 * err
        if twice >= dy:
            err += dy
            x0 += sx
        if twice <= dx:
            err += dx
            y0 += sy


def _circle(buf: bytearray, width: int, height: int, cx: int, cy: int, radius: int, color: RGB, filled: bool = False, thickness: int = 3) -> None:
    inner2 = max(0, radius - thickness) ** 2
    outer2 = radius ** 2
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if d2 <= outer2 and (filled or d2 >= inner2):
                _set_pixel(buf, width, height, x, y, color)


def _diamond(buf: bytearray, width: int, height: int, cx: int, cy: int, radius: int, color: RGB, thickness: int = 4) -> None:
    points = ((cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy), (cx, cy - radius))
    for a, b in zip(points, points[1:]):
        _line(buf, width, height, a, b, color, thickness)


def _quad(buf: bytearray, width: int, height: int, points: tuple[tuple[float, float], ...], color: RGB) -> None:
    """Fill a convex quadrilateral by scanline.

    Added for the fold plugin: a flap caught mid-rotation is a trapezoid, not a
    rectangle, so none of the existing primitives can paint it. Purely additive
    - no existing drawing path calls it, which ``tests/test_golden.py`` keeps
    honest.
    """
    if len(points) != 4:
        raise ValueError("a quad needs exactly four points")
    ys = [point[1] for point in points]
    top = max(0, int(math.floor(min(ys))))
    bottom = min(height, int(math.ceil(max(ys))) + 1)
    edges = tuple(zip(points, points[1:] + points[:1]))
    for y in range(top, bottom):
        centre = y + 0.5
        crossings = []
        for (ax, ay), (bx, by) in edges:
            if (ay <= centre < by) or (by <= centre < ay):
                crossings.append(ax + (bx - ax) * (centre - ay) / (by - ay))
        if len(crossings) < 2:
            continue
        left, right = min(crossings), max(crossings)
        _rect(buf, width, height, int(round(left)), y, int(round(right)), y + 1, color)


def _text(buf: bytearray, width: int, height: int, x: int, y: int, text: str, color: RGB, scale: int = 4) -> None:
    cursor = x
    for character in text.upper():
        glyph = FONT.get(character, FONT[" "])
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit == "1":
                    _rect(buf, width, height, cursor + col * scale, y + row * scale, cursor + (col + 1) * scale, y + (row + 1) * scale, color)
        cursor += 6 * scale


def _blend(color: RGB, factor: float) -> RGB:
    return tuple(int(BACKGROUND[i] + (color[i] - BACKGROUND[i]) * max(0.0, min(1.0, factor))) for i in range(3))  # type: ignore[return-value]


class RasterRenderer:
    width = 720
    height = 720
    board_left = 90
    board_top = 108
    board_size = 540

    def _center(self, scene: MazeScene, cell: tuple[int, int]) -> tuple[float, float]:
        size = self.board_size / scene.puzzle.width
        return self.board_left + (cell[0] + 0.5) * size, self.board_top + (cell[1] + 0.5) * size

    def render_frame(self, scene: MazeScene, frame: int) -> bytes:
        timeline = scene.plan.timeline
        total = timeline["total"]
        if not 0 <= frame < total:
            raise ValueError("frame out of range")
        buf = bytearray(bytes(BACKGROUND) * self.width * self.height)
        appearance = max(1, timeline["appearance"])
        fade = min(1.0, frame / appearance)
        structure = _blend(STRUCTURE, fade)
        panel = _blend(PANEL, fade)
        _rect(buf, self.width, self.height, 72, 90, 648, 666, panel)
        _text(buf, self.width, self.height, 90, 40, "SOLVE THE MAZE", WHITE, 4)
        puzzle = scene.puzzle
        size = self.board_size / puzzle.width
        edges = set(puzzle.edges)
        # Draw each absent adjacency as a wall, including the outer boundary.
        for y in range(puzzle.height):
            for x in range(puzzle.width):
                left = self.board_left + x * size
                top = self.board_top + y * size
                right, bottom = left + size, top + size
                cell = (x, y)
                if y == 0 or normalized_edge(cell, (x, y - 1)) not in edges:
                    _line(buf, self.width, self.height, (left, top), (right, top), structure, 5)
                if x == 0 or normalized_edge(cell, (x - 1, y)) not in edges:
                    _line(buf, self.width, self.height, (left, top), (left, bottom), structure, 5)
                if y == puzzle.height - 1:
                    _line(buf, self.width, self.height, (left, bottom), (right, bottom), structure, 5)
                if x == puzzle.width - 1:
                    _line(buf, self.width, self.height, (right, top), (right, bottom), structure, 5)
        sx, sy = self._center(scene, puzzle.start)
        gx, gy = self._center(scene, puzzle.goal)
        _circle(buf, self.width, self.height, int(sx), int(sy), int(size * 0.24), _blend(ACTOR, fade), False, 5)
        _circle(buf, self.width, self.height, int(sx), int(sy), int(size * 0.13), _blend(ACTOR, fade), True)
        _diamond(buf, self.width, self.height, int(gx), int(gy), int(size * 0.24), _blend(GOAL, fade), 5)
        reveal = timeline["reveal_start"]
        solve_end = timeline["solve_end"]
        if frame < reveal:
            phase = "THINK"
            progress = max(0.0, (frame - timeline["appearance"]) / max(1, timeline["thinking"]))
            _rect(buf, self.width, self.height, 90, 682, 630, 692, MUTED)
            _rect(buf, self.width, self.height, 90, 682, 90 + int(540 * min(1.0, progress)), 692, ACTOR)
        else:
            solve_progress = min(1.0, (frame - reveal + 1) / max(1, timeline["solve"]))
            phase = "CLEAR" if frame >= solve_end else "TRACE"
            segment_progress = solve_progress * (len(scene.path) - 1)
            completed = int(segment_progress)
            partial = segment_progress - completed
            for index in range(min(completed, len(scene.path) - 1)):
                _line(buf, self.width, self.height, self._center(scene, scene.path[index]), self._center(scene, scene.path[index + 1]), ACTOR, 10)
            if completed < len(scene.path) - 1:
                a = self._center(scene, scene.path[completed])
                b = self._center(scene, scene.path[completed + 1])
                end = (a[0] + (b[0] - a[0]) * partial, a[1] + (b[1] - a[1]) * partial)
                _line(buf, self.width, self.height, a, end, ACTOR, 10)
                _circle(buf, self.width, self.height, int(end[0]), int(end[1]), 9, WHITE, True)
            if frame >= solve_end:
                for a, b in zip(scene.path, scene.path[1:]):
                    _line(buf, self.width, self.height, self._center(scene, a), self._center(scene, b), GOAL, 7)
                pulse = 20 + int(8 * (0.5 + 0.5 * math.sin((frame - solve_end) * math.pi / 5)))
                _circle(buf, self.width, self.height, int(gx), int(gy), pulse, GOAL, False, 4)
        label_width = len(phase) * 24 - 4
        _text(buf, self.width, self.height, (self.width - label_width) // 2, 674, phase, GOAL if phase == "CLEAR" else WHITE, 4)
        return bytes(buf)

    def render(self, scene: MazeScene, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for frame in range(scene.plan.timeline["total"]):
            path = directory / f"frame_{frame:04d}.ppm"
            path.write_bytes(f"P6\n{self.width} {self.height}\n255\n".encode("ascii") + self.render_frame(scene, frame))
            paths.append(path)
        return paths


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    with path.open("rb") as handle:
        if handle.readline().strip() != b"P6":
            raise ValueError("not a binary PPM")
        width, height = map(int, handle.readline().split())
        if handle.readline().strip() != b"255":
            raise ValueError("unsupported PPM range")
        data = handle.read()
    if len(data) != width * height * 3:
        raise ValueError("truncated PPM")
    return width, height, data


def build_contact_ppm(frame_paths: list[Path], labels: list[str], destination: Path) -> None:
    images = [read_ppm(path) for path in frame_paths]
    width, height = images[0][0], images[0][1]
    canvas_width, canvas_height = width * len(images), height + 60
    canvas = bytearray(bytes(BACKGROUND) * canvas_width * canvas_height)
    for index, (_, _, data) in enumerate(images):
        for y in range(height):
            src = y * width * 3
            dst = (y * canvas_width + index * width) * 3
            canvas[dst:dst + width * 3] = data[src:src + width * 3]
        _text(canvas, canvas_width, canvas_height, index * width + 20, height + 16, labels[index], WHITE, 3)
    destination.write_bytes(f"P6\n{canvas_width} {canvas_height}\n255\n".encode("ascii") + canvas)
