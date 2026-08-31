from __future__ import annotations

from .core import sha256_value
from .models import PresentationPlan, Solution, TimelineSpec
from .pipes import PipePuzzleSpec, PipeRules, trace_pipes


def pipe_plan(
    puzzle: PipePuzzleSpec, solution: Solution, rules: PipeRules,
    timeline: TimelineSpec | None = None,
) -> PresentationPlan:
    timeline = timeline or TimelineSpec()
    trace = trace_pipes(puzzle, solution.actions, rules)
    if not rules.is_goal(puzzle, trace.final):
        raise ValueError("pipes presentation solution does not connect START to GOAL")
    if solution.final_state_hash != sha256_value(trace.final.to_dict()):
        raise ValueError("pipes presentation final hash mismatch")
    frames = timeline.frames()
    frames["goal_keyframe"] = frames["result_end"]
    cumulative = 0
    rotations = []
    for index, action in enumerate(solution.actions):
        turns = action.params["quarter_turns"][0]
        duration = abs(turns)
        rotations.append({
            "kind": "piece_rotation", "action_index": index,
            "cell": action.params["cell"], "quarter_turns": turns,
            "start_unit": cumulative, "end_unit": cumulative + duration,
        })
        cumulative += duration
    cues = (
        {"kind":"neutral_progress","start_frame":frames["appearance"],"end_frame":frames["reveal_start"] - 1},
        *rotations,
        {"kind":"network_flow","start_frame":frames["solve_end"],"end_frame":frames["result_end"] - 1,"state_mutation":False},
    )
    return PresentationPlan(
        "1.0.0", "pipes-rotate-then-flow", "1", "minimal-v1",
        sha256_value(solution.to_dict()), solution.actions, frames, tuple(cues),
    )
