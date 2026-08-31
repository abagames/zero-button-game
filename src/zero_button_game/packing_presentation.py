from __future__ import annotations

from .core import sha256_value
from .models import PresentationPlan, Solution, TimelineSpec
from .packing import PackingPuzzleSpec, PackingRules, replay_packing


def packing_plan(
    puzzle: PackingPuzzleSpec, solution: Solution, rules: PackingRules,
    timeline: TimelineSpec | None = None,
) -> PresentationPlan:
    timeline = timeline or TimelineSpec()
    trace = replay_packing(puzzle, solution.actions, rules)
    if not rules.is_goal(puzzle, trace.final):
        raise ValueError("packing presentation solution does not cover the hole")
    if solution.final_state_hash != sha256_value(trace.final.to_dict()):
        raise ValueError("packing presentation final hash mismatch")
    frames = timeline.frames()
    frames["goal_keyframe"] = frames["result_end"]
    placements = []
    for index, action in enumerate(solution.actions):
        placements.append({
            "kind": "place_piece", "action_index": index,
            "piece": action.params["piece"][0],
            "to": list(action.params["to"]), "cells": action.params["cells"][0],
            "start_unit": index, "end_unit": index + 1,
        })
    cues = (
        {"kind": "neutral_progress", "start_frame": frames["appearance"], "end_frame": frames["reveal_start"] - 1},
        *placements,
        {"kind": "hole_filled", "start_frame": frames["solve_end"], "end_frame": frames["result_end"] - 1, "state_mutation": False},
    )
    return PresentationPlan(
        "1.0.0", "packing-place-then-seal", "1", "minimal-v1",
        sha256_value(solution.to_dict()), solution.actions, frames, tuple(cues),
    )
