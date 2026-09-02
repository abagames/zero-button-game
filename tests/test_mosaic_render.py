from __future__ import annotations

import hashlib
import unittest

from zero_button_game.models import TimelineSpec
from zero_button_game.mosaic import MosaicRules, MosaicSolver, replay_mosaic
from zero_button_game.mosaic_presentation import mosaic_plan
from zero_button_game.mosaic_render import (
    BOARD_LEFT, BOARD_PX, BOARD_TOP, CELL_PX, MIN_CELL_PX,
    MosaicRenderer, MosaicSceneBuilder, alternate_mosaic_scene,
)
from tests.test_mosaic_logic import fixture


def scene_bundle():
    puzzle = fixture()
    rules = MosaicRules()
    solution = MosaicSolver(rules).solve(puzzle)
    timeline = TimelineSpec(thinking_duration=3.7, solve_duration=2.0)
    plan = mosaic_plan(puzzle, solution, rules, timeline)
    trace = replay_mosaic(puzzle, solution.actions, rules)
    return puzzle, rules, solution, plan, trace, MosaicSceneBuilder().build(puzzle, plan, trace)


class MosaicRenderTests(unittest.TestCase):
    def test_pre_reveal_is_neutral_and_reveal_is_solution_dependent(self):
        _, _, _, plan, _, scene = scene_bundle()
        renderer = MosaicRenderer()
        alternate = alternate_mosaic_scene(scene)
        for frame in range(plan.timeline["reveal_start"]):
            self.assertEqual(
                hashlib.sha256(renderer.render_frame(scene, frame)).digest(),
                hashlib.sha256(renderer.render_frame(alternate, frame)).digest(),
            )
        reveal = plan.timeline["reveal_start"]
        self.assertNotEqual(renderer.render_frame(scene, reveal), renderer.render_frame(alternate, reveal))

    def test_cyclic_motion_maps_each_action_and_wraps(self):
        _, _, _, _, _, scene = scene_bundle()
        renderer = MosaicRenderer()
        for index, signature in enumerate(scene.actions):
            snapshot = renderer.shift_snapshot_for_units(scene, index + 0.5)
            self.assertEqual((snapshot["axis"], snapshot["line"], snapshot["delta"]), signature)
            self.assertEqual(snapshot["action_index"], index)
            self.assertGreater(snapshot["progress"], 0)
            self.assertLess(snapshot["progress"], 1)
            boundary = renderer.shift_snapshot_for_units(scene, index + 1)
            self.assertEqual(boundary["tiles"], scene.states[index + 1])

    def test_clear_appears_only_after_the_last_shift(self):
        _, rules, _, plan, trace, scene = scene_bundle()
        renderer = MosaicRenderer()
        before = renderer.semantic_snapshot(scene, plan.timeline["solve_end"] - 1)
        after = renderer.semantic_snapshot(scene, plan.timeline["solve_end"])
        self.assertFalse(before["solved"])
        self.assertTrue(after["solved"])
        self.assertTrue(rules.is_goal(scene.puzzle, trace.final))
        self.assertNotEqual(renderer.render_frame(scene, plan.timeline["solve_end"] - 1), renderer.render_frame(scene, plan.timeline["solve_end"]))

    def test_geometry_fragments_and_motion_meet_delivery_contract(self):
        _, _, _, plan, _, scene = scene_bundle()
        renderer = MosaicRenderer()
        self.assertEqual((renderer.width, renderer.height), (720, 720))
        self.assertEqual(CELL_PX, 160)
        self.assertGreaterEqual(CELL_PX, MIN_CELL_PX)
        self.assertGreaterEqual(BOARD_LEFT, 36)
        self.assertGreaterEqual(BOARD_TOP, 36)
        self.assertLessEqual(BOARD_LEFT + BOARD_PX, 684)
        self.assertLessEqual(BOARD_TOP + BOARD_PX, 684)
        hashes = {hashlib.sha256(renderer._tiles[(scene.puzzle.art_name, 8, tile_id)]).digest() for tile_id in range(9)}
        self.assertEqual(len(hashes), 9)
        for frame in (0, plan.timeline["reveal_start"], plan.timeline["solve_end"], plan.timeline["total"] - 1):
            pixels = renderer.render_frame(scene, frame)
            self.assertEqual(len(pixels), 720 * 720 * 3)
            self.assertGreater(len(set(pixels)), 3)
        for frame in range(plan.timeline["reveal_start"], plan.timeline["solve_end"] - 4, 4):
            self.assertNotEqual(renderer.render_frame(scene, frame), renderer.render_frame(scene, frame + 4))


if __name__ == "__main__":
    unittest.main()
