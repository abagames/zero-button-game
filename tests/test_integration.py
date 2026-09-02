import tempfile
import unittest
from pathlib import Path

from zero_button_game.core import read_json, sha256_file, write_json
from zero_button_game.pipeline import GenerationRequest, generate
from zero_button_game.preset_loader import CURRENT_BANDS, default_preset_root
from zero_button_game.validation import validate_instance


class IntegrationTests(unittest.TestCase):
    def test_all_plugins_seed_to_validated_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                ("maze", "easy", 4), ("maze", "medium", 18),
                ("maze", "target", 20260821),
                ("pipes", "easy", 1), ("pipes", "medium", 18), ("pipes", "target", 121),
                # One low-cost vertical slice for every newer plugin.  Easy keeps
                # the integration suite's render/encode cost bounded while still
                # exercising generation, plugin-specific validation and export.
                ("parking", "easy", 20260822),
                ("packing", "easy", 20260822),
                ("lights", "easy", 20260822),
                ("fold", "easy", 11),
                ("mosaic", "easy", 20260901),
            )
            for puzzle_type, band, seed in cases:
                with self.subTest(puzzle_type=puzzle_type, band=band):
                    output = Path(directory) / puzzle_type / band
                    result = generate(GenerationRequest(puzzle_type, 1, band, seed, output))
                    self.assertEqual(len(result.instances), 1)
                    instance = result.instances[0]
                    for name in ("problem.json", "solution.json", "presentation.json", "metadata.json", "animation.gif", "preview.mp4", "contact_sheet.png", "validation.json"):
                        self.assertTrue((instance / name).is_file(), name)
                    report = validate_instance(instance, strict=True)
                    self.assertEqual(report["status"], "passed")
                    metadata = read_json(instance / "metadata.json")
                    self.assertEqual(metadata["puzzle"]["type"], puzzle_type)
                    self.assertEqual(metadata["difficulty"]["accepted_band"], band)
                    self.assertIn(band, CURRENT_BANDS)
                    preset_path = default_preset_root() / metadata["difficulty"]["quality_preset_source"]
                    self.assertEqual(metadata["difficulty"]["quality_preset_sha256"], sha256_file(preset_path))
                    self.assertTrue(metadata["difficulty"]["quality_preset_source"].startswith("current/"))
                    self.assertEqual(metadata["difficulty"]["quality_preset_hash_basis"], "source-json-bytes-v1")
                    if puzzle_type == "maze" and band == "target":
                        traits = metadata["difficulty"]["generation_traits"]
                        self.assertGreaterEqual(len(traits["active_traits"]), 2)
                        self.assertEqual(traits["active_satisfied_count"], len(traits["active_traits"]))
                        self.assertEqual(metadata["difficulty"]["human"]["status"], "calibrated-within-person-target")
                    if puzzle_type == "pipes":
                        self.assertEqual(metadata["difficulty"]["human"]["status"], "uncalibrated-pipes-unique-v3")
                        uniqueness = metadata["solution"]["uniqueness"]
                        self.assertEqual(uniqueness["normalized_solution_count"], 1)
                        self.assertEqual(uniqueness["unique_path_count"], 1)
                        checks = read_json(instance / "validation.json")["checks_passed"]
                        for check in ("rotation_action_rendering", "source_goal_connection", "goal_action_minimality", "normalized_solution_unique", "emitted_signature_canonical", "flow_after_connection", "flow_goal_reached", "flow_state_immutable"):
                            self.assertIn(check, checks)
                        if band == "target":
                            self.assertEqual(metadata["timeline"]["problem_to_reveal_seconds"], 8.0)
                            self.assertEqual(metadata["timeline"]["reveal_start_frame"], 160)
                            calibration = metadata["timing_calibration"]
                            self.assertEqual(calibration["timing_status"], "calibrated-within-person-target")
                            self.assertEqual(calibration["previous_evaluated_thinking_time_seconds"], 5.0)
                            self.assertEqual(calibration["source_evaluation"], "studies/timing_sweep_round2_calibration_2026-08-23.json")
                        original_metadata = metadata
                        tampered = read_json(instance / "metadata.json")
                        tampered["solution"]["uniqueness"]["normalized_solution_count"] = 2
                        write_json(instance / "metadata.json", tampered)
                        with self.assertRaisesRegex(ValueError, "metadata uniqueness evidence"):
                            validate_instance(instance, strict=True)
                        write_json(instance / "metadata.json", original_metadata)
                    if puzzle_type in {"parking", "packing", "lights", "fold", "mosaic"}:
                        self.assertEqual(
                            metadata["difficulty"]["human"]["status"],
                            f"uncalibrated-{puzzle_type}-v1",
                        )
                    self.assertGreaterEqual(metadata["timeline"]["solve_frames"], metadata["solution"]["action_count"] * 2)
                    self.assertEqual(metadata["validation"]["status"], "passed")

    def test_thinking_time_override_remains_non_standard_and_does_not_mutate_json(self):
        preset = default_preset_root() / "current" / "maze-easy.json"
        before = preset.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            result = generate(GenerationRequest(
                "maze", 1, "easy", 4, Path(directory),
                thinking_time_seconds=3.0, formats=("mp4",),
            ))
            metadata = read_json(result.instances[0] / "metadata.json")
            self.assertEqual(metadata["timeline"]["problem_to_reveal_seconds"], 3.0)
            self.assertEqual(metadata["timing_calibration"]["calibration_status"], "comparison-override-not-standard")
            self.assertEqual(metadata["timing_calibration"]["target_standard_thinking_time_seconds"], 2.5)
        self.assertEqual(preset.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
