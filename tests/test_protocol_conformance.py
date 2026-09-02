import json
import re
import unittest
from pathlib import Path

from zero_button_game.protocol import PLUGIN_PROTOCOL_VERSION, conformance_failures
from zero_button_game.registry import PLUGINS, get_plugin
from zero_button_game.validation import ACCEPTED_TIMING_CALIBRATION_ROUNDS


SCHEMA_DIRECTORY = Path(__file__).resolve().parents[1] / "schemas"
EXPECTED_SCHEMA_FILENAMES = {
    "fold-to-target-exact.schema.json",
    "lights-toggle-plus-gf2.schema.json",
    "metadata.schema.json",
    "models.schema.json",
    "mosaic-row-column-cyclic-shift.schema.json",
    "packing-exact-cover-norotate.schema.json",
    "parking-rush-hour-slide.schema.json",
    "pipes-solution-uniqueness.schema.json",
    "pipes-source-goal-unique.schema.json",
    "pipes-source-goal.schema.json",
}


class ProtocolConformanceTest(unittest.TestCase):
    def test_schema_files_use_stable_names_and_resolvable_ids(self):
        schema_paths = set(SCHEMA_DIRECTORY.glob("*.schema.json"))
        self.assertEqual({path.name for path in schema_paths}, EXPECTED_SCHEMA_FILENAMES)
        for path in schema_paths:
            with self.subTest(schema=path.name):
                self.assertIsNone(re.search(r"(?:^|[-_])v\d+(?=\.|[-_])", path.name))
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(document["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertEqual(document["$id"], path.name)
                self.assertEqual(SCHEMA_DIRECTORY / document["$id"], path)

    def test_registered_plugins_conform(self):
        self.assertEqual(PLUGIN_PROTOCOL_VERSION, "plugin_protocol_v1")
        for name, plugin in PLUGINS.items():
            with self.subTest(plugin=name):
                self.assertEqual(conformance_failures(plugin), [])

    def test_optional_members_are_declared_where_used(self):
        # Optional members: absence is legal, presence must be callable.
        self.assertIsNone(getattr(get_plugin("maze"), "metadata_contract_checks", None))
        for name in PLUGINS:
            self.assertIsNotNone(getattr(get_plugin(name), "timing_calibration_profile", None))
        self.assertIsNone(getattr(get_plugin("pipes"), "candidate_rejection_reason", None))

    def test_timing_profiles_match_the_accepted_current_rounds(self):
        current = {
            (item["puzzle_type"], item["band"]): item
            for item in ACCEPTED_TIMING_CALIBRATION_ROUNDS[1:]
        }
        self.assertEqual(set(current), {(name, band) for name in PLUGINS for band in ("easy", "medium", "target")})
        for key, accepted in current.items():
            with self.subTest(puzzle_type=key[0], band=key[1]):
                profile = get_plugin(key[0]).timing_calibration_profile(key[1])
                self.assertEqual(profile["standard_thinking_time_seconds"], accepted["thinking_time_seconds"])
                self.assertEqual(profile["previous_evaluated_thinking_time_seconds"], accepted["previous"])
                self.assertEqual(profile["calibration_status"], accepted["calibration_status"])
                self.assertEqual(profile["timing_status"], accepted["timing_status"])
                self.assertEqual(profile["source_evaluation"], accepted["source_evaluation"])

    def test_missing_required_member_fails(self):
        class MissingMember:
            puzzle_type = "dummy"
            plugin_version = "1.0.0"

        failures = conformance_failures(MissingMember())
        self.assertTrue(any("missing required member" in item for item in failures), failures)

    def test_wrong_return_structure_fails(self):
        class BadReturns:
            """A pipes clone whose contract-facing return values are wrong."""

            def __getattr__(self, name):
                return getattr(get_plugin("pipes"), name)

            puzzle_type = "pipes"
            plugin_version = "1.2.0"
            rules = get_plugin("pipes").rules
            solver = get_plugin("pipes").solver
            scene_builder = get_plugin("pipes").scene_builder
            solver_reject_codes = frozenset({"UNSOLVABLE"})

            @staticmethod
            def difficulty(puzzle, solution, rules):
                report = dict(get_plugin("pipes").difficulty(puzzle, solution, rules))
                report.pop("human")  # missing "human"
                return report

            @staticmethod
            def quality_filter(difficulty, band):
                return None

            @staticmethod
            def render_contract_checks(scene, renderer):
                return True  # not a (passed, failed) tuple

            @staticmethod
            def animation_units(solution):
                return 0.5  # not a positive int

        failures = conformance_failures(BadReturns())
        self.assertTrue(any("'mechanical' and 'human'" in item for item in failures), failures)
        self.assertTrue(any("render_contract_checks must return" in item for item in failures), failures)
        self.assertTrue(any("animation_units" in item for item in failures), failures)

    def test_bad_renderer_fails(self):
        class ShortFrames:
            def __getattr__(self, name):
                return getattr(get_plugin("maze"), name)

            puzzle_type = "maze"
            plugin_version = "1.1.0"
            rules = get_plugin("maze").rules
            solver = get_plugin("maze").solver
            scene_builder = get_plugin("maze").scene_builder
            solver_reject_codes = frozenset({"UNSOLVABLE"})

            @staticmethod
            def renderer_factory():
                base = get_plugin("maze").renderer_factory()

                class Truncating:
                    board_size = base.board_size

                    def render(self, scene, directory):
                        return base.render(scene, directory)

                    def render_frame(self, scene, frame):
                        return base.render_frame(scene, frame)[:100]

                return Truncating()

        failures = conformance_failures(ShortFrames())
        self.assertTrue(any("raw RGB bytes" in item for item in failures), failures)


if __name__ == "__main__":
    unittest.main()
