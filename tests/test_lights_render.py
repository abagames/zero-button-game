import dataclasses
import hashlib
import unittest
from unittest import mock

from zero_button_game.core import StableRng, derive_seed
from zero_button_game.lights import (
    LightsRules, LightsSolver, generate_lights, lights_difficulty_preset, plus_cells, replay_lights,
)
from zero_button_game.lights_presentation import lights_plan
from zero_button_game.lights_render import (
    CELL_PX, MINI_CELL_PX, TITLE, LightsRenderer, LightsSceneBuilder, alternate_lights_scene,
)
from zero_button_game.models import TimelineSpec
from zero_button_game.registry import get_plugin
from zero_button_game.render import FONT
from zero_button_game.validation import pre_reveal_neutrality_failure

SAFE = (36, 36, 684, 684)
_SCENES: dict[tuple[str, int, int], tuple] = {}


def _scene(band="target", candidate=0, master_seed=20260822):
    key = (band, candidate, master_seed)
    if key not in _SCENES:
        _SCENES[key] = _build_scene(band, candidate, master_seed)
    return _SCENES[key]


def _build_scene(band, candidate, master_seed):
    rules = LightsRules()
    puzzle = generate_lights(
        StableRng(derive_seed(master_seed, "lights", candidate, "generation")),
        lights_difficulty_preset(band),
    )
    solution = LightsSolver(rules).solve(puzzle)
    units = len(solution.actions)
    timeline = TimelineSpec(solve_duration=max(2.0, min(4.0, units * 2 / 20)))
    plan = lights_plan(puzzle, solution, rules, timeline)
    trace = replay_lights(puzzle, plan.logical_steps, rules)
    return LightsSceneBuilder().build(puzzle, plan, trace), LightsRenderer(), plan


class LightsRenderContractTests(unittest.TestCase):
    def test_title_and_labels_use_available_glyphs(self):
        for text in (TITLE, "THINK", "PRESS", "LIT", "CLEAR"):
            for character in text:
                self.assertIn(character, FONT, f"{character!r} missing from the bitmap font")
        # J and Q are genuinely absent from the font; nothing here may use them.
        self.assertNotIn("J", FONT)
        self.assertNotIn("Q", FONT)

    def test_pre_reveal_neutrality_and_reveal_boundary(self):
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, plan = _scene(band)
                plugin = get_plugin("lights")
                self.assertIsNone(pre_reveal_neutrality_failure(plugin, renderer, scene, plan.timeline))
                alternate = alternate_lights_scene(scene)
                self.assertNotEqual(alternate.presses, scene.presses)
                self.assertNotEqual(alternate.presses[0], scene.presses[0])
                reveal = plan.timeline["reveal_start"]
                self.assertIsNone(renderer.semantic_snapshot(scene, reveal - 1)["press_cell"])
                self.assertIsNotNone(renderer.semantic_snapshot(scene, reveal)["press_cell"])
                self.assertNotEqual(
                    hashlib.sha256(renderer.render_frame(scene, reveal)).digest(),
                    hashlib.sha256(renderer.render_frame(alternate, reveal)).digest(),
                )

    def test_two_press_scene_still_rotates_to_a_different_first_press(self):
        """|S| = 2 is the floor: the cyclic shift must still change the reveal."""
        scene, renderer, plan = _scene("easy")
        self.assertEqual(len(scene.presses), 2)
        alternate = alternate_lights_scene(scene)
        self.assertEqual(set(alternate.presses), set(scene.presses))
        self.assertNotEqual(alternate.presses[0], scene.presses[0])
        self.assertEqual(alternate.states[0], scene.states[0])
        self.assertEqual(alternate.states[-1], scene.states[-1])
        reveal = plan.timeline["reveal_start"]
        self.assertNotEqual(
            hashlib.sha256(renderer.render_frame(scene, reveal)).digest(),
            hashlib.sha256(renderer.render_frame(alternate, reveal)).digest(),
        )
        self.assertIsNone(pre_reveal_neutrality_failure(get_plugin("lights"), renderer, scene, plan.timeline))

    def test_presses_are_distinct_and_at_least_two(self):
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, _, _ = _scene(band)
                self.assertGreaterEqual(len(scene.presses), 2)
                self.assertEqual(len(set(scene.presses)), len(scene.presses))

    def test_pre_reveal_snapshot_and_legend_ignore_the_solution(self):
        scene, renderer, plan = _scene()
        alternate = alternate_lights_scene(scene)
        self.assertEqual(renderer.legend_geometry(scene), renderer.legend_geometry(alternate))
        for frame in range(plan.timeline["reveal_start"]):
            self.assertEqual(
                renderer.semantic_snapshot(scene, frame), renderer.semantic_snapshot(alternate, frame)
            )
            self.assertEqual(
                renderer.semantic_snapshot(scene, frame)["lights"], tuple(scene.puzzle.initial)
            )

    def test_solve_phase_never_stalls_for_200ms(self):
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, plan = _scene(band)
                for frame in range(plan.timeline["reveal_start"], plan.timeline["solve_end"] - 4, 4):
                    self.assertNotEqual(
                        renderer.render_frame(scene, frame), renderer.render_frame(scene, frame + 4),
                        f"solve stalls between frames {frame} and {frame + 4}",
                    )

    def test_press_marker_animates_on_every_single_solve_frame(self):
        """The anchored marker alone carries the solve past the 4-frame check."""
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, plan = _scene(band)
                previous = None
                for frame in range(plan.timeline["reveal_start"], plan.timeline["solve_end"]):
                    signature = renderer.marker_signature(scene, frame)
                    if previous is not None:
                        self.assertNotEqual(signature, previous, f"marker stalls at frame {frame}")
                    previous = signature

    def test_marker_never_travels_between_cells(self):
        """Every indicated point sits exactly on the cell centre being pressed."""
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, plan = _scene(band)
                for frame in range(plan.timeline["reveal_start"], plan.timeline["solve_end"]):
                    snapshot = renderer.semantic_snapshot(scene, frame)
                    cell = snapshot["press_cell"]
                    if cell is None:
                        self.assertIsNone(snapshot["focus"])
                        continue
                    self.assertEqual(snapshot["focus"], renderer.cell_center(scene, cell))

    def test_focus_bracket_closes_in_on_the_pressed_cell(self):
        scene, renderer, _ = _scene()
        for order in range(len(scene.presses)):
            opening = renderer.press_snapshot_for_units(scene, float(order))["focus_span"]
            closing = renderer.press_snapshot_for_units(scene, order + 1 - 1e-9)["focus_span"]
            self.assertGreater(opening, closing)
            # It ends inside the pressed cell, so the indication is unambiguous.
            self.assertLessEqual(closing, CELL_PX / 2)

    def test_pressed_cells_keep_their_order_badges(self):
        scene, renderer, plan = _scene()
        for order in range(1, len(scene.presses) + 1):
            snapshot = renderer.press_snapshot_for_units(scene, order + 0.5) if order < len(scene.presses) \
                else renderer.press_snapshot_for_units(scene, float(order))
            self.assertEqual(snapshot["pressed"], order)
        # Badges stay inside their own cell.
        for order, cell in enumerate(scene.presses, start=1):
            x0, y0, x1, y1 = renderer._badge_box(scene, cell, order)
            ox, oy = renderer.board_origin(scene)
            left, top = ox + cell[0] * CELL_PX, oy + cell[1] * CELL_PX
            self.assertGreaterEqual(x0, left)
            self.assertGreaterEqual(y0, top)
            self.assertLessEqual(x1, left + CELL_PX)
            self.assertLessEqual(y1, top + CELL_PX)
        self.assertEqual(renderer.semantic_snapshot(scene, plan.timeline["reveal_start"])["pressed"], 0)

    def test_all_order_badges_remain_through_lit_and_clear(self):
        """A still LIT or CLEAR frame must recover the complete press order."""
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, plan = _scene(band)
                expected = [(cell, order) for order, cell in enumerate(scene.presses, start=1)]
                timeline = plan.timeline
                for phase, frame in (
                    ("LIT start", timeline["solve_end"]),
                    ("LIT end", timeline["result_end"] - 1),
                    ("CLEAR start", timeline["result_end"]),
                    ("CLEAR end", timeline["total"] - 1),
                ):
                    with self.subTest(phase=phase), mock.patch.object(
                        renderer, "_draw_badge", wraps=renderer._draw_badge
                    ) as draw_badge:
                        renderer.render_frame(scene, frame)
                        observed = [(call.args[2], call.args[3]) for call in draw_badge.call_args_list]
                        self.assertEqual(observed, expected)

    def test_frame_zero_is_not_blank_and_bounds_stay_in_safe_area(self):
        scene, renderer, plan = _scene()
        first = renderer.render_frame(scene, 0)
        last = renderer.render_frame(scene, plan.timeline["total"] - 1)
        self.assertGreater(len(set(first)), 3)
        self.assertGreater(len(set(last)), 3)
        bounds = scene.semantic_bounds
        self.assertLessEqual(SAFE[0], bounds[0])
        self.assertLessEqual(SAFE[1], bounds[1])
        self.assertLessEqual(bounds[2], SAFE[2])
        self.assertLessEqual(bounds[3], SAFE[3])

    def test_board_and_legend_geometry_fit_the_safe_area(self):
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, _ = _scene(band)
                bx0, by0, bx1, by1 = renderer.board_extent(scene)
                self.assertGreaterEqual(bx0, SAFE[0])
                self.assertGreaterEqual(by0, SAFE[1])
                self.assertLessEqual(bx1, SAFE[2])
                self.assertLessEqual(by1, SAFE[3])
                legend = renderer.legend_geometry(scene)
                for name in ("goal_panel", "rule_panel"):
                    box = legend[name]
                    self.assertGreaterEqual(box[0], SAFE[0])
                    self.assertLessEqual(box[2], SAFE[2])
                    # The legend sits strictly below the board, never over it.
                    self.assertGreaterEqual(box[1], by1)
                    self.assertLessEqual(box[3], SAFE[3])

    def test_render_contract_checks_pass_for_every_band(self):
        plugin = get_plugin("lights")
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                scene, renderer, plan = _scene(band)
                passed, failed = plugin.render_contract_checks(scene, renderer)
                self.assertEqual(failed, [])
                for check in (
                    "board_fully_lit", "minimum_cell_size", "minimum_legend_cell_size",
                    "board_and_legend_within_safe_area", "legend_solution_independent",
                    "toggle_action_rendering", "press_marker_animates_every_frame", "lit_after_last_press",
                    "unique_press_set", "lit_state_immutable", "legend_cue_neutral",
                ):
                    self.assertIn(check, passed)
                contract = plugin.visual_contract(scene, renderer)
                self.assertGreaterEqual(contract["cell_px"], contract["minimum_cell_px"])
                self.assertGreaterEqual(contract["cell_body_px"], contract["minimum_cell_body_px"])
                self.assertGreaterEqual(contract["mini_cell_px"], contract["minimum_mini_cell_px"])
                self.assertIs(contract["legend_solution_dependent"], False)

    def test_presses_match_the_replayed_states(self):
        scene, renderer, _ = _scene()
        for order, step in enumerate(scene.trace.steps):
            snapshot = renderer.press_snapshot_for_units(scene, float(order + 1))
            self.assertEqual(snapshot["pressed"], order + 1)
            self.assertEqual(snapshot["lights"], tuple(step.after.lights))
        final = renderer.press_snapshot_for_units(scene, float(len(scene.presses)))
        self.assertIsNone(final["press_cell"])
        self.assertTrue(all(value == 1 for value in final["lights"]))

    def test_mid_press_blends_exactly_the_plus(self):
        scene, renderer, _ = _scene()
        for order, cell in enumerate(scene.presses):
            middle = renderer.press_snapshot_for_units(scene, order + 0.5)
            self.assertEqual(middle["press_cell"], cell)
            self.assertAlmostEqual(middle["progress"], 0.5)
            self.assertEqual(
                set(middle["blend"]), set(plus_cells(scene.puzzle.width, scene.puzzle.height, cell))
            )
            self.assertLessEqual(len(middle["blend"]), 5)
            self.assertGreater(middle["pulse_radius"], 0.0)

    def test_alternate_scene_states_stay_consistent(self):
        scene, _, _ = _scene()
        alternate = alternate_lights_scene(scene)
        self.assertEqual(alternate.states[0], scene.states[0])
        self.assertEqual(alternate.states[-1], scene.states[-1])
        self.assertTrue(all(value == 1 for value in alternate.states[-1]))

    def test_scene_builder_rejects_short_or_repeated_press_sets(self):
        scene, _, plan = _scene()
        trimmed = dataclasses.replace(scene.trace, steps=scene.trace.steps[:1])
        with self.assertRaises(ValueError):
            LightsSceneBuilder().build(scene.puzzle, plan, trimmed)
        repeated = dataclasses.replace(
            scene.trace, steps=scene.trace.steps[:1] + scene.trace.steps[:1] + scene.trace.steps[1:]
        )
        with self.assertRaises(ValueError):
            LightsSceneBuilder().build(scene.puzzle, plan, repeated)

    def test_geometry_constants_are_the_measured_layout(self):
        self.assertEqual(CELL_PX, 96)
        self.assertEqual(MINI_CELL_PX, 32)
        self.assertEqual(TITLE, "ALL LIGHTS ON")


if __name__ == "__main__":
    unittest.main()
