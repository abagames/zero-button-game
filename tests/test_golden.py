import hashlib
import unittest

from zero_button_game.core import StableRng, derive_seed, sha256_value
from zero_button_game.maze import MazeRules, MazeSolver, generate_maze, replay
from zero_button_game.presentation import direct_plan
from zero_button_game.pipes import PipeRules, PipeSolver, generate_pipes, trace_pipes
from zero_button_game.pipes_presentation import pipe_plan
from zero_button_game.pipes_render import PipeRenderer, PipeSceneBuilder
from zero_button_game.render import MazeSceneBuilder, RasterRenderer


GOLDENS = {
    (42, 2): {
        "problem": "sha256:1e40aa80138003d8547e410718979d2e419a49c1b6e3973f392b63142f1df18c",
        "solution": "sha256:feb27a263d802c21fdcec797636f09152ed2ca1e0e8f87571fd4e10872434c72",
        "frames": ["deb3719caecc89fed4bb15eadd838608016d5c2aae947631b4ad3e30f5b5cd4e", "fcf80802be9c0843fbd6bf42e1aab7f91b8470867522a6bfe3c027436d293544", "ad04a664b623be32fee57b69f81384c243b2cc4f1cb615b5db858dab06f2ecd5", "176dbfed075dd373adce326818407b8ec3ff791a22c02624fd2f33baab17113c", "38f9beef26e3a52e3184a5ace6cc2e0939729fd63fc8e786678ca2b50f2b1ca6"],
    },
    (314159, 0): {
        "problem": "sha256:f14e19d965a06c362cd6a65149c431acb4398e12e1b76cf5d56a2cbcfcac9cbc",
        "solution": "sha256:587bb07a0145f4891f41e5e39434763b492600ea0baf59ae83b9640019f26ef4",
        "frames": ["78cc6a37e08270ec1686d14f9c50e371c3ccccc8af94e8a5c92edab94c2dce40", "f9eba8a531b1286c2b444508839280ffc01dd95e5a67a84f7c79db011afcf23d", "38fd2178fab184806862764c9dbdc70630aeaa150e952605bc92f46be7996da9", "3d51994d960f4fdec44fcbbba11108b7aa2ee747d4b2df3ca4422377860c0a0a", "e338886dbae85ef30b66bd5ed28e263dd4b5fd7e525455950bac8af7dd47e62c"],
    },
    (314159, 1): {
        "problem": "sha256:2f0b6142ce8078d45ecfabdc40093872564761ef72e479445f30111eb49e26ed",
        "solution": "sha256:20a7829ec2a546e5da66efc434447f74dbb6b130ab22ad918c7d4ef353200c8f",
        "frames": ["351097f9864fd62d0914a19b42559491e69448820c721d03d96566d6faef817e", "7b7352aa9a514d73ec799ecbc8694ca4d89782cad6fed9fba6d43fdd0347f9ca", "8993b6966c606a889ab6e68a30380c10a68c7b4d5d7bbf0626a1cffe17946858", "141bb7c5de6a0bdac723636a700bced2aa57a2e6b4f5f5278c01410e4e745cd2", "05a7f29b0e19c5cae12891e41ee2039e36391f764e18e8cfe4207fec60b8b1b5"],
    },
}


class GoldenTests(unittest.TestCase):
    def test_three_seed_logical_and_key_frames(self):
        rules = MazeRules()
        renderer = RasterRenderer()
        for (master_seed, candidate), expected in GOLDENS.items():
            with self.subTest(master_seed=master_seed, candidate=candidate):
                puzzle = generate_maze(StableRng(derive_seed(master_seed, "maze", candidate, "generation")))
                solution = MazeSolver(rules).solve(puzzle)
                plan = direct_plan(puzzle, solution, rules)
                scene = MazeSceneBuilder().build(puzzle, plan, replay(puzzle, solution.actions, rules))
                timeline = plan.timeline
                indices = [timeline["appearance"], timeline["appearance"] + timeline["thinking"] // 2, timeline["reveal_start"], timeline["reveal_start"] + timeline["solve"] * 3 // 4, timeline["solve_end"] + timeline["result"] // 2]
                self.assertEqual(sha256_value(puzzle.to_dict()), expected["problem"])
                self.assertEqual(sha256_value(solution.to_dict()), expected["solution"])
                actual_frames = [hashlib.sha256(renderer.render_frame(scene, index)).hexdigest() for index in indices]
                self.assertEqual(actual_frames, expected["frames"])

    def test_pipes_seed_two_logical_and_key_frames(self):
        rules = PipeRules()
        puzzle = generate_pipes(StableRng(derive_seed(2, "pipes", 0, "generation")), 3, 3)
        solution = PipeSolver(rules).solve(puzzle)
        plan = pipe_plan(puzzle, solution, rules)
        scene = PipeSceneBuilder().build(puzzle, plan, trace_pipes(puzzle, solution.actions, rules))
        timeline = plan.timeline
        indices = [
            timeline["appearance"], timeline["appearance"] + timeline["thinking"] // 2,
            timeline["reveal_start"], timeline["reveal_start"] + timeline["solve"] * 3 // 4,
            timeline["goal_keyframe"],
        ]
        self.assertEqual(sha256_value(puzzle.to_dict()), "sha256:27f51710be251a62b4898234f63dd1090bbb8e134d3c48abbd7dbf94ee48a33d")
        self.assertEqual(sha256_value(solution.to_dict()), "sha256:726752b0949abc0df6ad8584720f596f5f7c21d389ea6cde768815342953d543")
        expected = [
            "845a27c8112d320f183fe542efe7a1de834c7df1d551d71820d5677d7bd6b13b",
            "be32f2d4a68e4b8a82ce01d9f3e1b7290d0b85471a7ec4b75df7b0ebe3b90114",
            "42b274eb6e42111a2693d42675a1ad9a6d9c94d79b8b83267093ad23427b8595",
            "2538d72c3848d41879374b994c4d389114f0aa00c3169f79484de83fdfe817f8",
            "aea73bf791ab0ecdba032f48b94fcc208574182b63c6a3ba717a04e721d84a6c",
        ]
        actual = [hashlib.sha256(PipeRenderer().render_frame(scene, index)).hexdigest() for index in indices]
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
