"""Deterministic, logic-only quality audit for every registered plugin."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .core import StableRng, derive_seed, sha256_value
from .preset_loader import PresetRoots, load_preset, use_preset_root
from .registry import get_plugin


AUDIT_SCHEMA_VERSION = "quality-audit-v1"


@dataclass(frozen=True)
class QualityAuditRequest:
    puzzle_type: str
    difficulty_band: str
    master_seed: int
    candidate_count: int
    preset_root: Path | PresetRoots | None = None


def _scalar_paths(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten scalar metric leaves while keeping their public dotted names."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_scalar_paths(value[key], child))
        return result
    if value is None or isinstance(value, (str, int, float, bool)):
        return {prefix: value}
    if isinstance(value, (list, tuple)):
        return {prefix: value}
    return {}


def _metric_catalog(rows: list[dict[str, Any]]) -> dict[str, Any]:
    flattened = [_scalar_paths(row) for row in rows]
    available = sorted({key for row in flattened for key in row})
    summaries: dict[str, dict[str, int | float]] = {}
    for key in available:
        values = [row[key] for row in flattened if key in row and isinstance(row[key], (int, float)) and not isinstance(row[key], bool)]
        if not values:
            continue
        total = sum(values)
        summaries[key] = {
            "count": len(values),
            "min": min(values),
            "median": median(values),
            "max": max(values),
            "mean": round(total / len(values), 6),
        }
    return {"available": available, "summaries": summaries}


def _solver_metrics(solution: Any, difficulty: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "solver_id": solution.solver_id,
        "solver_version": solution.solver_version,
        "optimality": solution.optimality,
        "action_count": len(solution.actions),
        "cost": solution.cost,
        "expanded_nodes": solution.expanded_nodes,
    }
    if "solution_uniqueness" in difficulty:
        metrics["uniqueness"] = difficulty["solution_uniqueness"]
    return metrics


def _solver_rejection(error: RuntimeError, known_codes: set[str] | frozenset[str]) -> tuple[str, dict[str, Any] | None]:
    code = getattr(error, "code", str(error))
    reason = code if code in known_codes else "SOLVER_ERROR"
    diagnostics = getattr(error, "diagnostics", None)
    if not diagnostics:
        return reason, None
    public = {
        key: diagnostics[key]
        for key in (
            "status", "minimum_quarter_turn_cost", "normalized_solution_count",
            "normalized_solution_count_capped", "unique_path_count", "search",
        )
        if key in diagnostics
    }
    return reason, public or None


def _candidate_record(
    request: QualityAuditRequest,
    plugin: Any,
    preset: dict[str, Any],
    candidate_index: int,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    generation_seed = derive_seed(request.master_seed, request.puzzle_type, candidate_index, "generation")
    record: dict[str, Any] = {
        "candidate_index": candidate_index,
        "generation_seed_hex": f"{generation_seed:032x}",
    }

    try:
        puzzle = plugin.generate_candidate(StableRng(generation_seed), dict(preset))
    except ValueError as error:
        classify = getattr(plugin, "candidate_rejection_reason", None)
        reason = None if classify is None else classify(preset, error)
        if reason is None:
            raise
        record.update({"outcome": "rejected", "reason": reason, "details": [str(error)]})
        record["candidate_sha256"] = sha256_value(record)
        return record, None, None

    record["problem_sha256"] = sha256_value(puzzle.to_dict())
    structure_errors = plugin.rules.validate_structure(puzzle)
    if structure_errors:
        record.update({"outcome": "rejected", "reason": "INVALID_STRUCTURE", "details": structure_errors})
        record["candidate_sha256"] = sha256_value(record)
        return record, None, None

    try:
        solution = plugin.solver.solve(puzzle)
    except RuntimeError as error:
        reason, diagnostics = _solver_rejection(error, plugin.solver_reject_codes)
        record.update({"outcome": "rejected", "reason": reason})
        if diagnostics is not None:
            record["solver_diagnostics"] = diagnostics
        record["candidate_sha256"] = sha256_value(record)
        return record, None, diagnostics

    record["solution_sha256"] = sha256_value(solution.to_dict())
    logical_failures = plugin.validate_solution(puzzle, solution, plugin.rules)
    if logical_failures:
        record.update({"outcome": "rejected", "reason": "ILLEGAL_SOLUTION", "details": logical_failures})
        record["candidate_sha256"] = sha256_value(record)
        return record, None, None

    difficulty = plugin.difficulty(puzzle, solution, plugin.rules)
    difficulty_metrics = difficulty["mechanical"]
    solver_metrics = _solver_metrics(solution, difficulty)
    record["difficulty_metrics"] = difficulty_metrics
    record["solver_metrics"] = solver_metrics
    reason = plugin.quality_filter(difficulty, request.difficulty_band)
    if reason is None:
        record["outcome"] = "accepted"
    else:
        record.update({"outcome": "rejected", "reason": reason})
    record["candidate_sha256"] = sha256_value(record)
    return record, difficulty_metrics, solver_metrics


def audit_quality(request: QualityAuditRequest) -> dict[str, Any]:
    """Scan candidates without rendering media or writing persistent output."""
    if request.candidate_count <= 0:
        raise ValueError("candidate_count must be positive")
    if not 0 <= request.master_seed < 2**64:
        raise ValueError("master_seed must be an unsigned 64-bit integer")

    with use_preset_root(request.preset_root):
        plugin = get_plugin(request.puzzle_type)
        preset_record = load_preset(request.puzzle_type, request.difficulty_band, request.preset_root)
        preset = preset_record.runtime_copy()
        candidates: list[dict[str, Any]] = []
        difficulty_rows: list[dict[str, Any]] = []
        solver_rows: list[dict[str, Any]] = []
        rejections: Counter[str] = Counter()
        accepted = 0
        for candidate_index in range(request.candidate_count):
            record, difficulty_metrics, solver_metrics = _candidate_record(
                request, plugin, preset, candidate_index,
            )
            candidates.append(record)
            if record["outcome"] == "accepted":
                accepted += 1
            else:
                rejections[record["reason"]] += 1
            if difficulty_metrics is not None:
                difficulty_rows.append(difficulty_metrics)
            if solver_metrics is not None:
                solver_rows.append(solver_metrics)

    report: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "type": request.puzzle_type,
        "difficulty": request.difficulty_band,
        "seed": request.master_seed,
        "candidate_count": request.candidate_count,
        "scanned": len(candidates),
        "accepted": accepted,
        "rejected": len(candidates) - accepted,
        "acceptance_rate": round(accepted / len(candidates), 6),
        "rejection_reasons": dict(sorted(rejections.items())),
        "quality_preset": {
            "name": preset["name"],
            "source": preset_record.source_reference,
            "sha256": preset_record.source_sha256,
        },
        "capabilities": {
            "candidate_generation": True,
            "structure_validation": True,
            "solver": True,
            "solution_validation": True,
            "difficulty_metrics": True,
            "quality_rejection": True,
            "candidate_generation_rejection_classification": callable(
                getattr(plugin, "candidate_rejection_reason", None)
            ),
            "solution_uniqueness_metrics": any("uniqueness" in row for row in solver_rows),
            "media_generation": False,
            "persistent_output": False,
        },
        "metrics": {
            "difficulty": _metric_catalog(difficulty_rows),
            "solver": _metric_catalog(solver_rows),
        },
        "candidates": candidates,
        "reproducibility": {
            "rng_algorithm": "SplitMix64",
            "rng_derivation_version": 1,
            "identity_algorithm": "sha256-canonical-json-v1",
            "hash_excludes": ["runtime"],
            "runtime_timing_included": False,
        },
    }
    report["audit_sha256"] = sha256_value(report)
    return report
