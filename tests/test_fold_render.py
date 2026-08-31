import hashlib
import tempfile
import unittest
from pathlib import Path

from zero_button_game.core import StableRng, derive_seed
from zero_button_game.fold import (
    FoldRules, FoldSolver, actions_for_folds, fold_difficulty_preset, generate_fold, replay_fold,
    solution_folds, split_folds,
)
from zero_button_game.fold_presentation import fold_plan
from zero_button_game.fold_render import (
    CELL_PX, MINI_CELL_PX, MIN_CELL_PX, MIN_MINI_CELL_PX, PANEL_BOX, SAFE_AREA, FoldRenderer, FoldSceneBuilder,
    alternate_fold_scene, visible_value,
)
from zero_button_game.models import TimelineSpec
from zero_button_game.registry import get_plugin
from zero_button_game.render import BACKGROUND, _quad, _rect

FRAME_BYTES = 720 * 720 * 3


def build_scene(band="medium", seed=2026, index=1):
    rules = FoldRules()
    puzzle = generate_fold(StableRng(derive_seed(seed, "fold", index, "generation")), fold_difficulty_preset(band))
    solution = FoldSolver(rules).solve(puzzle)
    plan = fold_plan(puzzle, solution, rules, TimelineSpec())
    trace = replay_fold(puzzle, solution.actions, rules)
    return puzzle, solution, plan, FoldSceneBuilder().build(puzzle, plan, trace), rules


class QuadPrimitiveTests(unittest.TestCase):
    def test_quad_fills_an_axis_aligned_rectangle(self):
        buf = bytearray(bytes(BACKGROUND) * 40 * 40)
        reference = bytearray(bytes(BACKGROUND) * 40 * 40)
        _quad(buf, 40, 40, ((10.0, 10.0), (30.0, 10.0), (30.0, 25.0), (10.0, 25.0)), (255, 0, 0))
        _rect(reference, 40, 40, 10, 10, 30, 25, (255, 0, 0))
        self.assertEqual(bytes(buf), bytes(reference))

    def test_quad_fills_a_trapezoid_row_by_row(self):
        buf = bytearray(bytes(BACKGROUND) * 40 * 40)
        _quad(buf, 40, 40, ((20.0, 5.0), (20.0, 35.0), (30.0, 30.0), (30.0, 10.0)), (0, 255, 0))
        def pixel(x, y):
            offset = (y * 40 + x) * 3
            return tuple(buf[offset:offset + 3])
        self.assertEqual(pixel(25, 20), (0, 255, 0))
        self.assertEqual(pixel(25, 6), BACKGROUND)
        self.assertEqual(pixel(5, 20), BACKGROUND)

    def test_quad_requires_four_points(self):
        buf = bytearray(bytes(BACKGROUND) * 10 * 10)
        with self.assertRaises(ValueError):
            _quad(buf, 10, 10, ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)), (1, 2, 3))

    def test_quad_is_used_by_the_fold_renderer_only(self):
        root = Path(__file__).resolve().parents[1] / "src" / "zero_button_game"
        users = sorted(
            path.name for path in root.glob("*.py")
            if "_quad(" in path.read_text(encoding="utf-8") and path.name != "render.py"
        )
        self.assertEqual(users, ["fold_render.py"])


class FoldSceneBuilderTests(unittest.TestCase):
    def test_builder_accepts_a_solved_trace(self):
        _, solution, _, scene, _ = build_scene()
        self.assertEqual(scene.folds, solution_folds(solution))
        self.assertEqual(len(scene.states), len(scene.folds) + 1)

    def test_builder_rejects_an_unsolved_trace(self):
        puzzle, solution, plan, _, rules = build_scene()
        short = replay_fold(puzzle, solution.actions[:-1], rules)
        with self.assertRaises(ValueError):
            FoldSceneBuilder().build(puzzle, plan, short)

    def test_builder_rejects_a_single_axis_solution(self):
        puzzle, solution, plan, _, rules = build_scene()
        vertical, _ = split_folds(solution_folds(solution))
        trace = replay_fold(puzzle, actions_for_folds(puzzle, tuple((0, line, direction) for line, direction in vertical), rules), rules)
        with self.assertRaises(ValueError):
            FoldSceneBuilder().build(puzzle, plan, trace)


class FoldRendererTests(unittest.TestCase):
    def setUp(self):
        self.puzzle, self.solution, self.plan, self.scene, self.rules = build_scene()
        self.renderer = FoldRenderer()
        self.timeline = self.plan.timeline

    def test_every_frame_is_full_size_raw_rgb(self):
        for frame in (0, self.timeline["appearance"], self.timeline["reveal_start"], self.timeline["solve_end"], self.timeline["total"] - 1):
            with self.subTest(frame=frame):
                self.assertEqual(len(self.renderer.render_frame(self.scene, frame)), FRAME_BYTES)

    def test_frame_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            self.renderer.render_frame(self.scene, self.timeline["total"])

    def test_frame_zero_is_not_blank(self):
        frame = self.renderer.render_frame(self.scene, 0)
        self.assertNotEqual(set(frame), {*bytes(BACKGROUND)})
        # the title is drawn without fade, so frame zero always carries pixels
        self.assertGreater(sum(1 for index in range(0, len(frame), 3) if frame[index] > 200), 500)

    def test_pre_reveal_frames_are_neutral_under_the_perturbation(self):
        alternate = alternate_fold_scene(self.scene)
        self.assertNotEqual(alternate.folds, self.scene.folds)
        for frame in range(0, self.timeline["reveal_start"], 7):
            with self.subTest(frame=frame):
                self.assertEqual(self.renderer.render_frame(self.scene, frame), self.renderer.render_frame(alternate, frame))

    def test_reveal_frame_changes_under_the_perturbation(self):
        alternate = alternate_fold_scene(self.scene)
        reveal = self.timeline["reveal_start"]
        self.assertNotEqual(self.renderer.render_frame(self.scene, reveal), self.renderer.render_frame(alternate, reveal))

    def test_the_perturbation_is_still_a_legal_solution(self):
        alternate = alternate_fold_scene(self.scene)
        trace = replay_fold(self.puzzle, alternate.plan.logical_steps, self.rules)
        self.assertTrue(self.rules.is_goal(self.puzzle, trace.final))
        self.assertEqual(tuple(state.extent for state in alternate.states)[-1], tuple(self.puzzle.target))

    def test_snapshot_boundaries_match_the_replay_states(self):
        for index in range(len(self.scene.folds)):
            middle = self.renderer.fold_snapshot_for_units(self.scene, index + 0.5)
            self.assertEqual(middle["fold_index"], index)
            self.assertEqual(middle["fold"], self.scene.folds[index])
            self.assertEqual(middle["state"], self.scene.states[index])
            self.assertTrue(0.0 < middle["progress"] < 1.0)
            self.assertEqual(self.renderer.fold_snapshot_for_units(self.scene, float(index + 1))["state"], self.scene.states[index + 1])

    def test_snapshot_clamps_outside_the_unit_range(self):
        total = float(len(self.scene.folds))
        self.assertEqual(self.renderer.fold_snapshot_for_units(self.scene, -5.0)["units"], 0.0)
        self.assertEqual(self.renderer.fold_snapshot_for_units(self.scene, total + 5.0)["units"], total)
        self.assertIsNone(self.renderer.fold_snapshot_for_units(self.scene, total)["fold"])

    def test_pre_reveal_snapshot_reads_the_flat_sheet_only(self):
        snapshot = self.renderer.semantic_snapshot(self.scene, self.timeline["reveal_start"] - 1)
        self.assertEqual(snapshot["state"], self.scene.states[0])
        self.assertIsNone(snapshot["fold"])
        self.assertFalse(snapshot["solved"])

    def test_fold_angle_advances_on_every_solve_frame(self):
        previous = None
        for frame in range(self.timeline["reveal_start"], self.timeline["solve_end"]):
            snapshot = self.renderer.semantic_snapshot(self.scene, frame)
            if previous is not None:
                self.assertGreater(snapshot["units"], previous["units"])
                if snapshot["fold_index"] == previous["fold_index"]:
                    self.assertGreater(snapshot["angle"], previous["angle"])
            previous = snapshot

    def test_solve_frames_all_differ(self):
        digests = [
            hashlib.sha256(self.renderer.render_frame(self.scene, frame)).hexdigest()
            for frame in range(self.timeline["reveal_start"], self.timeline["solve_end"], 3)
        ]
        self.assertEqual(len(set(digests)), len(digests))

    def test_visible_value_is_the_union_of_the_stack(self):
        final = self.scene.states[-1]
        x0, y0, x1, y1 = final.extent
        for y in range(y0, y1):
            for x in range(x0, x1):
                self.assertEqual(visible_value(final, (x, y)) == 2, final.covered_at((x, y)))

    def test_final_stack_has_depth_to_draw(self):
        self.assertGreaterEqual(self.scene.states[-1].max_depth(), 2)

    def test_layout_stays_inside_the_safe_area(self):
        geometry = self.renderer.legend_geometry(self.scene)
        board = self.renderer.board_extent(self.scene)
        self.assertGreaterEqual(CELL_PX, MIN_CELL_PX)
        self.assertGreaterEqual(MINI_CELL_PX, MIN_MINI_CELL_PX)
        for box in (board, tuple(geometry["goal_panel"]), tuple(geometry["rule_panel"]), PANEL_BOX):
            with self.subTest(box=box):
                self.assertGreaterEqual(box[0], SAFE_AREA[0])
                self.assertGreaterEqual(box[1], SAFE_AREA[1])
                self.assertLessEqual(box[2], SAFE_AREA[2])
                self.assertLessEqual(box[3], SAFE_AREA[3])

    def test_legend_and_target_never_read_the_fold_class(self):
        alternate = alternate_fold_scene(self.scene)
        self.assertEqual(self.renderer.legend_geometry(alternate), self.renderer.legend_geometry(self.scene))
        self.assertEqual(self.renderer.target_box(alternate), self.renderer.target_box(self.scene))
        self.assertEqual(self.renderer.target_box(self.scene)[2] - self.renderer.target_box(self.scene)[0],
                         (self.puzzle.target[2] - self.puzzle.target[0]) * CELL_PX)

    def test_render_writes_one_ppm_per_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.renderer.render(self.scene, Path(directory))
            self.assertEqual(len(paths), self.timeline["total"])
            self.assertTrue(all(path.exists() for path in paths))
            self.assertEqual(len(paths[0].read_bytes()) - len(b"P6\n720 720\n255\n"), FRAME_BYTES)


class FoldPluginContractTests(unittest.TestCase):
    def setUp(self):
        self.plugin = get_plugin("fold")
        self.puzzle, self.solution, self.plan, self.scene, self.rules = build_scene()
        self.renderer = self.plugin.renderer_factory()

    def test_render_contract_checks_pass(self):
        passed, failed = self.plugin.render_contract_checks(self.scene, self.renderer)
        self.assertEqual(failed, [])
        for name in (
            "target_filled_at_goal", "board_and_legend_within_safe_area", "legend_solution_independent",
            "fold_action_rendering", "fold_angle_advances_every_frame", "filled_after_last_fold",
            "outline_and_depth_carry_state", "unique_fold_class", "legend_cue_neutral",
        ):
            self.assertIn(name, passed)

    def test_visual_contract_claims_more_than_colour(self):
        contract = self.plugin.visual_contract(self.scene, self.renderer)
        self.assertEqual(contract["safe_area"], [36, 36, 684, 684])
        self.assertTrue(contract["state_change_not_color_only"])
        self.assertFalse(contract["legend_solution_dependent"])
        self.assertEqual(contract["semantic_bounds"], list(PANEL_BOX))

    def test_metadata_contract_checks_agree_with_the_solver(self):
        difficulty = self.plugin.difficulty(self.puzzle, self.solution, self.rules)
        self.plugin.quality_filter(difficulty, "medium")
        passed, failed = self.plugin.metadata_contract_checks(self.puzzle, self.solution, {"difficulty": difficulty})
        self.assertEqual((passed, failed), (["uniqueness_metadata"], []))

    def test_plan_cues_declare_neutral_legend_and_target(self):
        kinds = {cue["kind"] for cue in self.plan.visual_cues}
        self.assertTrue({"rule_legend", "target_outline", "fold_crease", "target_filled"} <= kinds)
        for cue in self.plan.visual_cues:
            if cue["kind"] in {"rule_legend", "target_outline"}:
                self.assertFalse(cue["solution_dependent"])
                self.assertFalse(cue["state_mutation"])

    def test_animation_units_match_the_fold_count(self):
        self.assertEqual(self.plugin.animation_units(self.solution), len(self.scene.folds))

    def test_timeline_and_calibration_labels(self):
        standards = {
            "easy": (4.0, "fold-easy-standard-4s-v1"),
            "medium": (6.0, "fold-medium-standard-6s-v1"),
            "target": (6.0, "fold-target-standard-6s-v1"),
        }
        for band, (seconds, label) in standards.items():
            with self.subTest(band=band):
                self.assertEqual(self.plugin.calibration_label(band), "uncalibrated-fold-v1")
                self.assertEqual(self.plugin.difficulty_preset(band)["thinking_time_seconds"], seconds)
                self.assertEqual(self.plugin.timeline_preset_label(band, seconds, False), label)
                profile = self.plugin.timing_calibration_profile(band)
                self.assertEqual(profile["standard_thinking_time_seconds"], seconds)
                self.assertEqual(profile["calibration_status"], "calibrated-within-person-timing-only")
                self.assertEqual(
                    profile["source_evaluation"],
                    "studies/timing_sweep_round5_fold_calibration_2026-08-24.json",
                )


if __name__ == "__main__":
    unittest.main()
