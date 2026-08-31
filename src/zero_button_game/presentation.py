from __future__ import annotations

from .core import sha256_value
from .maze import MazeRules, replay
from .models import PresentationPlan, PuzzleSpec, Solution, TimelineSpec


def direct_plan(puzzle: PuzzleSpec, solution: Solution, rules: MazeRules, timeline: TimelineSpec | None = None) -> PresentationPlan:
    timeline = timeline or TimelineSpec()
    trace = replay(puzzle, solution.actions, rules)
    if not rules.is_goal(puzzle, trace.final):
        raise ValueError("presentation solution does not reach goal")
    if trace.final and solution.final_state_hash != sha256_value(trace.final.to_dict()):
        raise ValueError("presentation final hash mismatch")
    frames = timeline.frames()
    cues = (
        {"kind": "neutral_progress", "start_frame": frames["appearance"], "end_frame": frames["reveal_start"] - 1},
        {"kind": "path_trace", "start_frame": frames["reveal_start"], "end_frame": frames["solve_end"] - 1},
        {"kind": "goal_light_front", "start_frame": frames["solve_end"], "end_frame": frames["result_end"] - 1},
    )
    return PresentationPlan("1.0.0", "direct", "1", "minimal-v1", sha256_value(solution.to_dict()), solution.actions, frames, cues)
