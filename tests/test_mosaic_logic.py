from __future__ import annotations

import dataclasses
import unittest
from collections import deque

from zero_button_game.core import StableRng, derive_seed, sha256_value
from zero_button_game.models import Action
from zero_button_game.mosaic import (
    MOSAIC_GENERATOR_VERSION, MosaicPuzzleSpec, MosaicRules, MosaicSolver,
    action_signature, shift_tiles,
)
from zero_button_game.registry import get_plugin


def fixture() -> MosaicPuzzleSpec:
    return MosaicPuzzleSpec(
        "1.0.0", "mosaic", MOSAIC_GENERATOR_VERSION, 3,
        (2, 4, 1, 3, 7, 5, 0, 8, 6), tuple(range(9)), "four-petal-star",
    )


def independent_oracle(puzzle: MosaicPuzzleSpec, max_depth: int = 8) -> tuple[int | None, int]:
    actions = tuple((axis, line, delta) for axis in (0, 1) for line in range(3) for delta in (-1, 1))

    def move(tiles, action):
        axis, line, delta = action
        following = list(tiles)
        for position in range(3):
            sx, sy = ((position, line) if axis == 0 else (line, position))
            tx = (sx + delta) % 3 if axis == 0 else sx
            ty = (sy + delta) % 3 if axis == 1 else sy
            following[ty * 3 + tx] = tiles[sy * 3 + sx]
        return tuple(following)

    queue = deque([puzzle.initial_tiles])
    distance = {puzzle.initial_tiles: 0}
    ways = {puzzle.initial_tiles: 1}
    goal_depth = None
    while queue:
        state = queue.popleft()
        depth = distance[state]
        if depth >= max_depth or (goal_depth is not None and depth >= goal_depth):
            continue
        for action in actions:
            following = move(state, action)
            if following not in distance:
                distance[following] = depth + 1
                ways[following] = ways[state]
                queue.append(following)
            elif distance[following] == depth + 1:
                ways[following] += ways[state]
            if following == puzzle.goal_tiles and goal_depth is None:
                goal_depth = depth + 1
    return distance.get(puzzle.goal_tiles), ways.get(puzzle.goal_tiles, 0)


class MosaicLogicTests(unittest.TestCase):
    def test_solver_proves_one_three_action_shortest_sequence(self):
        puzzle = fixture()
        analysis = MosaicSolver().analyze(puzzle)
        self.assertEqual(analysis["status"], "unique")
        self.assertEqual(analysis["depth"], 3)
        self.assertEqual(analysis["shortest_path_count"], 1)
        self.assertEqual(independent_oracle(puzzle), (3, 1))

    def test_actions_are_cyclic_state_hashed_and_replayable(self):
        puzzle = fixture()
        rules = MosaicRules()
        solution = MosaicSolver(rules).solve(puzzle)
        state = rules.initial_state(puzzle)
        self.assertEqual(solution.initial_state_hash, sha256_value(state.to_dict()))
        for action in solution.actions:
            axis, line, delta = action_signature(action)
            before = state.tiles
            state = rules.apply(puzzle, state, action)
            changed = {index for index, pair in enumerate(zip(before, state.tiles)) if pair[0] != pair[1]}
            expected = ({line * 3 + x for x in range(3)} if axis == "row" else {y * 3 + line for y in range(3)})
            self.assertEqual(changed, expected)
            self.assertEqual(state.tiles, shift_tiles(before, 3, axis, line, delta))
        self.assertTrue(rules.is_goal(puzzle, state))
        self.assertEqual(solution.final_state_hash, sha256_value(state.to_dict()))

    def test_stale_malformed_and_wrong_kind_actions_fail_closed(self):
        puzzle = fixture()
        rules = MosaicRules()
        state = rules.initial_state(puzzle)
        action = rules.action_for(state, "row", 0, 1)
        with self.assertRaisesRegex(ValueError, "precondition"):
            rules.apply(puzzle, state, dataclasses.replace(action, precondition={"state_hash": "sha256:stale"}))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            rules.apply(puzzle, state, Action(1, "toggle_cell", action.actor_id, action.params, action.precondition))
        with self.assertRaisesRegex(ValueError, "malformed"):
            rules.apply(puzzle, state, dataclasses.replace(action, params={"axis": [], "line": [0], "delta": [1]}))

    def test_all_bands_generate_deterministically_and_accept_within_budget(self):
        plugin = get_plugin("mosaic")
        expected_ranges = {"easy": (2, 2), "medium": (3, 3), "target": (4, 4)}
        for band, (low, high) in expected_ranges.items():
            preset = plugin.difficulty_preset(band)
            accepted = None
            for index in range(100):
                seed = derive_seed(20260901, "mosaic", index, "generation")
                first = plugin.generate_candidate(StableRng(seed), preset)
                second = plugin.generate_candidate(StableRng(seed), preset)
                self.assertEqual(first, second)
                try:
                    solution = plugin.solver.solve(first)
                except RuntimeError:
                    continue
                difficulty = plugin.difficulty(first, solution, plugin.rules)
                if plugin.quality_filter(difficulty, band) is None:
                    accepted = (solution, difficulty)
                    break
            self.assertIsNotNone(accepted, band)
            solution, difficulty = accepted
            self.assertEqual(len(solution.actions), low)
            self.assertEqual(low, high)
            self.assertEqual(difficulty["solution_uniqueness"]["shortest_path_count"], 1)
            self.assertFalse(difficulty["mechanical"]["independent_line_repairs"])
            self.assertEqual(difficulty["human"]["status"], "uncalibrated-mosaic-v1")


if __name__ == "__main__":
    unittest.main()
