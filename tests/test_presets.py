import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from zero_button_game.pipeline import timeline_for_request
from zero_button_game.preset_loader import (
    CURRENT_PRESET_FILES, PresetLoader, PresetRoots, PresetValidationError,
    default_preset_root, difficulty_preset, use_preset_root,
)
from zero_button_game.registry import get_plugin
from zero_button_game.sequence import SequenceRequest, sequence_component_requests


# Functional runtime values are unchanged from the JSON-source migration;
# only the user-facing preset name is now the stable plugin-band identity.
EXPECTED_STABLE_RUNTIME = {
    ("maze", "easy"): {"name":"maze-easy","min_cost":12,"max_cost":18,"thinking_time_seconds":2.5,"min_decisions":1,"max_decisions":2,"min_false_leads":1,"max_false_leads":3,"min_deep_false_leads":0,"max_deep_false_leads":1,"min_goal_zone_false_leads":0},
    ("maze", "medium"): {"name":"maze-medium","min_cost":20,"max_cost":26,"thinking_time_seconds":2.5,"min_decisions":3,"max_decisions":5,"min_false_leads":3,"max_false_leads":5,"min_deep_false_leads":2,"max_deep_false_leads":5,"min_goal_zone_false_leads":1},
    ("maze", "target"): {"name":"maze-target","width":9,"height":9,"endpoint_profile":"mixed","thinking_time_seconds":3.5,"min_cost":36,"max_cost":40,"min_decisions":6,"max_decisions":14,"min_false_leads":6,"max_false_leads":16,"min_deep_false_leads":4,"max_deep_false_leads":16,"min_goal_zone_false_leads":0},
    ("pipes", "easy"): {"name":"pipes-easy","width":3,"height":3,"thinking_time_seconds":4.0,"min_required_path_length":5,"min_required_rotation_pieces":1,"min_required_quarter_turns":1,"min_candidate_routes":1,"max_required_quarter_turns":6,"min_difficulty_score":10,"max_difficulty_score":22},
    ("pipes", "medium"): {"name":"pipes-medium","width":4,"height":4,"thinking_time_seconds":6.0,"min_required_path_length":7,"min_required_rotation_pieces":2,"min_required_quarter_turns":3,"min_candidate_routes":2,"max_required_quarter_turns":10,"min_difficulty_score":23,"max_difficulty_score":31},
    ("pipes", "target"): {"name":"pipes-target","width":4,"height":4,"thinking_time_seconds":8.0,"min_required_path_length":7,"min_required_rotation_pieces":3,"min_required_quarter_turns":5,"min_candidate_routes":2,"min_near_optimal_routes":2,"min_false_connection_edges":1,"max_required_quarter_turns":14,"min_difficulty_score":32,"max_difficulty_score":80},
    ("parking", "easy"): {"name":"parking-easy","band":"easy","width":5,"height":5,"vehicle_count":9,"walk_steps":14,"blocker_count":2,"thinking_time_seconds":4.0,"search_attempts":600,"solve_state_budget":60000,"min_moves":4,"max_moves":4,"min_involved_vehicles":4,"min_blocking_chain_depth":2,"min_reversal_moves":0,"max_slide_cells":12,"min_difficulty_score":55,"max_difficulty_score":63},
    ("parking", "medium"): {"name":"parking-medium","band":"medium","width":6,"height":6,"vehicle_count":13,"walk_steps":18,"blocker_count":2,"thinking_time_seconds":4.0,"search_attempts":600,"solve_state_budget":60000,"min_moves":4,"max_moves":4,"min_involved_vehicles":4,"min_blocking_chain_depth":2,"min_reversal_moves":0,"max_slide_cells":13,"min_difficulty_score":64,"max_difficulty_score":70},
    ("parking", "target"): {"name":"parking-target","band":"target","width":6,"height":6,"vehicle_count":13,"walk_steps":18,"blocker_count":2,"thinking_time_seconds":8.0,"search_attempts":3000,"solve_state_budget":60000,"min_moves":6,"max_moves":7,"min_involved_vehicles":6,"min_blocking_chain_depth":2,"min_reversal_moves":0,"max_slide_cells":16,"min_difficulty_score":84,"max_difficulty_score":130},
    ("packing", "easy"): {"name":"packing-easy","band":"easy","width":4,"height":3,"piece_count":3,"thinking_time_seconds":4.0,"search_attempts":400,"solve_node_budget":200000,"min_pieces":3,"max_pieces":3,"min_concave_pieces":1,"min_dead_placements":0,"min_difficulty_score":46,"max_difficulty_score":62},
    ("packing", "medium"): {"name":"packing-medium","band":"medium","width":4,"height":4,"piece_count":3,"thinking_time_seconds":4.0,"search_attempts":600,"solve_node_budget":200000,"min_pieces":3,"max_pieces":3,"min_concave_pieces":2,"min_dead_placements":3,"min_difficulty_score":63,"max_difficulty_score":78},
    ("packing", "target"): {"name":"packing-target","band":"target","width":4,"height":4,"piece_count":4,"thinking_time_seconds":8.0,"search_attempts":2000,"solve_node_budget":200000,"min_pieces":4,"max_pieces":4,"min_concave_pieces":2,"min_dead_placements":8,"min_difficulty_score":79,"max_difficulty_score":160},
    ("lights", "easy"): {"name":"lights-easy","band":"easy","width":5,"height":4,"press_count":2,"search_attempts":400,"thinking_time_seconds":6.0,"min_presses":2,"max_presses":2,"min_lit_clusters":2,"max_greedy_reductions":8,"min_difficulty_score":92,"max_difficulty_score":117},
    ("lights", "medium"): {"name":"lights-medium","band":"medium","width":5,"height":4,"press_count":3,"search_attempts":600,"thinking_time_seconds":8.0,"min_presses":3,"max_presses":3,"min_lit_clusters":2,"max_greedy_reductions":6,"min_difficulty_score":118,"max_difficulty_score":153},
    ("lights", "target"): {"name":"lights-target","band":"target","width":5,"height":4,"press_count":4,"search_attempts":2000,"thinking_time_seconds":8.0,"min_presses":4,"max_presses":4,"min_lit_clusters":3,"max_greedy_reductions":5,"min_difficulty_score":154,"max_difficulty_score":220},
    ("fold", "easy"): {"name":"fold-easy","band":"easy","width":6,"height":6,"search_attempts":32,"thinking_time_seconds":4.0,"min_folds":2,"max_folds":2,"min_target_side":3,"max_decoy_creases":40,"min_difficulty_score":180,"max_difficulty_score":290},
    ("fold", "medium"): {"name":"fold-medium","band":"medium","width":6,"height":6,"search_attempts":64,"thinking_time_seconds":6.0,"min_folds":3,"max_folds":3,"min_target_side":2,"max_decoy_creases":60,"min_difficulty_score":300,"max_difficulty_score":440},
    ("fold", "target"): {"name":"fold-target","band":"target","width":6,"height":6,"search_attempts":240,"thinking_time_seconds":6.0,"min_folds":4,"max_folds":4,"min_target_side":2,"max_decoy_creases":80,"min_difficulty_score":450,"max_difficulty_score":620},
}

class PresetLoaderTests(unittest.TestCase):
    def copied_repository(self, directory: str) -> Path:
        root = Path(directory) / "repository"
        shutil.copytree(default_preset_root(), root / "presets")
        return root

    @staticmethod
    def current_path(root: Path, filename: str) -> Path:
        return root / "presets" / "current" / filename

    @staticmethod
    def rewrite(path: Path, edit) -> None:
        document = json.loads(path.read_text(encoding="utf-8"))
        edit(document)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    def test_catalog_explicitly_covers_current_and_shared_files(self):
        self.assertEqual(PresetLoader().audit_catalog(), {"current": 18, "shared": 2, "total": 20})

    def test_all_18_runtime_values_equal_stable_name_expectations(self):
        self.assertEqual(set(CURRENT_PRESET_FILES), set(EXPECTED_STABLE_RUNTIME))
        for key, expected in EXPECTED_STABLE_RUNTIME.items():
            with self.subTest(plugin=key[0], band=key[1]):
                self.assertEqual(difficulty_preset(*key), expected)
                self.assertEqual(get_plugin(key[0]).difficulty_preset(key[1]), expected)

    def test_source_hash_is_exactly_the_parsed_json_bytes(self):
        for key in CURRENT_PRESET_FILES:
            record = PresetLoader().load(*key)
            self.assertEqual(record.source_bytes, record.path.read_bytes())
            self.assertEqual(record.source_sha256, "sha256:" + hashlib.sha256(record.path.read_bytes()).hexdigest())
            self.assertEqual(record.preset_id, record.document["name"])

    def test_json_thinking_time_changes_plugin_and_sequence_runtime_without_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copied_repository(directory)
            path = self.current_path(root, "pipes-easy.json")
            self.rewrite(path, lambda value: value["presentation"].update(thinking_time_seconds=4.5))
            with use_preset_root(root):
                self.assertEqual(get_plugin("pipes").difficulty_preset("easy")["thinking_time_seconds"], 4.5)
            request = SequenceRequest("pipes", 42, Path(directory) / "out", preset_root=root)
            component = sequence_component_requests(request, Path(directory) / "stage")[0]
            self.assertEqual(timeline_for_request(component, 8).frames()["reveal_start"], 90)
            self.rewrite(path, lambda value: value["presentation"].update(thinking_time_seconds=5.0))
            self.assertEqual(PresetLoader(root).load("pipes", "easy").runtime["thinking_time_seconds"], 5.0)

    def test_unknown_plugin_and_band_fail_closed(self):
        with self.assertRaisesRegex(PresetValidationError, "unknown preset plugin"):
            PresetLoader().load("future", "easy")
        with self.assertRaisesRegex(PresetValidationError, "unknown preset band"):
            PresetLoader().load("pipes", "hard")

    def test_missing_file_and_json_parse_error_are_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copied_repository(directory)
            self.current_path(root, "parking-easy.json").unlink()
            with self.assertRaisesRegex(PresetValidationError, "preset file is missing"):
                PresetLoader(root).load("parking", "easy")
        with tempfile.TemporaryDirectory() as directory:
            root = self.copied_repository(directory)
            self.current_path(root, "packing-easy.json").write_text("{ broken", encoding="utf-8")
            with self.assertRaisesRegex(PresetValidationError, "invalid preset JSON"):
                PresetLoader(root).load("packing", "easy")

    def test_identity_type_required_range_and_grid_errors_fail_closed(self):
        cases = (
            ("puzzle_type", lambda d: d.update(puzzle_type="maze"), "plugin mismatch"),
            ("difficulty", lambda d: d.update(difficulty="medium"), "band mismatch"),
            ("name", lambda d: d.update(name="pipes-renamed"), "id/name mismatch"),
            ("required", lambda d: d["mechanical"].pop("candidate_routes_min"), "missing required"),
            ("type", lambda d: d["board"].update(width=True), "must be an integer"),
            ("range", lambda d: d["presentation"].update(thinking_time_seconds=20.5), "between 2.5 and 20.0"),
            ("grid", lambda d: d["presentation"].update(thinking_time_seconds=8.03), "20fps frame grid"),
        )
        for label, edit, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = self.copied_repository(directory)
                self.rewrite(self.current_path(root, "pipes-target.json"), edit)
                with self.assertRaisesRegex(PresetValidationError, message):
                    PresetLoader(root).load("pipes", "target")

    def test_duplicate_keys_ids_and_escaping_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copied_repository(directory)
            path = self.current_path(root, "pipes-easy.json")
            path.write_text('{"name":"a","name":"b"}', encoding="utf-8")
            with self.assertRaisesRegex(PresetValidationError, "duplicate JSON key"):
                PresetLoader(root).load("pipes", "easy")
        with tempfile.TemporaryDirectory() as directory:
            root = self.copied_repository(directory)
            self.rewrite(
                root / "presets" / "shared" / "standard.json",
                lambda d: d.update(preset="maze-easy"),
            )
            with self.assertRaisesRegex(PresetValidationError, "duplicate preset id"):
                PresetLoader(root).audit_catalog()
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory)
            root = container / "current"
            root.mkdir()
            outside = container / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            (root / "pipes-easy.json").symlink_to(outside)
            with self.assertRaisesRegex(PresetValidationError, "escapes root"):
                PresetLoader(root).load("pipes", "easy")

    def test_current_runtime_is_lazy_and_can_be_audited_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "current"
            shutil.copytree(default_preset_root() / "current", current)
            loader = PresetLoader(current)
            for plugin, band in CURRENT_PRESET_FILES:
                self.assertEqual(loader.load(plugin, band).runtime, EXPECTED_STABLE_RUNTIME[(plugin, band)])
            self.assertEqual(
                loader.audit_catalog(("current",)),
                {"current": 18, "shared": 0, "total": 18},
            )

    def test_explicit_category_roots_support_current_and_shared_catalogs(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.copied_repository(directory)
            roots = PresetRoots.explicit(
                current=repository / "presets" / "current",
                shared=repository / "presets" / "shared",
            )
            self.assertEqual(PresetLoader(roots).audit_catalog()["total"], 20)
            with use_preset_root(repository):
                self.assertEqual(get_plugin("maze").difficulty_preset("target")["name"], "maze-target")

    def test_catalog_rejects_unlisted_extra_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copied_repository(directory)
            (root / "presets" / "current" / "extra.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(PresetValidationError, "extra=.*extra.json"):
                PresetLoader(root).audit_catalog()


if __name__ == "__main__":
    unittest.main()
