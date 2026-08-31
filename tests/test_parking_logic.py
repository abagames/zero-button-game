import json
import unittest
from collections import deque
from pathlib import Path

from zero_button_game.core import StableRng, derive_seed, sha256_value
from zero_button_game.models import Action
from zero_button_game.parking import (
    HORIZONTAL, VERTICAL, ParkingPuzzleSpec, ParkingRules, ParkingSolveRejected, ParkingSolver,
    board_occupancy, draw_candidate, generate_parking, is_solved, normalize_moves, parking_difficulty_preset,
    parking_difficulty_report, parking_quality_rejection, replay_parking, slide_moves,
    validate_parking_solution,
)

# Generating a band candidate runs a uniqueness screen, so the results are
# cached across the test methods that need them.
_GENERATED: dict[tuple[int, str, int], ParkingPuzzleSpec] = {}


def generated(master_seed: int, band: str, candidate: int = 0) -> ParkingPuzzleSpec:
    key = (master_seed, band, candidate)
    if key not in _GENERATED:
        _GENERATED[key] = generate_parking(
            StableRng(derive_seed(master_seed, "parking", candidate, "generation")),
            parking_difficulty_preset(band),
        )
    return _GENERATED[key]


GOLDEN_SEEDS = ((20260822, "easy", 0), (20260822, "medium", 0), (9614, "target", 0))
GOLDEN_HASHES = {
    "easy": {
        "problem": "sha256:0ac7f328f3242d21a937e969d24401ca259bf2f79f224c221a47d69f6ac518b7",
        "solution": "sha256:853b3bc097ab2c5cbd9c700e7d45beae61b6fc7845ad825858a02956c317dc29",
        "moves": 4,
    },
    "medium": {
        "problem": "sha256:c495db3ac507c11065e0fd808b84763fee433e4593f13706378c6b2a20cf562d",
        "solution": "sha256:df87ee1340503155220293c7b4bd6356bf2709b7dc0b6eb48496b53f2f28216f",
        "moves": 4,
    },
    "target": {
        "problem": "sha256:0411eab452776161fb79ca1d94f77d98e7c86600213e18c4ee6cff7d3956cd3c",
        "solution": "sha256:739b06399e9d0b5f88868e42a1c975f7cb015f40920c6f07c04e3407c39813f3",
        "moves": 6,
    },
}


def independent_minimum_moves(puzzle: ParkingPuzzleSpec) -> int | None:
    """Oracle written independently of ParkingSolver.

    It rebuilds the occupancy grid from raw vehicle geometry on every expansion
    and performs a plain BFS with no path counting or parent bookkeeping.
    """
    width, height = puzzle.width, puzzle.height
    vehicles = puzzle.vehicles
    target = next(index for index, item in enumerate(vehicles) if item[4] == puzzle.target_id)
    goal_offset = width - vehicles[target][0]

    def cells(state):
        grid = {}
        for index, (x, y, length, orientation, _) in enumerate(vehicles):
            offset = state[index]
            for step in range(length):
                cell = (x + offset + step, y) if orientation == HORIZONTAL else (x, y + offset + step)
                if 0 <= cell[0] < width and 0 <= cell[1] < height:
                    grid[cell] = index
        return grid

    def successors(state):
        grid = cells(state)
        result = []
        for index, (x, y, length, orientation, _) in enumerate(vehicles):
            offset = state[index]
            for direction in (1, -1):
                distance = 1
                while True:
                    new_offset = offset + direction * distance
                    body = [
                        (x + new_offset + step, y) if orientation == HORIZONTAL else (x, y + new_offset + step)
                        for step in range(length)
                    ]
                    if any(not (0 <= cx < width and 0 <= cy < height) for cx, cy in body):
                        break
                    if any(grid.get(cell) not in (None, index) for cell in body):
                        break
                    candidate = list(state)
                    candidate[index] = new_offset
                    result.append(tuple(candidate))
                    distance += 1
        # The target may additionally drive off the east rim when the rest of
        # its row is empty.
        tx, ty, tlength, _, _ = vehicles[target]
        offset = state[target]
        if offset != goal_offset and all(
            grid.get((column, ty)) in (None, target) for column in range(tx + offset + tlength, width)
        ):
            candidate = list(state)
            candidate[target] = goal_offset
            result.append(tuple(candidate))
        return result

    start = (0,) * len(vehicles)
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        state, depth = queue.popleft()
        if state[target] == goal_offset:
            return depth
        for nxt in successors(state):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, depth + 1))
    return None


class ParkingLogicTests(unittest.TestCase):
    def test_target_preset_uses_tightened_structural_thresholds(self):
        target = parking_difficulty_preset("target")
        self.assertEqual(target["width"], 6)
        self.assertEqual(target["height"], 6)
        self.assertEqual(target["search_attempts"], 3000)
        self.assertEqual(target["min_moves"], 6)
        self.assertEqual(target["min_involved_vehicles"], 6)
        self.assertEqual(target["min_difficulty_score"], 84)

        recorded = json.loads(
            (Path(__file__).parents[1] / "presets" / "current" / "parking-target.json").read_text()
        )
        self.assertEqual(recorded["generation"]["search_attempts"], target["search_attempts"])
        self.assertEqual(recorded["mechanical"]["normalized_moves"][0], target["min_moves"])
        self.assertEqual(recorded["mechanical"]["involved_vehicles_min"], target["min_involved_vehicles"])
        self.assertEqual(recorded["mechanical"]["difficulty_score"][0], target["min_difficulty_score"])

    def test_json_round_trip_and_structure(self):
        rules = ParkingRules()
        puzzle = generated(20260822, "easy")
        self.assertEqual(ParkingPuzzleSpec.from_dict(puzzle.to_dict()), puzzle)
        self.assertEqual(rules.validate_structure(puzzle), [])
        self.assertEqual(puzzle.ruleset, "rush-hour-slide-v1")
        self.assertEqual(puzzle.exit_side, "east")

    def test_structure_rejects_broken_boards(self):
        rules = ParkingRules()
        base = ParkingPuzzleSpec(
            "1.0.0", "parking", "parking-gen-1", 6, 6,
            ((0, 2, 2, HORIZONTAL, 0), (3, 0, 3, VERTICAL, 1)), 0,
        )
        self.assertEqual(rules.validate_structure(base), [])
        overlapping = ParkingPuzzleSpec(
            "1.0.0", "parking", "parking-gen-1", 6, 6,
            ((0, 2, 2, HORIZONTAL, 0), (0, 2, 2, VERTICAL, 1)), 0,
        )
        self.assertIn("overlapping vehicles", rules.validate_structure(overlapping))
        vertical_target = ParkingPuzzleSpec(
            "1.0.0", "parking", "parking-gen-1", 6, 6, ((0, 2, 2, VERTICAL, 0),), 0,
        )
        self.assertIn("target vehicle must be horizontal", rules.validate_structure(vertical_target))
        off_board = ParkingPuzzleSpec(
            "1.0.0", "parking", "parking-gen-1", 6, 6, ((5, 2, 2, HORIZONTAL, 0),), 0,
        )
        self.assertIn("vehicle outside the board", rules.validate_structure(off_board))
        wrong_exit = ParkingPuzzleSpec(
            "1.0.0", "parking", "parking-gen-1", 6, 6, ((0, 2, 2, HORIZONTAL, 0),), 0, "west",
        )
        self.assertIn("only the east exit is supported", rules.validate_structure(wrong_exit))

    def test_illegal_actions_are_rejected(self):
        rules = ParkingRules()
        puzzle = ParkingPuzzleSpec(
            "1.0.0", "parking", "parking-gen-1", 6, 6,
            ((0, 2, 2, HORIZONTAL, 0), (2, 1, 3, VERTICAL, 1)), 0,
        )
        state = rules.initial_state(puzzle)
        good_hash = sha256_value(state.to_dict())
        # wrong precondition hash
        with self.assertRaisesRegex(ValueError, "precondition"):
            rules.apply(puzzle, state, Action(1, "move_piece", "vehicle-0", {"vehicle": [0], "delta": [-0 + 1], "slide_cells": [1], "axis": [0]}, {"state_hash": "sha256:bad"}))
        # sliding through the wall
        with self.assertRaisesRegex(ValueError, "illegal vehicle slide"):
            rules.apply(puzzle, state, Action(1, "move_piece", "vehicle-0", {"vehicle": [0], "delta": [-1], "slide_cells": [1], "axis": [0]}, {"state_hash": good_hash}))
        # sliding through another vehicle
        with self.assertRaisesRegex(ValueError, "illegal vehicle slide"):
            rules.apply(puzzle, state, Action(1, "move_piece", "vehicle-0", {"vehicle": [0], "delta": [3], "slide_cells": [3], "axis": [0]}, {"state_hash": good_hash}))
        # moving off-axis
        with self.assertRaisesRegex(ValueError, "wrong axis"):
            rules.apply(puzzle, state, Action(1, "move_piece", "vehicle-0", {"vehicle": [0], "delta": [1], "slide_cells": [1], "axis": [1]}, {"state_hash": good_hash}))
        # inconsistent slide_cells
        with self.assertRaisesRegex(ValueError, "invalid move_piece parameters"):
            rules.apply(puzzle, state, Action(1, "move_piece", "vehicle-0", {"vehicle": [0], "delta": [1], "slide_cells": [2], "axis": [0]}, {"state_hash": good_hash}))
        # unknown vehicle
        with self.assertRaisesRegex(ValueError, "illegal vehicle slide"):
            rules.apply(puzzle, state, Action(1, "move_piece", "vehicle-9", {"vehicle": [9], "delta": [1], "slide_cells": [1], "axis": [0]}, {"state_hash": good_hash}))
        # a legal one still works and only moves along the axis
        legal = rules.legal_actions(puzzle, state)
        self.assertTrue(legal)
        after = rules.apply(puzzle, state, legal[0])
        self.assertEqual(sum(1 for a, b in zip(after.positions, state.positions) if a != b), 1)

    def test_unsolvable_and_ambiguous_boards_carry_reject_codes(self):
        rules = ParkingRules()
        solver = ParkingSolver(rules)
        wedged = ParkingPuzzleSpec(
            "1.0.0", "parking", "parking-gen-1", 6, 6,
            (
                (0, 0, 2, HORIZONTAL, 0),
                (2, 0, 3, VERTICAL, 1), (3, 0, 3, VERTICAL, 2),
                (4, 0, 3, VERTICAL, 3), (5, 0, 3, VERTICAL, 4),
                (2, 3, 3, VERTICAL, 5), (3, 3, 3, VERTICAL, 6),
                (4, 3, 3, VERTICAL, 7), (5, 3, 3, VERTICAL, 8),
            ), 0,
        )
        self.assertEqual(rules.validate_structure(wedged), [])
        with self.assertRaises(ParkingSolveRejected) as caught:
            solver.solve(wedged)
        self.assertEqual(caught.exception.code, "UNSOLVABLE")
        ambiguous = ParkingPuzzleSpec(
            "1.0.0", "parking", "parking-gen-1", 6, 6,
            ((0, 2, 2, HORIZONTAL, 0), (4, 1, 2, VERTICAL, 1)), 0,
        )
        with self.assertRaises(ParkingSolveRejected) as caught:
            solver.solve(ambiguous)
        self.assertEqual(caught.exception.code, "MULTIPLE_MINIMAL_PATHS")

    def test_golden_three_seeds(self):
        rules = ParkingRules()
        solver = ParkingSolver(rules)
        for master_seed, band, candidate in GOLDEN_SEEDS:
            with self.subTest(band=band):
                preset = parking_difficulty_preset(band)
                puzzle = generated(master_seed, band, candidate)
                self.assertEqual(rules.validate_structure(puzzle), [])
                solution = solver.solve(puzzle)
                self.assertEqual(validate_parking_solution(puzzle, solution, rules), [])
                self.assertEqual(solution.optimality, "proven_unique_minimum_moves")
                self.assertTrue(solution.answer_equivalence_key.startswith("unique:"))
                self.assertEqual(solution.cost, len(solution.actions))
                self.assertEqual(len(solution.actions), GOLDEN_HASHES[band]["moves"])
                self.assertEqual(independent_minimum_moves(puzzle), len(solution.actions))
                difficulty = parking_difficulty_report(puzzle, solution, rules)
                self.assertIsNone(parking_quality_rejection(difficulty, band))
                if band == "target":
                    mechanical = difficulty["mechanical"]
                    self.assertGreaterEqual(mechanical["normalized_moves"], 6)
                    self.assertGreaterEqual(mechanical["involved_vehicles"], 6)
                    self.assertGreaterEqual(mechanical["difficulty_score"], 84)
                self.assertEqual(sha256_value(puzzle.to_dict()), GOLDEN_HASHES[band]["problem"])
                self.assertEqual(sha256_value(solution.to_dict()), GOLDEN_HASHES[band]["solution"])

    def test_one_hundred_seed_property_against_independent_oracle(self):
        rules = ParkingRules()
        solver = ParkingSolver(rules)
        preset = parking_difficulty_preset("easy")
        accepted = 0
        rejected = 0
        for candidate_index in range(100):
            with self.subTest(candidate_index=candidate_index):
                seed = derive_seed(20260822, "parking", candidate_index, "generation")
                first = draw_candidate(StableRng(seed), preset)
                self.assertEqual(first, draw_candidate(StableRng(seed), preset))
                self.assertEqual(rules.validate_structure(first), [])
                analysis = solver.analyze(first)
                oracle = independent_minimum_moves(first)
                self.assertEqual(analysis["minimum_moves"], oracle)
                if analysis["status"] != "unique":
                    with self.assertRaises(ParkingSolveRejected) as caught:
                        solver.solve(first)
                    self.assertIn(caught.exception.code, {"UNSOLVABLE", "MULTIPLE_MINIMAL_PATHS"})
                    rejected += 1
                    continue
                accepted += 1
                solution = solver.solve(first)
                self.assertEqual(len(solution.actions), oracle)
                self.assertEqual(validate_parking_solution(first, solution, rules), [])
                state = rules.initial_state(first)
                for action in solution.actions:
                    self.assertIn(action, rules.legal_actions(first, state))
                    state = rules.apply(first, state, action)
                self.assertTrue(rules.is_goal(first, state))
                moves = tuple((a.params["vehicle"][0], a.params["delta"][0]) for a in solution.actions)
                self.assertEqual(normalize_moves(moves), moves)
        self.assertGreater(accepted, 0)
        self.assertGreater(rejected, 0)

    def test_replay_and_goal_semantics(self):
        rules = ParkingRules()
        solver = ParkingSolver(rules)
        preset = parking_difficulty_preset("medium")
        puzzle = generated(20260822, "medium")
        solution = solver.solve(puzzle)
        trace = replay_parking(puzzle, solution.actions, rules)
        self.assertEqual(len(trace.steps), len(solution.actions))
        self.assertFalse(rules.is_goal(puzzle, trace.initial))
        self.assertTrue(rules.is_goal(puzzle, trace.final))
        self.assertTrue(is_solved(puzzle, trace.final.positions))
        # The released target occupies no board cell any more.
        self.assertNotIn(puzzle.index_of(puzzle.target_id), set(board_occupancy(puzzle, trace.final.positions).values()))
        # Solved states are terminal.
        self.assertEqual(slide_moves(puzzle, trace.final.positions), [])
        # Every intermediate state keeps the board consistent.
        for step in trace.steps:
            grid = board_occupancy(puzzle, step.after.positions)
            self.assertEqual(len(grid), len(set(grid)))

    def test_normalize_folds_consecutive_same_vehicle_slides(self):
        self.assertEqual(normalize_moves(((0, 1), (0, 2), (1, -1))), ((0, 3), (1, -1)))
        self.assertEqual(normalize_moves(((0, 1), (0, -1), (1, 2))), ((1, 2),))
        self.assertEqual(normalize_moves(((0, 1), (1, 1), (0, 1))), ((0, 1), (1, 1), (0, 1)))

    def test_generated_candidates_are_deterministic(self):
        for band in ("easy", "medium"):
            with self.subTest(band=band):
                preset = parking_difficulty_preset(band)
                seed = derive_seed(4242, "parking", 0, "generation")
                self.assertEqual(
                    generate_parking(StableRng(seed), preset),
                    generate_parking(StableRng(seed), preset),
                )


if __name__ == "__main__":
    unittest.main()
