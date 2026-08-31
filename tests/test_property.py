import unittest
from collections import deque

from zero_button_game.core import StableRng, derive_seed, sha256_value
from zero_button_game.maze import MazeRules, MazeSolver, difficulty_report, generate_maze, quality_rejection, replay
from zero_button_game.presentation import direct_plan


def independent_shortest_cost(puzzle, rules):
    queue = deque([(puzzle.start, 0)])
    visited = {puzzle.start}
    while queue:
        cell, cost = queue.popleft()
        if cell == puzzle.goal:
            return cost
        for nxt in rules.neighbors(puzzle, cell):
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, cost + 1))
    raise AssertionError("generated maze was unsolvable")


class MazePropertyTests(unittest.TestCase):
    def test_one_hundred_seeds(self):
        rules = MazeRules()
        solver = MazeSolver(rules)
        for candidate in range(100):
            with self.subTest(candidate=candidate):
                seed = derive_seed(20260820, "maze", candidate, "generation")
                puzzle_a = generate_maze(StableRng(seed))
                puzzle_b = generate_maze(StableRng(seed))
                self.assertEqual(puzzle_a, puzzle_b)
                self.assertEqual(rules.validate_structure(puzzle_a), [])
                solution_a = solver.solve(puzzle_a)
                solution_b = solver.solve(puzzle_b)
                self.assertEqual(solution_a, solution_b)
                self.assertEqual(solution_a.cost, independent_shortest_cost(puzzle_a, rules))
                state = rules.initial_state(puzzle_a)
                for action in solution_a.actions:
                    self.assertIn(action, rules.legal_actions(puzzle_a, state))
                    state = rules.apply(puzzle_a, state, action)
                self.assertTrue(rules.is_goal(puzzle_a, state))
                self.assertEqual(sha256_value(state.to_dict()), solution_a.final_state_hash)
                plan = direct_plan(puzzle_a, solution_a, rules)
                trace = replay(puzzle_a, plan.logical_steps, rules)
                self.assertTrue(rules.is_goal(puzzle_a, trace.final))

    def test_easy_and_medium_bands_accept_reproducible_multiaxis_candidates(self):
        rules = MazeRules()
        solver = MazeSolver(rules)
        accepted = {band: [] for band in ("easy", "medium")}
        for candidate in range(200):
            seed = derive_seed(20260820, "maze", candidate, "generation")
            puzzle = generate_maze(StableRng(seed))
            solution = solver.solve(puzzle)
            for band in accepted:
                report = difficulty_report(puzzle, solution, rules)
                if quality_rejection(report, band) is None:
                    accepted[band].append((candidate, puzzle, solution, report))
        for band, results in accepted.items():
            with self.subTest(band=band):
                self.assertGreaterEqual(len(results), 5)
                for candidate, puzzle, solution, report in results:
                    repeat = generate_maze(StableRng(derive_seed(20260820, "maze", candidate, "generation")))
                    self.assertEqual(repeat, puzzle)
                    self.assertEqual(report["accepted_band"], band)
                    trace = replay(puzzle, solution.actions, rules)
                    self.assertTrue(rules.is_goal(puzzle, trace.final))

    def test_target_mixed_selected_candidates_have_diverse_satisfied_recipes(self):
        from zero_button_game.maze import difficulty_preset
        rules = MazeRules()
        solver = MazeSolver(rules)
        preset = difficulty_preset("target")
        recipes = set()
        for candidate_index in (1, 2, 8, 9, 10, 16):
            with self.subTest(candidate_index=candidate_index):
                seed = derive_seed(20260821, "maze", candidate_index, "generation")
                first = generate_maze(StableRng(seed), preset["width"], preset["height"], preset["endpoint_profile"])
                second = generate_maze(StableRng(seed), preset["width"], preset["height"], preset["endpoint_profile"])
                self.assertEqual(first, second)
                first_solution = solver.solve(first)
                self.assertEqual(first_solution, solver.solve(second))
                trace = replay(first, first_solution.actions, rules)
                self.assertTrue(rules.is_goal(first, trace.final))
                report = difficulty_report(first, first_solution, rules)
                self.assertIsNone(quality_rejection(report, "target"))
                traits = report["generation_traits"]
                self.assertGreaterEqual(len(traits["active_traits"]), 2)
                self.assertEqual(traits["active_satisfied_count"], len(traits["active_traits"]))
                recipes.add(tuple(traits["active_traits"]))
        self.assertGreaterEqual(len(recipes), 5)


if __name__ == "__main__":
    unittest.main()
