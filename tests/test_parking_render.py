import hashlib
import unittest

from zero_button_game.core import StableRng, derive_seed
from zero_button_game.models import TimelineSpec
from zero_button_game.parking import (
    ParkingRules, ParkingSolver, generate_parking, parking_difficulty_preset, replay_parking,
)
from zero_button_game.parking_presentation import parking_plan
from zero_button_game.parking_render import ParkingRenderer, ParkingSceneBuilder, alternate_parking_scene
from zero_button_game.registry import get_plugin
from zero_button_game.render import FONT
from zero_button_game.validation import pre_reveal_neutrality_failure


_SCENES: dict[tuple[str, int, int], tuple] = {}


def _scene(band="target", candidate=0, master_seed=None):
    if master_seed is None:
        master_seed = 9614 if band == "target" else 20260822
    key = (band, candidate, master_seed)
    if key not in _SCENES:
        _SCENES[key] = _build_scene(band, candidate, master_seed)
    return _SCENES[key]


def _build_scene(band, candidate, master_seed):
    rules = ParkingRules()
    puzzle = generate_parking(StableRng(derive_seed(master_seed, "parking", candidate, "generation")), parking_difficulty_preset(band))
    solution = ParkingSolver(rules).solve(puzzle)
    units = sum(action.params["slide_cells"][0] for action in solution.actions)
    timeline = TimelineSpec(solve_duration=max(2.0, min(4.0, units * 2 / 20)))
    plan = parking_plan(puzzle, solution, rules, timeline)
    trace = replay_parking(puzzle, plan.logical_steps, rules)
    return ParkingSceneBuilder().build(puzzle, plan, trace), ParkingRenderer(), plan


class ParkingRenderContractTests(unittest.TestCase):
    def test_title_and_labels_use_available_glyphs(self):
        for text in ("GET THE CAR OUT", "THINK", "SLIDE", "EXIT", "CLEAR"):
            for character in text:
                self.assertIn(character, FONT, f"{character!r} missing from the bitmap font")

    def test_pre_reveal_neutrality_and_reveal_boundary(self):
        scene, renderer, plan = _scene()
        plugin = get_plugin("parking")
        self.assertIsNone(pre_reveal_neutrality_failure(plugin, renderer, scene, plan.timeline))
        alternate = alternate_parking_scene(scene)
        self.assertNotEqual(alternate.moves, scene.moves)
        self.assertNotEqual(alternate.moves[0][0], scene.moves[0][0])
        reveal = plan.timeline["reveal_start"]
        self.assertIsNone(renderer.semantic_snapshot(scene, reveal - 1)["moving_vehicle"])
        self.assertIsNotNone(renderer.semantic_snapshot(scene, reveal)["moving_vehicle"])
        self.assertNotEqual(
            hashlib.sha256(renderer.render_frame(scene, reveal)).digest(),
            hashlib.sha256(renderer.render_frame(alternate, reveal)).digest(),
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

    def test_render_contract_checks_pass_for_every_band(self):
        plugin = get_plugin("parking")
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, plan = _scene(band)
                passed, failed = plugin.render_contract_checks(scene, renderer)
                self.assertEqual(failed, [])
                for check in (
                    "target_vehicle_released", "minimum_cell_size", "minimum_vehicle_body",
                    "minimum_exit_gap", "slide_action_rendering", "release_after_last_slide",
                    "normalized_solution_unique", "release_state_immutable",
                ):
                    self.assertIn(check, passed)
                contract = plugin.visual_contract(scene, renderer)
                self.assertGreaterEqual(contract["cell_size_px"], contract["minimum_cell_size_px"])
                self.assertGreaterEqual(contract["exit_gap_px"], contract["minimum_exit_gap_px"])

    def test_slide_positions_match_the_replayed_states(self):
        scene, renderer, _ = _scene()
        cumulative = 0
        for step in scene.trace.steps:
            cumulative += step.action.params["slide_cells"][0]
            snapshot = renderer.position_snapshot_for_units(scene, float(cumulative))
            self.assertEqual(tuple(round(value) for value in snapshot["offsets"]), step.after.positions)
        final = renderer.position_snapshot_for_units(scene, float(cumulative))
        self.assertTrue(ParkingRules().is_goal(scene.puzzle, scene.trace.final))
        self.assertEqual(tuple(round(value) for value in final["offsets"]), scene.trace.final.positions)


if __name__ == "__main__":
    unittest.main()
