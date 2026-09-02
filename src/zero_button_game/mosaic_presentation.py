from __future__ import annotations

from .core import sha256_value
from .models import PresentationPlan, Solution, TimelineSpec
from .mosaic import MosaicPuzzleSpec, MosaicRules, action_signature, replay_mosaic


def mosaic_plan(
    puzzle: MosaicPuzzleSpec, solution: Solution, rules: MosaicRules,
    timeline: TimelineSpec | None = None,
) -> PresentationPlan:
    timeline = timeline or TimelineSpec()
    trace = replay_mosaic(puzzle, solution.actions, rules)
    if not rules.is_goal(puzzle, trace.final):
        raise ValueError("mosaic presentation must finish on the emblem")
    if solution.final_state_hash != sha256_value(trace.final.to_dict()):
        raise ValueError("mosaic presentation final hash mismatch")
    frames = timeline.frames()
    frames["goal_keyframe"] = frames["solve_end"]
    shifts = []
    for index, action in enumerate(solution.actions):
        axis, line, delta = action_signature(action)
        shifts.append({
            "kind": "cyclic_line_shift", "action_index": index,
            "axis": axis, "line": line, "delta": delta,
            "start_unit": index, "end_unit": index + 1,
        })
    cues = (
        {"kind": "neutral_progress", "start_frame": frames["appearance"], "end_frame": frames["reveal_start"] - 1, "solution_dependent": False},
        {"kind": "emblem_fragments", "start_frame": 0, "end_frame": frames["total"] - 1, "state_mutation": False, "solution_dependent": False},
        *shifts,
        {"kind": "emblem_complete", "start_frame": frames["solve_end"], "end_frame": frames["result_end"] - 1, "state_mutation": False},
    )
    return PresentationPlan(
        "1.0.0", "mosaic-line-shift-then-clear", "1", "minimal-v1",
        sha256_value(solution.to_dict()), solution.actions, frames, tuple(cues),
    )
