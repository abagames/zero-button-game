"""Plugin protocol v1.

The pipeline talks to puzzle plugins by duck typing. This module freezes that
contract in two layers:

* ``PuzzlePlugin`` - a ``typing.Protocol`` used for documentation and static
  typing. It is deliberately NOT ``runtime_checkable``: an ``isinstance`` check
  against a runtime-checkable Protocol only verifies attribute presence, which
  is exactly the weak check this contract must not rely on.
* ``conformance_failures`` - an executable checker that verifies presence,
  callability and the structure of returned values, by actually driving a
  plugin through one generation cycle. This is what the conformance test runs.

An ABC was rejected because the three plugins are frozen dataclasses built from
module-level functions via ``staticmethod``; requiring inheritance would change
their construction without adding a single check that the checker below does
not already perform.

Three classes of contract member:

* REQUIRED - every plugin must provide it; the pipeline calls it unconditionally.
* OPTIONAL - looked up with ``getattr(..., None)``; absence is a valid choice.
* PLUGIN-SPECIFIC - values a plugin declares freely (labels, reject codes,
  calibration profiles). The protocol fixes their type and who asks for them,
  never their content.
"""

from __future__ import annotations

from typing import Any, Protocol

PLUGIN_PROTOCOL_VERSION = "plugin_protocol_v1"

FRAME_BYTES = 720 * 720 * 3

REQUIRED_ATTRIBUTES = (
    "puzzle_type", "plugin_version", "rules", "solver", "scene_builder",
    "solver_reject_codes", "difficulty_preset", "generate_candidate", "problem_from_dict",
    "difficulty", "quality_filter", "replay", "validate_solution", "presentation",
    "animation_units", "renderer_factory", "alternate_scene", "visual_contract",
    "render_contract_checks", "calibration_label", "timeline_preset_label",
)
REQUIRED_RULES_ATTRIBUTES = ("validate_structure", "initial_state", "legal_actions", "apply", "is_goal")
OPTIONAL_ATTRIBUTES = ("metadata_contract_checks", "candidate_rejection_reason", "timing_calibration_profile")


class PuzzlePlugin(Protocol):
    """Static shape of a puzzle plugin (see module docstring for the layers)."""

    puzzle_type: str
    plugin_version: str
    rules: Any
    solver: Any
    scene_builder: Any
    solver_reject_codes: frozenset[str]

    def difficulty_preset(self, band: str) -> dict: ...
    def generate_candidate(self, rng: Any, preset: dict) -> Any: ...
    def problem_from_dict(self, value: dict) -> Any: ...
    def difficulty(self, puzzle: Any, solution: Any, rules: Any) -> dict: ...
    def quality_filter(self, difficulty: dict, band: str) -> str | None: ...
    def replay(self, puzzle: Any, actions: Any, rules: Any) -> Any: ...
    def validate_solution(self, puzzle: Any, solution: Any, rules: Any) -> list[str]: ...
    def presentation(self, puzzle: Any, solution: Any, rules: Any, timeline: Any) -> Any: ...
    def animation_units(self, solution: Any) -> int: ...
    def renderer_factory(self) -> Any: ...
    def alternate_scene(self, scene: Any) -> Any: ...
    def visual_contract(self, scene: Any, renderer: Any) -> dict: ...
    def render_contract_checks(self, scene: Any, renderer: Any) -> tuple[list[str], list[str]]: ...
    def calibration_label(self, band: str) -> str: ...
    def timeline_preset_label(self, band: str, seconds: float, overridden: bool) -> str: ...


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _structural_failures(plugin: Any) -> list[str]:
    failures: list[str] = []
    for name in REQUIRED_ATTRIBUTES:
        if not hasattr(plugin, name):
            failures.append(f"missing required member: {name}")
    if not failures:
        if not isinstance(plugin.puzzle_type, str) or not plugin.puzzle_type:
            failures.append("puzzle_type must be a non-empty string")
        if not isinstance(plugin.plugin_version, str) or plugin.plugin_version.count(".") != 2:
            failures.append("plugin_version must be a MAJOR.MINOR.PATCH string")
        codes = plugin.solver_reject_codes
        if not isinstance(codes, (frozenset, set)) or not codes or not all(isinstance(code, str) for code in codes):
            failures.append("solver_reject_codes must be a non-empty set of strings")
        for name in REQUIRED_ATTRIBUTES:
            member = getattr(plugin, name)
            if name in {"puzzle_type", "plugin_version", "rules", "solver", "scene_builder", "solver_reject_codes"}:
                continue
            if not callable(member):
                failures.append(f"{name} must be callable")
        for name in REQUIRED_RULES_ATTRIBUTES:
            if not callable(getattr(plugin.rules, name, None)):
                failures.append(f"rules.{name} must be callable")
        if not callable(getattr(plugin.solver, "solve", None)):
            failures.append("solver.solve must be callable")
        if not callable(getattr(plugin.scene_builder, "build", None)):
            failures.append("scene_builder.build must be callable")
    for name in OPTIONAL_ATTRIBUTES:
        member = getattr(plugin, name, None)
        if member is not None and not callable(member):
            failures.append(f"optional member {name} must be callable when present")
    return failures


def _sample(plugin: Any, band: str, max_candidates: int) -> tuple[Any, Any] | None:
    """First (puzzle, solution) the plugin itself accepts, or None."""
    from .core import StableRng, derive_seed

    preset = plugin.difficulty_preset(band)
    for index in range(max_candidates):
        seed = derive_seed(20260822, plugin.puzzle_type, index, "conformance")
        try:
            puzzle = plugin.generate_candidate(StableRng(seed), preset)
        except ValueError:
            continue
        if plugin.rules.validate_structure(puzzle):
            continue
        try:
            solution = plugin.solver.solve(puzzle)
        except RuntimeError:
            continue
        if plugin.validate_solution(puzzle, solution, plugin.rules):
            continue
        if plugin.quality_filter(plugin.difficulty(puzzle, solution, plugin.rules), band):
            continue
        return puzzle, solution
    return None


def _behavioural_failures(plugin: Any, band: str, max_candidates: int) -> list[str]:
    from .models import TimelineSpec

    failures: list[str] = []
    preset = plugin.difficulty_preset(band)
    if not isinstance(preset, dict) or "name" not in preset:
        return [f"difficulty_preset({band!r}) must return a dict carrying a preset name"]
    try:
        plugin.difficulty_preset("no-such-band")
        failures.append("difficulty_preset must raise ValueError for an unknown band")
    except ValueError:
        pass
    except Exception as error:  # noqa: BLE001 - any other error is a contract failure
        failures.append(f"difficulty_preset raised {type(error).__name__} instead of ValueError")
    if not isinstance(plugin.calibration_label(band), str):
        failures.append("calibration_label must return a string")
    if not isinstance(plugin.timeline_preset_label(band, 5.0, False), str):
        failures.append("timeline_preset_label must return a string")
    profile = getattr(plugin, "timing_calibration_profile", None)
    if profile is not None:
        value = profile(band)
        if value is not None:
            required = {
                "variant", "baseline_thinking_time_seconds", "previous_evaluated_thinking_time_seconds",
                "standard_thinking_time_seconds", "calibration_change", "calibration_status",
                "source_evaluation", "calibration_scope", "structural_difficulty_status", "timing_status",
            }
            if not isinstance(value, dict) or not required <= set(value):
                failures.append("timing_calibration_profile must return None or a full profile dict")
    sample = _sample(plugin, band, max_candidates)
    if sample is None:
        return failures + [f"no accepted {band} candidate within {max_candidates} attempts"]
    puzzle, solution = sample
    if getattr(puzzle, "puzzle_type", None) != plugin.puzzle_type:
        failures.append("generate_candidate returned a puzzle of a different puzzle_type")
    if plugin.problem_from_dict(puzzle.to_dict()) != puzzle:
        failures.append("problem_from_dict does not round-trip problem.to_dict()")
    for action in solution.actions:
        if "state_hash" not in action.precondition:
            failures.append("solution actions must carry a precondition.state_hash")
            break
    difficulty = plugin.difficulty(puzzle, solution, plugin.rules)
    if not isinstance(difficulty, dict) or not {"mechanical", "human"} <= set(difficulty):
        failures.append("difficulty must return a dict with 'mechanical' and 'human'")
    verdict = plugin.quality_filter(difficulty, band)
    if verdict is not None and not isinstance(verdict, str):
        failures.append("quality_filter must return None or a reason code string")
    if not _is_string_list(plugin.validate_solution(puzzle, solution, plugin.rules)):
        failures.append("validate_solution must return a list of strings")
    units = plugin.animation_units(solution)
    if not isinstance(units, int) or units < 1:
        failures.append("animation_units must return a positive int")
        units = 1
    timeline = TimelineSpec()
    plan = plugin.presentation(puzzle, solution, plugin.rules, timeline)
    if not isinstance(getattr(plan, "timeline", None), dict) or "reveal_start" not in plan.timeline:
        return failures + ["presentation must return a plan whose .timeline carries reveal_start"]
    trace = plugin.replay(puzzle, plan.logical_steps, plugin.rules)
    if not hasattr(trace, "final"):
        return failures + ["replay must return a trace exposing .final"]
    if not plugin.rules.is_goal(puzzle, trace.final):
        failures.append("presentation replay does not reach the goal state")
    scene = plugin.scene_builder.build(puzzle, plan, trace)
    for name in ("plan", "puzzle", "semantic_bounds"):
        if not hasattr(scene, name):
            failures.append(f"scene must expose .{name}")
    bounds = getattr(scene, "semantic_bounds", None)
    if not (isinstance(bounds, (tuple, list)) and len(bounds) == 4):
        failures.append("scene.semantic_bounds must be a 4-tuple")
    renderer = plugin.renderer_factory()
    if not callable(getattr(renderer, "render", None)):
        failures.append("renderer must expose render(scene, directory)")
    frame = renderer.render_frame(scene, 0)
    if not isinstance(frame, bytes) or len(frame) != FRAME_BYTES:
        failures.append(f"render_frame must return {FRAME_BYTES} raw RGB bytes")
    alternate = plugin.alternate_scene(scene)
    if type(alternate) is not type(scene):
        failures.append("alternate_scene must return a scene of the same type")
    else:
        reveal = plan.timeline["reveal_start"]
        if renderer.render_frame(alternate, 0) != frame:
            failures.append("alternate_scene changes a pre-reveal frame")
        if renderer.render_frame(alternate, reveal) == renderer.render_frame(scene, reveal):
            failures.append("alternate_scene does not change the reveal frame")
    contract = plugin.visual_contract(scene, renderer)
    if not isinstance(contract, dict) or not {"semantic_bounds", "safe_area"} <= set(contract):
        failures.append("visual_contract must return a dict with semantic_bounds and safe_area")
    checks = plugin.render_contract_checks(scene, renderer)
    if not (isinstance(checks, tuple) and len(checks) == 2 and all(_is_string_list(part) for part in checks)):
        failures.append("render_contract_checks must return (passed: list[str], failed: list[str])")
    elif checks[1]:
        failures.append(f"render_contract_checks failed on its own sample: {checks[1]}")
    checker = getattr(plugin, "metadata_contract_checks", None)
    if checker is not None:
        result = checker(puzzle, solution, {})
        if not (isinstance(result, tuple) and len(result) == 2 and all(_is_string_list(part) for part in result)):
            failures.append("metadata_contract_checks must return (passed: list[str], failed: list[str])")
    soft = getattr(plugin, "candidate_rejection_reason", None)
    if soft is not None:
        reason = soft(preset, ValueError("probe"))
        if reason is not None and not isinstance(reason, str):
            failures.append("candidate_rejection_reason must return None or a reason code string")
    return failures


def conformance_failures(plugin: Any, band: str = "easy", max_candidates: int = 40) -> list[str]:
    """Every way ``plugin`` violates plugin protocol v1 (empty list = conformant)."""
    failures = _structural_failures(plugin)
    if failures:
        return failures
    try:
        return _behavioural_failures(plugin, band, max_candidates)
    except Exception as error:  # noqa: BLE001 - a raising plugin is a failing plugin
        return [f"{type(error).__name__} while exercising the plugin: {error}"]
