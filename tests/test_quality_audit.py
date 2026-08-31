import io
import json
import tempfile
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zero_button_game.audit_quality import QualityAuditRequest, audit_quality
from zero_button_game.cli import build_parser, main
from zero_button_game.core import sha256_value
from zero_button_game.registry import PLUGINS, registered_puzzle_types


CASES = {
    "maze": ("easy", 4),
    "pipes": ("easy", 1),
    "parking": ("easy", 20260822),
    "packing": ("easy", 20260822),
    "lights": ("easy", 20260822),
    "fold": ("easy", 11),
}

REQUIRED_KEYS = {
    "schema_version", "type", "difficulty", "seed", "candidate_count",
    "scanned", "accepted", "rejected", "acceptance_rate",
    "rejection_reasons", "quality_preset", "capabilities", "metrics",
    "candidates", "reproducibility", "audit_sha256",
}


class QualityAuditContractTests(unittest.TestCase):
    def test_every_registered_genre_returns_the_common_contract(self):
        self.assertEqual(set(CASES), set(registered_puzzle_types()))
        for puzzle_type in registered_puzzle_types():
            band, seed = CASES[puzzle_type]
            with self.subTest(puzzle_type=puzzle_type):
                report = audit_quality(QualityAuditRequest(puzzle_type, band, seed, 1))
                self.assertEqual(set(report), REQUIRED_KEYS)
                self.assertEqual(report["type"], puzzle_type)
                self.assertEqual(report["candidate_count"], 1)
                self.assertEqual(report["scanned"], 1)
                self.assertEqual(report["accepted"] + report["rejected"], 1)
                self.assertEqual(sum(report["rejection_reasons"].values()), report["rejected"])
                self.assertEqual(set(report["metrics"]), {"difficulty", "solver"})
                self.assertIn("available", report["metrics"]["difficulty"])
                self.assertIn("available", report["metrics"]["solver"])
                self.assertFalse(report["capabilities"]["media_generation"])
                self.assertFalse(report["capabilities"]["persistent_output"])
                self.assertFalse(report["reproducibility"]["runtime_timing_included"])

    def test_identical_inputs_and_hashes_are_deterministic(self):
        request = QualityAuditRequest("pipes", "easy", 1, 2)
        first = audit_quality(request)
        second = audit_quality(request)
        self.assertEqual(first, second)
        reported_hash = first.pop("audit_sha256")
        self.assertEqual(reported_hash, sha256_value(first))
        for candidate in first["candidates"]:
            candidate = dict(candidate)
            reported_candidate_hash = candidate.pop("candidate_sha256")
            self.assertEqual(reported_candidate_hash, sha256_value(candidate))

    def test_cli_prints_json_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            before = set(Path(directory).iterdir())
            stream = io.StringIO()
            with chdir(directory), redirect_stdout(stream):
                status = main([
                    "audit-quality", "--type", "maze", "--difficulty", "easy",
                    "--seed", "4", "--candidates", "1",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stream.getvalue())["type"], "maze")
            self.assertEqual(set(Path(directory).iterdir()), before)

    def test_invalid_arguments_fail_at_the_cli_boundary(self):
        parser = build_parser()
        invalid = (
            ["audit-quality", "--type", "not-registered", "--seed", "1"],
            ["audit-quality", "--type", "maze", "--seed", "-1"],
            ["audit-quality", "--type", "maze", "--seed", "1", "--candidates", "0"],
            ["audit-quality", "--type", "maze", "--seed", "1", "--difficulty", "impossible"],
        )
        for argv in invalid:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                parser.parse_args(argv)
            self.assertEqual(caught.exception.code, 2)
        with self.assertRaises(ValueError):
            audit_quality(QualityAuditRequest("maze", "easy", 1, 0))

    def test_rejection_reasons_are_aggregated_through_the_plugin_boundary(self):
        class Puzzle:
            puzzle_type = "maze"

            def __init__(self, marker):
                self.marker = marker

            def to_dict(self):
                return {"puzzle_type": self.puzzle_type, "marker": self.marker}

        class Rules:
            @staticmethod
            def validate_structure(puzzle):
                return []

        class Solver:
            @staticmethod
            def solve(puzzle):
                return SimpleNamespace(
                    solver_id="fake", solver_version="1", optimality="test",
                    actions=(), cost=puzzle.marker % 7, expanded_nodes=1,
                    to_dict=lambda: {"marker": puzzle.marker},
                )

        class RejectingPlugin:
            puzzle_type = "maze"
            rules = Rules()
            solver = Solver()
            solver_reject_codes = frozenset({"UNSOLVABLE"})

            @staticmethod
            def generate_candidate(rng, preset):
                return Puzzle(rng.next_u64())

            @staticmethod
            def validate_solution(puzzle, solution, rules):
                return []

            @staticmethod
            def difficulty(puzzle, solution, rules):
                return {"mechanical": {"bucket": puzzle.marker % 3}, "human": {}}

            @staticmethod
            def quality_filter(difficulty, band):
                return f"BUCKET_{difficulty['mechanical']['bucket']}"

        with patch.dict(PLUGINS, {"maze": RejectingPlugin()}):
            report = audit_quality(QualityAuditRequest("maze", "easy", 7, 12))
        expected = {}
        for candidate in report["candidates"]:
            reason = candidate["reason"]
            expected[reason] = expected.get(reason, 0) + 1
        self.assertGreaterEqual(len(expected), 2)
        self.assertEqual(report["accepted"], 0)
        self.assertEqual(report["rejection_reasons"], dict(sorted(expected.items())))


if __name__ == "__main__":
    unittest.main()
