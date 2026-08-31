from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class PuzzleSpec:
    schema_version: str
    puzzle_type: str
    generator_version: str
    width: int
    height: int
    start: tuple[int, int]
    goal: tuple[int, int]
    edges: tuple[tuple[tuple[int, int], tuple[int, int]], ...]
    ruleset: str = "perfect-maze-v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PuzzleSpec":
        return cls(
            schema_version=value["schema_version"], puzzle_type=value["puzzle_type"],
            generator_version=value["generator_version"], width=value["width"], height=value["height"],
            start=tuple(value["start"]), goal=tuple(value["goal"]),
            edges=tuple((tuple(a), tuple(b)) for a, b in value["edges"]),
            ruleset=value.get("ruleset", "perfect-maze-v1"),
        )


@dataclass(frozen=True)
class PuzzleState:
    puzzle_type: str
    step: int
    current: tuple[int, int]
    visited: tuple[tuple[int, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Action:
    action_version: int
    kind: str
    actor_id: str
    params: dict[str, list[int]]
    precondition: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Action":
        return cls(value["action_version"], value["kind"], value["actor_id"], value["params"], value["precondition"])


@dataclass(frozen=True)
class Solution:
    schema_version: str
    solver_id: str
    solver_version: str
    optimality: str
    actions: tuple[Action, ...]
    initial_state_hash: str
    final_state_hash: str
    cost: int
    expanded_nodes: int
    answer_equivalence_key: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["actions"] = [action.to_dict() for action in self.actions]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Solution":
        return cls(
            value["schema_version"], value["solver_id"], value["solver_version"], value["optimality"],
            tuple(Action.from_dict(a) for a in value["actions"]), value["initial_state_hash"],
            value["final_state_hash"], value["cost"], value["expanded_nodes"], value["answer_equivalence_key"],
        )


@dataclass(frozen=True)
class TimelineSpec:
    preset: str = "standard"
    appearance_duration: float = 0.30
    thinking_duration: float = 2.20
    anticipation_duration: float = 0.0
    solve_duration: float = 2.0
    result_duration: float = 0.70
    transition_duration: float = 0.80
    fps: int = 20
    loop_count: int = 0

    def frames(self) -> dict[str, int]:
        if self.fps <= 0:
            raise ValueError("fps must be positive")
        durations = {
            "appearance": self.appearance_duration, "thinking": self.thinking_duration,
            "anticipation": self.anticipation_duration, "solve": self.solve_duration,
            "result": self.result_duration, "transition": self.transition_duration,
        }
        if any(value < 0 for value in durations.values()):
            raise ValueError("timeline durations must be non-negative")
        result = {name: int((Decimal(str(value)) * self.fps).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) for name, value in durations.items()}
        result["reveal_start"] = result["appearance"] + result["thinking"] + result["anticipation"]
        result["solve_end"] = result["reveal_start"] + result["solve"]
        result["result_end"] = result["solve_end"] + result["result"]
        result["total"] = sum(result[name] for name in durations)
        return result


@dataclass(frozen=True)
class PresentationPlan:
    schema_version: str
    policy: str
    policy_version: str
    theme: str
    source_solution_hash: str
    logical_steps: tuple[Action, ...]
    timeline: dict[str, int]
    visual_cues: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["logical_steps"] = [a.to_dict() for a in self.logical_steps]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PresentationPlan":
        return cls(
            value["schema_version"], value["policy"], value["policy_version"], value["theme"],
            value["source_solution_hash"], tuple(Action.from_dict(a) for a in value["logical_steps"]),
            value["timeline"], tuple(value["visual_cues"]),
        )


@dataclass(frozen=True)
class ReplayStep:
    action: Action
    before: PuzzleState
    after: PuzzleState


@dataclass(frozen=True)
class ReplayTrace:
    initial: PuzzleState
    steps: tuple[ReplayStep, ...]
    final: PuzzleState
