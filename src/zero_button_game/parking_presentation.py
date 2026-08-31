from __future__ import annotations

from .core import sha256_value
from .models import PresentationPlan, Solution, TimelineSpec
from .parking import ParkingPuzzleSpec, ParkingRules, replay_parking


def parking_plan(
    puzzle: ParkingPuzzleSpec, solution: Solution, rules: ParkingRules,
    timeline: TimelineSpec | None = None,
) -> PresentationPlan:
    timeline = timeline or TimelineSpec()
    trace = replay_parking(puzzle, solution.actions, rules)
    if not rules.is_goal(puzzle, trace.final):
        raise ValueError("parking presentation solution does not clear the east exit")
    if solution.final_state_hash != sha256_value(trace.final.to_dict()):
        raise ValueError("parking presentation final hash mismatch")
    frames = timeline.frames()
    frames["goal_keyframe"] = frames["result_end"]
    cumulative = 0
    slides = []
    for index, action in enumerate(solution.actions):
        cells = action.params["slide_cells"][0]
        slides.append({
            "kind": "slide_move", "action_index": index,
            "vehicle": action.params["vehicle"][0], "delta": action.params["delta"][0],
            "slide_cells": cells, "axis": action.params["axis"][0],
            "start_unit": cumulative, "end_unit": cumulative + cells,
        })
        cumulative += cells
    cues = (
        {"kind":"neutral_progress","start_frame":frames["appearance"],"end_frame":frames["reveal_start"] - 1},
        *slides,
        {"kind":"exit_release","start_frame":frames["solve_end"],"end_frame":frames["result_end"] - 1,"state_mutation":False},
    )
    return PresentationPlan(
        "1.0.0", "parking-slide-then-release", "1", "minimal-v1",
        sha256_value(solution.to_dict()), solution.actions, frames, tuple(cues),
    )
