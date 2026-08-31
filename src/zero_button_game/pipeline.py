from __future__ import annotations

import os
import platform
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .core import StableRng, derive_seed, read_json, sha256_file, sha256_value, write_json
from .export import DEFAULT_FORMATS, export_keyframes_and_contact, export_media, parse_formats, tool_version
from .models import TimelineSpec
from .preset_loader import PresetRoots, load_preset, use_preset_root
from .registry import get_plugin
from .validation import pre_reveal_neutrality_failure, validate_instance


@dataclass(frozen=True)
class GenerationRequest:
    puzzle_type: str
    count: int
    difficulty_band: str
    master_seed: int
    output: Path
    max_candidates: int | None = None
    keep_frames: bool = False
    resume: bool = False
    force: bool = False
    thinking_time_seconds: float | None = None
    timing_variant: str | None = None
    # Delivery formats to encode. The default keeps both artifacts, which is
    # what every work made before --format existed contains.
    formats: tuple[str, ...] = DEFAULT_FORMATS
    # Explicit dependency injection for tests and alternate checked-out preset
    # trees. None uses the repository-relative default, never the process CWD.
    preset_root: Path | PresetRoots | None = None


@dataclass(frozen=True)
class GenerationResult:
    instances: tuple[Path, ...]
    candidate_count: int
    rejection_count: int


class GenerationExhausted(RuntimeError):
    pass


LEGACY_PROBLEM_TO_REVEAL_SECONDS = 2.5
MINIMUM_PROBLEM_TO_REVEAL_SECONDS = 2.5
MAXIMUM_PROBLEM_TO_REVEAL_SECONDS = 20.0
TARGET_STANDARD_PROBLEM_TO_REVEAL_SECONDS = 8.0


def _preset_thinking_time(request: GenerationRequest) -> float | None:
    preset = load_preset(request.puzzle_type, request.difficulty_band, request.preset_root).runtime
    value = preset.get("thinking_time_seconds")
    return None if value is None else float(value)


def _generic_override_profile(request: GenerationRequest) -> dict:
    """Record kept when a plugin/band has no calibration round of its own.

    ``--thinking-time`` is accepted for every plugin because comparison
    material has to be generated for all of them, but a work made that way is
    never a standard one: the metadata block says so explicitly.
    """
    plugin = get_plugin(request.puzzle_type)
    standard = _preset_thinking_time(request)
    return {
        "variant": f"{request.puzzle_type}-{request.difficulty_band}-comparison-override",
        "baseline_thinking_time_seconds": LEGACY_PROBLEM_TO_REVEAL_SECONDS,
        "previous_evaluated_thinking_time_seconds": None,
        "standard_thinking_time_seconds": LEGACY_PROBLEM_TO_REVEAL_SECONDS if standard is None else standard,
        "calibration_change": "explicit comparison override; no timing calibration round for this plugin band",
        "calibration_status": "comparison-override-not-standard",
        "source_evaluation": None,
        "calibration_scope": "comparison material only; not a calibrated standard",
        "structural_difficulty_status": plugin.calibration_label(request.difficulty_band),
        "timing_status": "comparison-override-not-standard",
    }


def timing_calibration_profile(request: GenerationRequest, preset: dict | None = None) -> dict | None:
    """Timing-calibration profile for the request, or None.

    A plugin that declares a profile for the band contributes its calibration
    round. Any other plugin still accepts ``--thinking-time``; the override is
    then recorded as a non-standard comparison condition.
    """
    plugin = get_plugin(request.puzzle_type)
    declare = getattr(plugin, "timing_calibration_profile", None)
    declared = None if declare is None else declare(request.difficulty_band)
    if declared is not None:
        # The recorded previous value remains historical evidence, while the
        # current standard is always the value loaded from JSON.
        current = preset if preset is not None else plugin.difficulty_preset(request.difficulty_band)
        declared = {**declared, "standard_thinking_time_seconds": float(current["thinking_time_seconds"])}
        return declared
    if request.thinking_time_seconds is None:
        return None
    return _generic_override_profile(request)


def effective_problem_to_reveal_seconds(request: GenerationRequest) -> float | None:
    if request.thinking_time_seconds is not None:
        return float(request.thinking_time_seconds)
    return _preset_thinking_time(request)


def timeline_for_request(request: GenerationRequest, animation_units: int) -> TimelineSpec:
    """Build a timeline while treating thinking_time as frame-zero to reveal_start."""
    base = TimelineSpec()
    solve_duration = max(base.solve_duration, min(4.0, animation_units * 2 / base.fps))
    requested = effective_problem_to_reveal_seconds(request)
    if requested is None:
        return TimelineSpec(preset="standard-adaptive-solve", solve_duration=solve_duration)
    if requested < MINIMUM_PROBLEM_TO_REVEAL_SECONDS or requested > MAXIMUM_PROBLEM_TO_REVEAL_SECONDS:
        raise ValueError("thinking time must be between the 2.5s baseline and 20.0s")
    frame_count = requested * base.fps
    if abs(frame_count - round(frame_count)) > 1e-9:
        raise ValueError("thinking time must align to the 20fps frame grid")
    stable_thinking = round(requested - base.appearance_duration - base.anticipation_duration, 6)
    with use_preset_root(request.preset_root):
        label = get_plugin(request.puzzle_type).timeline_preset_label(
            request.difficulty_band, requested, request.thinking_time_seconds is not None,
        )
    return TimelineSpec(
        preset=label,
        thinking_duration=stable_thinking,
        solve_duration=solve_duration,
    )


def _runtime() -> dict:
    return {
        "python": platform.python_version(), "implementation": platform.python_implementation(),
        "numpy": "not-used", "pillow": "not-used", "zero_button_game": __version__,
        "ffmpeg": tool_version("ffmpeg"), "ffprobe": tool_version("ffprobe"), "gifsicle": tool_version("gifsicle"),
    }


def _artifact_metadata(probe: dict) -> dict:
    order = ["kind", "path", "sha256", "bytes", "width", "height", "fps", "frames", "duration_ms", "codec", "pixel_format", "colors", "dither", "loop_count"]
    return {key: probe[key] for key in order if key in probe}


def _backup_conflict(path: Path) -> None:
    superseded = path.parent.parent.parent / "superseded"
    superseded.mkdir(parents=True, exist_ok=True)
    index = 1
    while (superseded / f"{path.name}-{index:04d}").exists():
        index += 1
    shutil.move(str(path), str(superseded / f"{path.name}-{index:04d}"))


def _build_one(request: GenerationRequest, candidate_index: int, rejections: list[dict]) -> Path | None:
    plugin = get_plugin(request.puzzle_type)
    generation_seed = derive_seed(request.master_seed, request.puzzle_type, candidate_index, "generation")
    presentation_seed = derive_seed(request.master_seed, request.puzzle_type, candidate_index, "presentation")
    preset_record = load_preset(request.puzzle_type, request.difficulty_band, request.preset_root)
    requested_preset = preset_record.runtime_copy()
    try:
        puzzle = plugin.generate_candidate(StableRng(generation_seed), requested_preset)
    except ValueError as error:
        soft = getattr(plugin, "candidate_rejection_reason", None)
        reason = None if soft is None else soft(requested_preset, error)
        if reason is None:
            raise
        rejections.append({
            "candidate_index": candidate_index,
            "reason": reason,
            "details": [str(error)],
        })
        return None
    structure_errors = plugin.rules.validate_structure(puzzle)
    if structure_errors:
        rejections.append({"candidate_index": candidate_index, "reason": "INVALID_STRUCTURE", "details": structure_errors})
        return None
    try:
        solution = plugin.solver.solve(puzzle)
    except RuntimeError as error:
        known = set(plugin.solver_reject_codes)
        code = getattr(error, "code", str(error))
        reason = code if code in known else "SOLVER_ERROR"
        rejection = {"candidate_index": candidate_index, "reason": reason}
        diagnostics = getattr(error, "diagnostics", None)
        if diagnostics:
            rejection["solver_diagnostics"] = {
                key: diagnostics[key] for key in (
                    "status", "minimum_quarter_turn_cost", "normalized_solution_count",
                    "normalized_solution_count_capped", "unique_path_count", "search",
                ) if key in diagnostics
            }
        rejections.append(rejection)
        return None
    logical_failures = plugin.validate_solution(puzzle, solution, plugin.rules)
    if logical_failures:
        rejections.append({"candidate_index": candidate_index, "reason": "ILLEGAL_SOLUTION", "details": logical_failures})
        return None
    difficulty = plugin.difficulty(puzzle, solution, plugin.rules)
    reason = plugin.quality_filter(difficulty, request.difficulty_band)
    if reason:
        rejection = {"candidate_index": candidate_index, "reason": reason, "difficulty": difficulty["mechanical"]}
        if "generation_traits" in difficulty:
            rejection["generation_traits"] = difficulty["generation_traits"]
        rejections.append(rejection)
        return None
    timeline_spec = timeline_for_request(request, plugin.animation_units(solution))
    plan = plugin.presentation(puzzle, solution, plugin.rules, timeline_spec)
    # Presentation steps are independently replayed before any renderer is called.
    trace = plugin.replay(puzzle, plan.logical_steps, plugin.rules)
    if not plugin.rules.is_goal(puzzle, trace.final):
        rejections.append({"candidate_index": candidate_index, "reason": "ILLEGAL_SOLUTION", "details": ["presentation does not reach goal"]})
        return None
    problem_hash = sha256_value(puzzle.to_dict())
    instance_id = f"{request.puzzle_type}-{request.master_seed:06d}-{problem_hash[-8:]}"
    parent = request.output / request.puzzle_type
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / instance_id
    if destination.exists():
        if request.resume:
            existing = read_json(destination / "problem.json")
            if sha256_value(existing) == problem_hash:
                return destination
            raise FileExistsError("OUTPUT_CONFLICT: resume target has different content")
        if request.force:
            _backup_conflict(destination)
        else:
            raise FileExistsError(f"OUTPUT_CONFLICT: {destination} exists (use --resume or --force)")
    staging = Path(tempfile.mkdtemp(prefix=f".{instance_id}-", dir=parent))
    try:
        write_json(staging / "problem.json", puzzle.to_dict())
        write_json(staging / "solution.json", solution.to_dict())
        write_json(staging / "presentation.json", plan.to_dict())
        frames_dir = staging / "frames"
        renderer = plugin.renderer_factory()
        scene = plugin.scene_builder.build(puzzle, plan, trace)
        frames = renderer.render(scene, frames_dir)
        keyframe_hashes = export_keyframes_and_contact(frames, staging, plan.timeline, timeline_spec.fps)
        formats = parse_formats(",".join(request.formats))
        media_probes = export_media(frames_dir, staging, timeline_spec.fps, plan.timeline["total"], formats)
        # Neutrality oracle: before reveal, changing the solution path cannot change pixels.
        neutrality_failure = pre_reveal_neutrality_failure(plugin, renderer, scene, plan.timeline)
        if neutrality_failure is not None:
            raise ValueError(f"LEAKS_SOLUTION: {neutrality_failure}")
        # Solve frames must actually animate.
        reveal = plan.timeline["reveal_start"]
        animation_changes = frames[reveal].read_bytes() != frames[min(reveal + 1, len(frames) - 1)].read_bytes()
        if not animation_changes:
            raise ValueError("solve animation has no frame change")
        difficulty["quality_preset_sha256"] = preset_record.source_sha256
        difficulty["quality_preset_hash_basis"] = "source-json-bytes-v1"
        difficulty["quality_preset_source"] = preset_record.source_reference
        difficulty["quality_preset_schema_version"] = preset_record.document.get("schema_version")
        timeline = {
            "preset": timeline_spec.preset, "appearance_frames": plan.timeline["appearance"],
            "thinking_frames": plan.timeline["thinking"], "anticipation_frames": plan.timeline["anticipation"],
            "solve_frames": plan.timeline["solve"], "result_frames": plan.timeline["result"],
            "transition_frames": plan.timeline["transition"], "fps": timeline_spec.fps,
            "total_frames": plan.timeline["total"], "reveal_start_frame": plan.timeline["reveal_start"],
            "problem_to_reveal_seconds": plan.timeline["reveal_start"] / timeline_spec.fps,
            "thinking_phase_seconds": plan.timeline["thinking"] / timeline_spec.fps,
        }
        solution_metadata = {
            "solver_id": solution.solver_id, "solver_version": solution.solver_version, "optimality": solution.optimality,
            "action_count": len(solution.actions), "cost": solution.cost, "solution_sha256": sha256_value(solution.to_dict()),
        }
        if "solution_uniqueness" in difficulty:
            solution_metadata["uniqueness"] = difficulty["solution_uniqueness"]
        metadata = {
            "schema_version": "1.0.0", "instance_id": instance_id,
            "puzzle": {"type": request.puzzle_type, "plugin_version": plugin.plugin_version, "generator_version": puzzle.generator_version, "ruleset": puzzle.ruleset, "problem_sha256": problem_hash, "answer_equivalence_key": solution.answer_equivalence_key},
            "provenance": {
                "master_seed": request.master_seed, "candidate_index": candidate_index, "rng_algorithm": "SplitMix64",
                "rng_derivation_version": 1, "generation_seed_hex": f"{generation_seed:032x}",
                "presentation_seed_hex": f"{presentation_seed:032x}", "code_revision": "non-git-workspace", "runtime": _runtime(),
            },
            "solution": solution_metadata,
            "difficulty": difficulty, "timeline": timeline,
            "presentation": {"policy": plan.policy, "policy_version": plan.policy_version, "theme": plan.theme},
            "artifacts": [_artifact_metadata(probe) for probe in media_probes],
            "reproducibility": {"keyframe_sha256": keyframe_hashes, "content_hash_excludes": ["validation.validated_at"]},
            "validation": {
                "status": "passed", "checks_passed": ["solution_replay", "goal", "action_legality", "pre_reveal_neutrality", "solve_animation"],
                "checks_failed": [], "validated_at": "1970-01-01T00:00:00Z",
            },
        }
        calibration_profile = timing_calibration_profile(request, requested_preset)
        if calibration_profile is not None:
            effective_thinking_time = plan.timeline["reveal_start"] / timeline_spec.fps
            standard = calibration_profile["standard_thinking_time_seconds"]
            calibrated_standard = (
                effective_thinking_time == standard
                and calibration_profile["calibration_status"] != "comparison-override-not-standard"
            )
            metadata["timing_calibration"] = {
                "variant": request.timing_variant or calibration_profile["variant"],
                "baseline_thinking_time_seconds": calibration_profile["baseline_thinking_time_seconds"],
                "previous_evaluated_thinking_time_seconds": calibration_profile["previous_evaluated_thinking_time_seconds"],
                "target_standard_thinking_time_seconds": standard,
                "thinking_time_seconds": effective_thinking_time,
                "thinking_time_definition": "seconds from frame zero to reveal_start; includes the fixed appearance phase",
                "changed_field": "timeline.problem_to_reveal_seconds",
                "calibration_change": calibration_profile["calibration_change"] if calibrated_standard else f"explicit comparison override of the {standard}s standard",
                "calibration_status": calibration_profile["calibration_status"] if calibrated_standard else "comparison-override-not-standard",
                "source_evaluation": calibration_profile["source_evaluation"],
                "derived_fields": ["timeline.thinking_frames", "timeline.reveal_start_frame", "timeline.total_frames", "artifact frames/duration", "keyframe/contact sampling"],
                "fixed_fields": {
                    "appearance_frames": plan.timeline["appearance"],
                    "anticipation_frames": plan.timeline["anticipation"],
                    "solve_frames": plan.timeline["solve"],
                    "result_frames": plan.timeline["result"],
                    "transition_frames": plan.timeline["transition"],
                    "fps": timeline_spec.fps,
                },
                "calibration_scope": calibration_profile["calibration_scope"],
                "structural_difficulty_status": calibration_profile["structural_difficulty_status"],
                "timing_status": calibration_profile["timing_status"] if calibrated_standard else "comparison-override-not-standard",
                "problem_sha256": problem_hash,
                "solution_sha256": solution_metadata["solution_sha256"],
            }
        write_json(staging / "metadata.json", metadata)
        write_json(staging / "validation.json", {"status": "pending"})
        report = validate_instance(staging, strict=True)
        report["checks_passed"] = sorted(set(report["checks_passed"] + ["pre_reveal_neutrality", "solve_animation", "safe_area", "keyframe_hashes"]))
        report["visual_contract"] = {**plugin.visual_contract(scene, renderer), "pre_reveal_neutral":True}
        write_json(staging / "validation.json", report)
        metadata["validation"]["checks_passed"] = report["checks_passed"]
        write_json(staging / "metadata.json", metadata)
        # Ensure the final metadata still agrees with the artifacts.
        validate_instance(staging, strict=True)
        if not request.keep_frames:
            for frame in frames:
                frame.unlink()
            frames_dir.rmdir()
        os.replace(staging, destination)
        return destination
    except Exception:
        # Staging is intentionally retained as evidence for diagnosis; it is never a completed instance.
        raise


def _generate(request: GenerationRequest) -> GenerationResult:
    if request.count <= 0:
        raise ValueError("count must be positive")
    plugin = get_plugin(request.puzzle_type)
    plugin.difficulty_preset(request.difficulty_band)
    max_candidates = request.max_candidates or max(request.count * 50, 100)
    request.output.mkdir(parents=True, exist_ok=True)
    accepted: list[Path] = []
    rejections: list[dict] = []
    candidate_index = 0
    while len(accepted) < request.count and candidate_index < max_candidates:
        result = _build_one(request, candidate_index, rejections)
        if result is not None:
            accepted.append(result)
        candidate_index += 1
    manifest = request.output / "manifest.jsonl"
    calibration = plugin.calibration_label(request.difficulty_band)
    manifest.write_text("".join(__import__("json").dumps({"instance": str(path.relative_to(request.output)), "status": "passed", "calibration": calibration, "difficulty": request.difficulty_band}, sort_keys=True) + "\n" for path in accepted), encoding="utf-8")
    (request.output / "rejections.jsonl").write_text("".join(__import__("json").dumps(item, sort_keys=True) + "\n" for item in rejections), encoding="utf-8")
    if len(accepted) < request.count:
        raise GenerationExhausted(f"accepted {len(accepted)} of {request.count} after {candidate_index} candidates")
    return GenerationResult(tuple(accepted), candidate_index, len(rejections))


def generate(request: GenerationRequest) -> GenerationResult:
    """Generate using one pinned preset root for all plugin calls."""
    with use_preset_root(request.preset_root):
        return _generate(request)
