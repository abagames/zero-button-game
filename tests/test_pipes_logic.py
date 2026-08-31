import unittest
from dataclasses import replace
from heapq import heappop, heappush

from zero_button_game.core import StableRng, derive_seed, sha256_value
from zero_button_game.models import Action, Solution
from zero_button_game.pipes import PipePuzzleSpec, PipeRules, PipeSolveRejected, PipeSolver, PipeState, generate_pipes, pipe_difficulty_preset, pipe_difficulty_report, pipe_quality_rejection, replay_pipes


class PipeLogicTests(unittest.TestCase):
    def test_round_trip_illegal_action_and_solver_replay(self):
        rules = PipeRules()
        puzzle = generate_pipes(StableRng(derive_seed(27, "pipes", 0, "generation")))
        self.assertEqual(PipePuzzleSpec.from_dict(puzzle.to_dict()), puzzle)
        self.assertEqual(rules.validate_structure(puzzle), [])
        solution = PipeSolver(rules).solve(puzzle)
        self.assertGreater(len(solution.actions), 0)
        final = replay_pipes(puzzle, solution.actions, rules)
        self.assertTrue(rules.is_goal(puzzle, final))
        self.assertEqual(sha256_value(final.to_dict()), solution.final_state_hash)
        first = solution.actions[0]
        bad = Action(first.action_version, first.kind, first.actor_id, first.params, {"state_hash":"sha256:bad"})
        with self.assertRaisesRegex(ValueError, "precondition"):
            rules.apply(puzzle, rules.initial_state(puzzle), bad)

    def test_one_hundred_seed_generator_solver_property(self):
        rules = PipeRules()
        solver = PipeSolver(rules)
        accepted = 0
        rejected = {"MULTIPLE_MINIMAL_PATHS": 0, "MULTIPLE_MINIMAL_SIGNATURES": 0}
        for candidate_index in range(100):
            with self.subTest(candidate_index=candidate_index):
                seed = derive_seed(20260821, "pipes", candidate_index, "generation")
                first = generate_pipes(StableRng(seed))
                second = generate_pipes(StableRng(seed))
                self.assertEqual(first, second)
                self.assertEqual(rules.validate_structure(first), [])
                analysis = solver.analyze_minimum_solutions(first)
                self.assertEqual(analysis, solver.analyze_minimum_solutions(second))
                if not analysis["solution_uniqueness"]:
                    expected = "MULTIPLE_MINIMAL_PATHS" if analysis["unique_path_count"] != 1 else "MULTIPLE_MINIMAL_SIGNATURES"
                    with self.assertRaisesRegex(PipeSolveRejected, expected):
                        solver.solve(first)
                    rejected[expected] += 1
                    continue
                first_solution = solver.solve(first)
                self.assertEqual(first_solution, solver.solve(second))
                accepted += 1
                state = rules.initial_state(first)
                for action in first_solution.actions:
                    self.assertIn(action, rules.legal_actions(first, state))
                    state = rules.apply(first, state, action)
                self.assertTrue(rules.is_goal(first, state))
                self.assertEqual(
                    {tuple(action.params["cell"]) for action in first_solution.actions}
                    - set(rules.connected_path(first, state)),
                    set(),
                )
                for action in first_solution.actions:
                    rotations = list(state.rotations)
                    cell = tuple(action.params["cell"])
                    rotations[cell[1] * first.width + cell[0]] = 0
                    self.assertFalse(rules.is_goal(first, PipeState("pipes", 0, tuple(rotations))))
                self.assertEqual(analysis["normalized_solution_count"], 1)
                self.assertEqual(analysis["unique_path_count"], 1)
        self.assertEqual(accepted + sum(rejected.values()), 100)
        self.assertGreater(accepted, 50)
        self.assertGreater(sum(rejected.values()), 0)

    def test_solver_matches_independent_minimum_turn_oracle(self):
        rules = PipeRules()
        puzzle = generate_pipes(StableRng(derive_seed(2, "pipes", 0, "generation")), 3, 3)
        solution = PipeSolver(rules).solve(puzzle)
        start = (0,) * len(puzzle.initial_masks)
        heap = [(0, start)]
        best = {start: 0}
        oracle_cost = None
        while heap:
            cost, rotations = heappop(heap)
            if cost != best[rotations]:
                continue
            state = PipeState("pipes", 0, rotations)
            if rules.is_goal(puzzle, state):
                oracle_cost = cost
                break
            for index in range(len(rotations)):
                for delta in (-1, 1):
                    nxt = list(rotations)
                    nxt[index] = (nxt[index] + delta) % 4
                    nxt = tuple(nxt)
                    if cost + 1 < best.get(nxt, 10**9):
                        best[nxt] = cost + 1
                        heappush(heap, (cost + 1, nxt))
        self.assertEqual(solution.cost, oracle_cost)

    def test_normalized_equivalence_and_ambiguity_fixtures(self):
        rules = PipeRules()
        solver = PipeSolver(rules)
        unique = generate_pipes(StableRng(derive_seed(2, "pipes", 0, "generation")), 3, 3)
        solution = solver.solve(unique)

        # Independent Actions commute: presentation order is not solution identity.
        forward = solver.normalized_action_evidence(unique, solution.actions)
        reverse = solver.normalized_action_evidence(unique, tuple(reversed(solution.actions)))
        self.assertEqual(forward["signatures"], reverse["signatures"])

        def synthetic(turns):
            return (Action(1, "rotate_piece", "pipe-0", {"cell":[0, 0], "quarter_turns":[turns]}, {}),)

        # 180 CW/CCW collapse; +270 collapses to -90 with cost one.
        self.assertEqual(
            solver.normalized_action_evidence(unique, synthetic(2))["canonical_signed_rotations"],
            solver.normalized_action_evidence(unique, synthetic(-2))["canonical_signed_rotations"],
        )
        plus_270 = solver.normalized_action_evidence(unique, synthetic(3))
        minus_90 = solver.normalized_action_evidence(unique, synthetic(-1))
        self.assertEqual(plus_270["canonical_signed_rotations"], minus_90["canonical_signed_rotations"])
        self.assertEqual(plus_270["canonical_cost"], 1)
        self.assertTrue(plus_270["has_redundant_turns"])

        # Same final orientations (no rotated panels), two active paths: still two solutions.
        cycle = PipePuzzleSpec(
            "1.2.0", "pipes", "fixture-cycle", 3, 3, (0, 0), (2, 2),
            (6, 10, 12, 5, 1, 5, 3, 10, 9), "source-to-goal-unique-v3",
        )
        cycle_analysis = solver.analyze_minimum_solutions(cycle)
        self.assertEqual(cycle_analysis["minimum_quarter_turn_cost"], 0)
        self.assertEqual(cycle_analysis["normalized_solution_count"], 2)
        self.assertEqual(cycle_analysis["unique_path_count"], 2)
        with self.assertRaisesRegex(PipeSolveRejected, "MULTIPLE_MINIMAL_PATHS"):
            solver.solve(cycle)

        # Legacy Medium has equal-cost witnesses with different paths and panel sets.
        multiple_sets = PipePuzzleSpec(
            "1.2.0", "pipes", "fixture-panel-sets", 4, 4, (0, 0), (3, 3),
            (12, 3, 3, 4, 10, 5, 6, 12, 10, 9, 12, 10, 6, 4, 12, 9),
            "source-to-goal-unique-v3",
        )
        multiple_analysis = solver.analyze_minimum_solutions(multiple_sets)
        self.assertEqual(multiple_analysis["normalized_solution_count"], 2)
        self.assertEqual(multiple_analysis["unique_path_count"], 2)
        self.assertNotEqual(
            multiple_analysis["signatures"][0]["panels"],
            multiple_analysis["signatures"][1]["panels"],
        )

    def test_reordered_actions_validate_as_same_unique_solution(self):
        rules = PipeRules()
        solver = PipeSolver(rules)
        puzzle = generate_pipes(StableRng(derive_seed(2, "pipes", 0, "generation")), 3, 3)
        canonical = solver.solve(puzzle)
        state = rules.initial_state(puzzle)
        rebound = []
        for action in reversed(canonical.actions):
            rebound_action = Action(
                action.action_version, action.kind, action.actor_id, action.params,
                {"state_hash": sha256_value(state.to_dict())},
            )
            state = rules.apply(puzzle, state, rebound_action)
            rebound.append(rebound_action)
        reordered = Solution(
            canonical.schema_version, canonical.solver_id, canonical.solver_version,
            canonical.optimality, tuple(rebound), canonical.initial_state_hash,
            sha256_value(state.to_dict()), canonical.cost, canonical.expanded_nodes,
            canonical.answer_equivalence_key,
        )
        from zero_button_game.pipes import validate_pipe_solution
        self.assertEqual(validate_pipe_solution(puzzle, reordered, rules), [])

    def test_source_goal_allows_unused_leaks_but_legacy_variant_does_not(self):
        rules = PipeRules()
        puzzle = PipePuzzleSpec(
            "1.1.0", "pipes", "fixture", 3, 3, (0, 0), (2, 2),
            (2, 10, 12, 1, 1, 5, 1, 1, 1), "source-to-goal-v2",
        )
        state = rules.initial_state(puzzle)
        self.assertTrue(rules.is_goal(puzzle, state))
        self.assertEqual(rules.connected_path(puzzle, state), ((0, 0), (1, 0), (2, 0), (2, 1), (2, 2)))
        self.assertFalse(rules.is_goal(replace(puzzle, ruleset="connected-no-leaks-v1"), state))

    def test_source_goal_v2_solver_remains_readable_and_replayable(self):
        rules = PipeRules()
        puzzle = PipePuzzleSpec(
            "1.1.0", "pipes", "pipes-gen-2", 3, 3, (0, 0), (2, 2),
            (8, 2, 4, 5, 12, 13, 3, 5, 12), "source-to-goal-v2",
        )
        solution = PipeSolver(rules).solve(puzzle)
        self.assertEqual(solution.solver_id, "pipes-source-goal")
        self.assertTrue(rules.is_goal(puzzle, replay_pipes(puzzle, solution.actions, rules)))
        from zero_button_game.pipes import validate_pipe_solution
        self.assertEqual(validate_pipe_solution(puzzle, solution, rules), [])

    def test_easy_medium_target_are_mechanical_and_uncalibrated(self):
        rules = PipeRules()
        solver = PipeSolver(rules)
        previous_minimum = -1
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                preset = pipe_difficulty_preset(band)
                self.assertGreater(preset["min_difficulty_score"], previous_minimum)
                previous_minimum = preset["min_difficulty_score"]
                self.assertLess(preset["max_difficulty_score"], pipe_difficulty_preset(
                    "medium" if band == "easy" else "target" if band == "medium" else "target"
                )["min_difficulty_score"] if band != "target" else 10**9)
        puzzle = generate_pipes(StableRng(derive_seed(2, "pipes", 0, "generation")), 3, 3)
        report = pipe_difficulty_report(puzzle, solver.solve(puzzle), rules)
        self.assertEqual(report["human"]["status"], "uncalibrated-pipes-unique-v3")
        self.assertEqual(report["solution_uniqueness"]["normalized_solution_count"], 1)
        for metric in ("required_path_length", "required_rotation_pieces", "candidate_routes", "false_connection_edges", "difficulty_score", "goal_irrelevant_actions"):
            self.assertIn(metric, report["mechanical"])


if __name__ == "__main__":
    unittest.main()
