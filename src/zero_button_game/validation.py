from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .core import read_json, sha256_file, sha256_value
from .export import DEFAULT_FORMATS, decode_check, gifsicle_info, probe_media
from .models import Action, PresentationPlan, Solution
from .registry import get_plugin


def pre_reveal_neutrality_failure(plugin, renderer, scene, timeline) -> str | None:
    """Pre-reveal neutrality oracle.

    Every frame before ``reveal_start`` - frame 0, ``appearance``, the thinking
    midpoint and ``reveal_start - 1`` included, i.e. every index the public
    contact sheet can pick - must be pixel identical for the scene and for an
    alternate scene whose solution ordering was perturbed. The mirror image is
    also asserted: ``reveal_start`` itself must already depend on the solution,
    so that a boundary that slides in either direction is detected.
    """
    alternate = plugin.alternate_scene(scene)
    reveal = timeline["reveal_start"]
    for frame in range(reveal):
        if hashlib.sha256(renderer.render_frame(scene, frame)).digest() != hashlib.sha256(renderer.render_frame(alternate, frame)).digest():
            return f"pre-reveal frame depends on solution (frame {frame} of {reveal})"
    if hashlib.sha256(renderer.render_frame(scene, reveal)).digest() == hashlib.sha256(renderer.render_frame(alternate, reveal)).digest():
        return f"reveal boundary is late: frame {reveal} does not yet depend on the solution"
    return None


def validate_logical(problem, solution: Solution) -> list[str]:
    plugin = get_plugin(problem.puzzle_type)
    return plugin.validate_solution(problem, solution, plugin.rules)


# Calibration rounds that have been a declared timing standard. Historical
# pipes Target 5.0s remains listed so existing works validate unchanged.
ACCEPTED_TIMING_CALIBRATION_ROUNDS = (
    {
        "puzzle_type": "pipes", "band": "target",
        "thinking_time_seconds": 5.0,
        "previous": 3.5,
        "calibration_status": "calibrated-within-person-target-timing",
        "timing_status": "calibrated-within-person-target",
        "source_evaluation": "studies/pipes_target_blind_3_5s_evaluation_2026-08-21.json",
    },
    {
        # Historical lights/easy works used 8.0s. Keep accepting those bytes
        # while the current JSON authority remains 6.0s.
        "puzzle_type": "lights", "band": "easy",
        "thinking_time_seconds": 8.0,
        "previous": None,
        "calibration_status": "calibrated-within-person-timing-only",
        "timing_status": "calibrated-within-person-timing-only",
        "source_evaluation": "studies/timing_sweep_round2_calibration_2026-08-23.json",
    },
    {
        # Existing pipes/medium works used the previous 4.0s standard.
        "puzzle_type": "pipes", "band": "medium",
        "thinking_time_seconds": 4.0,
        "previous": 2.5,
        "calibration_status": "calibrated-within-person-timing-only",
        "timing_status": "calibrated-within-person-timing-only",
        "source_evaluation": "studies/timing_sweep_round3_calibration_2026-08-23.json",
    },
    *(
        {
            "puzzle_type": puzzle_type, "band": band,
            "thinking_time_seconds": seconds, "previous": previous,
            "calibration_status": status, "timing_status": timing_status,
            "source_evaluation": source,
        }
        for puzzle_type, band, seconds, previous, source, status, timing_status in (
            ("maze", "easy", 2.5, 2.5, "studies/timing_sweep_round3_calibration_2026-08-23.json", "calibrated-within-person-timing-only", "calibrated-within-person-timing-only"),
            ("maze", "medium", 2.5, 2.5, "studies/timing_sweep_round3_calibration_2026-08-23.json", "calibrated-within-person-timing-only", "calibrated-within-person-timing-only"),
            ("maze", "target", 3.5, 2.5, "studies/timing_sweep_round2_calibration_2026-08-23.json", "calibrated-within-person-target-timing", "calibrated-within-person-target"),
            ("pipes", "easy", 4.0, 2.5, "studies/timing_sweep_round3_calibration_2026-08-23.json", "calibrated-within-person-timing-only", "calibrated-within-person-timing-only"),
            ("pipes", "medium", 6.0, 4.0, "studies/timing_sweep_round3_calibration_2026-08-23.json", "calibrated-within-person-timing-only", "calibrated-within-person-timing-only"),
            ("pipes", "target", 8.0, 5.0, "studies/timing_sweep_round2_calibration_2026-08-23.json", "calibrated-within-person-target-timing", "calibrated-within-person-target"),
            *((kind, band, seconds, previous, source, "calibrated-within-person-timing-only", "calibrated-within-person-timing-only")
              for kind, band, seconds, previous, source in (
                  ("parking", "easy", 4.0, 2.5, "studies/timing_sweep_round3_calibration_2026-08-23.json"),
                  ("parking", "medium", 4.0, 2.5, "studies/timing_sweep_round3_calibration_2026-08-23.json"),
                  ("parking", "target", 8.0, 5.0, "studies/timing_sweep_round2_calibration_2026-08-23.json"),
                  ("packing", "easy", 4.0, 2.5, "studies/timing_sweep_round3_calibration_2026-08-23.json"),
                  ("packing", "medium", 4.0, 2.5, "studies/timing_sweep_round3_calibration_2026-08-23.json"),
                  ("packing", "target", 8.0, 5.0, "studies/timing_sweep_round2_calibration_2026-08-23.json"),
                  ("lights", "easy", 6.0, None, "studies/timing_sweep_round2_calibration_2026-08-23.json"),
                  ("lights", "medium", 8.0, None, "studies/timing_sweep_round2_calibration_2026-08-23.json"),
                  ("lights", "target", 8.0, 6.5, "studies/timing_sweep_round3_calibration_2026-08-23.json"),
                  ("fold", "easy", 4.0, 2.5, "studies/timing_sweep_round5_fold_calibration_2026-08-24.json"),
                  ("fold", "medium", 6.0, 4.0, "studies/timing_sweep_round5_fold_calibration_2026-08-24.json"),
                  ("fold", "target", 6.0, 4.0, "studies/timing_sweep_round5_fold_calibration_2026-08-24.json"),
              )),
            *(("lights", band, seconds, previous, "none-unvalidated-retiming-2026-09-02", "uncalibrated-standard-candidate", "candidate-pending-selection")
              for band, seconds, previous in (("easy", 4.0, 6.0), ("medium", 6.0, 8.0))),
        )
    ),
)


def timing_calibration_status_matches(
    calibration: dict, requested: float, puzzle_type: str | None, band: str | None,
) -> bool:
    """Accept current or historical declared timing records without rewriting history."""
    timing_status = calibration.get("timing_status")
    if timing_status in {
        "candidate-pending-selection",
        "calibrated-within-person-target",
        "calibrated-within-person-timing-only",
    }:
        return any(
            requested == round_["thinking_time_seconds"]
            and puzzle_type == round_["puzzle_type"]
            and band == round_["band"]
            and calibration.get("previous_evaluated_thinking_time_seconds") == round_["previous"]
            and calibration.get("target_standard_thinking_time_seconds") == round_["thinking_time_seconds"]
            and calibration.get("calibration_status") == round_["calibration_status"]
            and timing_status == round_["timing_status"]
            and calibration.get("source_evaluation") == round_["source_evaluation"]
            for round_ in ACCEPTED_TIMING_CALIBRATION_ROUNDS
        )
    if timing_status == "comparison-override-not-standard":
        standard = calibration.get("target_standard_thinking_time_seconds")
        return (
            isinstance(standard, (int, float))
            and not isinstance(standard, bool)
            and standard > 0
            and calibration.get("calibration_status") == "comparison-override-not-standard"
        )
    return False


ARTIFACT_FILENAMES = {"gif": "animation.gif", "mp4": "preview.mp4"}


def declared_formats(metadata: dict) -> tuple[str, ...]:
    """Delivery formats a work claims to contain, in canonical order.

    The source of truth is ``metadata.artifacts``: every work ever produced
    records one entry per encoded artifact there, so works made before
    ``--format`` existed report ``("gif", "mp4")`` without needing a new field.
    A work with no artifact entries at all falls back to the historical default
    so that the media checks are never silently skipped.
    """
    kinds = {item.get("kind") for item in metadata.get("artifacts", [])}
    present = tuple(kind for kind in DEFAULT_FORMATS if kind in kinds)
    return present or DEFAULT_FORMATS


def validate_instance(instance: Path, strict: bool = True) -> dict:
    problem_value = read_json(instance / "problem.json")
    plugin = get_plugin(problem_value["puzzle_type"])
    problem = plugin.problem_from_dict(problem_value)
    solution = Solution.from_dict(read_json(instance / "solution.json"))
    presentation = PresentationPlan.from_dict(read_json(instance / "presentation.json"))
    metadata = read_json(instance / "metadata.json")
    logical_failures = validate_logical(problem, solution)
    expected_frames = metadata["timeline"]["total_frames"]
    expected_fps = metadata["timeline"]["fps"]
    expected_duration = expected_frames / expected_fps
    checks_passed = []
    checks_failed = list(logical_failures)
    if not logical_failures:
        checks_passed.extend(["structure", "solution_replay", "action_legality", "goal", "final_state_hash"])
    metadata_checker = getattr(plugin, "metadata_contract_checks", None)
    if metadata_checker is not None:
        metadata_passed, metadata_failed = metadata_checker(problem, solution, metadata)
        checks_passed.extend(metadata_passed)
        checks_failed.extend(metadata_failed)
    timeline_pairs = {
        "appearance_frames": "appearance", "thinking_frames": "thinking",
        "anticipation_frames": "anticipation", "solve_frames": "solve",
        "result_frames": "result", "transition_frames": "transition",
        "total_frames": "total", "reveal_start_frame": "reveal_start",
    }
    if all(metadata["timeline"].get(meta_key) == presentation.timeline.get(plan_key) for meta_key, plan_key in timeline_pairs.items()):
        checks_passed.append("timeline_metadata")
    else:
        checks_failed.append("metadata timeline differs from presentation")
    calibration = metadata.get("timing_calibration")
    if calibration is not None:
        fixed = calibration.get("fixed_fields", {})
        fixed_expected = {
            "appearance_frames": presentation.timeline["appearance"],
            "anticipation_frames": presentation.timeline["anticipation"],
            "solve_frames": presentation.timeline["solve"],
            "result_frames": presentation.timeline["result"],
            "transition_frames": presentation.timeline["transition"],
            "fps": expected_fps,
        }
        requested = presentation.timeline["reveal_start"] / expected_fps
        common_calibration_ok = (
            calibration.get("baseline_thinking_time_seconds") == 2.5
            and calibration.get("thinking_time_seconds") == requested
            and calibration.get("changed_field") == "timeline.problem_to_reveal_seconds"
            and isinstance(calibration.get("structural_difficulty_status"), str)
            and calibration.get("structural_difficulty_status") != ""
            and fixed == fixed_expected
            and calibration.get("problem_sha256") == metadata.get("puzzle", {}).get("problem_sha256")
            and calibration.get("solution_sha256") == metadata.get("solution", {}).get("solution_sha256")
        )
        puzzle_type = metadata.get("puzzle", {}).get("type")
        band = metadata.get("difficulty", {}).get("accepted_band")
        status_ok = timing_calibration_status_matches(calibration, requested, puzzle_type, band)
        calibration_ok = common_calibration_ok and status_ok
        if calibration_ok:
            checks_passed.append("timing_calibration_metadata")
        else:
            checks_failed.append("timing calibration metadata differs from presentation or solution")
    rules = plugin.rules
    try:
        presentation_trace = plugin.replay(problem, presentation.logical_steps, rules)
        if not rules.is_goal(problem, presentation_trace.final):
            checks_failed.append("presentation does not reach goal")
        elif sha256_value(solution.to_dict()) != presentation.source_solution_hash:
            checks_failed.append("presentation source solution hash mismatch")
        else:
            checks_passed.append("presentation_replay")
        renderer = plugin.renderer_factory()
        scene = plugin.scene_builder.build(problem, presentation, presentation_trace)
        neutrality_failure = pre_reveal_neutrality_failure(plugin, renderer, scene, presentation.timeline)
        if neutrality_failure is not None:
            checks_failed.append(neutrality_failure)
        else:
            checks_passed.append("pre_reveal_neutrality")
        motion_failed = False
        for frame in range(presentation.timeline["reveal_start"], presentation.timeline["solve_end"] - 4, 4):
            if renderer.render_frame(scene, frame) == renderer.render_frame(scene, frame + 4):
                motion_failed = True
                break
        if motion_failed:
            checks_failed.append("solve has a meaningless static interval over 200ms")
        else:
            checks_passed.append("solve_animation")
        safe = (36, 36, 684, 684)
        bounds = scene.semantic_bounds
        if not (safe[0] <= bounds[0] and safe[1] <= bounds[1] and bounds[2] <= safe[2] and bounds[3] <= safe[3]):
            checks_failed.append("semantic bounds exceed safe area")
        else:
            checks_passed.append("safe_area")
        plugin_passed, plugin_failed = plugin.render_contract_checks(scene, renderer)
        checks_passed.extend(plugin_passed)
        checks_failed.extend(plugin_failed)
        first = renderer.render_frame(scene, 0)
        last = renderer.render_frame(scene, presentation.timeline["total"] - 1)
        if len(set(first)) <= 3 or len(set(last)) <= 3:
            checks_failed.append("first or last frame is blank")
        else:
            checks_passed.append("nonblank_endpoints")
    except Exception as error:
        checks_failed.append(f"presentation/render contract: {error}")
    probes = {}
    formats = declared_formats(metadata)
    for kind in formats:
        path = instance / ARTIFACT_FILENAMES[kind]
        try:
            decode_check(path)
            probe = probe_media(path)
            probes[kind] = probe
            if (probe["width"], probe["height"]) != (720, 720):
                checks_failed.append(f"{kind}: wrong dimensions")
            if probe["frames"] != expected_frames:
                checks_failed.append(f"{kind}: expected {expected_frames} frames, got {probe['frames']}")
            if abs(probe["fps"] - expected_fps) > 0.01:
                checks_failed.append(f"{kind}: wrong fps {probe['fps']}")
            if abs(probe["duration_ms"] / 1000 - expected_duration) > 1 / expected_fps + 0.001:
                checks_failed.append(f"{kind}: duration mismatch")
            if probe["bytes"] <= 0:
                checks_failed.append(f"{kind}: empty artifact")
            checks_passed.append(f"{kind}_decode")
        except Exception as error:
            checks_failed.append(f"{kind}: {error}")
    if "gif" in probes:
        info = gifsicle_info(instance / "animation.gif")
        if "loop forever" not in info.lower():
            checks_failed.append("gif: loop metadata is not infinite")
        if "transparent" in info.lower():
            checks_failed.append("gif: transparency is not allowed")
        if "local color table" in info.lower():
            checks_failed.append("gif: local color tables are not allowed")
        palette_match = re.search(r"global color table \[(\d+)\]", info)
        gif_metadata = next((item for item in metadata.get("artifacts", []) if item.get("kind") == "gif"), None)
        if not palette_match or gif_metadata is None or gif_metadata.get("colors") != int(palette_match.group(1)):
            checks_failed.append("gif: metadata palette size mismatch")
        delays = [float(value) for value in re.findall(r"delay ([0-9.]+)s", info)]
        if not delays or any(value <= 0 for value in delays):
            checks_failed.append("gif: invalid frame delay")
        if probes["gif"]["bytes"] > 8 * 1024 * 1024:
            checks_failed.append("gif: exceeds 8 MiB")
        if probes["gif"]["frames"] > 1:
            checks_passed.extend(["gif_animation", "gif_loop", "gif_frame_delays", "gif_global_palette", "gif_opaque", "gif_size"])
    if "mp4" in probes:
        if probes["mp4"]["codec"] != "h264" or probes["mp4"]["pixel_format"] != "yuv420p":
            checks_failed.append("mp4: wrong codec or pixel format")
        else:
            checks_passed.append("mp4_h264_yuv420p")
    for required in ("presentation.json", "contact_sheet.png", "validation.json"):
        if not (instance / required).exists():
            checks_failed.append(f"missing {required}")
    # contact_sheet_full.png and keyframes_full/ are review-only artifacts that
    # exist only for instances rendered after the public/review split. They are
    # deliberately NOT required here so pre-split outputs keep validating.
    expected_keyframes = metadata.get("reproducibility", {}).get("keyframe_sha256", {})
    for name, expected_hash in expected_keyframes.items():
        # Pre-reveal keyframes live in keyframes/; reveal-onward ones in
        # keyframes_full/. Instances produced before that split keep everything
        # in keyframes/, so both locations are accepted.
        candidates = [instance / "keyframes" / f"{name}.png", instance / "keyframes_full" / f"{name}.png"]
        path = next((item for item in candidates if item.exists()), candidates[0])
        if not path.exists() or sha256_file(path) != expected_hash:
            checks_failed.append(f"keyframe hash mismatch: {name}")
    if expected_keyframes and not any(message.startswith("keyframe hash mismatch") for message in checks_failed):
        checks_passed.append("keyframe_hashes")
    for item in metadata.get("artifacts", []):
        if item["kind"] in probes and item["sha256"] != probes[item["kind"]]["sha256"]:
            checks_failed.append(f"{item['kind']}: metadata sha mismatch")
    report = {
        "schema_version": "1.0.0", "status": "passed" if not checks_failed else "failed",
        "checks_passed": sorted(set(checks_passed)), "checks_failed": checks_failed, "media_probes": probes,
        "visual_contract": plugin.visual_contract(scene, renderer) if "scene" in locals() and "renderer" in locals() else {},
    }
    if strict and checks_failed:
        raise ValueError("validation failed: " + "; ".join(checks_failed))
    return report


def load_action(value: dict) -> Action:
    return Action.from_dict(value)
