from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping


CURRENT_BANDS = ("easy", "medium", "target")
CURRENT_PLUGINS = ("maze", "pipes", "parking", "packing", "lights", "fold", "mosaic")

# This is intentionally an explicit repository-relative catalog. Discovery is
# ambiguous and would make unlisted experiments look current. The catalog
# contains identities and locations only; every functional value is read from
# the selected JSON document.
CURRENT_PRESET_FILES: Mapping[tuple[str, str], str] = MappingProxyType({
    ("maze", "easy"): "presets/current/maze-easy.json",
    ("maze", "medium"): "presets/current/maze-medium.json",
    ("maze", "target"): "presets/current/maze-target.json",
    ("pipes", "easy"): "presets/current/pipes-easy.json",
    ("pipes", "medium"): "presets/current/pipes-medium.json",
    ("pipes", "target"): "presets/current/pipes-target.json",
    ("parking", "easy"): "presets/current/parking-easy.json",
    ("parking", "medium"): "presets/current/parking-medium.json",
    ("parking", "target"): "presets/current/parking-target.json",
    ("packing", "easy"): "presets/current/packing-easy.json",
    ("packing", "medium"): "presets/current/packing-medium.json",
    ("packing", "target"): "presets/current/packing-target.json",
    ("lights", "easy"): "presets/current/lights-easy.json",
    ("lights", "medium"): "presets/current/lights-medium.json",
    ("lights", "target"): "presets/current/lights-target.json",
    ("fold", "easy"): "presets/current/fold-easy.json",
    ("fold", "medium"): "presets/current/fold-medium.json",
    ("fold", "target"): "presets/current/fold-target.json",
    ("mosaic", "easy"): "presets/current/mosaic-easy.json",
    ("mosaic", "medium"): "presets/current/mosaic-medium.json",
    ("mosaic", "target"): "presets/current/mosaic-target.json",
})

SHARED_PRESET_FILES: Mapping[str, str] = MappingProxyType({
    "standard": "presets/shared/standard.json",
    "standard-adaptive-solve": "presets/shared/standard-adaptive-solve.json",
})


class PresetValidationError(ValueError):
    """A catalog or preset document is unsafe, missing, or invalid."""


@dataclass(frozen=True)
class PresetRecord:
    plugin: str
    band: str
    preset_id: str
    path: Path
    source_bytes: bytes
    source_sha256: str
    document: Mapping[str, object]
    runtime: Mapping[str, object]
    source_reference: str

    def runtime_copy(self) -> dict:
        return dict(self.runtime)


@dataclass(frozen=True)
class PresetRoots:
    """Explicit roots for the current runtime catalog and shared contracts."""

    current: Path
    shared: Path | None = None

    @classmethod
    def from_repository(cls, root: Path | str) -> "PresetRoots":
        repository = Path(root)
        return cls(
            current=repository / "presets" / "current",
            shared=repository / "presets" / "shared",
        )

    @classmethod
    def explicit(
        cls, *, current: Path | str, shared: Path | str | None = None,
    ) -> "PresetRoots":
        return cls(
            current=Path(current),
            shared=None if shared is None else Path(shared),
        )


_ROOT_OVERRIDE: ContextVar[PresetRoots | None] = ContextVar("zero_button_game_preset_roots", default=None)


def default_repository_root() -> Path:
    """The checked-out repository root, independent of the process CWD."""
    return Path(__file__).resolve().parents[2]


def default_preset_root() -> Path:
    """The user-facing preset collection root (contains current/ and shared/)."""
    return default_repository_root() / "presets"


def default_preset_roots() -> PresetRoots:
    return PresetRoots.from_repository(default_repository_root())


def _roots_from_path(root: Path | str) -> PresetRoots:
    selected = Path(root)
    if (selected / "presets" / "current").is_dir():
        return PresetRoots.from_repository(selected)
    if (selected / "current").is_dir():
        return PresetRoots.explicit(current=selected / "current", shared=selected / "shared")
    return PresetRoots.explicit(current=selected)


def _coerce_roots(root: Path | str | PresetRoots | None) -> PresetRoots:
    if isinstance(root, PresetRoots):
        return root
    if root is not None:
        return _roots_from_path(root)
    return _ROOT_OVERRIDE.get() or default_preset_roots()


@contextmanager
def use_preset_root(root: Path | str | PresetRoots | None) -> Iterator[None]:
    """Inject a repository/current/category root set without global caching."""
    if root is None:
        yield
        return
    token = _ROOT_OVERRIDE.set(_coerce_roots(root))
    try:
        yield
    finally:
        _ROOT_OVERRIDE.reset(token)


def _reject_constant(value: str) -> None:
    raise PresetValidationError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PresetValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(raw: bytes, path: Path) -> dict[str, object]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PresetValidationError(f"preset is not UTF-8: {path}: {error}") from error
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except PresetValidationError:
        raise
    except json.JSONDecodeError as error:
        raise PresetValidationError(
            f"invalid preset JSON: {path}:{error.lineno}:{error.colno}: {error.msg}"
        ) from error
    if not isinstance(value, dict):
        raise PresetValidationError(f"preset root must be a JSON object: {path}")
    return value


def _safe_file(root: Path, filename: str) -> Path:
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise PresetValidationError(f"unsafe preset catalog path: {filename!r}")
    resolved_root = root.resolve()
    candidate = (resolved_root / filename).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise PresetValidationError(f"preset path escapes root: {filename!r}") from error
    if not candidate.is_file():
        raise PresetValidationError(f"preset file is missing: {candidate}")
    return candidate


def _at(document: Mapping[str, object], *path: str) -> object:
    value: object = document
    for part in path:
        if not isinstance(value, dict) or part not in value:
            raise PresetValidationError(f"missing required preset field: {'.'.join(path)}")
        value = value[part]
    return value


def _string(document: Mapping[str, object], *path: str) -> str:
    value = _at(document, *path)
    if not isinstance(value, str) or not value:
        raise PresetValidationError(f"preset field must be a non-empty string: {'.'.join(path)}")
    return value


def _integer(document: Mapping[str, object], *path: str, minimum: int = 0) -> int:
    value = _at(document, *path)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PresetValidationError(
            f"preset field must be an integer >= {minimum}: {'.'.join(path)}"
        )
    return value


def _number(document: Mapping[str, object], *path: str) -> float:
    value = _at(document, *path)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PresetValidationError(f"preset field must be a number: {'.'.join(path)}")
    return float(value)


def _range(document: Mapping[str, object], *path: str) -> tuple[int, int]:
    value = _at(document, *path)
    if (
        not isinstance(value, list) or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or value[0] > value[1]
    ):
        raise PresetValidationError(f"preset field must be an ordered two-integer range: {'.'.join(path)}")
    return value[0], value[1]


def _thinking_time(document: Mapping[str, object]) -> float:
    value = _number(document, "presentation", "thinking_time_seconds")
    if not 2.5 <= value <= 20.0:
        raise PresetValidationError("presentation.thinking_time_seconds must be between 2.5 and 20.0")
    if abs(value * 20 - round(value * 20)) > 1e-9:
        raise PresetValidationError("presentation.thinking_time_seconds must align to the 20fps frame grid")
    definition = _string(document, "presentation", "thinking_time_definition")
    if definition != "frame zero to reveal_start":
        raise PresetValidationError(
            "presentation.thinking_time_definition must be 'frame zero to reveal_start'"
        )
    return value


def _runtime_maze(document: Mapping[str, object], band: str) -> dict[str, object]:
    source: Mapping[str, object] = document
    if band == "target":
        mechanical = _at(document, "mechanical")
        if not isinstance(mechanical, dict):
            raise PresetValidationError("preset field must be an object: mechanical")
        source = mechanical
    result: dict[str, object] = {"name": _string(document, "name")}
    for field in (
        "min_cost", "max_cost", "min_decisions", "max_decisions", "min_false_leads",
        "max_false_leads", "min_deep_false_leads", "max_deep_false_leads",
        "min_goal_zone_false_leads",
    ):
        result[field] = _integer(source, field)
    if band == "target":
        result.update({
            "width": _integer(document, "board", "width", minimum=1),
            "height": _integer(document, "board", "height", minimum=1),
            "endpoint_profile": _string(document, "generation", "endpoint_profile"),
        })
    result["thinking_time_seconds"] = _thinking_time(document)
    return result


def _runtime_pipes(document: Mapping[str, object], band: str) -> dict[str, object]:
    turns = _range(document, "mechanical", "required_quarter_turns")
    score = _range(document, "mechanical", "difficulty_score")
    result: dict[str, object] = {
        "name": _string(document, "name"),
        "width": _integer(document, "board", "width", minimum=1),
        "height": _integer(document, "board", "height", minimum=1),
        "thinking_time_seconds": _thinking_time(document),
        "min_required_path_length": _integer(document, "mechanical", "required_path_length_min"),
        "min_required_rotation_pieces": _integer(document, "mechanical", "required_rotation_pieces_min"),
        "min_required_quarter_turns": turns[0],
        "min_candidate_routes": _integer(document, "mechanical", "candidate_routes_min"),
        "max_required_quarter_turns": turns[1],
        "min_difficulty_score": score[0],
        "max_difficulty_score": score[1],
    }
    optional = {
        "near_optimal_routes_min": "min_near_optimal_routes",
        "false_connection_edges_min": "min_false_connection_edges",
    }
    mechanical = _at(document, "mechanical")
    assert isinstance(mechanical, dict)
    for json_name, runtime_name in optional.items():
        if json_name in mechanical:
            result[runtime_name] = _integer(document, "mechanical", json_name)
    return result


def _runtime_parking(document: Mapping[str, object], band: str) -> dict[str, object]:
    moves = _range(document, "mechanical", "normalized_moves")
    score = _range(document, "mechanical", "difficulty_score")
    return {
        "name": _string(document, "name"), "band": band,
        "width": _integer(document, "board", "width", minimum=1),
        "height": _integer(document, "board", "height", minimum=1),
        "vehicle_count": _integer(document, "generation", "vehicle_count", minimum=1),
        "walk_steps": _integer(document, "generation", "walk_steps", minimum=1),
        "blocker_count": _integer(document, "generation", "blocker_count"),
        "thinking_time_seconds": _thinking_time(document),
        "search_attempts": _integer(document, "generation", "search_attempts", minimum=1),
        "solve_state_budget": _integer(document, "generation", "solve_state_budget", minimum=1),
        "min_moves": moves[0], "max_moves": moves[1],
        "min_involved_vehicles": _integer(document, "mechanical", "involved_vehicles_min"),
        "min_blocking_chain_depth": _integer(document, "mechanical", "blocking_chain_depth_min"),
        "min_reversal_moves": _integer(document, "mechanical", "reversal_moves_min"),
        "max_slide_cells": _integer(document, "mechanical", "slide_cells_max"),
        "min_difficulty_score": score[0], "max_difficulty_score": score[1],
    }


def _runtime_packing(document: Mapping[str, object], band: str) -> dict[str, object]:
    pieces = _range(document, "mechanical", "pieces")
    score = _range(document, "mechanical", "difficulty_score")
    return {
        "name": _string(document, "name"), "band": band,
        "width": _integer(document, "board", "hole_grid_width", minimum=1),
        "height": _integer(document, "board", "hole_grid_height", minimum=1),
        "piece_count": _integer(document, "generation", "piece_count", minimum=1),
        "thinking_time_seconds": _thinking_time(document),
        "search_attempts": _integer(document, "generation", "search_attempts", minimum=1),
        "solve_node_budget": _integer(document, "generation", "solve_node_budget", minimum=1),
        "min_pieces": pieces[0], "max_pieces": pieces[1],
        "min_concave_pieces": _integer(document, "mechanical", "concave_pieces_min"),
        "min_dead_placements": _integer(document, "mechanical", "dead_placements_min"),
        "min_difficulty_score": score[0], "max_difficulty_score": score[1],
    }


def _runtime_lights(document: Mapping[str, object], band: str) -> dict[str, object]:
    presses = _range(document, "mechanical", "presses")
    score = _range(document, "mechanical", "difficulty_score")
    return {
        "name": _string(document, "name"), "band": band,
        "width": _integer(document, "board", "width", minimum=1),
        "height": _integer(document, "board", "height", minimum=1),
        "press_count": _integer(document, "generation", "press_count", minimum=1),
        "search_attempts": _integer(document, "generation", "search_attempts", minimum=1),
        "thinking_time_seconds": _thinking_time(document),
        "min_presses": presses[0], "max_presses": presses[1],
        "min_lit_clusters": _integer(document, "mechanical", "lit_clusters_min"),
        "max_greedy_reductions": _integer(document, "mechanical", "greedy_reduction_max"),
        "min_difficulty_score": score[0], "max_difficulty_score": score[1],
    }


def _runtime_fold(document: Mapping[str, object], band: str) -> dict[str, object]:
    folds = _range(document, "mechanical", "folds")
    score = _range(document, "mechanical", "difficulty_score")
    return {
        "name": _string(document, "name"), "band": band,
        "width": _integer(document, "sheet", "width", minimum=1),
        "height": _integer(document, "sheet", "height", minimum=1),
        "search_attempts": _integer(document, "generation", "search_attempts", minimum=1),
        "thinking_time_seconds": _thinking_time(document),
        "min_folds": folds[0], "max_folds": folds[1],
        "min_target_side": _integer(document, "generation", "min_target_side", minimum=1),
        "max_decoy_creases": _integer(document, "mechanical", "decoy_creases_max"),
        "min_difficulty_score": score[0], "max_difficulty_score": score[1],
    }


def _runtime_mosaic(document: Mapping[str, object], band: str) -> dict[str, object]:
    shifts = _range(document, "mechanical", "shortest_actions")
    score = _range(document, "mechanical", "difficulty_score")
    return {
        "name": _string(document, "name"), "band": band,
        "size": _integer(document, "board", "size", minimum=1),
        "search_attempts": _integer(document, "generation", "search_attempts", minimum=1),
        "solve_node_budget": _integer(document, "generation", "solve_node_budget", minimum=1),
        "thinking_time_seconds": _thinking_time(document),
        "min_shifts": shifts[0], "max_shifts": shifts[1],
        "min_cross_axis_pairs": _integer(document, "mechanical", "cross_axis_pairs_min", minimum=1),
        "min_misplaced_tiles": _integer(document, "mechanical", "misplaced_tiles_min", minimum=1),
        "min_difficulty_score": score[0], "max_difficulty_score": score[1],
    }


_RUNTIME_BUILDERS = {
    "maze": _runtime_maze,
    "pipes": _runtime_pipes,
    "parking": _runtime_parking,
    "packing": _runtime_packing,
    "lights": _runtime_lights,
    "fold": _runtime_fold,
    "mosaic": _runtime_mosaic,
}


class PresetLoader:
    """Load and validate categorized presets from explicit roots.

    There is no cache: edits and per-test roots are visible on the next call,
    and the bytes hashed in a returned record are exactly the bytes parsed.
    """

    def __init__(self, root: Path | str | PresetRoots | None = None):
        self.roots = _coerce_roots(root)
        # Compatibility attribute: it now means the current category root.
        self.root = self.roots.current

    @staticmethod
    def _filename(relative_path: str) -> str:
        return Path(relative_path).name

    def _category_file(self, category: str, relative_path: str) -> Path:
        root = getattr(self.roots, category)
        if root is None:
            raise PresetValidationError(f"preset {category} root is not configured")
        return _safe_file(root, self._filename(relative_path))

    def load(self, plugin: str, band: str) -> PresetRecord:
        if plugin not in CURRENT_PLUGINS:
            raise PresetValidationError(f"unknown preset plugin: {plugin}")
        if band not in CURRENT_BANDS:
            raise PresetValidationError(f"unknown preset band for {plugin}: {band}")
        relative_path = CURRENT_PRESET_FILES[(plugin, band)]
        filename = self._filename(relative_path)
        path = self._category_file("current", relative_path)
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise PresetValidationError(f"cannot read preset file: {path}: {error}") from error
        document = _parse_json(raw, path)
        preset_id = _string(document, "name")
        expected_id = filename.removesuffix(".json")
        if preset_id != expected_id:
            raise PresetValidationError(
                f"preset id/name mismatch for {plugin}/{band}: expected {expected_id!r}, got {preset_id!r}"
            )
        if _string(document, "puzzle_type") != plugin:
            raise PresetValidationError(f"preset plugin mismatch for {plugin}/{band}")
        if _string(document, "difficulty") != band:
            raise PresetValidationError(f"preset band mismatch for {plugin}/{band}")
        _string(document, "schema_version")
        _string(document, "ruleset")
        runtime = _RUNTIME_BUILDERS[plugin](document, band)
        for minimum, maximum in (
            (key, "max_" + key.removeprefix("min_"))
            for key in runtime if key.startswith("min_")
        ):
            if maximum in runtime and runtime[minimum] > runtime[maximum]:
                raise PresetValidationError(f"preset minimum exceeds maximum: {minimum}/{maximum}")
        return PresetRecord(
            plugin=plugin,
            band=band,
            preset_id=preset_id,
            path=path,
            source_bytes=raw,
            source_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
            document=MappingProxyType(document),
            runtime=MappingProxyType(runtime),
            source_reference=Path(relative_path).relative_to("presets").as_posix(),
        )

    def audit_catalog(self, categories: tuple[str, ...] = ("current", "shared")) -> dict[str, int]:
        catalogs = {
            "current": {
                self._filename(path).removesuffix(".json"): path
                for path in CURRENT_PRESET_FILES.values()
            },
            "shared": dict(SHARED_PRESET_FILES),
        }
        if any(category not in catalogs for category in categories):
            raise PresetValidationError(f"unknown preset catalog category: {categories!r}")
        all_entries = {key: value for catalog in catalogs.values() for key, value in catalog.items()}
        if len(all_entries) != 23 or len(set(all_entries.values())) != 23:
            raise PresetValidationError("preset catalog must contain 23 unique ids and files")
        seen_ids: set[str] = set()
        counts = {"current": 0, "shared": 0}
        roots = {"current": "current", "shared": "shared"}
        for category in categories:
            category_root = getattr(self.roots, roots[category])
            if category_root is None:
                raise PresetValidationError(f"preset {roots[category]} root is not configured")
            expected_files = {self._filename(path) for path in catalogs[category].values()}
            actual_files = {
                path.name for path in category_root.iterdir()
                if path.is_file() and path.suffix == ".json" and path.name != "manifest.json"
            } if category_root.is_dir() else set()
            if actual_files != expected_files:
                missing = sorted(expected_files - actual_files)
                extra = sorted(actual_files - expected_files)
                raise PresetValidationError(
                    f"preset {category} catalog files differ: missing={missing}, extra={extra}"
                )
            for expected_id, relative_path in catalogs[category].items():
                path = self._category_file(roots[category], relative_path)
                document = _parse_json(path.read_bytes(), path)
                actual_id = document.get("name", document.get("preset"))
                if actual_id in seen_ids:
                    raise PresetValidationError(f"duplicate preset id in catalog: {actual_id}")
                seen_ids.add(str(actual_id))
                if actual_id != expected_id:
                    raise PresetValidationError(
                        f"catalog id mismatch for {relative_path}: expected {expected_id!r}, got {actual_id!r}"
                    )
                counts[category] += 1
        counts["total"] = sum(counts.values())
        return counts


def load_preset(plugin: str, band: str, root: Path | str | PresetRoots | None = None) -> PresetRecord:
    return PresetLoader(root).load(plugin, band)


def difficulty_preset(plugin: str, band: str, root: Path | str | PresetRoots | None = None) -> dict:
    return load_preset(plugin, band, root).runtime_copy()
