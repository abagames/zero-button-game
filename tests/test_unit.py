import unittest
from pathlib import Path

from zero_button_game.core import StableRng, derive_seed, sha256_value
from zero_button_game.export import DEFAULT_FORMATS, parse_formats
from zero_button_game.cli import build_parser
from zero_button_game.maze import MIXED_TRAIT_TARGETS, MazeRules, MazeSolver, difficulty_preset, difficulty_report, generate_maze, quality_rejection
from zero_button_game.models import Action, PuzzleSpec, Solution, TimelineSpec
from zero_button_game.pipeline import GenerationRequest, timeline_for_request
from zero_button_game.registry import get_plugin
from zero_button_game.validation import declared_formats, timing_calibration_status_matches


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.rules = MazeRules()
        self.puzzle = generate_maze(StableRng(derive_seed(42, "maze", 2, "generation")))

    def test_seed_fixed_vectors(self):
        self.assertEqual(derive_seed(42, "maze", 0, "generation"), 0x19F98E5FC0C997FF2D098A3796BEE392)
        self.assertEqual(derive_seed(42, "maze", 2, "presentation"), 0xFFB69D06C896C2706C3C690F0129B95F)

    def test_timeline_round_half_up_and_boundaries(self):
        frames = TimelineSpec().frames()
        self.assertEqual(frames["appearance"], 6)
        self.assertEqual(frames["reveal_start"], 50)
        self.assertEqual(frames["total"], 120)
        with self.assertRaises(ValueError):
            TimelineSpec(thinking_duration=-0.1).frames()

    def test_pipes_target_thinking_time_changes_only_pre_reveal_phase(self):
        baseline = timeline_for_request(GenerationRequest("pipes", 1, "target", 121, Path("unused")), 8).frames()
        self.assertEqual(baseline["reveal_start"], 160)
        self.assertEqual(baseline["total"], 230)
        fixed = {key: baseline[key] for key in ("appearance", "anticipation", "solve", "result", "transition")}
        for seconds, expected_total in ((3.0, 130), (3.5, 140), (4.0, 150), (12.0, 310), (20.0, 470)):
            request = GenerationRequest(
                "pipes", 1, "target", 121, Path("unused"),
                thinking_time_seconds=seconds, timing_variant=f"{seconds:.1f}",
            )
            frames = timeline_for_request(request, 8).frames()
            self.assertEqual(frames["reveal_start"], int(seconds * 20))
            self.assertEqual(frames["total"], expected_total)
            self.assertEqual({key: frames[key] for key in fixed}, fixed)
        # The override is available for every plugin, not only the ones with a
        # calibration round of their own.
        for puzzle_type in ("maze", "parking", "packing", "lights", "fold"):
            overridden = timeline_for_request(
                GenerationRequest(puzzle_type, 1, "target", 1, Path("unused"), thinking_time_seconds=12.0), 8
            ).frames()
            self.assertEqual(overridden["reveal_start"], 240)
        with self.assertRaisesRegex(ValueError, "between the 2.5s baseline and 20.0s"):
            timeline_for_request(GenerationRequest("maze", 1, "target", 1, Path("unused"), thinking_time_seconds=20.5), 8)
        with self.assertRaisesRegex(ValueError, "between the 2.5s baseline and 20.0s"):
            timeline_for_request(GenerationRequest("pipes", 1, "target", 1, Path("unused"), thinking_time_seconds=2.4), 8)
        with self.assertRaisesRegex(ValueError, "20fps frame grid"):
            timeline_for_request(GenerationRequest("pipes", 1, "target", 1, Path("unused"), thinking_time_seconds=8.03), 8)
        # Current JSON is authoritative for every standard duration.
        for band, reveal_start, total in (("easy", 80, 150), ("medium", 120, 190)):
            swept = timeline_for_request(GenerationRequest("pipes", 1, band, 1, Path("unused")), 8).frames()
            self.assertEqual(swept["reveal_start"], reveal_start)
            self.assertEqual(swept["total"], total)
        standard_thinking_time = {
            "maze": {"easy": 2.5, "medium": 2.5, "target": 3.5},
            "pipes": {"easy": 4.0, "medium": 6.0, "target": 8.0},
            "parking": {"easy": 4.0, "medium": 4.0, "target": 8.0},
            "packing": {"easy": 4.0, "medium": 4.0, "target": 8.0},
            "lights": {"easy": 4.0, "medium": 6.0, "target": 8.0},
            "fold": {"easy": 4.0, "medium": 6.0, "target": 6.0},
        }
        for puzzle_type, bands in standard_thinking_time.items():
            for band, seconds in bands.items():
                self.assertEqual(get_plugin(puzzle_type).difficulty_preset(band)["thinking_time_seconds"], seconds)
        maze_target = timeline_for_request(GenerationRequest("maze", 1, "target", 1, Path("unused")), 8).frames()
        self.assertEqual(maze_target["reveal_start"], 70)
        for band in ("easy", "medium"):
            maze_band = timeline_for_request(GenerationRequest("maze", 1, band, 1, Path("unused")), 8).frames()
            self.assertEqual(maze_band["reveal_start"], 50)
            self.assertEqual(maze_band["total"], 120)

    def test_timeline_preset_label_follows_the_preset_standard(self):
        """The label must not carry a second copy of the standard seconds."""
        from zero_button_game.registry import GENERIC_TIMELINE_PRESET_LABEL
        expected = {
            ("maze", "easy"): "maze-easy-standard-2.5s-v1",
            ("maze", "target"): "maze-target-standard-3.5s-v1",
            ("pipes", "easy"): "pipes-easy-standard-4s-v1",
            ("pipes", "target"): "pipes-target-standard-8s-v1",
            ("parking", "medium"): "parking-medium-standard-4s-v1",
            ("packing", "target"): "packing-target-standard-8s-v1",
            ("lights", "easy"): "lights-easy-standard-4s-v1",
            ("fold", "target"): "fold-target-standard-6s-v1",
        }
        for (puzzle_type, band), label in expected.items():
            plugin = get_plugin(puzzle_type)
            seconds = plugin.difficulty_preset(band)["thinking_time_seconds"]
            self.assertEqual(plugin.timeline_preset_label(band, seconds, False), label)
            # An explicit override is never a standard work.
            self.assertEqual(plugin.timeline_preset_label(band, seconds, True), GENERIC_TIMELINE_PRESET_LABEL)
            # A non-standard duration falls back to the generic label.
            self.assertEqual(plugin.timeline_preset_label(band, seconds + 0.5, False), GENERIC_TIMELINE_PRESET_LABEL)

    def test_historical_lights_6_8_8_timing_metadata_remains_accepted(self):
        source_by_band = {
            "easy": "studies/timing_sweep_round2_calibration_2026-08-23.json",
            "medium": "studies/timing_sweep_round2_calibration_2026-08-23.json",
            "target": "studies/timing_sweep_round3_calibration_2026-08-23.json",
        }
        for band, seconds, previous in (("easy", 6.0, None), ("medium", 8.0, None), ("target", 8.0, 6.5)):
            calibration = {
                "previous_evaluated_thinking_time_seconds": previous,
                "target_standard_thinking_time_seconds": seconds,
                "calibration_status": "calibrated-within-person-timing-only",
                "timing_status": "calibrated-within-person-timing-only",
                "source_evaluation": source_by_band[band],
            }
            with self.subTest(band=band):
                self.assertTrue(timing_calibration_status_matches(calibration, seconds, "lights", band))

    def test_problem_and_solution_round_trip(self):
        self.assertEqual(PuzzleSpec.from_dict(self.puzzle.to_dict()), self.puzzle)
        solution = MazeSolver(self.rules).solve(self.puzzle)
        self.assertEqual(Solution.from_dict(solution.to_dict()), solution)

    def test_structure_and_plugin_registry(self):
        self.assertEqual(self.rules.validate_structure(self.puzzle), [])
        self.assertEqual(get_plugin("maze").puzzle_type, "maze")
        self.assertEqual(get_plugin("pipes").puzzle_type, "pipes")
        with self.assertRaises(ValueError):
            get_plugin("future-puzzle")

    def test_illegal_action_and_precondition_rejected(self):
        state = self.rules.initial_state(self.puzzle)
        legal = self.rules.legal_actions(self.puzzle, state)[0]
        bad_precondition = Action(1, legal.kind, legal.actor_id, legal.params, {"state_hash": "sha256:bad"})
        with self.assertRaisesRegex(ValueError, "precondition"):
            self.rules.apply(self.puzzle, state, bad_precondition)
        illegal_destination = Action(1, "traverse_edge", "traveler", {"from_node": list(state.current), "to_node": [6, 6]}, {"state_hash": sha256_value(state.to_dict())})
        with self.assertRaisesRegex(ValueError, "illegal"):
            self.rules.apply(self.puzzle, state, illegal_destination)

    def test_multiaxis_difficulty_presets(self):
        selected = {
            "easy": (4, {"solution_cost": 12, "decision_count": 1, "false_leads": 1}),
            "medium": (18, {"solution_cost": 24, "decision_count": 3, "false_leads": 3}),
        }
        for band, (seed, expected) in selected.items():
            puzzle = generate_maze(StableRng(derive_seed(seed, "maze", 0, "generation")))
            solution = MazeSolver(self.rules).solve(puzzle)
            report = difficulty_report(puzzle, solution, self.rules)
            self.assertIsNone(quality_rejection(report, band))
            self.assertEqual(report["accepted_band"], band)
            for key, value in expected.items():
                self.assertEqual(report["mechanical"][key], value)
        old_report = difficulty_report(self.puzzle, MazeSolver(self.rules).solve(self.puzzle), self.rules)
        self.assertEqual(quality_rejection(old_report, "medium"), "TOO_TRIVIAL")
        with self.assertRaises(ValueError):
            difficulty_preset("impossible")

    def test_existing_cli_shape_remains_compatible(self):
        args = build_parser().parse_args([
            "generate", "--type", "maze", "--count", "1", "--difficulty", "medium",
            "--seed", "42", "--theme", "minimal-v1", "--timeline", "standard",
            "--format", "gif,mp4", "--output", "output/compatibility-test",
        ])
        self.assertEqual(args.difficulty, "medium")
        self.assertEqual(args.seed, 42)
        target = build_parser().parse_args([
            "generate", "--type", "maze", "--difficulty", "target",
            "--seed", "20260821", "--output", "output/target-test",
        ])
        self.assertEqual(target.difficulty, "target")

    def test_project_package_rename_has_no_legacy_shim(self):
        repository = Path(__file__).resolve().parents[1]
        self.assertTrue((repository / "src" / "zero_button_game" / "__main__.py").is_file())
        legacy_package = "puzzle" + "_gif"
        self.assertFalse((repository / "src" / legacy_package).exists())
        packaging = (repository / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "zero-button-game"', packaging)
        self.assertIn('zero-button-game = "zero_button_game.cli:main"', packaging)
        pipes = build_parser().parse_args([
            "generate", "--type", "pipes", "--difficulty", "target",
            "--seed", "121", "--thinking-time", "3.5", "--timing-variant", "B",
            "--output", "output/pipes-test",
        ])
        self.assertEqual(pipes.type, "pipes")
        self.assertEqual(pipes.thinking_time, 3.5)
        self.assertEqual(pipes.timing_variant, "B")

    def test_target_mixed_recipe_is_seeded_and_enforces_each_active_trait(self):
        seed = derive_seed(20260821, "maze", 1, "generation")
        preset = difficulty_preset("target")
        first = generate_maze(StableRng(seed), preset["width"], preset["height"], preset["endpoint_profile"])
        second = generate_maze(StableRng(seed), preset["width"], preset["height"], preset["endpoint_profile"])
        self.assertEqual(first, second)
        self.assertEqual(first.ruleset, "perfect-maze-v1:mixed-v1:folded_path@2+goal_zone_traps@3")
        report = difficulty_report(first, MazeSolver(self.rules).solve(first), self.rules)
        self.assertIsNone(quality_rejection(report, "target"))
        recipe = report["generation_traits"]
        self.assertGreaterEqual(len(recipe["active_traits"]), 2)
        self.assertEqual(recipe["active_satisfied_count"], len(recipe["active_traits"]))
        for trait in recipe["active_traits"]:
            for metric, minimum in MIXED_TRAIT_TARGETS[trait].items():
                self.assertGreaterEqual(report["mechanical"][metric], minimum)
        self.assertEqual(report["human"]["status"], "calibrated-within-person-target")
        recipe["active_satisfied_count"] -= 1
        self.assertEqual(quality_rejection(report, "target"), "TRAIT_REQUIREMENTS_NOT_MET")


class FormatOptionTests(unittest.TestCase):
    """--format: parsing, plumbing, and which media checks a work gets."""

    def test_parse_formats_accepts_the_three_supported_values(self):
        self.assertEqual(parse_formats("gif"), ("gif",))
        self.assertEqual(parse_formats("mp4"), ("mp4",))
        self.assertEqual(parse_formats("gif,mp4"), ("gif", "mp4"))

    def test_parse_formats_normalizes_order_and_whitespace(self):
        # mp4,gif must produce the same work as gif,mp4.
        self.assertEqual(parse_formats("mp4,gif"), ("gif", "mp4"))
        self.assertEqual(parse_formats(" GIF , mp4 "), ("gif", "mp4"))

    def test_parse_formats_rejects_bad_values(self):
        for bad in ("webp", "gif,webp", "", " , ", "gif,gif"):
            with self.assertRaises(ValueError):
                parse_formats(bad)

    def test_cli_default_is_both_formats(self):
        args = build_parser().parse_args([
            "generate", "--type", "maze", "--seed", "42", "--output", "output/format-default",
        ])
        self.assertEqual(args.formats, ("gif", "mp4"))
        self.assertEqual(args.formats, DEFAULT_FORMATS)

    def test_cli_accepts_each_format_selection(self):
        for value, expected in (("gif", ("gif",)), ("mp4", ("mp4",)), ("gif,mp4", ("gif", "mp4")), ("mp4,gif", ("gif", "mp4"))):
            args = build_parser().parse_args([
                "generate", "--type", "maze", "--seed", "42",
                "--format", value, "--output", "output/format-test",
            ])
            self.assertEqual(args.formats, expected)

    def test_cli_rejects_unsupported_format(self):
        for bad in ("webp", "gif,webp", "avi"):
            with self.assertRaises(SystemExit) as caught:
                build_parser().parse_args([
                    "generate", "--type", "maze", "--seed", "42",
                    "--format", bad, "--output", "output/format-test",
                ])
            self.assertEqual(caught.exception.code, 2)

    def test_generation_request_carries_formats(self):
        default = GenerationRequest("maze", 1, "medium", 42, Path("unused"))
        self.assertEqual(default.formats, ("gif", "mp4"))
        narrowed = GenerationRequest("maze", 1, "medium", 42, Path("unused"), formats=("gif",))
        self.assertEqual(narrowed.formats, ("gif",))

    def test_declared_formats_reads_metadata_artifacts(self):
        both = {"artifacts": [{"kind": "gif"}, {"kind": "mp4"}]}
        self.assertEqual(declared_formats(both), ("gif", "mp4"))
        self.assertEqual(declared_formats({"artifacts": [{"kind": "gif"}]}), ("gif",))
        self.assertEqual(declared_formats({"artifacts": [{"kind": "mp4"}]}), ("mp4",))
        # Canonical order regardless of how the entries are stored.
        self.assertEqual(declared_formats({"artifacts": [{"kind": "mp4"}, {"kind": "gif"}]}), ("gif", "mp4"))

    def test_declared_formats_falls_back_for_works_without_artifacts(self):
        # A work with no artifact entries must never silently skip media checks.
        self.assertEqual(declared_formats({}), DEFAULT_FORMATS)
        self.assertEqual(declared_formats({"artifacts": []}), DEFAULT_FORMATS)
        self.assertEqual(declared_formats({"artifacts": [{"kind": "contact_sheet"}]}), DEFAULT_FORMATS)


if __name__ == "__main__":
    unittest.main()
