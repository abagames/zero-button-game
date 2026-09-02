from __future__ import annotations

from dataclasses import dataclass

from .core import StableRng
from .lights import (
    LIGHTS_EQUIVALENCE_VERSION, LightsPuzzleSpec, LightsRules, LightsSolver, generate_lights,
    lights_difficulty_preset, lights_difficulty_report, lights_quality_rejection, plus_cells, replay_lights,
    validate_lights_solution,
)
from .fold import (
    FOLD_EQUIVALENCE_VERSION, FoldPuzzleSpec, FoldRules, FoldSolver, action_fold, fold_difficulty_preset,
    fold_difficulty_report, fold_quality_rejection, generate_fold, replay_fold, validate_fold_solution,
)
from .fold_presentation import fold_plan
from .fold_render import (
    CELL_PX as FOLD_CELL_PX, MINI_CELL_PX as FOLD_MINI_CELL_PX, MIN_CELL_PX as FOLD_MIN_CELL_PX,
    MIN_MINI_CELL_PX as FOLD_MIN_MINI_CELL_PX, FoldRenderer, FoldScene, FoldSceneBuilder, alternate_fold_scene,
)
from .lights_presentation import lights_plan
from .lights_render import (
    CELL_PX, MINI_CELL_PX, LightsRenderer, LightsScene, LightsSceneBuilder, alternate_lights_scene,
)
from .maze import MazeRules, MazeSolver, difficulty_preset, difficulty_report, generate_maze, quality_rejection, replay, validate_maze_solution
from .models import PuzzleSpec, Solution, TimelineSpec
from .mosaic import (
    MOSAIC_EQUIVALENCE_VERSION, MosaicPuzzleSpec, MosaicRules, MosaicSolver,
    action_signature as mosaic_action_signature, generate_mosaic, mosaic_difficulty_preset,
    mosaic_difficulty_report, mosaic_quality_rejection, replay_mosaic,
    validate_mosaic_solution,
)
from .mosaic_presentation import mosaic_plan
from .mosaic_render import (
    BOARD_PX as MOSAIC_BOARD_PX, CELL_PX as MOSAIC_CELL_PX,
    MIN_CELL_PX as MOSAIC_MIN_CELL_PX, MIN_STROKE_PX as MOSAIC_MIN_STROKE_PX,
    MosaicRenderer, MosaicScene, MosaicSceneBuilder, alternate_mosaic_scene,
)
from .pipes import (
    PIPE_EQUIVALENCE_VERSION, PipePuzzleSpec, PipeRules, PipeSolver, PipeState, generate_pipes, pipe_difficulty_preset,
    pipe_difficulty_report, pipe_quality_rejection, trace_pipes, validate_pipe_solution,
)
from .parking import (
    PARKING_EQUIVALENCE_VERSION, ParkingPuzzleSpec, ParkingRules, ParkingSolver, generate_parking,
    parking_difficulty_preset, parking_difficulty_report, parking_quality_rejection, replay_parking,
    validate_parking_solution,
)
from .packing import (
    MAX_TRAY_WIDTH_CELLS, PACKING_EQUIVALENCE_VERSION, PackingPuzzleSpec, PackingRules, PackingSolver,
    generate_packing, packing_difficulty_preset, packing_difficulty_report, packing_quality_rejection,
    replay_packing, shape_bbox, validate_packing_solution,
)
from .packing_presentation import packing_plan
from .packing_render import (
    HOLE_CELL_PX, PIECE_MARGIN_HOLE, PIECE_MARGIN_TRAY, TRAY_CELL_PX, PackingRenderer, PackingScene,
    PackingSceneBuilder, alternate_packing_scene,
)
from .parking_presentation import parking_plan
from .parking_render import ParkingRenderer, ParkingScene, ParkingSceneBuilder, alternate_parking_scene
from .pipes_presentation import pipe_plan
from .pipes_render import PipeRenderer, PipeScene, PipeSceneBuilder, alternate_pipe_scene
from .presentation import direct_plan
from .render import MazeScene, MazeSceneBuilder, RasterRenderer


GENERIC_TIMELINE_PRESET_LABEL = "standard-adaptive-solve-timing-calibration"


def _seconds_text(seconds: float) -> str:
    value = float(seconds)
    return str(int(value)) if value == int(value) else str(value)


def standard_timeline_preset_label(puzzle_type: str, preset: dict, band: str, seconds: float, overridden: bool) -> str:
    """Plugin-specific timeline label when the work runs at the band's own standard.

    The standard second count is read from the difficulty preset itself instead
    of being written again here, so the branch cannot silently die when a
    preset is re-timed. Anything else - an explicit ``--thinking-time`` override
    or a band with no declared standard - keeps the generic label.
    """
    if overridden:
        return GENERIC_TIMELINE_PRESET_LABEL
    standard = preset.get("thinking_time_seconds")
    if standard is None or float(standard) != float(seconds):
        return GENERIC_TIMELINE_PRESET_LABEL
    return f"{puzzle_type}-{band}-standard-{_seconds_text(standard)}s-v1"


def _timing_profile(
    puzzle_type: str, band: str, *, standard: float, previous: float | None,
    source: str, structural_status: str, target_timing: bool = False,
) -> dict:
    """Build a profile from one of the committed single-evaluator sweeps."""
    status = "calibrated-within-person-target-timing" if target_timing else "calibrated-within-person-timing-only"
    timing_status = "calibrated-within-person-target" if target_timing else "calibrated-within-person-timing-only"
    previous_text = "no earlier selected standard" if previous is None else f"{_seconds_text(previous)}s previously evaluated"
    return {
        "variant": f"{puzzle_type}-{band}-standard-{_seconds_text(standard)}s",
        "baseline_thinking_time_seconds": 2.5,
        "previous_evaluated_thinking_time_seconds": previous,
        "standard_thinking_time_seconds": standard,
        "calibration_change": f"{previous_text} -> {_seconds_text(standard)}s adopted by the recorded sweep",
        "calibration_status": status,
        "source_evaluation": source,
        "calibration_scope": f"single-evaluator within-person {puzzle_type} {band}; presentation timing only",
        "structural_difficulty_status": structural_status,
        "timing_status": timing_status,
    }



@dataclass(frozen=True)
class MazePlugin:
    puzzle_type: str = "maze"
    plugin_version: str = "1.1.0"
    rules = MazeRules()
    solver = MazeSolver(rules)
    scene_builder = MazeSceneBuilder()
    solver_reject_codes = frozenset({"UNSOLVABLE", "SOLVE_BUDGET_EXCEEDED"})

    @staticmethod
    def timeline_preset_label(band: str, seconds: float, overridden: bool) -> str:
        return standard_timeline_preset_label("maze", difficulty_preset(band), band, seconds, overridden)

    @staticmethod
    def timing_calibration_profile(band: str) -> dict | None:
        previous_values = {"easy": 2.5, "medium": 2.5, "target": 2.5}
        if band not in previous_values:
            return None
        standard = float(difficulty_preset(band)["thinking_time_seconds"])
        previous = previous_values[band]
        source = "studies/timing_sweep_round2_calibration_2026-08-23.json" if band == "target" else "studies/timing_sweep_round3_calibration_2026-08-23.json"
        return _timing_profile("maze", band, standard=standard, previous=previous, source=source,
                               structural_status=MazePlugin.calibration_label(band), target_timing=band == "target")

    @staticmethod
    def candidate_rejection_reason(preset: dict, error: Exception) -> str | None:
        # Mixed endpoint selection may legitimately fail for a given seed; it is
        # a candidate rejection, not a generator defect.
        return "ENDPOINT_SELECTION_FAILED" if preset.get("endpoint_profile") == "mixed" else None

    @staticmethod
    def difficulty_preset(band: str) -> dict:
        return difficulty_preset(band)

    @staticmethod
    def generate_candidate(rng: StableRng, preset: dict) -> PuzzleSpec:
        return generate_maze(
            rng, preset.get("width", 7), preset.get("height", 7),
            preset.get("endpoint_profile", "corners"),
        )

    problem_from_dict = staticmethod(PuzzleSpec.from_dict)
    difficulty = staticmethod(difficulty_report)
    quality_filter = staticmethod(quality_rejection)
    replay = staticmethod(replay)
    validate_solution = staticmethod(validate_maze_solution)
    presentation = staticmethod(direct_plan)

    @staticmethod
    def animation_units(solution: Solution) -> int:
        return len(solution.actions)

    @staticmethod
    def renderer_factory() -> RasterRenderer:
        return RasterRenderer()

    @staticmethod
    def alternate_scene(scene: MazeScene) -> MazeScene:
        return MazeScene(scene.puzzle, scene.plan, tuple(reversed(scene.path)), scene.semantic_bounds)

    @staticmethod
    def visual_contract(scene: MazeScene, renderer: RasterRenderer) -> dict:
        return {
            "semantic_bounds":list(scene.semantic_bounds), "safe_area":[36,36,684,684],
            "minimum_wall_px":5, "minimum_path_px":7,
            "cell_size_px":round(renderer.board_size / scene.puzzle.width, 2),
            "minimum_cell_size_px":54,
        }

    @staticmethod
    def render_contract_checks(scene: MazeScene, renderer: RasterRenderer) -> tuple[list[str], list[str]]:
        cell_size = renderer.board_size / scene.puzzle.width
        return (["minimum_cell_size"], []) if cell_size >= 54 else ([], [f"maze cell size is below readable minimum: {cell_size:.1f}px"])

    @staticmethod
    def calibration_label(band: str) -> str:
        if band in {"easy", "medium"}:
            return "uncalibrated-too-easy"
        return "calibrated-within-person-target"


@dataclass(frozen=True)
class PipesPlugin:
    puzzle_type: str = "pipes"
    plugin_version: str = "1.2.0"
    rules = PipeRules()
    solver = PipeSolver(rules)
    scene_builder = PipeSceneBuilder()
    solver_reject_codes = frozenset({
        "UNSOLVABLE", "SOLVE_BUDGET_EXCEEDED", "MULTIPLE_MINIMAL_PATHS", "MULTIPLE_MINIMAL_SIGNATURES",
    })

    @staticmethod
    def timeline_preset_label(band: str, seconds: float, overridden: bool) -> str:
        return standard_timeline_preset_label("pipes", pipe_difficulty_preset(band), band, seconds, overridden)

    @staticmethod
    def timing_calibration_profile(band: str) -> dict | None:
        previous_values = {"easy": 2.5, "medium": 4.0, "target": 5.0}
        if band not in previous_values:
            return None
        standard = float(pipe_difficulty_preset(band)["thinking_time_seconds"])
        previous = previous_values[band]
        source = "studies/timing_sweep_round2_calibration_2026-08-23.json" if band == "target" else "studies/timing_sweep_round3_calibration_2026-08-23.json"
        return _timing_profile("pipes", band, standard=standard, previous=previous, source=source,
                               structural_status=PipesPlugin.calibration_label(band), target_timing=band == "target")

    difficulty_preset = staticmethod(pipe_difficulty_preset)

    @staticmethod
    def generate_candidate(rng: StableRng, preset: dict) -> PipePuzzleSpec:
        return generate_pipes(rng, preset["width"], preset["height"])

    problem_from_dict = staticmethod(PipePuzzleSpec.from_dict)
    difficulty = staticmethod(pipe_difficulty_report)
    quality_filter = staticmethod(pipe_quality_rejection)
    replay = staticmethod(trace_pipes)
    validate_solution = staticmethod(validate_pipe_solution)
    presentation = staticmethod(pipe_plan)

    @staticmethod
    def animation_units(solution: Solution) -> int:
        return sum(abs(action.params["quarter_turns"][0]) for action in solution.actions)

    @staticmethod
    def renderer_factory() -> PipeRenderer:
        return PipeRenderer()

    alternate_scene = staticmethod(alternate_pipe_scene)

    @staticmethod
    def metadata_contract_checks(puzzle: PipePuzzleSpec, solution: Solution, metadata: dict) -> tuple[list[str], list[str]]:
        if puzzle.ruleset != "source-to-goal-unique-v3":
            return [], []
        analysis = PipesPlugin.solver.analyze_minimum_solutions(puzzle)
        expected = analysis["signatures"][0] if analysis["normalized_solution_count"] == 1 else None
        recorded = metadata.get("solution", {}).get("uniqueness", {})
        ok = (
            expected is not None
            and recorded.get("status") == "unique"
            and recorded.get("normalized_solution_count") == 1
            and recorded.get("unique_path_count") == 1
            and recorded.get("normalized_signature_hash") == expected["normalized_signature_hash"]
            and recorded.get("path_identity_hash") == expected["path_identity_hash"]
            and recorded.get("equivalence_policy_version") == PIPE_EQUIVALENCE_VERSION
            and solution.answer_equivalence_key == "unique:" + expected["normalized_signature_hash"]
        )
        return (["uniqueness_metadata"], []) if ok else ([], ["metadata uniqueness evidence differs from solver oracle"])

    @staticmethod
    def visual_contract(scene: PipeScene, renderer: PipeRenderer) -> dict:
        return {
            "semantic_bounds":list(scene.semantic_bounds), "safe_area":[36,36,684,684],
            "minimum_connector_px":16, "minimum_flow_inner_px":5,
            "cell_size_px":round(renderer.board_size / scene.puzzle.width, 2),
            "minimum_cell_size_px":96, "state_change_not_color_only":True,
        }

    @staticmethod
    def render_contract_checks(scene: PipeScene, renderer: PipeRenderer) -> tuple[list[str], list[str]]:
        passed = []
        failed = []
        if PipesPlugin.rules.is_goal(scene.puzzle, scene.trace.final):
            passed.append("source_goal_connection")
        else:
            failed.append("solved pipes do not connect START to GOAL")
        cell_size = renderer.board_size / scene.puzzle.width
        if cell_size >= 96:
            passed.append("minimum_cell_size")
        else:
            failed.append(f"pipes cell size is below readable minimum: {cell_size:.1f}px")
        cumulative = 0.0
        rotation_mapping_ok = True
        for step in scene.trace.steps:
            turns = step.action.params["quarter_turns"][0]
            duration = abs(turns)
            cell = step.action.params["cell"]
            index = cell[1] * scene.puzzle.width + cell[0]
            middle = renderer.rotation_snapshot_for_units(scene, cumulative + duration / 2)
            expected_sign = 1 if turns > 0 else -1
            if (
                middle["current_piece"] != index
                or middle["current_delta_degrees"] * expected_sign <= 0
                or abs(middle["current_delta_degrees"]) >= duration * 90
            ):
                rotation_mapping_ok = False
            cumulative += duration
            boundary = renderer.rotation_snapshot_for_units(scene, cumulative)
            if boundary["rotations"] != step.after.rotations:
                rotation_mapping_ok = False
        (passed if rotation_mapping_ok else failed).append(
            "rotation_action_rendering" if rotation_mapping_ok else "rendered rotation does not match rotate_piece actions"
        )
        before_flow = renderer.semantic_snapshot(scene, scene.plan.timeline["solve_end"] - 1)
        flow_start = renderer.semantic_snapshot(scene, scene.plan.timeline["solve_end"])
        flow_end = renderer.semantic_snapshot(scene, scene.plan.timeline["result_end"] - 1)
        flow_timing_ok = not before_flow["flow_reached"] and bool(flow_start["flow_reached"])
        (passed if flow_timing_ok else failed).append(
            "flow_after_connection" if flow_timing_ok else "flow starts before the final connection or fails to start"
        )
        flow_goal_ok = (
            flow_end["flow_goal_reached"]
            and tuple(flow_end["flow_reached"]) == scene.flow_order
            and scene.flow_order == PipesPlugin.rules.connected_path(scene.puzzle, scene.trace.final)
        )
        (passed if flow_goal_ok else failed).append(
            "flow_goal_reached" if flow_goal_ok else "flow front does not stay on the canonical START-to-GOAL path"
        )
        action_minimality_ok = True
        for step in scene.trace.steps:
            rotations = list(scene.trace.final.rotations)
            cell = tuple(step.action.params["cell"])
            rotations[cell[1] * scene.puzzle.width + cell[0]] = 0
            if PipesPlugin.rules.is_goal(scene.puzzle, PipeState("pipes", 0, tuple(rotations))):
                action_minimality_ok = False
        (passed if action_minimality_ok else failed).append(
            "goal_action_minimality" if action_minimality_ok else "solution contains a GOAL-irrelevant rotation"
        )
        if scene.puzzle.ruleset == "source-to-goal-unique-v3":
            uniqueness = PipesPlugin.solver.analyze_minimum_solutions(scene.puzzle)
            unique_ok = uniqueness["normalized_solution_count"] == 1 and uniqueness["unique_path_count"] == 1
            (passed if unique_ok else failed).append(
                "normalized_solution_unique" if unique_ok else "normalized minimum solution or path is not unique"
            )
            emitted = PipesPlugin.solver.normalized_action_evidence(scene.puzzle, tuple(step.action for step in scene.trace.steps))
            signature_ok = (
                unique_ok and len(emitted["signatures"]) == 1
                and emitted["signatures"][0]["normalized_signature_hash"]
                == uniqueness["signatures"][0]["normalized_signature_hash"]
                and not emitted["has_redundant_turns"] and not emitted["has_duplicate_panels"]
            )
            (passed if signature_ok else failed).append(
                "emitted_signature_canonical" if signature_ok else "emitted actions differ from canonical unique signature"
            )
        flow_cues = [cue for cue in scene.plan.visual_cues if cue.get("kind") == "network_flow"]
        flow_state_ok = len(flow_cues) == 1 and flow_cues[0].get("state_mutation") is False
        (passed if flow_state_ok else failed).append(
            "flow_state_immutable" if flow_state_ok else "flow cue is not explicitly state-neutral"
        )
        return passed, failed

    @staticmethod
    def calibration_label(band: str) -> str:
        return "uncalibrated-pipes-unique-v3"


@dataclass(frozen=True)
class ParkingPlugin:
    puzzle_type: str = "parking"
    plugin_version: str = "1.0.0"
    rules = ParkingRules()
    solver = ParkingSolver(rules)
    scene_builder = ParkingSceneBuilder()
    solver_reject_codes = frozenset({"UNSOLVABLE", "SOLVE_BUDGET_EXCEEDED", "MULTIPLE_MINIMAL_PATHS"})

    @staticmethod
    def timeline_preset_label(band: str, seconds: float, overridden: bool) -> str:
        return standard_timeline_preset_label("parking", parking_difficulty_preset(band), band, seconds, overridden)

    @staticmethod
    def timing_calibration_profile(band: str) -> dict | None:
        previous_values = {"easy": 2.5, "medium": 2.5, "target": 5.0}
        if band not in previous_values:
            return None
        standard = float(parking_difficulty_preset(band)["thinking_time_seconds"])
        previous = previous_values[band]
        source = "studies/timing_sweep_round2_calibration_2026-08-23.json" if band == "target" else "studies/timing_sweep_round3_calibration_2026-08-23.json"
        return _timing_profile("parking", band, standard=standard, previous=previous, source=source,
                               structural_status=ParkingPlugin.calibration_label(band))

    difficulty_preset = staticmethod(parking_difficulty_preset)

    @staticmethod
    def generate_candidate(rng: StableRng, preset: dict) -> ParkingPuzzleSpec:
        return generate_parking(rng, preset)

    problem_from_dict = staticmethod(ParkingPuzzleSpec.from_dict)
    difficulty = staticmethod(parking_difficulty_report)
    quality_filter = staticmethod(parking_quality_rejection)
    replay = staticmethod(replay_parking)
    validate_solution = staticmethod(validate_parking_solution)
    presentation = staticmethod(parking_plan)

    @staticmethod
    def animation_units(solution: Solution) -> int:
        return sum(action.params["slide_cells"][0] for action in solution.actions)

    @staticmethod
    def renderer_factory() -> ParkingRenderer:
        return ParkingRenderer()

    alternate_scene = staticmethod(alternate_parking_scene)

    @staticmethod
    def metadata_contract_checks(puzzle: ParkingPuzzleSpec, solution: Solution, metadata: dict) -> tuple[list[str], list[str]]:
        recorded = metadata.get("difficulty", {}).get("solution_uniqueness", {})
        ok = (
            recorded.get("status") == "unique"
            and recorded.get("minimal_path_count") == 1
            and recorded.get("minimum_moves") == len(solution.actions)
            and recorded.get("equivalence_policy_version") == PARKING_EQUIVALENCE_VERSION
            and solution.answer_equivalence_key == "unique:" + str(recorded.get("normalized_signature_hash"))
        )
        return (["uniqueness_metadata"], []) if ok else ([], ["metadata uniqueness evidence differs from solver oracle"])

    @staticmethod
    def visual_contract(scene: ParkingScene, renderer: ParkingRenderer) -> dict:
        cell = renderer.cell_size(scene)
        return {
            "semantic_bounds":list(scene.semantic_bounds), "safe_area":[36,36,684,684],
            "cell_size_px":round(cell, 2), "minimum_cell_size_px":72,
            "minimum_vehicle_body_px":48, "vehicle_body_px":round(cell - 2 * 9, 2),
            "minimum_exit_gap_px":24, "exit_gap_px":round(cell - 8, 2),
            "state_change_not_color_only":True,
        }

    @staticmethod
    def render_contract_checks(scene: ParkingScene, renderer: ParkingRenderer) -> tuple[list[str], list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        rules = ParkingPlugin.rules
        if rules.is_goal(scene.puzzle, scene.trace.final):
            passed.append("target_vehicle_released")
        else:
            failed.append("solved parking board does not release the target vehicle")
        cell = renderer.cell_size(scene)
        if cell >= 72:
            passed.append("minimum_cell_size")
        else:
            failed.append(f"parking cell size is below readable minimum: {cell:.1f}px")
        if cell - 2 * 9 >= 48:
            passed.append("minimum_vehicle_body")
        else:
            failed.append("vehicle body is thinner than the readable minimum")
        if cell - 8 >= 24:
            passed.append("minimum_exit_gap")
        else:
            failed.append("east exit gap is narrower than the readable minimum")
        cumulative = 0.0
        mapping_ok = True
        for step in scene.trace.steps:
            delta = step.action.params["delta"][0]
            span = abs(delta)
            index = scene.puzzle.index_of(step.action.params["vehicle"][0])
            middle = renderer.position_snapshot_for_units(scene, cumulative + span / 2)
            travelled = middle["offsets"][index] - _static_offset(scene, step.before, index)
            if middle["moving_vehicle"] != index or travelled * (1 if delta > 0 else -1) <= 0 or abs(travelled) >= span:
                mapping_ok = False
            cumulative += span
            boundary = renderer.position_snapshot_for_units(scene, cumulative)
            if tuple(round(value) for value in boundary["offsets"]) != step.after.positions:
                mapping_ok = False
        (passed if mapping_ok else failed).append(
            "slide_action_rendering" if mapping_ok else "rendered slide does not match move_piece actions"
        )
        before = renderer.semantic_snapshot(scene, scene.plan.timeline["solve_end"] - 1)
        after = renderer.semantic_snapshot(scene, scene.plan.timeline["solve_end"])
        release_ok = not before["released"] and after["released"]
        (passed if release_ok else failed).append(
            "release_after_last_slide" if release_ok else "exit release does not follow the last slide"
        )
        analysis = ParkingPlugin.solver.analyze(scene.puzzle)
        unique_ok = analysis["status"] == "unique" and analysis["minimum_moves"] == len(scene.moves)
        (passed if unique_ok else failed).append(
            "normalized_solution_unique" if unique_ok else "normalized minimal move sequence is not unique"
        )
        cues = [cue for cue in scene.plan.visual_cues if cue.get("kind") == "exit_release"]
        cue_ok = len(cues) == 1 and cues[0].get("state_mutation") is False
        (passed if cue_ok else failed).append(
            "release_state_immutable" if cue_ok else "exit release cue is not explicitly state-neutral"
        )
        return passed, failed

    @staticmethod
    def calibration_label(band: str) -> str:
        return "uncalibrated-parking-v1"


@dataclass(frozen=True)
class PackingPlugin:
    puzzle_type: str = "packing"
    plugin_version: str = "1.0.0"
    rules = PackingRules()
    solver = PackingSolver(rules)
    scene_builder = PackingSceneBuilder()
    solver_reject_codes = frozenset({"UNSOLVABLE", "SOLVE_BUDGET_EXCEEDED", "MULTIPLE_COVERS"})

    @staticmethod
    def timeline_preset_label(band: str, seconds: float, overridden: bool) -> str:
        return standard_timeline_preset_label("packing", packing_difficulty_preset(band), band, seconds, overridden)

    @staticmethod
    def timing_calibration_profile(band: str) -> dict | None:
        previous_values = {"easy": 2.5, "medium": 2.5, "target": 5.0}
        if band not in previous_values:
            return None
        standard = float(packing_difficulty_preset(band)["thinking_time_seconds"])
        previous = previous_values[band]
        source = "studies/timing_sweep_round2_calibration_2026-08-23.json" if band == "target" else "studies/timing_sweep_round3_calibration_2026-08-23.json"
        return _timing_profile("packing", band, standard=standard, previous=previous, source=source,
                               structural_status=PackingPlugin.calibration_label(band))

    difficulty_preset = staticmethod(packing_difficulty_preset)

    @staticmethod
    def generate_candidate(rng: StableRng, preset: dict) -> PackingPuzzleSpec:
        return generate_packing(rng, preset)

    problem_from_dict = staticmethod(PackingPuzzleSpec.from_dict)
    difficulty = staticmethod(packing_difficulty_report)
    quality_filter = staticmethod(packing_quality_rejection)
    replay = staticmethod(replay_packing)
    validate_solution = staticmethod(validate_packing_solution)
    presentation = staticmethod(packing_plan)

    @staticmethod
    def animation_units(solution: Solution) -> int:
        return len(solution.actions)

    @staticmethod
    def renderer_factory() -> PackingRenderer:
        return PackingRenderer()

    alternate_scene = staticmethod(alternate_packing_scene)

    @staticmethod
    def candidate_rejection_reason(preset: dict, error: Exception) -> str | None:
        # A random tiling can legitimately fail to place the requested pieces.
        return "PACKING_TILING_FAILED" if str(error).startswith("PACKING_") else None

    @staticmethod
    def metadata_contract_checks(puzzle: PackingPuzzleSpec, solution: Solution, metadata: dict) -> tuple[list[str], list[str]]:
        recorded = metadata.get("difficulty", {}).get("solution_uniqueness", {})
        ok = (
            recorded.get("status") == "unique"
            and recorded.get("cover_count") == 1
            and recorded.get("equivalence_policy_version") == PACKING_EQUIVALENCE_VERSION
            and solution.answer_equivalence_key == "unique:" + str(recorded.get("normalized_signature_hash"))
        )
        return (["uniqueness_metadata"], []) if ok else ([], ["metadata uniqueness evidence differs from solver oracle"])

    @staticmethod
    def visual_contract(scene: PackingScene, renderer: PackingRenderer) -> dict:
        tray = renderer.tray_extent(scene)
        return {
            "semantic_bounds":list(scene.semantic_bounds), "safe_area":[36,36,684,684],
            "hole_cell_px":HOLE_CELL_PX, "minimum_hole_cell_px":96,
            "tray_cell_px":TRAY_CELL_PX, "minimum_tray_cell_px":54,
            "hole_piece_body_px":HOLE_CELL_PX - 2 * PIECE_MARGIN_HOLE,
            "tray_piece_body_px":TRAY_CELL_PX - 2 * PIECE_MARGIN_TRAY,
            "minimum_piece_body_px":48,
            "tray_extent_px":[round(value, 2) for value in tray],
            "state_change_not_color_only":True,
        }

    @staticmethod
    def render_contract_checks(scene: PackingScene, renderer: PackingRenderer) -> tuple[list[str], list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        rules = PackingPlugin.rules
        if rules.is_goal(scene.puzzle, scene.trace.final):
            passed.append("exact_cover_complete")
        else:
            failed.append("solved packing board does not cover the hole exactly")
        if HOLE_CELL_PX >= 96:
            passed.append("minimum_hole_cell_size")
        else:
            failed.append(f"packing hole cell is below readable minimum: {HOLE_CELL_PX}px")
        if TRAY_CELL_PX >= 54:
            passed.append("minimum_tray_cell_size")
        else:
            failed.append(f"packing tray cell is below readable minimum: {TRAY_CELL_PX}px")
        if TRAY_CELL_PX - 2 * PIECE_MARGIN_TRAY >= 48:
            passed.append("minimum_piece_body")
        else:
            failed.append("tray piece body is thinner than the readable minimum")
        # The tray is laid out from tray_slots, so its real extent is checked
        # against the safe area rather than assumed from the preset.
        left, top, right, bottom = renderer.tray_extent(scene)
        hole_x, hole_y = renderer.hole_origin(scene)
        hole_right = hole_x + scene.puzzle.width * HOLE_CELL_PX
        hole_bottom = hole_y + scene.puzzle.height * HOLE_CELL_PX
        safe = (36, 36, 684, 684)
        layout_ok = (
            safe[0] <= left and right <= safe[2] and bottom <= safe[3]
            and safe[0] <= hole_x and hole_right <= safe[2]
            and safe[1] <= hole_y and hole_bottom <= top
            and sum(shape_bbox(shape)[0] for _, shape in scene.puzzle.pieces) <= MAX_TRAY_WIDTH_CELLS
        )
        (passed if layout_ok else failed).append(
            "hole_and_tray_within_safe_area" if layout_ok else "hole or tray leaves the safe area"
        )
        mapping_ok = True
        for order, (piece_index, anchor) in enumerate(scene.moves):
            middle = renderer.placement_snapshot_for_units(scene, order + 0.5)
            if (
                middle["moving_index"] != order or middle["moving_piece"] != piece_index
                or not 0.0 < middle["progress"] < 1.0
            ):
                mapping_ok = False
            boundary = renderer.placement_snapshot_for_units(scene, float(order + 1))
            if boundary["seated"] != order + 1:
                mapping_ok = False
            step = scene.trace.steps[order]
            if (step.action.params["piece"][0], tuple(step.action.params["to"])) != (
                scene.puzzle.pieces[piece_index][0], anchor
            ):
                mapping_ok = False
        (passed if mapping_ok else failed).append(
            "placement_action_rendering" if mapping_ok else "rendered placement does not match move_piece actions"
        )
        before = renderer.semantic_snapshot(scene, scene.plan.timeline["solve_end"] - 1)
        after = renderer.semantic_snapshot(scene, scene.plan.timeline["solve_end"])
        fill_ok = not before["filled"] and after["filled"] and after["seated"] == len(scene.moves)
        (passed if fill_ok else failed).append(
            "fill_after_last_placement" if fill_ok else "hole fill does not follow the last placement"
        )
        analysis = PackingPlugin.solver.analyze(scene.puzzle)
        unique_ok = analysis["status"] == "unique"
        (passed if unique_ok else failed).append(
            "unique_exact_cover" if unique_ok else "exact cover is not unique"
        )
        cues = [cue for cue in scene.plan.visual_cues if cue.get("kind") == "hole_filled"]
        cue_ok = len(cues) == 1 and cues[0].get("state_mutation") is False
        (passed if cue_ok else failed).append(
            "fill_state_immutable" if cue_ok else "hole fill cue is not explicitly state-neutral"
        )
        return passed, failed

    @staticmethod
    def calibration_label(band: str) -> str:
        return "uncalibrated-packing-v1"


@dataclass(frozen=True)
class LightsPlugin:
    puzzle_type: str = "lights"
    plugin_version: str = "1.0.0"
    rules = LightsRules()
    solver = LightsSolver(rules)
    scene_builder = LightsSceneBuilder()
    solver_reject_codes = frozenset({"UNSOLVABLE_TARGET", "MULTIPLE_PRESS_SETS", "SOLVER_INTERNAL_ERROR"})

    @staticmethod
    def timeline_preset_label(band: str, seconds: float, overridden: bool) -> str:
        return standard_timeline_preset_label("lights", lights_difficulty_preset(band), band, seconds, overridden)

    @staticmethod
    def timing_calibration_profile(band: str) -> dict | None:
        if band not in {"easy", "medium", "target"}:
            return None
        standard = float(lights_difficulty_preset(band)["thinking_time_seconds"])
        if band == "target":
            return _timing_profile(
                "lights", band, standard=standard, previous=6.5,
                source="studies/timing_sweep_round3_calibration_2026-08-23.json",
                structural_status=LightsPlugin.calibration_label(band),
            )
        previous = {"easy": 6.0, "medium": 8.0}[band]
        return {
            "variant": f"lights-{band}-candidate-{_seconds_text(standard)}s",
            "baseline_thinking_time_seconds": 2.5,
            "previous_evaluated_thinking_time_seconds": previous,
            "standard_thinking_time_seconds": standard,
            "calibration_change": (
                f"{_seconds_text(previous)}s prior calibrated standard -> "
                f"{_seconds_text(standard)}s selected as an uncalibrated candidate default"
            ),
            "calibration_status": "uncalibrated-standard-candidate",
            "source_evaluation": "none-unvalidated-retiming-2026-09-02",
            "calibration_scope": (
                f"Lights {band} presentation retiming; no human evaluation of the candidate default"
            ),
            "structural_difficulty_status": LightsPlugin.calibration_label(band),
            "timing_status": "candidate-pending-selection",
        }

    difficulty_preset = staticmethod(lights_difficulty_preset)

    @staticmethod
    def generate_candidate(rng: StableRng, preset: dict) -> LightsPuzzleSpec:
        return generate_lights(rng, preset)

    problem_from_dict = staticmethod(LightsPuzzleSpec.from_dict)
    difficulty = staticmethod(lights_difficulty_report)
    quality_filter = staticmethod(lights_quality_rejection)
    replay = staticmethod(replay_lights)
    validate_solution = staticmethod(validate_lights_solution)
    presentation = staticmethod(lights_plan)

    @staticmethod
    def animation_units(solution: Solution) -> int:
        return len(solution.actions)

    @staticmethod
    def renderer_factory() -> LightsRenderer:
        return LightsRenderer()

    alternate_scene = staticmethod(alternate_lights_scene)

    @staticmethod
    def candidate_rejection_reason(preset: dict, error: Exception) -> str | None:
        # A draw can legitimately produce a degenerate or invalid board.
        return "LIGHTS_LAYOUT_FAILED" if str(error).startswith("LIGHTS_") else None

    @staticmethod
    def metadata_contract_checks(puzzle: LightsPuzzleSpec, solution: Solution, metadata: dict) -> tuple[list[str], list[str]]:
        # The GF(2) rank proof is re-derived here and compared field by field
        # with what the instance recorded.
        analysis = LightsPlugin.solver.analyze(puzzle)
        oracle = {
            "status": analysis["status"], "nullity": analysis["nullity"],
            "rank": analysis["rank"], "proof": analysis["proof"],
        }
        expected = {
            "status": "unique", "nullity": 0,
            "rank": puzzle.width * puzzle.height, "proof": "gf2-full-column-rank",
        }
        recorded = metadata.get("difficulty", {}).get("solution_uniqueness", {})
        ok = (
            oracle == expected
            and all(recorded.get(key) == value for key, value in expected.items())
            and recorded.get("press_set_count") == 1
            and recorded.get("equivalence_policy_version") == LIGHTS_EQUIVALENCE_VERSION
            and solution.answer_equivalence_key == "unique:" + str(recorded.get("normalized_signature_hash"))
        )
        return (["uniqueness_metadata"], []) if ok else ([], ["metadata uniqueness evidence differs from solver oracle"])

    @staticmethod
    def visual_contract(scene: LightsScene, renderer: LightsRenderer) -> dict:
        legend = renderer.legend_geometry(scene)
        return {
            "semantic_bounds":list(scene.semantic_bounds), "safe_area":[36,36,684,684],
            "cell_px":CELL_PX, "minimum_cell_px":96,
            "cell_body_px":CELL_PX - 2 * 5, "minimum_cell_body_px":80,
            "mini_cell_px":MINI_CELL_PX, "minimum_mini_cell_px":28,
            "board_extent_px":list(renderer.board_extent(scene)),
            "legend_panels_px":[legend["goal_panel"], legend["rule_panel"]],
            "legend_solution_dependent":False,
            "state_change_not_color_only":False,
            "state_change_note":"a light has exactly two states; the anchored focus bracket, the numbered order badge and the cross pulse carry the action shape",
        }

    @staticmethod
    def render_contract_checks(scene: LightsScene, renderer: LightsRenderer) -> tuple[list[str], list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        rules = LightsPlugin.rules
        if rules.is_goal(scene.puzzle, scene.trace.final):
            passed.append("board_fully_lit")
        else:
            failed.append("solved lights board is not fully lit")
        if CELL_PX >= 96:
            passed.append("minimum_cell_size")
        else:
            failed.append(f"lights cell is below readable minimum: {CELL_PX}px")
        if MINI_CELL_PX >= 28:
            passed.append("minimum_legend_cell_size")
        else:
            failed.append("legend mini cell is below the readable minimum")
        safe = (36, 36, 684, 684)
        bx0, by0, bx1, by1 = renderer.board_extent(scene)
        legend = renderer.legend_geometry(scene)
        boxes = [legend["goal_panel"], legend["rule_panel"]]
        layout_ok = (
            safe[0] <= bx0 and bx1 <= safe[2] and safe[1] <= by0 and by1 <= safe[3]
            and all(safe[0] <= box[0] and box[2] <= safe[2] and by1 <= box[1] and box[3] <= safe[3] for box in boxes)
        )
        (passed if layout_ok else failed).append(
            "board_and_legend_within_safe_area" if layout_ok else "board or legend leaves the safe area"
        )
        # The legend is a property of the problem: it must be byte-identical
        # under the neutrality perturbation, and it must not consult the presses.
        alternate = alternate_lights_scene(scene)
        legend_ok = renderer.legend_geometry(alternate) == legend and alternate.presses != scene.presses
        pre_reveal_ok = all(
            renderer.semantic_snapshot(scene, frame) == renderer.semantic_snapshot(alternate, frame)
            for frame in range(scene.plan.timeline["reveal_start"])
        )
        neutral_ok = legend_ok and pre_reveal_ok
        (passed if neutral_ok else failed).append(
            "legend_solution_independent" if neutral_ok else "legend or pre-reveal snapshot depends on the press set"
        )
        mapping_ok = True
        for order, cell in enumerate(scene.presses):
            middle = renderer.press_snapshot_for_units(scene, order + 0.5)
            if (
                middle["press_index"] != order or middle["press_cell"] != cell
                or not 0.0 < middle["progress"] < 1.0
                or set(middle["blend"]) != set(plus_cells(scene.puzzle.width, scene.puzzle.height, cell))
            ):
                mapping_ok = False
            boundary = renderer.press_snapshot_for_units(scene, float(order + 1))
            if boundary["pressed"] != order + 1 or boundary["lights"] != tuple(scene.trace.steps[order].after.lights):
                mapping_ok = False
            step = scene.trace.steps[order]
            if step.action.kind != "toggle_cell" or tuple(step.action.params["cell"]) != cell:
                mapping_ok = False
        (passed if mapping_ok else failed).append(
            "toggle_action_rendering" if mapping_ok else "rendered press does not match toggle_cell actions"
        )
        # Nothing travels between cells any more: the point marker is the
        # channel that carries the solve animation on its own. Its rendered
        # geometry - which point is indicated, how far the focus bracket has
        # closed in, how far the cross pulse has spread, quantised to whole
        # pixels - must change on every single frame of the solve, not merely
        # every four.
        timeline = scene.plan.timeline
        marker_ok = True
        for frame in range(timeline["reveal_start"], timeline["solve_end"] - 1):
            if renderer.marker_signature(scene, frame) == renderer.marker_signature(scene, frame + 1):
                marker_ok = False
                break
        (passed if marker_ok else failed).append(
            "press_marker_animates_every_frame" if marker_ok
            else "solve press marker stalls between consecutive frames"
        )
        before = renderer.semantic_snapshot(scene, timeline["solve_end"] - 1)
        after = renderer.semantic_snapshot(scene, timeline["solve_end"])
        lit_ok = not before["solved"] and after["solved"] and after["lights"] == tuple(scene.trace.final.lights)
        (passed if lit_ok else failed).append(
            "lit_after_last_press" if lit_ok else "board lights up before or without the last press"
        )
        analysis = LightsPlugin.solver.analyze(scene.puzzle)
        unique_ok = analysis["status"] == "unique" and analysis["nullity"] == 0
        (passed if unique_ok else failed).append(
            "unique_press_set" if unique_ok else "press set is not provably unique"
        )
        cues = [cue for cue in scene.plan.visual_cues if cue.get("kind") == "board_lit"]
        cue_ok = len(cues) == 1 and cues[0].get("state_mutation") is False
        (passed if cue_ok else failed).append(
            "lit_state_immutable" if cue_ok else "board lit cue is not explicitly state-neutral"
        )
        legend_cues = [cue for cue in scene.plan.visual_cues if cue.get("kind") == "rule_legend"]
        legend_cue_ok = (
            len(legend_cues) == 1 and legend_cues[0].get("state_mutation") is False
            and legend_cues[0].get("solution_dependent") is False
        )
        (passed if legend_cue_ok else failed).append(
            "legend_cue_neutral" if legend_cue_ok else "rule legend cue is not declared solution-independent"
        )
        return passed, failed

    @staticmethod
    def calibration_label(band: str) -> str:
        return "uncalibrated-lights-v1"


@dataclass(frozen=True)
class FoldPlugin:
    puzzle_type: str = "fold"
    plugin_version: str = "1.0.0"
    rules = FoldRules()
    solver = FoldSolver(rules)
    scene_builder = FoldSceneBuilder()
    solver_reject_codes = frozenset({"NO_FOLD_SEQUENCE", "MULTIPLE_FOLD_SEQUENCES", "SOLVER_INTERNAL_ERROR"})

    @staticmethod
    def timeline_preset_label(band: str, seconds: float, overridden: bool) -> str:
        return standard_timeline_preset_label("fold", fold_difficulty_preset(band), band, seconds, overridden)

    @staticmethod
    def timing_calibration_profile(band: str) -> dict | None:
        previous_values = {"easy": 2.5, "medium": 4.0, "target": 4.0}
        if band not in previous_values:
            return None
        standard = float(fold_difficulty_preset(band)["thinking_time_seconds"])
        previous = previous_values[band]
        return _timing_profile(
            "fold", band, standard=standard, previous=previous,
            source="studies/timing_sweep_round5_fold_calibration_2026-08-24.json",
            structural_status=FoldPlugin.calibration_label(band),
        )

    difficulty_preset = staticmethod(fold_difficulty_preset)

    @staticmethod
    def generate_candidate(rng: StableRng, preset: dict) -> FoldPuzzleSpec:
        return generate_fold(rng, preset)

    problem_from_dict = staticmethod(FoldPuzzleSpec.from_dict)
    difficulty = staticmethod(fold_difficulty_report)
    quality_filter = staticmethod(fold_quality_rejection)
    replay = staticmethod(replay_fold)
    validate_solution = staticmethod(validate_fold_solution)
    presentation = staticmethod(fold_plan)

    @staticmethod
    def animation_units(solution: Solution) -> int:
        return len(solution.actions)

    @staticmethod
    def renderer_factory() -> FoldRenderer:
        return FoldRenderer()

    alternate_scene = staticmethod(alternate_fold_scene)

    @staticmethod
    def candidate_rejection_reason(preset: dict, error: Exception) -> str | None:
        # A colouring can legitimately have no target that exactly one fold
        # class reaches; that is a candidate rejection, not a generator defect.
        return "FOLD_LAYOUT_FAILED" if str(error).startswith("FOLD_") else None

    @staticmethod
    def metadata_contract_checks(puzzle: FoldPuzzleSpec, solution: Solution, metadata: dict) -> tuple[list[str], list[str]]:
        # The complete class enumeration is re-run here and compared field by
        # field with what the instance recorded.
        analysis = FoldPlugin.solver.analyze(puzzle)
        recorded = metadata.get("difficulty", {}).get("solution_uniqueness", {})
        ok = (
            analysis["status"] == "unique"
            and analysis["class_count"] == 1
            and recorded.get("status") == "unique"
            and recorded.get("fold_class_count") == 1
            and recorded.get("proof") == "complete-fold-class-enumeration"
            and recorded.get("equivalence_policy_version") == FOLD_EQUIVALENCE_VERSION
            and solution.answer_equivalence_key == "unique:" + str(recorded.get("normalized_signature_hash"))
        )
        return (["uniqueness_metadata"], []) if ok else ([], ["metadata uniqueness evidence differs from solver oracle"])

    @staticmethod
    def visual_contract(scene: FoldScene, renderer: FoldRenderer) -> dict:
        legend = renderer.legend_geometry(scene)
        return {
            "semantic_bounds": list(scene.semantic_bounds), "safe_area": [36, 36, 684, 684],
            "cell_px": FOLD_CELL_PX, "minimum_cell_px": FOLD_MIN_CELL_PX,
            "mini_cell_px": FOLD_MINI_CELL_PX, "minimum_mini_cell_px": FOLD_MIN_MINI_CELL_PX,
            "board_extent_px": list(renderer.board_extent(scene)),
            "target_box_px": [int(value) for value in renderer.target_box(scene)],
            "legend_panels_px": [legend["goal_panel"], legend["rule_panel"]],
            "legend_solution_dependent": False,
            "state_change_not_color_only": True,
            "state_change_note": (
                "every fold changes the sheet outline and swings a flap through a real angle; "
                "layer depth is drawn as nested inner outlines, so neither the move nor the "
                "stack depth is carried by colour alone"
            ),
        }

    @staticmethod
    def render_contract_checks(scene: FoldScene, renderer: FoldRenderer) -> tuple[list[str], list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        rules = FoldPlugin.rules
        timeline = scene.plan.timeline
        if rules.is_goal(scene.puzzle, scene.trace.final):
            passed.append("target_filled_at_goal")
        else:
            failed.append("folded sheet does not fill the target rectangle")
        if FOLD_CELL_PX >= FOLD_MIN_CELL_PX:
            passed.append("minimum_cell_size")
        else:
            failed.append(f"fold cell is below readable minimum: {FOLD_CELL_PX}px")
        if FOLD_MINI_CELL_PX >= FOLD_MIN_MINI_CELL_PX:
            passed.append("minimum_legend_cell_size")
        else:
            failed.append("legend mini cell is below the readable minimum")
        safe = (36, 36, 684, 684)
        bx0, by0, bx1, by1 = renderer.board_extent(scene)
        legend = renderer.legend_geometry(scene)
        boxes = [legend["goal_panel"], legend["rule_panel"]]
        layout_ok = (
            safe[0] <= bx0 and bx1 <= safe[2] and safe[1] <= by0 and by1 <= safe[3]
            and all(safe[0] <= box[0] and box[2] <= safe[2] and by1 <= box[1] and box[3] <= safe[3] for box in boxes)
        )
        (passed if layout_ok else failed).append(
            "board_and_legend_within_safe_area" if layout_ok else "board or legend leaves the safe area"
        )
        # The legend and the dashed target are properties of the problem: both
        # must be byte-identical under the neutrality perturbation, and neither
        # may consult the fold class.
        alternate = alternate_fold_scene(scene)
        independent = (
            renderer.legend_geometry(alternate) == legend
            and renderer.target_box(alternate) == renderer.target_box(scene)
            and alternate.folds != scene.folds
        )
        pre_reveal_ok = all(
            renderer.semantic_snapshot(scene, frame) == renderer.semantic_snapshot(alternate, frame)
            for frame in range(timeline["reveal_start"])
        )
        neutral_ok = independent and pre_reveal_ok
        (passed if neutral_ok else failed).append(
            "legend_solution_independent" if neutral_ok else "legend, target or pre-reveal snapshot depends on the fold class"
        )
        mapping_ok = True
        for order, fold in enumerate(scene.folds):
            middle = renderer.fold_snapshot_for_units(scene, order + 0.5)
            if (
                middle["fold_index"] != order or middle["fold"] != fold
                or not 0.0 < middle["progress"] < 1.0
                or middle["state"] != scene.states[order]
            ):
                mapping_ok = False
            boundary = renderer.fold_snapshot_for_units(scene, float(order + 1))
            if boundary["state"] != scene.states[order + 1]:
                mapping_ok = False
            step = scene.trace.steps[order]
            if step.action.kind != "fold_along" or action_fold(step.action) != fold:
                mapping_ok = False
        (passed if mapping_ok else failed).append(
            "fold_action_rendering" if mapping_ok else "rendered fold does not match fold_along actions"
        )
        # The flap angle is the channel that carries the solve animation on its
        # own: it must advance on every single frame of the solve.
        angle_ok = True
        for frame in range(timeline["reveal_start"], timeline["solve_end"] - 1):
            here = renderer.semantic_snapshot(scene, frame)
            following = renderer.semantic_snapshot(scene, frame + 1)
            if following["units"] <= here["units"]:
                angle_ok = False
                break
            if here["fold_index"] == following["fold_index"] and following["angle"] <= here["angle"]:
                angle_ok = False
                break
        (passed if angle_ok else failed).append(
            "fold_angle_advances_every_frame" if angle_ok else "flap angle stalls between consecutive frames"
        )
        before = renderer.semantic_snapshot(scene, timeline["solve_end"] - 1)
        after = renderer.semantic_snapshot(scene, timeline["solve_end"])
        filled_ok = (
            not before["solved"] and after["solved"]
            and after["state"] == scene.states[-1]
            and tuple(after["state"].extent) == tuple(scene.puzzle.target)
        )
        (passed if filled_ok else failed).append(
            "filled_after_last_fold" if filled_ok else "target fills before or without the last fold"
        )
        # Geometry, not colour, carries the state change: every fold strictly
        # shrinks the sheet.
        areas = [
            (state.extent[2] - state.extent[0]) * (state.extent[3] - state.extent[1])
            for state in scene.states
        ]
        shrink_ok = all(later < earlier for earlier, later in zip(areas, areas[1:]))
        depth_ok = scene.states[-1].max_depth() >= 2
        (passed if shrink_ok and depth_ok else failed).append(
            "outline_and_depth_carry_state" if shrink_ok and depth_ok
            else "sheet outline or stack depth does not change across the solve"
        )
        analysis = FoldPlugin.solver.analyze(scene.puzzle)
        unique_ok = analysis["status"] == "unique" and analysis["class_count"] == 1
        (passed if unique_ok else failed).append(
            "unique_fold_class" if unique_ok else "fold class is not provably unique"
        )
        cues = [cue for cue in scene.plan.visual_cues if cue.get("kind") == "target_filled"]
        cue_ok = len(cues) == 1 and cues[0].get("state_mutation") is False
        (passed if cue_ok else failed).append(
            "filled_state_immutable" if cue_ok else "target filled cue is not explicitly state-neutral"
        )
        neutral_cues = [
            cue for cue in scene.plan.visual_cues
            if cue.get("kind") in {"rule_legend", "target_outline"}
        ]
        cue_neutral_ok = len(neutral_cues) == 2 and all(
            cue.get("state_mutation") is False and cue.get("solution_dependent") is False
            for cue in neutral_cues
        )
        (passed if cue_neutral_ok else failed).append(
            "legend_cue_neutral" if cue_neutral_ok else "legend or target cue is not declared solution-independent"
        )
        return passed, failed

    @staticmethod
    def calibration_label(band: str) -> str:
        return "uncalibrated-fold-v1"


@dataclass(frozen=True)
class MosaicPlugin:
    puzzle_type: str = "mosaic"
    plugin_version: str = "1.0.0"
    rules = MosaicRules()
    solver = MosaicSolver(rules)
    scene_builder = MosaicSceneBuilder()
    solver_reject_codes = frozenset({
        "UNSOLVABLE_WITHIN_DEPTH", "SOLVE_BUDGET_EXCEEDED", "MULTIPLE_SHORTEST_SOLUTIONS",
    })

    @staticmethod
    def timeline_preset_label(band: str, seconds: float, overridden: bool) -> str:
        return standard_timeline_preset_label("mosaic", mosaic_difficulty_preset(band), band, seconds, overridden)

    @staticmethod
    def timing_calibration_profile(band: str) -> dict | None:
        if band not in {"easy", "medium", "target"}:
            return None
        standard = float(mosaic_difficulty_preset(band)["thinking_time_seconds"])
        return {
            "variant": f"mosaic-{band}-initial-{_seconds_text(standard)}s",
            "baseline_thinking_time_seconds": 2.5,
            "previous_evaluated_thinking_time_seconds": None,
            "standard_thinking_time_seconds": standard,
            "calibration_change": f"initial {_seconds_text(standard)}s default; not yet calibrated",
            "calibration_status": "uncalibrated-initial-standard",
            "source_evaluation": "none-initial-default",
            "calibration_scope": "Mosaic Shift initial per-band presentation default; no human timing evaluation",
            "structural_difficulty_status": "uncalibrated-mosaic-v1",
            "timing_status": "candidate-pending-selection",
        }

    difficulty_preset = staticmethod(mosaic_difficulty_preset)
    generate_candidate = staticmethod(generate_mosaic)
    problem_from_dict = staticmethod(MosaicPuzzleSpec.from_dict)
    difficulty = staticmethod(mosaic_difficulty_report)
    quality_filter = staticmethod(mosaic_quality_rejection)
    replay = staticmethod(replay_mosaic)
    validate_solution = staticmethod(validate_mosaic_solution)
    presentation = staticmethod(mosaic_plan)

    @staticmethod
    def animation_units(solution: Solution) -> int:
        return len(solution.actions)

    @staticmethod
    def renderer_factory() -> MosaicRenderer:
        return MosaicRenderer()

    alternate_scene = staticmethod(alternate_mosaic_scene)

    @staticmethod
    def metadata_contract_checks(puzzle: MosaicPuzzleSpec, solution: Solution, metadata: dict) -> tuple[list[str], list[str]]:
        analysis = MosaicPlugin.solver.analyze(puzzle)
        recorded = metadata.get("solution", {}).get("uniqueness", {})
        expected_path = tuple(mosaic_action_signature(action) for action in solution.actions)
        ok = (
            analysis["status"] == "unique"
            and analysis["shortest_path_count"] == 1
            and analysis["path"] == expected_path
            and recorded.get("status") == "unique"
            and recorded.get("shortest_path_count") == 1
            and recorded.get("shortest_depth") == len(solution.actions)
            and recorded.get("equivalence_policy_version") == MOSAIC_EQUIVALENCE_VERSION
            and solution.answer_equivalence_key == "unique:" + str(recorded.get("normalized_signature_hash"))
        )
        return (["uniqueness_metadata"], []) if ok else ([], ["metadata uniqueness evidence differs from solver oracle"])

    @staticmethod
    def visual_contract(scene: MosaicScene, renderer: MosaicRenderer) -> dict:
        return {
            "semantic_bounds": list(scene.semantic_bounds), "safe_area": [36, 36, 684, 684],
            "board_px": MOSAIC_BOARD_PX, "cell_px": MOSAIC_CELL_PX,
            "minimum_cell_px": MOSAIC_MIN_CELL_PX,
            "minimum_emblem_stroke_px": MOSAIC_MIN_STROKE_PX,
            "wrap_around_visible": True, "state_change_not_color_only": True,
            "state_change_note": "thick fragment outlines, cyclic translation and direction arrows carry state and motion independently of colour",
        }

    @staticmethod
    def render_contract_checks(scene: MosaicScene, renderer: MosaicRenderer) -> tuple[list[str], list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        timeline = scene.plan.timeline
        if MosaicPlugin.rules.is_goal(scene.puzzle, scene.trace.final):
            passed.append("emblem_restored")
        else:
            failed.append("final tile arrangement does not restore the emblem")
        (passed if MOSAIC_CELL_PX >= MOSAIC_MIN_CELL_PX else failed).append(
            "minimum_cell_size" if MOSAIC_CELL_PX >= MOSAIC_MIN_CELL_PX else "mosaic cell is below readable minimum"
        )
        mapping_ok = True
        for order, signature in enumerate(scene.actions):
            middle = renderer.shift_snapshot_for_units(scene, order + 0.5)
            if (
                (middle["axis"], middle["line"], middle["delta"]) != signature
                or middle["action_index"] != order or not 0.0 < middle["progress"] < 1.0
            ):
                mapping_ok = False
            boundary = renderer.shift_snapshot_for_units(scene, float(order + 1))
            if boundary["tiles"] != scene.states[order + 1]:
                mapping_ok = False
        (passed if mapping_ok else failed).append(
            "cyclic_shift_action_rendering" if mapping_ok else "rendered shift does not match shift_line actions"
        )
        before = renderer.semantic_snapshot(scene, timeline["solve_end"] - 1)
        after = renderer.semantic_snapshot(scene, timeline["solve_end"])
        clear_ok = not before["solved"] and after["solved"] and after["tiles"] == scene.puzzle.goal_tiles
        (passed if clear_ok else failed).append(
            "clear_after_last_shift" if clear_ok else "CLEAR appears before or without the last shift"
        )
        analysis = MosaicPlugin.solver.analyze(scene.puzzle)
        unique_ok = analysis["status"] == "unique" and analysis["shortest_path_count"] == 1
        (passed if unique_ok else failed).append(
            "unique_shortest_sequence" if unique_ok else "shift sequence is not uniquely shortest"
        )
        cues = [cue for cue in scene.plan.visual_cues if cue.get("kind") == "emblem_complete"]
        cue_ok = len(cues) == 1 and cues[0].get("state_mutation") is False
        (passed if cue_ok else failed).append(
            "completion_state_immutable" if cue_ok else "completion cue is not explicitly state-neutral"
        )
        return passed, failed

    @staticmethod
    def calibration_label(band: str) -> str:
        return "uncalibrated-mosaic-v1"


def _static_offset(scene: ParkingScene, state, index: int) -> float:
    return float(state.positions[index])


PLUGINS = {
    "maze": MazePlugin(), "pipes": PipesPlugin(), "parking": ParkingPlugin(),
    "packing": PackingPlugin(), "lights": LightsPlugin(), "fold": FoldPlugin(),
    "mosaic": MosaicPlugin(),
}


def registered_puzzle_types() -> tuple[str, ...]:
    """Stable registry order used by generic command-line interfaces."""
    return tuple(PLUGINS)


def get_plugin(puzzle_type: str):
    try:
        return PLUGINS[puzzle_type]
    except KeyError as error:
        raise ValueError(f"unknown puzzle type: {puzzle_type}") from error
