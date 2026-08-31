import json
import time
import unittest

from zero_button_game.core import StableRng, derive_seed, sha256_value
from zero_button_game.lights import (
    BOARD_HEIGHT, BOARD_WIDTH, LIGHTS_RULESET, LightsPuzzleSpec, LightsRules, LightsSolveRejected,
    LightsSolver, board_from_presses, board_symmetries, cell_index, chase_press_count, draw_candidate,
    generate_lights, greedy_reduction_count, lights_difficulty_preset, lights_difficulty_report,
    lights_quality_rejection, make_action, plus_cells, press_masks, press_set_signature, replay_lights,
    signature_hash, solution_press_set, validate_lights_solution,
)
from zero_button_game.models import Action, Solution

RULES = LightsRules()
SOLVER = LightsSolver(RULES)


def _puzzle(band="target", candidate=0, master_seed=20260822):
    return generate_lights(
        StableRng(derive_seed(master_seed, "lights", candidate, "generation")),
        lights_difficulty_preset(band),
    )


class LightsShapeTests(unittest.TestCase):
    def test_only_full_rank_shapes_are_accepted(self):
        """4x4 and 5x5 have nullity 4 and 2; 5x4 is the shape that can be unique."""
        expected = {(4, 4): 4, (5, 5): 2, (5, 4): 0}
        for (width, height), nullity in expected.items():
            with self.subTest(shape=(width, height)):
                self.assertEqual(_nullity(width, height), nullity)
        self.assertEqual((BOARD_WIDTH, BOARD_HEIGHT), (5, 4))

    def test_plus_mask_is_the_cell_and_its_orthogonal_neighbours(self):
        self.assertEqual(plus_cells(5, 4, (0, 0)), ((0, 0), (0, 1), (1, 0)))
        self.assertEqual(plus_cells(5, 4, (2, 2)), ((1, 2), (2, 1), (2, 2), (2, 3), (3, 2)))
        masks = press_masks(5, 4)
        self.assertEqual(len(masks), 20)
        for index, mask in enumerate(masks):
            cell = (index % 5, index // 5)
            self.assertEqual(bin(mask).count("1"), len(plus_cells(5, 4, cell)))


def _nullity(width, height):
    count = width * height
    masks = press_masks(width, height)
    rows = []
    for r in range(count):
        row = 0
        for c in range(count):
            if masks[c] >> r & 1:
                row |= 1 << c
        rows.append(row)
    rank = 0
    for column in range(count):
        pivot = next((r for r in range(rank, count) if rows[r] >> column & 1), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        for r in range(count):
            if r != rank and rows[r] >> column & 1:
                rows[r] ^= rows[rank]
        rank += 1
    return count - rank


class LightsGoldenTests(unittest.TestCase):
    """Three frozen seeds: structure, press set and equivalence key."""

    # Regenerated when the press bands were re-cut from 4/6/8 to 2/3/4.
    GOLDEN = {
        "easy": 2,
        "medium": 3,
        "target": 4,
    }

    def test_golden_seeds_are_stable(self):
        seen = {}
        for band, presses in self.GOLDEN.items():
            with self.subTest(band=band):
                puzzle = _puzzle(band)
                self.assertEqual(RULES.validate_structure(puzzle), [])
                solution = SOLVER.solve(puzzle)
                self.assertEqual(len(solution.actions), presses)
                self.assertEqual(solution.optimality, "proven_unique_gf2_press_set")
                self.assertEqual(
                    solution.answer_equivalence_key,
                    "unique:" + signature_hash(solution_press_set(solution)),
                )
                self.assertEqual(validate_lights_solution(puzzle, solution, RULES), [])
                seen[band] = sha256_value(puzzle.to_dict())
        self.assertEqual(len(set(seen.values())), 3, "golden seeds must not collide across bands")

    def test_golden_press_sets_reproduce_the_board(self):
        for band in self.GOLDEN:
            with self.subTest(band=band):
                puzzle = _puzzle(band)
                presses = solution_press_set(SOLVER.solve(puzzle))
                self.assertEqual(board_from_presses(puzzle.width, puzzle.height, presses), tuple(puzzle.initial))


class LightsPropertyTests(unittest.TestCase):
    def test_hundred_seeds_solve_uniquely_and_reach_the_goal(self):
        preset = lights_difficulty_preset("medium")
        for index in range(100):
            with self.subTest(seed=index):
                puzzle = draw_candidate(StableRng(derive_seed(7, "lights", index, "generation")), preset)
                self.assertEqual(RULES.validate_structure(puzzle), [])
                analysis = SOLVER.analyze(puzzle)
                self.assertEqual(analysis["status"], "unique")
                self.assertEqual(analysis["nullity"], 0)
                self.assertEqual(analysis["rank"], 20)
                solution = SOLVER.solve(puzzle)
                trace = replay_lights(puzzle, solution.actions, RULES)
                self.assertTrue(RULES.is_goal(puzzle, trace.final))
                self.assertEqual(validate_lights_solution(puzzle, solution, RULES), [])
                # Presses commute and a press set never repeats a cell.
                cells = solution_press_set(solution)
                self.assertEqual(len(set(cells)), len(cells))

    def test_press_order_does_not_change_the_final_board(self):
        puzzle = _puzzle("target")
        presses = solution_press_set(SOLVER.solve(puzzle))
        for rotation in range(len(presses)):
            shifted = presses[rotation:] + presses[:rotation]
            self.assertEqual(
                board_from_presses(puzzle.width, puzzle.height, shifted),
                tuple(puzzle.initial),
            )


class LightsIndependentOracleTests(unittest.TestCase):
    """Brute force every one of the 2^20 press subsets, once.

    The GF(2) rank argument claims exactly one subset reaches the all-lit
    board. This walks the whole subset lattice in Gray-code order - one XOR per
    subset - and counts the solutions directly, with no linear algebra
    involved. It is the only check in this file that does not trust the solver.
    """

    def test_gray_code_enumeration_agrees_with_the_rank_proof(self):
        puzzle = _puzzle("target")
        masks = press_masks(puzzle.width, puzzle.height)
        target = (1 << (puzzle.width * puzzle.height)) - 1
        state = 0
        for index, value in enumerate(puzzle.initial):
            if value:
                state |= 1 << index
        solutions = []
        current = state
        subset = 0
        if current == target:
            solutions.append(0)
        for step in range(1, 1 << len(masks)):
            bit = (step & -step).bit_length() - 1
            subset ^= 1 << bit
            current ^= masks[bit]
            if current == target:
                solutions.append(subset)
        self.assertEqual(len(solutions), 1, "the board must admit exactly one press set")
        cells = tuple(sorted(
            ((index % puzzle.width, index // puzzle.width)
             for index in range(len(masks)) if solutions[0] >> index & 1),
            key=lambda cell: (cell[1], cell[0]),
        ))
        analysis = SOLVER.analyze(puzzle)
        self.assertEqual(analysis["status"], "unique")
        self.assertEqual(press_set_signature(analysis["_press_set"]), press_set_signature(cells))
        self.assertEqual(
            SOLVER.solve(puzzle).answer_equivalence_key, "unique:" + signature_hash(cells)
        )


class LightsIllegalActionTests(unittest.TestCase):
    def setUp(self):
        self.puzzle = _puzzle("easy")
        self.state = RULES.initial_state(self.puzzle)

    def test_other_action_kinds_are_rejected(self):
        good = make_action(self.puzzle, self.state, (1, 1))
        for kind in ("move_piece", "rotate_piece", "traverse_edge", ""):
            with self.subTest(kind=kind):
                bad = Action(1, kind, good.actor_id, dict(good.params), dict(good.precondition))
                with self.assertRaises(ValueError):
                    RULES.apply(self.puzzle, self.state, bad)

    def test_state_hash_mismatch_is_rejected(self):
        action = make_action(self.puzzle, self.state, (1, 1))
        moved = RULES.apply(self.puzzle, self.state, action)
        with self.assertRaises(ValueError):
            RULES.apply(self.puzzle, moved, action)

    def test_out_of_board_cells_are_rejected(self):
        for cell in ((-1, 0), (0, -1), (5, 0), (0, 4), (99, 99)):
            with self.subTest(cell=cell):
                bad = Action(
                    1, "toggle_cell", f"cell-{cell[0]}-{cell[1]}", {"cell": [cell[0], cell[1]]},
                    {"state_hash": sha256_value(self.state.to_dict())},
                )
                with self.assertRaises(ValueError):
                    RULES.apply(self.puzzle, self.state, bad)

    def test_malformed_parameters_and_actor_are_rejected(self):
        base = {"state_hash": sha256_value(self.state.to_dict())}
        for params in ({"cell": [1]}, {"cell": [1, 1, 1]}, {}):
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    RULES.apply(self.puzzle, self.state, Action(1, "toggle_cell", "cell-1-1", params, base))
        with self.assertRaises(ValueError):
            RULES.apply(self.puzzle, self.state, Action(1, "toggle_cell", "cell-9-9", {"cell": [1, 1]}, base))

    def test_action_params_fit_the_protocol_v1_shape(self):
        """params must remain dict[str, list[int]] - the protocol's only real limit."""
        action = make_action(self.puzzle, self.state, (3, 2))
        self.assertEqual(action.kind, "toggle_cell")
        self.assertIsInstance(action.params, dict)
        for key, value in action.params.items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(value, list)
            self.assertTrue(all(isinstance(item, int) for item in value))
        self.assertEqual(action.params, {"cell": [3, 2]})


class LightsJsonRoundTripTests(unittest.TestCase):
    def test_problem_and_solution_round_trip(self):
        puzzle = _puzzle("medium")
        solution = SOLVER.solve(puzzle)
        restored = LightsPuzzleSpec.from_dict(json.loads(json.dumps(puzzle.to_dict())))
        self.assertEqual(restored, puzzle)
        self.assertEqual(restored.ruleset, LIGHTS_RULESET)
        restored_solution = Solution.from_dict(json.loads(json.dumps(solution.to_dict())))
        self.assertEqual(restored_solution, solution)
        self.assertEqual(validate_lights_solution(restored, restored_solution, RULES), [])

    def test_state_round_trips_through_its_dict(self):
        puzzle = _puzzle("easy")
        state = RULES.initial_state(puzzle)
        self.assertEqual(state.to_dict()["lights"], list(puzzle.initial))
        self.assertEqual(sha256_value(state.to_dict()), sha256_value(state.to_dict()))


class LightsStructureTests(unittest.TestCase):
    def test_structural_rejections(self):
        good = _puzzle("easy")
        cases = {
            "wrong puzzle type": good.__class__(good.schema_version, "maze", good.generator_version, good.width, good.height, good.initial, good.ruleset),
            "unsupported ruleset": good.__class__(good.schema_version, "lights", good.generator_version, good.width, good.height, good.initial, "other-v1"),
            "board shape is not the 5x4 full-rank shape": good.__class__(good.schema_version, "lights", good.generator_version, 4, 4, tuple([0] * 16), LIGHTS_RULESET),
            "board is already solved": good.__class__(good.schema_version, "lights", good.generator_version, 5, 4, tuple([1] * 20), LIGHTS_RULESET),
            "initial light value outside {0, 1}": good.__class__(good.schema_version, "lights", good.generator_version, 5, 4, (2,) + tuple(good.initial[1:]), LIGHTS_RULESET),
        }
        for expected, puzzle in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, RULES.validate_structure(puzzle))

    def test_legal_actions_cover_every_cell(self):
        puzzle = _puzzle("easy")
        actions = RULES.legal_actions(puzzle, RULES.initial_state(puzzle))
        self.assertEqual(len(actions), 20)
        self.assertEqual(
            {tuple(action.params["cell"]) for action in actions},
            {(x, y) for y in range(4) for x in range(5)},
        )
        self.assertTrue(all(action.kind == "toggle_cell" for action in actions))


class LightsDifficultyTests(unittest.TestCase):
    def test_bands_do_not_collapse_onto_one_press_count(self):
        """Measured over a generation batch, not asserted from the preset."""
        seen = {}
        scores = {}
        for band in ("easy", "medium", "target"):
            preset = lights_difficulty_preset(band)
            counts = set()
            band_scores = []
            accepted = 0
            for index in range(120):
                puzzle = draw_candidate(StableRng(derive_seed(20260822, "lights", index, "generation")), preset)
                if RULES.validate_structure(puzzle):
                    continue
                solution = SOLVER.solve(puzzle)
                report = lights_difficulty_report(puzzle, solution, RULES)
                if lights_quality_rejection(report, band) is not None:
                    continue
                accepted += 1
                counts.add(report["mechanical"]["press_count"])
                band_scores.append(report["mechanical"]["difficulty_score"])
            self.assertGreater(accepted, 0, f"{band} accepted nothing")
            seen[band] = counts
            scores[band] = (min(band_scores), max(band_scores))
        self.assertEqual(seen, {"easy": {2}, "medium": {3}, "target": {4}})
        # Score bands must not overlap either, or the bands are labels only.
        self.assertLess(scores["easy"][1], scores["medium"][0])
        self.assertLess(scores["medium"][1], scores["target"][0])

    def test_deceptiveness_metrics_actually_gate(self):
        preset = lights_difficulty_preset("target")
        rejected = set()
        for index in range(200):
            puzzle = draw_candidate(StableRng(derive_seed(20260822, "lights", index, "generation")), preset)
            if RULES.validate_structure(puzzle):
                continue
            report = lights_difficulty_report(puzzle, SOLVER.solve(puzzle), RULES)
            reason = lights_quality_rejection(report, "target")
            if reason is not None:
                rejected.add(reason)
        self.assertIn("TOO_GREEDY_FRIENDLY", rejected)
        self.assertIn("TOO_TRIVIAL", rejected)

    def test_symmetric_boards_are_rejected(self):
        # A press set symmetric under both mirrors yields a symmetric board.
        initial = board_from_presses(5, 4, [(0, 0), (4, 0), (0, 3), (4, 3)])
        puzzle = LightsPuzzleSpec("1.0.0", "lights", "lights-gen-1", 5, 4, initial, LIGHTS_RULESET)
        self.assertEqual(RULES.validate_structure(puzzle), [])
        self.assertIn("horizontal_mirror", board_symmetries(puzzle))
        report = lights_difficulty_report(puzzle, SOLVER.solve(puzzle), RULES)
        # Target is the band whose press count admits this four-press set, so the
        # symmetry rejection is the one that fires.
        self.assertEqual(lights_quality_rejection(report, "target"), "SYMMETRIC_BOARD")

    def test_short_press_sets_are_rejected_in_every_band(self):
        # |S| = 1: below the two-press floor the neutrality shift needs.
        initial = board_from_presses(5, 4, [(1, 1)])
        puzzle = LightsPuzzleSpec("1.0.0", "lights", "lights-gen-1", 5, 4, initial, LIGHTS_RULESET)
        report = lights_difficulty_report(puzzle, SOLVER.solve(puzzle), RULES)
        for band in ("easy", "medium", "target"):
            self.assertEqual(lights_quality_rejection(report, band), "PRESS_COUNT_TOO_LOW")

    def test_metric_helpers_are_well_defined(self):
        puzzle = _puzzle("target")
        self.assertTrue(0 <= greedy_reduction_count(puzzle) <= 20)
        self.assertTrue(0 <= chase_press_count(puzzle) <= 15)
        self.assertEqual(board_symmetries(puzzle), [])

    def test_unknown_band_raises(self):
        with self.assertRaises(ValueError):
            lights_difficulty_preset("no-such-band")


class LightsSolverPerformanceTests(unittest.TestCase):
    def test_solver_is_fast_enough_to_screen_batches(self):
        puzzle = _puzzle("target")
        start = time.perf_counter()
        for _ in range(200):
            SOLVER.analyze(puzzle)
        per_call_us = (time.perf_counter() - start) / 200 * 1e6
        self.assertLess(per_call_us, 5000, f"GF(2) analysis is {per_call_us:.0f}us per call")


class LightsSolutionValidationTests(unittest.TestCase):
    def test_tampered_solutions_are_reported(self):
        puzzle = _puzzle("medium")
        solution = SOLVER.solve(puzzle)
        wrong_key = Solution(
            solution.schema_version, solution.solver_id, solution.solver_version, solution.optimality,
            solution.actions, solution.initial_state_hash, solution.final_state_hash, solution.cost,
            solution.expanded_nodes, "unique:deadbeef",
        )
        self.assertIn("answer equivalence key mismatch", validate_lights_solution(puzzle, wrong_key, RULES))
        short = Solution(
            solution.schema_version, solution.solver_id, solution.solver_version, solution.optimality,
            solution.actions[:-1], solution.initial_state_hash, solution.final_state_hash,
            len(solution.actions) - 1, solution.expanded_nodes, solution.answer_equivalence_key,
        )
        self.assertTrue(validate_lights_solution(puzzle, short, RULES))

    def test_ambiguous_shape_is_rejected_by_the_solver(self):
        puzzle = LightsPuzzleSpec("1.0.0", "lights", "lights-gen-1", 4, 4, tuple([0] * 16), LIGHTS_RULESET)
        with self.assertRaises(LightsSolveRejected) as caught:
            SOLVER.solve(puzzle)
        self.assertEqual(caught.exception.code, "MULTIPLE_PRESS_SETS")


if __name__ == "__main__":
    unittest.main()
