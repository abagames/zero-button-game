import unittest

from zero_button_game.core import StableRng, derive_seed, sha256_value
from zero_button_game.models import Action
from zero_button_game.packing import (
    MAX_TRAY_WIDTH_CELLS, PACKING_RULESET, SHAPE_FAMILY, PackingPuzzleSpec, PackingRules,
    PackingSolveRejected, PackingSolver, anchors_for, cover_signature, draw_candidate,
    generate_packing, greedy_solvable, normalize_shape, packing_difficulty_preset,
    packing_difficulty_report, packing_quality_rejection, placed_cells, replay_packing,
    shape_bbox, signature_hash, solution_cover, validate_packing_solution,
)

_GENERATED: dict[tuple[int, str, int], PackingPuzzleSpec] = {}


def generated(master_seed: int, band: str, candidate: int = 0) -> PackingPuzzleSpec:
    key = (master_seed, band, candidate)
    if key not in _GENERATED:
        _GENERATED[key] = generate_packing(
            StableRng(derive_seed(master_seed, "packing", candidate, "generation")),
            packing_difficulty_preset(band),
        )
    return _GENERATED[key]


GOLDEN_SEEDS = ((20260822, "easy", 0), (20260822, "medium", 0), (20260822, "target", 0))


def independent_cover_count(puzzle: PackingPuzzleSpec, limit: int = 3) -> int:
    """Oracle written independently of PackingSolver.

    It works on piece *identities* rather than shapes and enumerates covers by
    brute force over an explicit placement table, then folds interchangeable
    piece permutations by collapsing to a shape/anchor signature at the end.
    """
    hole = set(puzzle.hole_cells)
    table = []
    for index, (_, shape) in enumerate(puzzle.pieces):
        options = []
        for y in range(-2, puzzle.height + 2):
            for x in range(-2, puzzle.width + 2):
                cells = placed_cells(shape, (x, y))
                if all(cell in hole for cell in cells):
                    options.append(((x, y), frozenset(cells)))
        table.append(options)
    found: set[tuple] = set()

    def walk(index: int, used: frozenset, chosen: list) -> None:
        if len(found) >= limit:
            return
        if index == len(table):
            if used == hole:
                found.add(cover_signature(
                    (puzzle.pieces[position][1], anchor) for position, anchor in enumerate(chosen)
                ))
            return
        for anchor, cells in table[index]:
            if cells & used:
                continue
            chosen.append(anchor)
            walk(index + 1, used | cells, chosen)
            chosen.pop()

    walk(0, frozenset(), [])
    return len(found)


class ShapeFamilyTest(unittest.TestCase):
    def test_family_is_normalized_connected_and_bounded(self):
        self.assertEqual(len(SHAPE_FAMILY), 14)
        for shape in SHAPE_FAMILY:
            self.assertEqual(normalize_shape(shape), shape)
            self.assertIn(len(shape), (3, 4))
            width, height = shape_bbox(shape)
            self.assertLessEqual(width, 3)
            self.assertLessEqual(height, 2)

    def test_family_has_no_rotational_folding(self):
        # No rotation means the horizontal and vertical I-trominoes would be
        # different pieces; only the horizontal one fits a 3x2 box.
        self.assertIn(((0, 0), (1, 0), (2, 0)), SHAPE_FAMILY)
        self.assertNotIn(((0, 0), (0, 1), (0, 2)), SHAPE_FAMILY)


class GoldenTest(unittest.TestCase):
    def test_golden_seeds_are_stable_and_unique(self):
        rules = PackingRules()
        solver = PackingSolver(rules)
        observed = {}
        for master_seed, band, candidate in GOLDEN_SEEDS:
            with self.subTest(band=band):
                puzzle = generated(master_seed, band, candidate)
                self.assertEqual(rules.validate_structure(puzzle), [])
                solution = solver.solve(puzzle)
                self.assertEqual(validate_packing_solution(puzzle, solution, rules), [])
                self.assertEqual(len(solution.actions), len(puzzle.pieces))
                self.assertTrue(solution.answer_equivalence_key.startswith("unique:"))
                self.assertEqual(independent_cover_count(puzzle), 1)
                report = packing_difficulty_report(puzzle, solution, rules)
                self.assertIsNone(packing_quality_rejection(report, band))
                observed[band] = (sha256_value(puzzle.to_dict()), sha256_value(solution.to_dict()))
        # Determinism: the same seeds must reproduce byte-identical artifacts.
        for master_seed, band, candidate in GOLDEN_SEEDS:
            _GENERATED.pop((master_seed, band, candidate))
            puzzle = generated(master_seed, band, candidate)
            solution = PackingSolver(PackingRules()).solve(puzzle)
            self.assertEqual(
                (sha256_value(puzzle.to_dict()), sha256_value(solution.to_dict())), observed[band]
            )

    def test_bands_have_disjoint_score_ranges(self):
        bands = [packing_difficulty_preset(band) for band in ("easy", "medium", "target")]
        for lower, upper in zip(bands, bands[1:]):
            self.assertLess(lower["max_difficulty_score"], upper["min_difficulty_score"])


class PropertyTest(unittest.TestCase):
    def test_hundred_seeds_hold_the_contract(self):
        rules = PackingRules()
        solver = PackingSolver(rules)
        checked = 0
        for index in range(100):
            preset = packing_difficulty_preset(("easy", "medium", "target")[index % 3])
            rng = StableRng(derive_seed(31337, "packing", index, "property"))
            try:
                puzzle = draw_candidate(rng, preset)
            except ValueError:
                continue
            if rules.validate_structure(puzzle):
                continue
            analysis = solver.analyze(puzzle)
            with self.subTest(index=index):
                # Independent oracle re-derives uniqueness from piece identities.
                oracle = independent_cover_count(puzzle)
                if analysis["status"] == "unique":
                    self.assertEqual(oracle, 1)
                elif analysis["status"] == "ambiguous":
                    self.assertGreater(oracle, 1)
                else:
                    self.assertEqual(oracle, 0)
                if analysis["status"] != "unique":
                    with self.assertRaises(PackingSolveRejected):
                        solver.solve(puzzle)
                    return_code = None
                    try:
                        solver.solve(puzzle)
                    except PackingSolveRejected as error:
                        return_code = error.code
                    self.assertIn(return_code, {"UNSOLVABLE", "MULTIPLE_COVERS"})
                    continue
                solution = solver.solve(puzzle)
                self.assertEqual(validate_packing_solution(puzzle, solution, rules), [])
                trace = replay_packing(puzzle, solution.actions, rules)
                self.assertTrue(rules.is_goal(puzzle, trace.final))
                self.assertEqual(
                    sorted(cell for anchor, (_, shape) in zip(trace.final.placements, puzzle.pieces)
                           for cell in placed_cells(shape, anchor)),
                    sorted(puzzle.hole_cells),
                )
                checked += 1
        self.assertGreater(checked, 20)


class IllegalActionTest(unittest.TestCase):
    def setUp(self):
        self.rules = PackingRules()
        self.puzzle = generated(20260822, "medium")
        self.solution = PackingSolver(self.rules).solve(self.puzzle)
        self.state = self.rules.initial_state(self.puzzle)
        self.hash = sha256_value(self.state.to_dict())

    def action(self, **overrides) -> Action:
        base = dict(
            action_version=1, kind="move_piece", actor_id="piece-0",
            params={"piece": [0], "to": [0, 0], "cells": [len(self.puzzle.pieces[0][1])]},
            precondition={"state_hash": self.hash},
        )
        base.update(overrides)
        return Action(**base)

    def test_wrong_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, self.state, self.action(kind="rotate_piece"))

    def test_stale_precondition_is_rejected(self):
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, self.state, self.action(precondition={"state_hash": "sha256:0"}))

    def test_actor_id_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, self.state, self.action(actor_id="piece-9"))

    def test_unknown_piece_is_rejected(self):
        with self.assertRaises(ValueError):
            self.rules.apply(
                self.puzzle, self.state,
                self.action(actor_id="piece-99", params={"piece": [99], "to": [0, 0], "cells": [3]}),
            )

    def test_placement_outside_the_hole_is_rejected(self):
        with self.assertRaises(ValueError):
            self.rules.apply(
                self.puzzle, self.state,
                self.action(params={"piece": [0], "to": [9, 9], "cells": [len(self.puzzle.pieces[0][1])]}),
            )

    def test_overlap_is_rejected(self):
        state = self.rules.apply(self.puzzle, self.state, self.solution.actions[0])
        first = self.solution.actions[0]
        replayed = Action(
            1, "move_piece", first.actor_id, dict(first.params),
            {"state_hash": sha256_value(state.to_dict())},
        )
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, state, replayed)

    def test_wrong_cell_count_is_rejected(self):
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, self.state, self.action(params={"piece": [0], "to": [0, 0], "cells": [99]}))

    def test_legal_actions_are_all_appliable(self):
        for action in self.rules.legal_actions(self.puzzle, self.state):
            self.rules.apply(self.puzzle, self.state, action)

    def test_partial_cover_is_not_a_goal(self):
        state = self.rules.apply(self.puzzle, self.state, self.solution.actions[0])
        self.assertFalse(self.rules.is_goal(self.puzzle, state))


class StructureTest(unittest.TestCase):
    def base(self) -> PackingPuzzleSpec:
        return generated(20260822, "easy")

    def test_valid_puzzle_has_no_errors(self):
        self.assertEqual(PackingRules().validate_structure(self.base()), [])

    def test_area_mismatch_is_reported(self):
        puzzle = self.base()
        broken = PackingPuzzleSpec(
            puzzle.schema_version, puzzle.puzzle_type, puzzle.generator_version,
            puzzle.width, puzzle.height, puzzle.hole_cells[:-1], puzzle.pieces,
            puzzle.tray_slots, puzzle.ruleset,
        )
        self.assertIn("piece area differs from hole area", PackingRules().validate_structure(broken))

    def test_wrong_ruleset_is_reported(self):
        puzzle = self.base()
        broken = PackingPuzzleSpec(
            puzzle.schema_version, puzzle.puzzle_type, puzzle.generator_version,
            puzzle.width, puzzle.height, puzzle.hole_cells, puzzle.pieces,
            puzzle.tray_slots, "some-other-ruleset",
        )
        self.assertIn("unsupported ruleset", PackingRules().validate_structure(broken))

    def test_tray_width_limit_is_enforced(self):
        wide = ((0, 0), (1, 0), (2, 0))
        pieces = tuple((index, wide) for index in range(4))
        hole = tuple(sorted({(x, y) for y in range(4) for x in range(3)}))
        puzzle = PackingPuzzleSpec(
            "1.0.0", "packing", "packing-gen-1", 3, 4, hole, pieces,
            ((0, 0), (3, 0), (6, 0), (9, 0)), PACKING_RULESET,
        )
        self.assertIn("tray row is wider than the readable strip", PackingRules().validate_structure(puzzle))
        self.assertGreater(sum(shape_bbox(shape)[0] for _, shape in pieces), MAX_TRAY_WIDTH_CELLS)


class NormalizationTest(unittest.TestCase):
    def test_interchangeable_pieces_are_one_cover(self):
        """Two identical 1x3 pieces filling a 3x2 hole: one cover, not two."""
        shape = ((0, 0), (1, 0), (2, 0))
        hole = tuple(sorted({(x, y) for y in range(2) for x in range(3)}))
        puzzle = PackingPuzzleSpec(
            "1.0.0", "packing", "packing-gen-1", 3, 3, hole,
            ((0, shape), (1, shape)), ((0, 0), (3, 0)), PACKING_RULESET,
        )
        rules = PackingRules()
        self.assertEqual(rules.validate_structure(puzzle), [])
        analysis = PackingSolver(rules).analyze(puzzle)
        self.assertEqual(analysis["status"], "unique")
        self.assertEqual(independent_cover_count(puzzle), 1)
        solution = PackingSolver(rules).solve(puzzle)
        self.assertEqual(validate_packing_solution(puzzle, solution, rules), [])
        # Swapping the two identical pieces keeps the equivalence key identical.
        swapped = tuple(reversed(solution_cover(puzzle, solution)))
        self.assertEqual(signature_hash(swapped), signature_hash(solution_cover(puzzle, solution)))

    def test_distinct_covers_are_counted_separately(self):
        """A 3x3 hole with an L, an I3 and an S admits two different covers."""
        hole = tuple(sorted({(x, y) for y in range(3) for x in range(3)}))
        pieces = (
            (0, ((0, 0), (0, 1), (1, 0))),
            (1, ((0, 0), (1, 0), (2, 0))),
            (2, ((0, 1), (1, 0), (1, 1))),
        )
        puzzle = PackingPuzzleSpec(
            "1.0.0", "packing", "packing-gen-1", 3, 3, hole, pieces, ((0, 0), (2, 0), (5, 0)), PACKING_RULESET,
        )
        rules = PackingRules()
        self.assertEqual(rules.validate_structure(puzzle), [])
        analysis = PackingSolver(rules).analyze(puzzle)
        self.assertEqual(analysis["status"], "ambiguous")
        self.assertGreater(independent_cover_count(puzzle), 1)
        with self.assertRaises(PackingSolveRejected) as raised:
            PackingSolver(rules).solve(puzzle)
        self.assertEqual(raised.exception.code, "MULTIPLE_COVERS")


class JsonRoundTripTest(unittest.TestCase):
    def test_problem_round_trips(self):
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                puzzle = generated(20260822, band)
                self.assertEqual(PackingPuzzleSpec.from_dict(puzzle.to_dict()), puzzle)
                self.assertEqual(
                    sha256_value(PackingPuzzleSpec.from_dict(puzzle.to_dict()).to_dict()),
                    sha256_value(puzzle.to_dict()),
                )

    def test_state_dict_is_json_shaped(self):
        rules = PackingRules()
        puzzle = generated(20260822, "easy")
        value = rules.initial_state(puzzle).to_dict()
        self.assertEqual(value["placements"], [[-1, -1]] * len(puzzle.pieces))


class DifficultyTest(unittest.TestCase):
    def test_metrics_are_reported(self):
        rules = PackingRules()
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                puzzle = generated(20260822, band)
                solution = PackingSolver(rules).solve(puzzle)
                report = packing_difficulty_report(puzzle, solution, rules)
                metrics = report["mechanical"]
                for key in (
                    "piece_count", "concave_pieces", "shape_duplication",
                    "dead_placements", "greedy_solvable", "difficulty_score",
                ):
                    self.assertIn(key, metrics)
                self.assertEqual(report["solution_uniqueness"]["cover_count"], 1)
                self.assertIsNone(packing_quality_rejection(report, band))
                self.assertEqual(report["accepted_band"], band)

    def test_two_piece_puzzle_is_rejected_for_the_shift_perturbation(self):
        shape = ((0, 0), (1, 0), (2, 0))
        hole = tuple(sorted({(x, y) for y in range(2) for x in range(3)}))
        puzzle = PackingPuzzleSpec(
            "1.0.0", "packing", "packing-gen-1", 3, 3, hole,
            ((0, shape), (1, shape)), ((0, 0), (3, 0)), PACKING_RULESET,
        )
        rules = PackingRules()
        solution = PackingSolver(rules).solve(puzzle)
        report = packing_difficulty_report(puzzle, solution, rules)
        self.assertEqual(packing_quality_rejection(report, "easy"), "PIECE_COUNT_TOO_LOW")

    def test_greedy_and_anchor_helpers(self):
        puzzle = generated(20260822, "target")
        self.assertIsInstance(greedy_solvable(puzzle), bool)
        hole = puzzle.hole()
        for _, shape in puzzle.pieces:
            for anchor in anchors_for(shape, hole, set()):
                self.assertTrue(all(cell in hole for cell in placed_cells(shape, anchor)))


if __name__ == "__main__":
    unittest.main()
