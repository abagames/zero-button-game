from __future__ import annotations

from .core import sha256_value
from .fold import FoldPuzzleSpec, FoldRules, action_fold, replay_fold
from .models import PresentationPlan, Solution, TimelineSpec


def fold_plan(
    puzzle: FoldPuzzleSpec, solution: Solution, rules: FoldRules,
    timeline: TimelineSpec | None = None,
) -> PresentationPlan:
    timeline = timeline or TimelineSpec()
    trace = replay_fold(puzzle, solution.actions, rules)
    if not rules.is_goal(puzzle, trace.final):
        raise ValueError("fold presentation solution does not fill the target rectangle")
    if solution.final_state_hash != sha256_value(trace.final.to_dict()):
        raise ValueError("fold presentation final hash mismatch")
    frames = timeline.frames()
    folds = []
    for index, action in enumerate(solution.actions):
        axis, line, direction = action_fold(action)
        folds.append({
            "kind": "fold_crease", "action_index": index,
            "axis": axis, "line": line, "dir": direction,
            "start_unit": index, "end_unit": index + 1,
        })
    cues = (
        {"kind": "neutral_progress", "start_frame": frames["appearance"], "end_frame": frames["reveal_start"] - 1},
        # Both the rule legend and the dashed target outline are drawn from the
        # problem alone - never from the fold class - and the cue records that
        # so the neutrality claim is auditable.
        {"kind": "rule_legend", "start_frame": 0, "end_frame": frames["total"] - 1, "state_mutation": False, "solution_dependent": False},
        {"kind": "target_outline", "start_frame": 0, "end_frame": frames["total"] - 1, "state_mutation": False, "solution_dependent": False},
        *folds,
        {"kind": "target_filled", "start_frame": frames["solve_end"], "end_frame": frames["result_end"] - 1, "state_mutation": False},
    )
    return PresentationPlan(
        "1.0.0", "fold-crease-then-fill", "1", "minimal-v1",
        sha256_value(solution.to_dict()), solution.actions, frames, tuple(cues),
    )
