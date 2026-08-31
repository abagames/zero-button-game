from __future__ import annotations

from .core import sha256_value
from .lights import LightsPuzzleSpec, LightsRules, replay_lights
from .models import PresentationPlan, Solution, TimelineSpec


def lights_plan(
    puzzle: LightsPuzzleSpec, solution: Solution, rules: LightsRules,
    timeline: TimelineSpec | None = None,
) -> PresentationPlan:
    timeline = timeline or TimelineSpec()
    trace = replay_lights(puzzle, solution.actions, rules)
    if not rules.is_goal(puzzle, trace.final):
        raise ValueError("lights presentation solution does not light the whole board")
    if solution.final_state_hash != sha256_value(trace.final.to_dict()):
        raise ValueError("lights presentation final hash mismatch")
    frames = timeline.frames()
    frames["goal_keyframe"] = frames["result_end"]
    presses = []
    for index, action in enumerate(solution.actions):
        presses.append({
            "kind": "press_cell", "action_index": index,
            "cell": list(action.params["cell"]),
            "start_unit": index, "end_unit": index + 1,
        })
    cues = (
        {"kind": "neutral_progress", "start_frame": frames["appearance"], "end_frame": frames["reveal_start"] - 1},
        # The legend tiles are drawn from the puzzle alone and never from the
        # press set; the cue records that so the neutrality claim is auditable.
        {"kind": "rule_legend", "start_frame": 0, "end_frame": frames["total"] - 1, "state_mutation": False, "solution_dependent": False},
        *presses,
        {"kind": "board_lit", "start_frame": frames["solve_end"], "end_frame": frames["result_end"] - 1, "state_mutation": False},
    )
    return PresentationPlan(
        "1.0.0", "lights-press-then-light", "1", "minimal-v1",
        sha256_value(solution.to_dict()), solution.actions, frames, tuple(cues),
    )
