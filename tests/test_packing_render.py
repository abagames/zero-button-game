import hashlib
import unittest

from zero_button_game.core import StableRng, derive_seed
from zero_button_game.models import TimelineSpec
from zero_button_game.packing import (
    MAX_TRAY_WIDTH_CELLS, PackingRules, PackingSolver, generate_packing, packing_difficulty_preset,
    placed_cells, replay_packing, shape_bbox,
)
from zero_button_game.packing_presentation import packing_plan
from zero_button_game.packing_render import (
    HOLE_CELL_PX, PACKING_VISUAL_ROLES, PIECE_EDGE_PX, PIECE_MARGIN_TRAY, TRAY_BOUNDARY_PX,
    TRAY_CELL_PX, PackingRenderer, PackingSceneBuilder, alternate_packing_scene,
)
from zero_button_game.registry import get_plugin
from zero_button_game.render import FONT
from zero_button_game.validation import pre_reveal_neutrality_failure

_SCENES: dict[tuple[str, int, int], tuple] = {}


def _scene(band="target", candidate=0, master_seed=20260822):
    key = (band, candidate, master_seed)
    if key not in _SCENES:
        _SCENES[key] = _build_scene(band, candidate, master_seed)
    return _SCENES[key]


def _build_scene(band, candidate, master_seed):
    rules = PackingRules()
    puzzle = generate_packing(
        StableRng(derive_seed(master_seed, "packing", candidate, "generation")),
        packing_difficulty_preset(band),
    )
    solution = PackingSolver(rules).solve(puzzle)
    units = len(solution.actions)
    timeline = TimelineSpec(solve_duration=max(2.0, min(4.0, units * 2 / 20)))
    plan = packing_plan(puzzle, solution, rules, timeline)
    trace = replay_packing(puzzle, plan.logical_steps, rules)
    return PackingSceneBuilder().build(puzzle, plan, trace), PackingRenderer(), plan


class PackingRenderContractTests(unittest.TestCase):
    @staticmethod
    def _pixel(frame, renderer, x, y):
        offset = (y * renderer.width + x) * 3
        return tuple(frame[offset:offset + 3])

    def test_title_and_labels_use_available_glyphs(self):
        for text in ("FILL THE HOLE", "THINK", "PACK", "FULL", "CLEAR"):
            for character in text:
                self.assertIn(character, FONT, f"{character!r} missing from the bitmap font")

    def test_pre_reveal_neutrality_and_reveal_boundary(self):
        scene, renderer, plan = _scene()
        plugin = get_plugin("packing")
        self.assertIsNone(pre_reveal_neutrality_failure(plugin, renderer, scene, plan.timeline))
        alternate = alternate_packing_scene(scene)
        self.assertNotEqual(alternate.moves, scene.moves)
        self.assertNotEqual(alternate.moves[0], scene.moves[0])
        reveal = plan.timeline["reveal_start"]
        self.assertIsNone(renderer.semantic_snapshot(scene, reveal - 1)["moving_piece"])
        self.assertIsNotNone(renderer.semantic_snapshot(scene, reveal)["moving_piece"])
        self.assertNotEqual(
            hashlib.sha256(renderer.render_frame(scene, reveal)).digest(),
            hashlib.sha256(renderer.render_frame(alternate, reveal)).digest(),
        )

    def test_pre_reveal_snapshot_ignores_the_solution(self):
        scene, renderer, plan = _scene()
        alternate = alternate_packing_scene(scene)
        for frame in range(plan.timeline["reveal_start"]):
            self.assertEqual(
                renderer.semantic_snapshot(scene, frame), renderer.semantic_snapshot(alternate, frame)
            )

    def test_solve_phase_never_stalls_for_200ms(self):
        scene, renderer, plan = _scene()
        for frame in range(plan.timeline["reveal_start"], plan.timeline["solve_end"] - 4, 4):
            self.assertNotEqual(
                renderer.render_frame(scene, frame), renderer.render_frame(scene, frame + 4),
                f"solve stalls between frames {frame} and {frame + 4}",
            )

    def test_frame_zero_is_not_blank_and_bounds_stay_in_safe_area(self):
        scene, renderer, plan = _scene()
        first = renderer.render_frame(scene, 0)
        last = renderer.render_frame(scene, plan.timeline["total"] - 1)
        self.assertGreater(len(set(first)), 3)
        self.assertGreater(len(set(last)), 3)
        safe = (36, 36, 684, 684)
        bounds = scene.semantic_bounds
        self.assertLessEqual(safe[0], bounds[0])
        self.assertLessEqual(safe[1], bounds[1])
        self.assertLessEqual(bounds[2], safe[2])
        self.assertLessEqual(bounds[3], safe[3])

    def test_hole_and_tray_geometry_fit_the_safe_area(self):
        safe = (36, 36, 684, 684)
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, _ = _scene(band)
                left, top, right, bottom = renderer.tray_extent(scene)
                hole_x, hole_y = renderer.hole_origin(scene)
                self.assertGreaterEqual(left, safe[0])
                self.assertLessEqual(right, safe[2])
                self.assertLessEqual(bottom, safe[3])
                self.assertGreaterEqual(hole_x, safe[0])
                self.assertLessEqual(hole_x + scene.puzzle.width * HOLE_CELL_PX, safe[2])
                self.assertGreaterEqual(hole_y, safe[1])
                # The tray never overlaps the hole region.
                self.assertLessEqual(hole_y + scene.puzzle.height * HOLE_CELL_PX, top)
                self.assertLessEqual(
                    sum(shape_bbox(shape)[0] for _, shape in scene.puzzle.pieces), MAX_TRAY_WIDTH_CELLS
                )

    def test_medium_and_target_tray_pieces_have_two_tone_boundaries(self):
        self.assertGreater(TRAY_BOUNDARY_PX, PIECE_EDGE_PX)
        for band in ("medium", "target"):
            with self.subTest(band=band):
                scene, renderer, plan = _scene(band)
                frame = renderer.render_frame(scene, plan.timeline["reveal_start"] - 1)
                for index, (_, shape) in enumerate(scene.puzzle.pieces):
                    cells = set(shape)
                    x, y = next((x, y) for x, y in sorted(cells) if (x, y - 1) not in cells)
                    ox, oy = renderer.tray_origin(scene, index)
                    sample_x = int(ox + (x + 0.5) * TRAY_CELL_PX)
                    edge_y = int(oy + y * TRAY_CELL_PX) + PIECE_MARGIN_TRAY
                    self.assertEqual(
                        self._pixel(frame, renderer, sample_x, edge_y),
                        PACKING_VISUAL_ROLES["piece_edge"],
                    )
                    self.assertEqual(
                        self._pixel(frame, renderer, sample_x, edge_y - TRAY_BOUNDARY_PX // 2),
                        PACKING_VISUAL_ROLES["tray_piece_boundary"],
                    )

    def test_render_contract_checks_pass_for_every_band(self):
        plugin = get_plugin("packing")
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, plan = _scene(band)
                passed, failed = plugin.render_contract_checks(scene, renderer)
                self.assertEqual(failed, [])
                for check in (
                    "exact_cover_complete", "minimum_hole_cell_size", "minimum_tray_cell_size",
                    "minimum_piece_body", "hole_and_tray_within_safe_area", "placement_action_rendering",
                    "fill_after_last_placement", "unique_exact_cover", "fill_state_immutable",
                ):
                    self.assertIn(check, passed)
                contract = plugin.visual_contract(scene, renderer)
                self.assertGreaterEqual(contract["hole_cell_px"], contract["minimum_hole_cell_px"])
                self.assertGreaterEqual(contract["tray_cell_px"], contract["minimum_tray_cell_px"])
                self.assertGreaterEqual(contract["tray_piece_body_px"], contract["minimum_piece_body_px"])

    def test_placements_match_the_replayed_states(self):
        scene, renderer, _ = _scene()
        for order, step in enumerate(scene.trace.steps):
            snapshot = renderer.placement_snapshot_for_units(scene, float(order + 1))
            self.assertEqual(snapshot["seated"], order + 1)
            covered = set()
            for index in range(order + 1):
                piece_index, anchor = scene.moves[index]
                covered.update(placed_cells(scene.puzzle.pieces[piece_index][1], anchor))
            replayed = set()
            for index, anchor in enumerate(step.after.placements):
                if anchor != (-1, -1):
                    replayed.update(placed_cells(scene.puzzle.pieces[index][1], anchor))
            self.assertEqual(covered, replayed)
        final = renderer.placement_snapshot_for_units(scene, float(len(scene.moves)))
        self.assertIsNone(final["moving_piece"])
        self.assertTrue(PackingRules().is_goal(scene.puzzle, scene.trace.final))

    def test_moving_piece_interpolates_from_tray_to_hole(self):
        scene, renderer, _ = _scene()
        piece_index, anchor = scene.moves[0]
        start = renderer.tray_origin(scene, piece_index)
        end = renderer.hole_target(scene, anchor)
        self.assertNotEqual(start, end)
        previous = None
        for step in range(1, 10):
            snapshot = renderer.placement_snapshot_for_units(scene, step / 10)
            self.assertEqual(snapshot["moving_piece"], piece_index)
            self.assertGreater(snapshot["progress"], 0.0)
            if previous is not None:
                self.assertGreater(snapshot["progress"], previous)
            previous = snapshot["progress"]
        self.assertEqual(TRAY_CELL_PX, renderer.tray_cell)
        self.assertEqual(HOLE_CELL_PX, renderer.hole_cell)

    def test_scene_builder_rejects_short_solutions(self):
        scene, _, plan = _scene()
        import dataclasses

        trimmed = dataclasses.replace(scene.trace, steps=scene.trace.steps[:2])
        with self.assertRaises(ValueError):
            PackingSceneBuilder().build(scene.puzzle, plan, trimmed)


if __name__ == "__main__":
    unittest.main()
