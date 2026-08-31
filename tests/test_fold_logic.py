import json
import unittest

from zero_button_game.core import StableRng, derive_seed, sha256_value
from zero_button_game.fold import (
    BLANK, COLOURED, FOLD_RULESET, MIN_FOLD_COUNT, NO_PAPER, FoldPuzzleSpec, FoldRules, FoldSolveRejected,
    FoldSolver, FoldState, action_fold, actions_for_folds, axis_chains, axis_folds, canonical_folds,
    chain_coordinate_map, class_signature, draw_candidate, enumerate_fold_classes, fold_difficulty_preset,
    fold_difficulty_report, fold_quality_rejection, fold_result_extent, fold_state, generate_fold,
    initial_fold_state, legal_folds, make_action, replay_fold, signature_hash, solution_folds, split_folds,
    validate_fold_solution,
)
from zero_button_game.models import Action, Solution


GOLDENS = {
    (11, 0, "easy"): {
        "problem": "sha256:311a844993790ee1a33b47be4fec2dc8aec28ac0af68b7ab608e4cfb5bb35781",
        "solution": "sha256:da4e187f297941968319d155f07961c36eda94c78dfcd8c38c0799a7f94e04f9",
        "folds": [(0, 3, 1), (1, 1, -1)], "target": [0, 1, 3, 6], "colours": 15,
    },
    (2026, 1, "medium"): {
        "problem": "sha256:a8ff276d68c2fa854e433854320cba0ec40f4d93959775504aeb51ffd7d50bb0",
        "solution": "sha256:cd32bd4c445cf57fe115d7164c8ff59ecdc84da503f43185d0ae3dab91b13cd6",
        "folds": [(0, 1, -1), (0, 2, -1), (1, 3, -1)], "target": [2, 3, 6, 6], "colours": 12,
    },
    (777, 0, "target"): {
        "problem": "sha256:501b862eaf4bb54be6415ccf9ea593242bfd90d6f390f6cc7dd4554c700d3adf",
        "solution": "sha256:16e11839e8888f1013fc9550201c66cad8c5dbe443ddc8e15dc1b7369cb49f63",
        "folds": [(0, 2, -1), (1, 1, -1), (1, 2, -1), (1, 4, -1)], "target": [2, 4, 6, 6], "colours": 8,
    },
}


def strip_sheet(values, width=4):
    """A one-row sheet whose cells are given explicitly (2 = coloured)."""
    return FoldState("fold", 0, (tuple(values),), (0, 0, width, 1))


def brute_force_classes(puzzle):
    """Independent oracle: raw sequence DFS over the *layer* simulation.

    This deliberately shares nothing with the solver's bitmask/chain-map path -
    it folds real layer stacks with ``fold_state`` and asks ``coverage_at``. Every
    intermediate extent must contain the target because folds only ever shrink
    the sheet, which is what keeps the search finite.
    """
    rules = FoldRules()
    tx0, ty0, tx1, ty1 = puzzle.target
    found = set()

    def contains(extent):
        x0, y0, x1, y1 = extent
        return x0 <= tx0 and tx1 <= x1 and y0 <= ty0 and ty1 <= y1

    def walk(state, folds):
        if tuple(state.extent) == tuple(puzzle.target):
            if folds and rules.is_goal(puzzle, state):
                vertical, horizontal = split_folds(folds)
                found.add((vertical, horizontal))
            return
        for fold in legal_folds(state.extent):
            following = fold_state(state, *fold)
            if contains(following.extent):
                walk(following, folds + [fold])

    walk(rules.initial_state(puzzle), [])
    return found


class FoldGeometryTests(unittest.TestCase):
    def test_fold_result_extent_keeps_the_larger_side(self):
        self.assertEqual(fold_result_extent(0, 6, 3, -1), (3, 6))
        self.assertEqual(fold_result_extent(0, 6, 3, 1), (0, 3))
        self.assertEqual(fold_result_extent(0, 6, 2, -1), (2, 6))
        self.assertEqual(fold_result_extent(0, 6, 4, 1), (0, 4))

    def test_fold_result_extent_rejects_the_larger_moving_side(self):
        with self.assertRaises(ValueError):
            fold_result_extent(0, 6, 2, 1)
        with self.assertRaises(ValueError):
            fold_result_extent(0, 6, 4, -1)

    def test_fold_result_extent_rejects_creases_outside_and_bad_directions(self):
        for line in (0, 6, 7, -1):
            with self.assertRaises(ValueError):
                fold_result_extent(0, 6, line, -1)
        with self.assertRaises(ValueError):
            fold_result_extent(0, 6, 3, 0)

    def test_six_by_six_sheet_has_twelve_legal_folds(self):
        self.assertEqual(len(axis_folds(0, 6)), 6)
        self.assertEqual(len(legal_folds((0, 0, 6, 6))), 12)

    def test_every_fold_strictly_shrinks_the_sheet(self):
        state = initial_fold_state(FoldPuzzleSpec("1.0.0", "fold", "g", 6, 6, (1,) * 36, (0, 0, 3, 3)))
        for fold in legal_folds(state.extent):
            following = fold_state(state, *fold)
            before = (state.extent[2] - state.extent[0]) * (state.extent[3] - state.extent[1])
            after = (following.extent[2] - following.extent[0]) * (following.extent[3] - following.extent[1])
            self.assertLess(after, before)

    def test_axis_chains_terminate_and_agree_with_the_coordinate_map(self):
        chains = axis_chains(0, 6)
        self.assertEqual(chains[0], ((), (0, 6)))
        self.assertTrue(all(len(chain) <= 5 for chain, _ in chains))
        for chain, extent in chains:
            mapping, derived = chain_coordinate_map(0, 6, chain)
            self.assertEqual(derived, extent)
            self.assertTrue(all(extent[0] <= value < extent[1] for value in mapping.values()))


class FoldLayerOrderTests(unittest.TestCase):
    """The stack order is invisible in the outline, so it is asserted directly."""

    def test_single_fold_puts_the_moving_half_on_top(self):
        state = strip_sheet((COLOURED, BLANK, BLANK, BLANK))
        folded = fold_state(state, 0, 2, -1)
        self.assertEqual(folded.extent, (2, 0, 4, 1))
        # bottom layer is the stationary half, top layer the reflected flap
        self.assertEqual(folded.layers, ((BLANK, BLANK), (BLANK, COLOURED)))

    def test_second_fold_reverses_the_moving_stack(self):
        state = strip_sheet((COLOURED, BLANK, COLOURED, BLANK))
        once = fold_state(state, 0, 2, -1)
        self.assertEqual(once.layers, ((COLOURED, BLANK), (BLANK, COLOURED)))
        twice = fold_state(once, 0, 3, -1)
        # stationary halves keep their order, the two flap halves arrive reversed;
        # dropping the reversal would give ((1,), (2,), (2,), (1,)).
        self.assertEqual(twice.layers, ((BLANK,), (COLOURED,), (BLANK,), (COLOURED,)))
        self.assertEqual(twice.extent, (3, 0, 4, 1))

    def test_layer_depth_counts_only_layers_carrying_paper(self):
        state = strip_sheet((COLOURED, BLANK, BLANK, BLANK))
        folded = fold_state(state, 0, 2, -1)
        self.assertEqual(folded.depth_at((2, 0)), 2)
        self.assertEqual(folded.max_depth(), 2)
        self.assertTrue(folded.covered_at((3, 0)))
        self.assertFalse(folded.covered_at((2, 0)))

    def test_folding_the_high_side_mirrors_the_other_way(self):
        state = strip_sheet((BLANK, BLANK, BLANK, COLOURED))
        folded = fold_state(state, 0, 2, 1)
        self.assertEqual(folded.extent, (0, 0, 2, 1))
        self.assertEqual(folded.layers, ((BLANK, BLANK), (COLOURED, BLANK)))

    def test_empty_halves_never_become_layers(self):
        state = FoldState("fold", 0, ((BLANK, BLANK), (NO_PAPER, COLOURED)), (0, 0, 2, 1))
        folded = fold_state(state, 0, 1, -1)
        # the second layer has no paper on the moving side, so it contributes
        # only one new layer rather than an all-empty one
        self.assertEqual(folded.layers, ((BLANK,), (COLOURED,), (BLANK,)))

    def test_axis_one_folds_reverse_the_stack_the_same_way(self):
        state = FoldState("fold", 0, ((COLOURED, BLANK, COLOURED, BLANK),), (0, 0, 1, 4))
        folded = fold_state(state, 1, 2, -1)
        self.assertEqual(folded.extent, (0, 2, 1, 4))
        self.assertEqual(folded.layers, ((COLOURED, BLANK), (BLANK, COLOURED)))


class FoldRulesTests(unittest.TestCase):
    def setUp(self):
        self.rules = FoldRules()
        self.puzzle = generate_fold(StableRng(derive_seed(11, "fold", 0, "generation")), fold_difficulty_preset("easy"))

    def test_structure_validation_accepts_a_generated_puzzle(self):
        self.assertEqual(FOLD_RULESET, "fold-to-target-exact-v1")
        self.assertEqual(self.puzzle.ruleset, FOLD_RULESET)
        self.assertEqual(self.rules.validate_structure(self.puzzle), [])

    def test_structure_validation_reports_every_defect(self):
        base = self.puzzle
        cases = {
            "wrong puzzle type": base.__class__(base.schema_version, "maze", base.generator_version, 6, 6, base.filled, base.target),
            "unsupported ruleset": base.__class__(base.schema_version, "fold", base.generator_version, 6, 6, base.filled, base.target, "nope"),
            "paper shape is not the 6x6 sheet": base.__class__(base.schema_version, "fold", base.generator_version, 5, 6, base.filled, base.target),
            "target rectangle leaves the sheet": base.__class__(base.schema_version, "fold", base.generator_version, 6, 6, base.filled, (0, 0, 7, 3)),
            "target rectangle is the whole unfolded sheet": base.__class__(base.schema_version, "fold", base.generator_version, 6, 6, base.filled, (0, 0, 6, 6)),
            "target rectangle is thinner than the readable minimum": base.__class__(base.schema_version, "fold", base.generator_version, 6, 6, base.filled, (0, 0, 1, 3)),
            "coloured cell count differs from the target cell count": base.__class__(base.schema_version, "fold", base.generator_version, 6, 6, (1,) + (0,) * 35, (0, 0, 3, 3)),
        }
        for message, puzzle in cases.items():
            with self.subTest(message=message):
                self.assertIn(message, self.rules.validate_structure(puzzle))

    def test_legal_actions_match_legal_folds(self):
        state = self.rules.initial_state(self.puzzle)
        actions = self.rules.legal_actions(self.puzzle, state)
        self.assertEqual(len(actions), 12)
        self.assertEqual(tuple(action_fold(action) for action in actions), legal_folds(state.extent))
        self.assertTrue(all(action.kind == "fold_along" for action in actions))

    def test_apply_rejects_a_foreign_action_kind(self):
        state = self.rules.initial_state(self.puzzle)
        action = make_action(state, 0, 3, -1)
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, state, Action(1, "toggle_cell", action.actor_id, action.params, action.precondition))

    def test_apply_rejects_a_stale_state_hash(self):
        state = self.rules.initial_state(self.puzzle)
        action = make_action(state, 0, 3, -1)
        moved = fold_state(state, 0, 3, -1)
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, moved, action)

    def test_apply_rejects_a_crease_outside_the_current_extent(self):
        state = self.rules.initial_state(self.puzzle)
        folded = fold_state(state, 0, 3, -1)
        action = make_action(folded, 0, 1, -1)
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, folded, action)

    def test_apply_rejects_a_mismatched_actor_id(self):
        state = self.rules.initial_state(self.puzzle)
        action = make_action(state, 0, 3, -1)
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, state, Action(1, "fold_along", "crease-0-4", action.params, action.precondition))

    def test_apply_rejects_malformed_parameters(self):
        state = self.rules.initial_state(self.puzzle)
        action = make_action(state, 0, 3, -1)
        for params in ({"axis": [0], "line": [3]}, {"axis": [0, 1], "line": [3], "dir": [-1]}, {"axis": 0, "line": [3], "dir": [-1]}):
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    self.rules.apply(self.puzzle, state, Action(1, "fold_along", "crease-0-3", params, action.precondition))

    def test_apply_rejects_an_oversized_moving_side(self):
        state = self.rules.initial_state(self.puzzle)
        action = make_action(state, 0, 2, 1)
        with self.assertRaises(ValueError):
            self.rules.apply(self.puzzle, state, action)


class FoldSolverTests(unittest.TestCase):
    def setUp(self):
        self.rules = FoldRules()
        self.solver = FoldSolver(self.rules)

    def test_golden_three_seeds(self):
        for (seed, index, band), expected in GOLDENS.items():
            with self.subTest(seed=seed, band=band):
                puzzle = generate_fold(StableRng(derive_seed(seed, "fold", index, "generation")), fold_difficulty_preset(band))
                solution = self.solver.solve(puzzle)
                self.assertEqual(sha256_value(puzzle.to_dict()), expected["problem"])
                self.assertEqual(sha256_value(solution.to_dict()), expected["solution"])
                self.assertEqual(list(solution_folds(solution)), [tuple(item) for item in expected["folds"]])
                self.assertEqual(list(puzzle.target), expected["target"])
                # The colour count is the target's area, every time: that is
                # what "no overlap" buys the reader of the still frame.
                self.assertEqual(sum(puzzle.filled), expected["colours"])
                target = expected["target"]
                self.assertEqual(
                    expected["colours"], (target[2] - target[0]) * (target[3] - target[1])
                )

    def test_solver_rejects_a_target_no_class_reaches(self):
        puzzle = FoldPuzzleSpec("1.0.0", "fold", "g", 6, 6, tuple([1] * 12 + [0] * 24), (0, 0, 3, 3))
        analysis = enumerate_fold_classes(puzzle)
        if analysis["status"] == "unsolvable":
            with self.assertRaises(FoldSolveRejected) as caught:
                self.solver.solve(puzzle)
            self.assertEqual(caught.exception.code, "NO_FOLD_SEQUENCE")
        else:  # pragma: no cover - guards the fixture, not the code
            self.skipTest("fixture is solvable")

    def test_solver_rejects_an_ambiguous_target(self):
        # Nine colours in the top-left 3x3 block reach the 3x3 target exactly
        # once by more than one fold class.
        filled = [0] * 36
        for y in range(3):
            for x in range(3):
                filled[y * 6 + x] = 1
        puzzle = FoldPuzzleSpec("1.0.0", "fold", "g", 6, 6, tuple(filled), (0, 0, 3, 3))
        self.assertEqual(enumerate_fold_classes(puzzle)["status"], "ambiguous")
        with self.assertRaises(FoldSolveRejected) as caught:
            self.solver.solve(puzzle)
        self.assertEqual(caught.exception.code, "MULTIPLE_FOLD_SEQUENCES")

    def test_a_fully_coloured_sheet_is_unsolvable_because_colour_would_stack(self):
        # The old rule accepted this happily: 36 colours certainly cover a 3x3
        # target. Under the exactly-once rule 27 of them land on top of others.
        puzzle = FoldPuzzleSpec("1.0.0", "fold", "g", 6, 6, (1,) * 36, (0, 0, 3, 3))
        self.assertIn(
            "coloured cell count differs from the target cell count",
            self.rules.validate_structure(puzzle),
        )
        self.assertEqual(enumerate_fold_classes(puzzle)["status"], "unsolvable")
        with self.assertRaises(FoldSolveRejected) as caught:
            self.solver.solve(puzzle)
        self.assertEqual(caught.exception.code, "NO_FOLD_SEQUENCE")

    def test_goal_rejects_a_sheet_whose_colours_overlap(self):
        """A folded sheet that covers every target cell but stacks two colours.

        The top two rows are coloured solid, so folding down to the 3x2 target
        lands the outline exactly on it and colours every cell inside it -
        twice over. That was a goal under the old union rule; here it is a
        rejection, and the puzzle is unsolvable outright.
        """
        filled = [0] * 36
        for y in range(2):
            for x in range(6):
                filled[y * 6 + x] = 1
        puzzle = FoldPuzzleSpec("1.0.0", "fold", "g", 6, 6, tuple(filled), (0, 0, 3, 2))
        state = self.rules.initial_state(puzzle)
        folded = state
        for axis, line, direction in ((0, 3, 1), (1, 3, 1), (1, 2, 1)):
            folded = fold_state(folded, axis, line, direction)
        self.assertEqual(folded.extent, (0, 0, 3, 2))
        self.assertTrue(all(folded.covered_at((x, y)) for y in range(2) for x in range(3)))
        self.assertTrue(all(folded.coverage_at((x, y)) == 2 for y in range(2) for x in range(3)))
        self.assertFalse(self.rules.is_goal(puzzle, folded))
        self.assertEqual(enumerate_fold_classes(puzzle)["status"], "unsolvable")

    def test_canonical_order_groups_the_axes(self):
        puzzle = generate_fold(StableRng(derive_seed(2026, "fold", 1, "generation")), fold_difficulty_preset("medium"))
        folds = solution_folds(self.solver.solve(puzzle))
        axes = [axis for axis, _, _ in folds]
        self.assertEqual(axes, sorted(axes))

    def test_folds_on_different_axes_commute(self):
        puzzle = generate_fold(StableRng(derive_seed(11, "fold", 0, "generation")), fold_difficulty_preset("easy"))
        state = self.rules.initial_state(puzzle)
        one = fold_state(fold_state(state, 0, 3, -1), 1, 3, -1)
        two = fold_state(fold_state(state, 1, 3, -1), 0, 3, -1)
        self.assertEqual(one.extent, two.extent)
        x0, y0, x1, y1 = one.extent
        cells = [(x, y) for y in range(y0, y1) for x in range(x0, x1)]
        self.assertEqual([one.covered_at(cell) for cell in cells], [two.covered_at(cell) for cell in cells])
        self.assertEqual([one.depth_at(cell) for cell in cells], [two.depth_at(cell) for cell in cells])

    def test_signature_is_order_insensitive_within_a_class(self):
        folds = ((0, 3, -1), (1, 4, 1), (0, 5, 1))
        shuffled = ((0, 3, -1), (0, 5, 1), (1, 4, 1))
        self.assertEqual(signature_hash(folds), signature_hash(shuffled))
        self.assertEqual(class_signature(folds), [[0, 3, -1], [0, 5, 1], [1, 4, 1]])
        self.assertEqual(canonical_folds(*split_folds(folds)), shuffled)

    def test_hundred_seed_uniqueness_against_an_independent_oracle(self):
        rules = self.rules
        checked = 0
        for index in range(100):
            preset = fold_difficulty_preset(("easy", "medium", "target")[index % 3])
            try:
                puzzle = draw_candidate(StableRng(derive_seed(4242, "fold", index, "property")), preset)
            except ValueError:
                continue
            if rules.validate_structure(puzzle):
                continue
            analysis = enumerate_fold_classes(puzzle)
            oracle = brute_force_classes(puzzle)
            self.assertEqual(set(analysis["_classes"]), oracle, f"index {index}")
            # ``draw_candidate`` is exact by construction but not unique by
            # construction: under the exactly-once rule a colouring is often
            # reachable by a second fold class, and the solver is what rejects
            # it. Only the unique ones can be solved.
            if analysis["status"] != "unique":
                self.assertEqual(analysis["status"], "ambiguous", f"index {index}")
                continue
            solution = FoldSolver(rules).solve(puzzle)
            self.assertEqual(validate_fold_solution(puzzle, solution, rules), [])
            trace = replay_fold(puzzle, solution.actions, rules)
            self.assertTrue(rules.is_goal(puzzle, trace.final))
            self.assertEqual(
                sum(puzzle.filled),
                (puzzle.target[2] - puzzle.target[0]) * (puzzle.target[3] - puzzle.target[1]),
            )
            checked += 1
        self.assertGreaterEqual(checked, 25)


class FoldSerialisationTests(unittest.TestCase):
    def setUp(self):
        self.rules = FoldRules()
        self.puzzle = generate_fold(StableRng(derive_seed(11, "fold", 0, "generation")), fold_difficulty_preset("easy"))
        self.solution = FoldSolver(self.rules).solve(self.puzzle)

    def test_problem_json_round_trip(self):
        value = json.loads(json.dumps(self.puzzle.to_dict()))
        self.assertEqual(FoldPuzzleSpec.from_dict(value), self.puzzle)

    def test_solution_json_round_trip(self):
        value = json.loads(json.dumps(self.solution.to_dict()))
        self.assertEqual(Solution.from_dict(value), self.solution)

    def test_state_dict_is_json_safe_and_order_sensitive(self):
        state = self.rules.initial_state(self.puzzle)
        folded = fold_state(state, 0, 3, -1)
        value = json.loads(json.dumps(folded.to_dict()))
        self.assertEqual(value["extent"], list(folded.extent))
        self.assertEqual(value["layers"], [list(layer) for layer in folded.layers])
        reversed_stack = FoldState("fold", folded.step, tuple(reversed(folded.layers)), folded.extent)
        self.assertNotEqual(sha256_value(folded.to_dict()), sha256_value(reversed_stack.to_dict()))

    def test_every_action_carries_a_state_hash_precondition(self):
        state = self.rules.initial_state(self.puzzle)
        for action in self.solution.actions:
            self.assertEqual(action.precondition["state_hash"], sha256_value(state.to_dict()))
            state = self.rules.apply(self.puzzle, state, action)


class FoldValidationTests(unittest.TestCase):
    def setUp(self):
        self.rules = FoldRules()
        self.puzzle = generate_fold(StableRng(derive_seed(11, "fold", 0, "generation")), fold_difficulty_preset("easy"))
        self.solution = FoldSolver(self.rules).solve(self.puzzle)

    def test_valid_solution_has_no_failures(self):
        self.assertEqual(validate_fold_solution(self.puzzle, self.solution, self.rules), [])

    def test_tampered_equivalence_key_is_caught(self):
        broken = Solution(
            self.solution.schema_version, self.solution.solver_id, self.solution.solver_version,
            self.solution.optimality, self.solution.actions, self.solution.initial_state_hash,
            self.solution.final_state_hash, self.solution.cost, self.solution.expanded_nodes, "unique:deadbeef",
        )
        self.assertIn("answer equivalence key mismatch", validate_fold_solution(self.puzzle, broken, self.rules))

    def test_truncated_solution_does_not_reach_the_target(self):
        broken = Solution(
            self.solution.schema_version, self.solution.solver_id, self.solution.solver_version,
            self.solution.optimality, self.solution.actions[:1], self.solution.initial_state_hash,
            self.solution.final_state_hash, 1, self.solution.expanded_nodes, self.solution.answer_equivalence_key,
        )
        failures = validate_fold_solution(self.puzzle, broken, self.rules)
        self.assertIn("folded sheet does not fill the target rectangle", failures)


class FoldDifficultyTests(unittest.TestCase):
    def setUp(self):
        self.rules = FoldRules()
        self.solver = FoldSolver(self.rules)

    def test_unknown_band_raises(self):
        with self.assertRaises(ValueError):
            fold_difficulty_preset("nope")

    def test_bands_are_generated_and_accepted(self):
        seen = {}
        for band, seed in (("easy", 11), ("medium", 2026), ("target", 777)):
            puzzle = generate_fold(StableRng(derive_seed(seed, "fold", 0 if band != "medium" else 1, "generation")), fold_difficulty_preset(band))
            solution = self.solver.solve(puzzle)
            report = fold_difficulty_report(puzzle, solution, self.rules)
            self.assertIsNone(fold_quality_rejection(report, band))
            self.assertEqual(report["accepted_band"], band)
            seen[band] = report["mechanical"]["difficulty_score"]
        self.assertLess(seen["easy"], seen["medium"])
        self.assertLess(seen["medium"], seen["target"])

    def test_band_score_windows_do_not_overlap(self):
        windows = [
            (fold_difficulty_preset(band)["min_difficulty_score"], fold_difficulty_preset(band)["max_difficulty_score"])
            for band in ("easy", "medium", "target")
        ]
        for earlier, later in zip(windows, windows[1:]):
            self.assertLess(earlier[1], later[0])

    def test_quality_filter_rejects_a_single_fold(self):
        report = {
            "mechanical": {"fold_count": 1, "vertical_folds": 1, "horizontal_folds": 0, "decoy_crease_count": 0, "difficulty_score": 300},
            "human": {"status": "x"}, "solution_uniqueness": {"status": "unique"},
        }
        self.assertEqual(fold_quality_rejection(report, "medium"), "FOLD_COUNT_TOO_LOW")
        self.assertGreaterEqual(MIN_FOLD_COUNT, 2)

    def test_quality_filter_rejects_a_single_axis_solution(self):
        report = {
            "mechanical": {"fold_count": 3, "vertical_folds": 3, "horizontal_folds": 0, "decoy_crease_count": 0, "difficulty_score": 320},
            "human": {"status": "x"}, "solution_uniqueness": {"status": "unique"},
        }
        self.assertEqual(fold_quality_rejection(report, "medium"), "SINGLE_AXIS_SOLUTION")

    def test_quality_filter_reports_ambiguity(self):
        report = {
            "mechanical": {"fold_count": 3, "vertical_folds": 2, "horizontal_folds": 1, "decoy_crease_count": 0, "difficulty_score": 320},
            "human": {"status": "x"}, "solution_uniqueness": {"status": "ambiguous"},
        }
        self.assertEqual(fold_quality_rejection(report, "medium"), "MULTIPLE_FOLD_SEQUENCES")

    def test_uniqueness_block_records_the_equivalence_policy(self):
        puzzle = generate_fold(StableRng(derive_seed(11, "fold", 0, "generation")), fold_difficulty_preset("easy"))
        solution = self.solver.solve(puzzle)
        block = fold_difficulty_report(puzzle, solution, self.rules)["solution_uniqueness"]
        self.assertEqual(block["equivalence_policy_version"], "fold-crease-class-v1")
        self.assertEqual(block["fold_class_count"], 1)
        self.assertEqual(block["proof"], "complete-fold-class-enumeration")
        self.assertEqual(solution.answer_equivalence_key, "unique:" + block["normalized_signature_hash"])


class FoldGenerationTests(unittest.TestCase):
    def test_generation_is_deterministic(self):
        first = generate_fold(StableRng(derive_seed(5, "fold", 3, "generation")), fold_difficulty_preset("medium"))
        second = generate_fold(StableRng(derive_seed(5, "fold", 3, "generation")), fold_difficulty_preset("medium"))
        self.assertEqual(first, second)

    def test_draw_candidate_reports_when_no_fold_class_fits_the_band(self):
        preset = dict(fold_difficulty_preset("easy"))
        preset["min_target_side"] = 6  # only the unfolded sheet is that wide
        with self.assertRaises(ValueError) as caught:
            draw_candidate(StableRng(1), preset)
        self.assertTrue(str(caught.exception).startswith("FOLD_NO_FOLD_PLAN"))

    def test_every_drawn_candidate_covers_its_target_exactly_once(self):
        rules = FoldRules()
        for band in ("easy", "medium", "target"):
            preset = fold_difficulty_preset(band)
            for index in range(40):
                with self.subTest(band=band, index=index):
                    puzzle = draw_candidate(StableRng(derive_seed(99, "fold", index, band)), preset)
                    target_cells = (puzzle.target[2] - puzzle.target[0]) * (puzzle.target[3] - puzzle.target[1])
                    self.assertEqual(sum(puzzle.filled), target_cells)
                    self.assertEqual(rules.validate_structure(puzzle), [])

    def test_generated_puzzles_carry_exactly_as_much_colour_as_the_target_holds(self):
        rules = FoldRules()
        for band in ("easy", "medium", "target"):
            with self.subTest(band=band):
                puzzle = generate_fold(StableRng(derive_seed(7, "fold", 0, band)), fold_difficulty_preset(band))
                target_cells = (puzzle.target[2] - puzzle.target[0]) * (puzzle.target[3] - puzzle.target[1])
                self.assertEqual(sum(puzzle.filled), target_cells)
                trace = replay_fold(puzzle, FoldSolver(rules).solve(puzzle).actions, rules)
                final = trace.final
                x0, y0, x1, y1 = final.extent
                self.assertTrue(all(
                    final.coverage_at((x, y)) == 1
                    for y in range(y0, y1) for x in range(x0, x1)
                ))

    def test_actions_for_folds_rebuilds_a_replayable_chain(self):
        rules = FoldRules()
        puzzle = generate_fold(StableRng(derive_seed(11, "fold", 0, "generation")), fold_difficulty_preset("easy"))
        folds = solution_folds(FoldSolver(rules).solve(puzzle))
        vertical, horizontal = split_folds(folds)
        swapped = tuple((1, line, direction) for line, direction in horizontal) + tuple(
            (0, line, direction) for line, direction in vertical
        )
        trace = replay_fold(puzzle, actions_for_folds(puzzle, swapped, rules), rules)
        self.assertTrue(rules.is_goal(puzzle, trace.final))


if __name__ == "__main__":
    unittest.main()
