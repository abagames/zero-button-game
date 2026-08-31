import hashlib
import unittest

from zero_button_game.core import StableRng, derive_seed
from zero_button_game.models import TimelineSpec
from zero_button_game.pipes import PipeRules, PipeSolver, generate_pipes, trace_pipes
from zero_button_game.pipes_presentation import pipe_plan
from zero_button_game.pipes_render import PipeRenderer, PipeSceneBuilder, alternate_pipe_scene
from zero_button_game.registry import get_plugin


class PipeRenderContractTests(unittest.TestCase):
    def test_rotation_neutrality_mapping_and_flow_contract(self):
        rules = PipeRules()
        puzzle = generate_pipes(StableRng(derive_seed(2, "pipes", 0, "generation")), 3, 3)
        solution = PipeSolver(rules).solve(puzzle)
        units = sum(abs(action.params["quarter_turns"][0]) for action in solution.actions)
        timeline = TimelineSpec(solve_duration=max(2.0, units * 2 / 20))
        plan = pipe_plan(puzzle, solution, rules, timeline)
        trace = trace_pipes(puzzle, plan.logical_steps, rules)
        scene = PipeSceneBuilder().build(puzzle, plan, trace)
        renderer = PipeRenderer()
        pre_frame = plan.timeline["appearance"] + plan.timeline["thinking"] // 2
        self.assertIsNone(renderer.semantic_snapshot(scene, pre_frame)["current_piece"])
        self.assertEqual(
            hashlib.sha256(renderer.render_frame(scene, pre_frame)).digest(),
            hashlib.sha256(renderer.render_frame(alternate_pipe_scene(scene), pre_frame)).digest(),
        )
        self.assertFalse(renderer.semantic_snapshot(scene, plan.timeline["solve_end"] - 1)["flow_reached"])
        end = renderer.semantic_snapshot(scene, plan.timeline["result_end"] - 1)
        self.assertTrue(end["flow_goal_reached"])
        self.assertEqual(tuple(end["flow_reached"]), scene.flow_order)
        self.assertLess(len(end["flow_reached"]), puzzle.width * puzzle.height)
        passed, failed = get_plugin("pipes").render_contract_checks(scene, renderer)
        self.assertEqual(failed, [])
        for check in ("rotation_action_rendering", "source_goal_connection", "goal_action_minimality", "normalized_solution_unique", "emitted_signature_canonical", "flow_after_connection", "flow_goal_reached", "flow_state_immutable"):
            self.assertIn(check, passed)


if __name__ == "__main__":
    unittest.main()
