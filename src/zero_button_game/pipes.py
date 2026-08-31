from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import product
from typing import Any

from .core import StableRng, sha256_value
from .models import Action, Solution

Cell = tuple[int, int]
NORTH, EAST, SOUTH, WEST = 1, 2, 4, 8
DIRECTIONS = ((0, -1, NORTH, SOUTH), (1, 0, EAST, WEST), (0, 1, SOUTH, NORTH), (-1, 0, WEST, EAST))
PIPE_EQUIVALENCE_POLICY = "path + sorted(panel_position, canonical_final_mask, minimal_turn_cost)"
PIPE_EQUIVALENCE_VERSION = "pipes-normalized-minimum-v1"


def rotate_mask(mask: int, quarter_turns: int) -> int:
    result = mask
    for _ in range(quarter_turns % 4):
        result = ((result << 1) & 0b1111) | ((result >> 3) & 1)
    return result


@dataclass(frozen=True)
class PipePuzzleSpec:
    schema_version: str
    puzzle_type: str
    generator_version: str
    width: int
    height: int
    source: Cell
    sink: Cell
    initial_masks: tuple[int, ...]
    ruleset: str = "source-to-goal-unique-v3"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PipePuzzleSpec":
        return cls(
            value["schema_version"], value["puzzle_type"], value["generator_version"],
            value["width"], value["height"], tuple(value["source"]), tuple(value["sink"]),
            tuple(value["initial_masks"]), value.get("ruleset", "connected-no-leaks-v1"),
        )


@dataclass(frozen=True)
class PipeState:
    puzzle_type: str
    step: int
    rotations: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PipeReplayStep:
    action: Action
    before: PipeState
    after: PipeState


@dataclass(frozen=True)
class PipeReplayTrace:
    initial: PipeState
    steps: tuple[PipeReplayStep, ...]
    final: PipeState


def _index(puzzle: PipePuzzleSpec, cell: Cell) -> int:
    return cell[1] * puzzle.width + cell[0]


def _cell(puzzle: PipePuzzleSpec, index: int) -> Cell:
    return index % puzzle.width, index // puzzle.width


def generate_pipes(rng: StableRng, width: int = 4, height: int = 4) -> PipePuzzleSpec:
    if not (3 <= width <= 5 and 3 <= height <= 5):
        raise ValueError("pipe dimensions must be between 3 and 5")
    cells = [(x, y) for y in range(height) for x in range(width)]
    start = cells[rng.randbelow(len(cells))]
    stack = [start]
    visited = {start}
    edges: list[tuple[Cell, Cell]] = []
    while stack:
        x, y = stack[-1]
        choices = [(x + dx, y + dy) for dx, dy, _, _ in DIRECTIONS]
        choices = [cell for cell in choices if 0 <= cell[0] < width and 0 <= cell[1] < height and cell not in visited]
        rng.shuffle(choices)
        if not choices:
            stack.pop()
            continue
        nxt = choices[0]
        edges.append((stack[-1], nxt))
        visited.add(nxt)
        stack.append(nxt)

    solved_masks = [0] * len(cells)
    for first, second in edges:
        dx, dy = second[0] - first[0], second[1] - first[1]
        for dir_x, dir_y, bit, opposite in DIRECTIONS:
            if (dx, dy) == (dir_x, dir_y):
                solved_masks[first[1] * width + first[0]] |= bit
                solved_masks[second[1] * width + second[0]] |= opposite
                break
    initial_masks = []
    changed = False
    for mask in solved_masks:
        rotation = rng.randbelow(4)
        rotated = rotate_mask(mask, rotation)
        initial_masks.append(rotated)
        changed |= rotated != mask
    if not changed:
        for index, mask in enumerate(solved_masks):
            rotated = rotate_mask(mask, 1)
            if rotated != mask:
                initial_masks[index] = rotated
                break
    return PipePuzzleSpec(
        "1.2.0", "pipes", "pipes-gen-3", width, height,
        (0, 0), (width - 1, height - 1), tuple(initial_masks),
    )


class PipeSolveRejected(RuntimeError):
    def __init__(self, code: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics or {}


def canonical_orientation_map(initial_mask: int) -> dict[int, tuple[int, int]]:
    """Map final mask to (minimal cost, deterministic signed turn).

    +2 is the canonical 180-degree presentation; +3 is normalized to -1.
    Rotationally symmetric masks collapse before cost comparison.
    """
    result: dict[int, tuple[int, int]] = {}
    scores: dict[int, tuple[int, int]] = {}
    for preference, turns in enumerate((0, 1, -1, 2, -2)):
        final_mask = rotate_mask(initial_mask, turns)
        candidate = (abs(turns), preference)
        if final_mask not in scores or candidate < scores[final_mask]:
            scores[final_mask] = candidate
            result[final_mask] = (abs(turns), turns)
    return result


def canonical_turn_for_final_mask(initial_mask: int, final_mask: int) -> tuple[int, int]:
    try:
        return canonical_orientation_map(initial_mask)[final_mask]
    except KeyError as error:
        raise ValueError("final mask is not a rotation of initial mask") from error


def pipe_signature_value(
    puzzle: "PipePuzzleSpec", path: tuple[Cell, ...], rotations: tuple[int, ...],
) -> dict[str, Any]:
    panels = []
    canonical_rotations = [0] * len(puzzle.initial_masks)
    for index, raw_turns in enumerate(rotations):
        final_mask = rotate_mask(puzzle.initial_masks[index], raw_turns)
        cost, canonical_turn = canonical_turn_for_final_mask(puzzle.initial_masks[index], final_mask)
        canonical_rotations[index] = canonical_turn
        if cost:
            x, y = _cell(puzzle, index)
            panels.append({
                "panel": [x, y], "canonical_final_mask": final_mask,
                "minimal_quarter_turn_cost": cost,
            })
    path_value = [list(cell) for cell in path]
    normalized = {
        "equivalence_policy": PIPE_EQUIVALENCE_POLICY,
        "equivalence_policy_version": PIPE_EQUIVALENCE_VERSION,
        "path_identity": path_value,
        "panels": panels,
        "minimal_quarter_turn_cost": sum(panel["minimal_quarter_turn_cost"] for panel in panels),
    }
    normalized["path_identity_hash"] = sha256_value(path_value)
    normalized["normalized_signature_hash"] = sha256_value({
        "path_identity": path_value, "panels": panels,
        "minimal_quarter_turn_cost": normalized["minimal_quarter_turn_cost"],
        "equivalence_policy_version": PIPE_EQUIVALENCE_VERSION,
    })
    normalized["canonical_signed_rotations"] = canonical_rotations
    return normalized


class PipeRules:
    def validate_structure(self, puzzle: PipePuzzleSpec) -> list[str]:
        errors = []
        if puzzle.puzzle_type != "pipes":
            errors.append("wrong puzzle type")
        if not (3 <= puzzle.width <= 5 and 3 <= puzzle.height <= 5):
            errors.append("dimensions outside 3..5")
        if len(puzzle.initial_masks) != puzzle.width * puzzle.height:
            errors.append("piece count mismatch")
        if any(mask <= 0 or mask > 15 for mask in puzzle.initial_masks):
            errors.append("invalid connector mask")
        cells = {(x, y) for y in range(puzzle.height) for x in range(puzzle.width)}
        if puzzle.source not in cells or puzzle.sink not in cells or puzzle.source == puzzle.sink:
            errors.append("invalid source or sink")
        if puzzle.ruleset not in {"source-to-goal-unique-v3", "source-to-goal-v2", "connected-no-leaks-v1"}:
            errors.append("unsupported ruleset")
        return errors

    def initial_state(self, puzzle: PipePuzzleSpec) -> PipeState:
        return PipeState("pipes", 0, (0,) * len(puzzle.initial_masks))

    def mask(self, puzzle: PipePuzzleSpec, state: PipeState, cell: Cell) -> int:
        index = _index(puzzle, cell)
        return rotate_mask(puzzle.initial_masks[index], state.rotations[index])

    def legal_actions(self, puzzle: PipePuzzleSpec, state: PipeState) -> tuple[Action, ...]:
        before_hash = sha256_value(state.to_dict())
        turn_values = (-2, -1, 1, 2) if puzzle.ruleset == "source-to-goal-unique-v3" else (1, 2, 3)
        return tuple(
            Action(1, "rotate_piece", f"pipe-{index}", {"cell": list(_cell(puzzle, index)), "quarter_turns": [turns]}, {"state_hash": before_hash})
            for index in range(len(puzzle.initial_masks)) for turns in turn_values
        )

    def apply(self, puzzle: PipePuzzleSpec, state: PipeState, action: Action) -> PipeState:
        if action.kind != "rotate_piece" or not action.actor_id.startswith("pipe-"):
            raise ValueError("unsupported action")
        if action.precondition.get("state_hash") != sha256_value(state.to_dict()):
            raise ValueError("action precondition mismatch")
        cell_value = action.params.get("cell", [])
        turns_value = action.params.get("quarter_turns", [])
        if len(cell_value) != 2 or len(turns_value) != 1:
            raise ValueError("invalid rotate_piece parameters")
        cell = tuple(cell_value)
        turns = turns_value[0]
        legal_turns = {-2, -1, 1, 2} if puzzle.ruleset == "source-to-goal-unique-v3" else {1, 2, 3}
        if not (0 <= cell[0] < puzzle.width and 0 <= cell[1] < puzzle.height and turns in legal_turns):
            raise ValueError("illegal pipe rotation")
        index = _index(puzzle, cell)
        if action.actor_id != f"pipe-{index}":
            raise ValueError("pipe actor/cell mismatch")
        rotations = list(state.rotations)
        rotations[index] = (rotations[index] + turns) % 4
        return PipeState("pipes", state.step + 1, tuple(rotations))

    def connected_path(self, puzzle: PipePuzzleSpec, state: PipeState) -> tuple[Cell, ...]:
        """Return the canonical shortest active START -> GOAL path.

        Dangling or mismatched connectors outside this mutually connected path
        are permitted by source-to-goal-v2 and never receive rendered flow.
        """
        if self.validate_structure(puzzle):
            return ()
        parent: dict[Cell, Cell | None] = {puzzle.source: None}
        queue = deque([puzzle.source])
        while queue:
            cell = queue.popleft()
            if cell == puzzle.sink:
                break
            mask = self.mask(puzzle, state, cell)
            for dx, dy, bit, opposite in DIRECTIONS:
                nxt = (cell[0] + dx, cell[1] + dy)
                if not (0 <= nxt[0] < puzzle.width and 0 <= nxt[1] < puzzle.height):
                    continue
                if (
                    mask & bit
                    and self.mask(puzzle, state, nxt) & opposite
                    and nxt not in parent
                ):
                    parent[nxt] = cell
                    queue.append(nxt)
        if puzzle.sink not in parent:
            return ()
        path = []
        current: Cell | None = puzzle.sink
        while current is not None:
            path.append(current)
            current = parent[current]
        return tuple(reversed(path))

    def connected_paths(
        self, puzzle: PipePuzzleSpec, state: PipeState, limit: int = 2,
    ) -> tuple[tuple[Cell, ...], ...]:
        """Enumerate active simple START -> GOAL paths, capped for ambiguity detection."""
        if limit <= 0 or self.validate_structure(puzzle):
            return ()
        found: list[tuple[Cell, ...]] = []
        path = [puzzle.source]
        visited = {puzzle.source}

        def search(cell: Cell) -> None:
            if len(found) >= limit:
                return
            if cell == puzzle.sink:
                found.append(tuple(path))
                return
            mask = self.mask(puzzle, state, cell)
            for dx, dy, bit, opposite in DIRECTIONS:
                nxt = (cell[0] + dx, cell[1] + dy)
                if not (0 <= nxt[0] < puzzle.width and 0 <= nxt[1] < puzzle.height) or nxt in visited:
                    continue
                if mask & bit and self.mask(puzzle, state, nxt) & opposite:
                    visited.add(nxt)
                    path.append(nxt)
                    search(nxt)
                    path.pop()
                    visited.remove(nxt)

        search(puzzle.source)
        return tuple(found)

    def _is_legacy_all_cell_goal(self, puzzle: PipePuzzleSpec, state: PipeState) -> bool:
        if self.validate_structure(puzzle):
            return False
        masks = [rotate_mask(mask, state.rotations[index]) for index, mask in enumerate(puzzle.initial_masks)]
        for index, mask in enumerate(masks):
            x, y = _cell(puzzle, index)
            for dx, dy, bit, opposite in DIRECTIONS:
                nx, ny = x + dx, y + dy
                connected = bool(mask & bit)
                if not (0 <= nx < puzzle.width and 0 <= ny < puzzle.height):
                    if connected:
                        return False
                    continue
                neighbor_connected = bool(masks[ny * puzzle.width + nx] & opposite)
                if connected != neighbor_connected:
                    return False
        reached = {puzzle.source}
        queue = deque([puzzle.source])
        while queue:
            cell = queue.popleft()
            mask = masks[_index(puzzle, cell)]
            for dx, dy, bit, _ in DIRECTIONS:
                nxt = (cell[0] + dx, cell[1] + dy)
                if mask & bit and nxt not in reached:
                    reached.add(nxt)
                    queue.append(nxt)
        return len(reached) == puzzle.width * puzzle.height and puzzle.sink in reached

    def is_goal(self, puzzle: PipePuzzleSpec, state: PipeState) -> bool:
        if puzzle.ruleset == "connected-no-leaks-v1":
            return self._is_legacy_all_cell_goal(puzzle, state)
        return bool(self.connected_path(puzzle, state))


class PipeSolver:
    solver_id = "pipes-source-goal-unique"
    solver_version = "3"

    def __init__(self, rules: PipeRules):
        self.rules = rules

    def solve(self, puzzle: PipePuzzleSpec, node_budget: int = 100_000) -> Solution:
        errors = self.rules.validate_structure(puzzle)
        if errors:
            raise ValueError("; ".join(errors))
        if puzzle.ruleset == "connected-no-leaks-v1":
            return self._solve_legacy_all_cells(puzzle, node_budget)
        if puzzle.ruleset == "source-to-goal-v2":
            return self._solve_source_goal_v2(puzzle, node_budget)
        return self._solve_unique_source_goal(puzzle, node_budget)

    @staticmethod
    def _required_bit(first: Cell, second: Cell) -> int:
        delta = second[0] - first[0], second[1] - first[1]
        for dx, dy, bit, _ in DIRECTIONS:
            if delta == (dx, dy):
                return bit
        raise ValueError("non-adjacent path cells")

    def analyze_minimum_solutions(
        self, puzzle: PipePuzzleSpec, node_budget: int = 100_000, signature_limit: int = 2,
    ) -> dict[str, Any]:
        """Prove minimum bidirectional turn cost and aggregate normalized solutions.

        Only the cheapest orientation(s) for each panel on a geometric path are
        expanded. Signatures and active path identities are capped at two,
        because generation needs the distinction 0 / 1 / multiple rather than
        unbounded enumeration of all equivalent witnesses.
        """
        path = [puzzle.source]
        visited = {puzzle.source}
        expanded = 0
        orientation_combinations = 0
        feasible_paths = 0
        best_cost: int | None = None
        signatures: dict[str, dict[str, Any]] = {}
        path_hashes: set[str] = set()
        count_capped = False
        near_costs: list[int] = []

        def choices_for(cell: Cell, required: int) -> tuple[tuple[int, int, int], ...]:
            options = [
                (final_mask, cost, turns)
                for final_mask, (cost, turns) in canonical_orientation_map(
                    puzzle.initial_masks[_index(puzzle, cell)]
                ).items()
                if final_mask & required == required
            ]
            if not options:
                return ()
            minimum = min(cost for _, cost, _ in options)
            return tuple(sorted(option for option in options if option[1] == minimum))

        def add_signature(value: dict[str, Any], rotations: tuple[int, ...]) -> None:
            nonlocal count_capped
            signature_hash = value["normalized_signature_hash"]
            path_hashes.add(value["path_identity_hash"])
            if signature_hash in signatures:
                return
            if len(signatures) >= signature_limit:
                count_capped = True
                return
            signatures[signature_hash] = {**value, "canonical_signed_rotations": list(rotations)}

        def evaluate() -> None:
            nonlocal feasible_paths, orientation_combinations, best_cost, signatures, path_hashes, count_capped
            all_choices = []
            for offset, cell in enumerate(path):
                required = 0
                if offset:
                    required |= self._required_bit(cell, path[offset - 1])
                if offset + 1 < len(path):
                    required |= self._required_bit(cell, path[offset + 1])
                options = choices_for(cell, required)
                if not options:
                    return
                all_choices.append(options)
            feasible_paths += 1
            path_minimum = sum(options[0][1] for options in all_choices)
            near_costs.append(path_minimum)
            if best_cost is not None and path_minimum > best_cost:
                return
            for selection in product(*all_choices):
                orientation_combinations += 1
                if expanded + orientation_combinations > node_budget:
                    raise PipeSolveRejected("SOLVE_BUDGET_EXCEEDED", {
                        "search_nodes": expanded, "orientation_combinations": orientation_combinations,
                    })
                rotations = [0] * len(puzzle.initial_masks)
                for cell, (_, _, turns) in zip(path, selection):
                    rotations[_index(puzzle, cell)] = turns
                state = PipeState("pipes", 0, tuple(rotations))
                active_paths = self.rules.connected_paths(puzzle, state, limit=2)
                if not active_paths:
                    continue
                value_cost = sum(abs(turns) for turns in rotations)
                if best_cost is None or value_cost < best_cost:
                    best_cost = value_cost
                    signatures = {}
                    path_hashes = set()
                    count_capped = False
                if value_cost != best_cost:
                    continue
                for active_path in active_paths:
                    value = pipe_signature_value(puzzle, active_path, tuple(rotations))
                    add_signature(value, tuple(rotations))

        def search(cell: Cell) -> None:
            nonlocal expanded
            expanded += 1
            if expanded > node_budget:
                raise PipeSolveRejected("SOLVE_BUDGET_EXCEEDED", {"search_nodes": expanded})
            if cell == puzzle.sink:
                evaluate()
                return
            for dx, dy, _, _ in DIRECTIONS:
                nxt = (cell[0] + dx, cell[1] + dy)
                if not (0 <= nxt[0] < puzzle.width and 0 <= nxt[1] < puzzle.height) or nxt in visited:
                    continue
                visited.add(nxt)
                path.append(nxt)
                search(nxt)
                path.pop()
                visited.remove(nxt)

        search(puzzle.source)
        normalized_count = len(signatures)
        unique_path_count = min(2, len(path_hashes))
        status = (
            "unsolvable" if best_cost is None
            else "unique" if normalized_count == 1 and unique_path_count == 1
            else "multiple-minimum-paths" if unique_path_count > 1
            else "multiple-minimum-signatures"
        )
        ordered = tuple(signatures[key] for key in sorted(signatures))
        return {
            "status": status,
            "solution_uniqueness": status == "unique",
            "minimum_quarter_turn_cost": best_cost,
            "normalized_solution_count": normalized_count,
            "normalized_solution_count_capped": count_capped,
            "unique_path_count": unique_path_count,
            "signatures": ordered,
            "equivalence_policy": PIPE_EQUIVALENCE_POLICY,
            "equivalence_policy_version": PIPE_EQUIVALENCE_VERSION,
            "search": {
                "node_budget": node_budget, "search_nodes": expanded,
                "orientation_combinations": orientation_combinations,
                "feasible_geometric_paths": feasible_paths,
                "near_optimal_path_count": (
                    sum(cost <= best_cost + 2 for cost in near_costs) if best_cost is not None else 0
                ),
                "signature_limit": signature_limit,
            },
        }

    def normalized_action_evidence(
        self, puzzle: PipePuzzleSpec, actions: tuple[Action, ...], path_limit: int = 2,
    ) -> dict[str, Any]:
        rotations = [0] * len(puzzle.initial_masks)
        raw_cost = 0
        touched = []
        for action in actions:
            cell = tuple(action.params.get("cell", ()))
            values = action.params.get("quarter_turns", ())
            if len(cell) != 2 or len(values) != 1:
                raise ValueError("invalid rotate action for normalization")
            index = _index(puzzle, cell)
            turns = values[0]
            rotations[index] = (rotations[index] + turns) % 4
            raw_cost += abs(turns)
            touched.append(index)
        canonical_rotations = []
        for index, rotation in enumerate(rotations):
            final_mask = rotate_mask(puzzle.initial_masks[index], rotation)
            _, canonical_turn = canonical_turn_for_final_mask(puzzle.initial_masks[index], final_mask)
            canonical_rotations.append(canonical_turn)
        state = PipeState("pipes", 0, tuple(canonical_rotations))
        paths = self.rules.connected_paths(puzzle, state, path_limit)
        signatures = tuple(pipe_signature_value(puzzle, path, tuple(canonical_rotations)) for path in paths)
        canonical_cost = sum(abs(turns) for turns in canonical_rotations)
        return {
            "raw_action_cost": raw_cost, "canonical_cost": canonical_cost,
            "has_redundant_turns": raw_cost != canonical_cost,
            "has_duplicate_panels": len(touched) != len(set(touched)),
            "signatures": signatures,
            "canonical_signed_rotations": canonical_rotations,
        }

    def _solve_unique_source_goal(self, puzzle: PipePuzzleSpec, node_budget: int) -> Solution:
        analysis = self.analyze_minimum_solutions(puzzle, node_budget)
        if analysis["status"] == "unsolvable":
            raise PipeSolveRejected("UNSOLVABLE", analysis)
        if analysis["unique_path_count"] != 1:
            raise PipeSolveRejected("MULTIPLE_MINIMAL_PATHS", analysis)
        if analysis["normalized_solution_count"] != 1:
            raise PipeSolveRejected("MULTIPLE_MINIMAL_SIGNATURES", analysis)
        chosen = analysis["signatures"][0]
        target_rotations = tuple(chosen["canonical_signed_rotations"])
        path_distance = {tuple(cell): index for index, cell in enumerate(chosen["path_identity"])}
        action_indices = sorted(
            (index for index, turns in enumerate(target_rotations) if turns),
            key=lambda index: (-path_distance[_cell(puzzle, index)], index),
        )
        state = self.rules.initial_state(puzzle)
        initial_hash = sha256_value(state.to_dict())
        actions = []
        for index in action_indices:
            action = Action(
                1, "rotate_piece", f"pipe-{index}",
                {"cell": list(_cell(puzzle, index)), "quarter_turns": [target_rotations[index]]},
                {"state_hash": sha256_value(state.to_dict())},
            )
            state = self.rules.apply(puzzle, state, action)
            actions.append(action)
        if not self.rules.is_goal(puzzle, state):
            raise RuntimeError("SOLVER_INTERNAL_ERROR")
        return Solution(
            "1.2.0", self.solver_id, self.solver_version,
            "proven_unique_normalized_minimum",
            tuple(actions), initial_hash, sha256_value(state.to_dict()),
            chosen["minimal_quarter_turn_cost"],
            analysis["search"]["search_nodes"] + analysis["search"]["orientation_combinations"],
            "unique:" + chosen["normalized_signature_hash"],
        )

    def route_candidates(self, puzzle: PipePuzzleSpec, node_budget: int = 100_000) -> tuple[list[dict], int]:
        """Enumerate shape-feasible simple routes without revealing one in problem data."""
        candidates: list[dict] = []
        path = [puzzle.source]
        visited = {puzzle.source}
        expanded = 0

        def evaluate() -> None:
            rotations = [0] * len(puzzle.initial_masks)
            for path_index, cell in enumerate(path):
                required = 0
                if path_index:
                    required |= self._required_bit(cell, path[path_index - 1])
                if path_index + 1 < len(path):
                    required |= self._required_bit(cell, path[path_index + 1])
                index = _index(puzzle, cell)
                valid_turns = [
                    turns for turns in range(4)
                    if rotate_mask(puzzle.initial_masks[index], turns) & required == required
                ]
                if not valid_turns:
                    return
                rotations[index] = min(valid_turns)
            state = PipeState("pipes", 0, tuple(rotations))
            if not self.rules.connected_path(puzzle, state):
                return
            signature = ",".join(map(str, rotations))
            tie = sha256((sha256_value(puzzle.to_dict()) + ":" + signature).encode("utf-8")).hexdigest()
            candidates.append({
                "path": tuple(path), "rotations": tuple(rotations),
                "quarter_turns": sum(rotations),
                "affected_pieces": sum(bool(value) for value in rotations),
                "path_length": len(path), "tie": tie,
            })

        def search(cell: Cell) -> None:
            nonlocal expanded
            expanded += 1
            if expanded > node_budget:
                raise RuntimeError("SOLVE_BUDGET_EXCEEDED")
            if cell == puzzle.sink:
                evaluate()
                return
            for dx, dy, _, _ in DIRECTIONS:
                nxt = (cell[0] + dx, cell[1] + dy)
                if not (0 <= nxt[0] < puzzle.width and 0 <= nxt[1] < puzzle.height) or nxt in visited:
                    continue
                visited.add(nxt)
                path.append(nxt)
                search(nxt)
                path.pop()
                visited.remove(nxt)

        search(puzzle.source)
        return candidates, expanded

    def _solve_source_goal_v2(self, puzzle: PipePuzzleSpec, node_budget: int) -> Solution:
        candidates, expanded = self.route_candidates(puzzle, node_budget)
        if not candidates:
            raise RuntimeError("UNSOLVABLE")
        # Definition of optimality: minimum clockwise quarter turns, then
        # affected panels, route length, then a puzzle-hash-derived tie-break.
        chosen = min(candidates, key=lambda item: (
            item["quarter_turns"], item["affected_pieces"], item["path_length"], item["tie"]
        ))
        target_rotations = chosen["rotations"]
        distance = {cell: index for index, cell in enumerate(chosen["path"])}
        action_indices = sorted(
            (index for index, turns in enumerate(target_rotations) if turns),
            key=lambda index: (-distance.get(_cell(puzzle, index), -1), index),
        )
        state = self.rules.initial_state(puzzle)
        initial_hash = sha256_value(state.to_dict())
        actions = []
        for index in action_indices:
            action = Action(
                1, "rotate_piece", f"pipe-{index}",
                {"cell": list(_cell(puzzle, index)), "quarter_turns": [target_rotations[index]]},
                {"state_hash": sha256_value(state.to_dict())},
            )
            state = self.rules.apply(puzzle, state, action)
            actions.append(action)
        if not self.rules.is_goal(puzzle, state):
            raise RuntimeError("SOLVER_INTERNAL_ERROR")
        answer_key = "source-goal-rotations:" + ",".join(map(str, target_rotations))
        return Solution(
            "1.1.0", "pipes-source-goal", "2",
            "proven_minimum_clockwise_quarter_turns",
            tuple(actions), initial_hash, sha256_value(state.to_dict()),
            chosen["quarter_turns"], expanded, answer_key,
        )

    def _solve_legacy_all_cells(self, puzzle: PipePuzzleSpec, node_budget: int) -> Solution:
        count = puzzle.width * puzzle.height
        domains = []
        for mask in puzzle.initial_masks:
            unique = {}
            for turns in range(4):
                unique.setdefault(rotate_mask(mask, turns), turns)
            domains.append(tuple((turns, rotated) for rotated, turns in sorted(unique.items())))
        assignment: list[tuple[int, int] | None] = [None] * count
        expanded = 0

        def viable(index: int, option: tuple[int, int]) -> bool:
            _, mask = option
            x, y = _cell(puzzle, index)
            for dx, dy, bit, opposite in DIRECTIONS:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < puzzle.width and 0 <= ny < puzzle.height):
                    if mask & bit:
                        return False
                    continue
                neighbor = assignment[ny * puzzle.width + nx]
                if neighbor is not None and bool(mask & bit) != bool(neighbor[1] & opposite):
                    return False
            return True

        def search() -> bool:
            nonlocal expanded
            expanded += 1
            if expanded > node_budget:
                raise RuntimeError("SOLVE_BUDGET_EXCEEDED")
            if all(option is not None for option in assignment):
                state = PipeState("pipes", 0, tuple(option[0] for option in assignment if option is not None))
                return self.rules.is_goal(puzzle, state)
            choices = []
            for index, current in enumerate(assignment):
                if current is None:
                    allowed = tuple(option for option in domains[index] if viable(index, option))
                    if not allowed:
                        return False
                    choices.append((len(allowed), index, allowed))
            _, index, allowed = min(choices, key=lambda item: (item[0], item[1]))
            for option in allowed:
                assignment[index] = option
                forward_ok = True
                x, y = _cell(puzzle, index)
                for dx, dy, _, _ in DIRECTIONS:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < puzzle.width and 0 <= ny < puzzle.height:
                        neighbor_index = ny * puzzle.width + nx
                        if assignment[neighbor_index] is None and not any(viable(neighbor_index, candidate) for candidate in domains[neighbor_index]):
                            forward_ok = False
                            break
                if forward_ok and search():
                    return True
                assignment[index] = None
            return False

        if not search():
            raise RuntimeError("UNSOLVABLE")
        target_rotations = tuple(option[0] for option in assignment if option is not None)
        solved_state = PipeState("pipes", 0, target_rotations)
        solved_masks = [self.rules.mask(puzzle, solved_state, _cell(puzzle, index)) for index in range(count)]
        distance = {puzzle.source: 0}
        queue = deque([puzzle.source])
        while queue:
            cell = queue.popleft()
            mask = solved_masks[_index(puzzle, cell)]
            for dx, dy, bit, _ in DIRECTIONS:
                nxt = (cell[0] + dx, cell[1] + dy)
                if mask & bit and nxt not in distance:
                    distance[nxt] = distance[cell] + 1
                    queue.append(nxt)
        # Independent rotations commute. Applying far pieces first and the
        # source-side pieces last makes the final connection visually legible.
        action_indices = sorted(
            (index for index, turns in enumerate(target_rotations) if turns),
            key=lambda index: (-distance[_cell(puzzle, index)], (_cell(puzzle, index)[0] + _cell(puzzle, index)[1]) % 2, index),
        )
        state = self.rules.initial_state(puzzle)
        initial_hash = sha256_value(state.to_dict())
        actions = []
        for index in action_indices:
            turns = target_rotations[index]
            action = Action(
                1, "rotate_piece", f"pipe-{index}",
                {"cell": list(_cell(puzzle, index)), "quarter_turns": [turns]},
                {"state_hash": sha256_value(state.to_dict())},
            )
            state = self.rules.apply(puzzle, state, action)
            actions.append(action)
        if not self.rules.is_goal(puzzle, state):
            raise RuntimeError("SOLVER_INTERNAL_ERROR")
        answer_key = "rotations:" + ",".join(map(str, target_rotations))
        return Solution(
            "1.0.0", "pipes-csp", "1", "proven_satisfying",
            tuple(actions), initial_hash, sha256_value(state.to_dict()), len(actions), expanded, answer_key,
        )


def replay_pipes(puzzle: PipePuzzleSpec, actions: tuple[Action, ...], rules: PipeRules) -> PipeState:
    return trace_pipes(puzzle, actions, rules).final


def trace_pipes(puzzle: PipePuzzleSpec, actions: tuple[Action, ...], rules: PipeRules) -> PipeReplayTrace:
    state = rules.initial_state(puzzle)
    initial = state
    steps = []
    for action in actions:
        before = state
        state = rules.apply(puzzle, state, action)
        steps.append(PipeReplayStep(action, before, state))
    return PipeReplayTrace(initial, tuple(steps), state)


def validate_pipe_solution(puzzle: PipePuzzleSpec, solution: Solution, rules: PipeRules) -> list[str]:
    failures = rules.validate_structure(puzzle)
    try:
        trace = trace_pipes(puzzle, solution.actions, rules)
    except ValueError as error:
        failures.append(f"illegal action: {error}")
        return failures
    if not rules.is_goal(puzzle, trace.final):
        failures.append("final state does not satisfy the selected pipe ruleset")
    if sha256_value(trace.initial.to_dict()) != solution.initial_state_hash:
        failures.append("initial state hash mismatch")
    if sha256_value(trace.final.to_dict()) != solution.final_state_hash:
        failures.append("final state hash mismatch")
    expected_cost = (
        len(solution.actions) if puzzle.ruleset == "connected-no-leaks-v1"
        else sum(abs(action.params["quarter_turns"][0]) for action in solution.actions)
    )
    if solution.cost != expected_cost:
        failures.append("solution cost mismatch")
    if puzzle.ruleset in {"source-to-goal-v2", "source-to-goal-unique-v3"}:
        for omitted_index in range(len(solution.actions)):
            rotations = list(trace.final.rotations)
            action = solution.actions[omitted_index]
            cell = tuple(action.params["cell"])
            rotations[_index(puzzle, cell)] = 0
            omitted_state = PipeState("pipes", 0, tuple(rotations))
            if rules.is_goal(puzzle, omitted_state):
                failures.append(f"goal-irrelevant action: {action.actor_id}")
        solver = PipeSolver(rules)
        if puzzle.ruleset == "source-to-goal-unique-v3":
            analysis = solver.analyze_minimum_solutions(puzzle)
            if analysis["normalized_solution_count"] != 1:
                failures.append("normalized minimum solution count is not one")
            if analysis["unique_path_count"] != 1:
                failures.append("minimum START-to-GOAL path identity is not unique")
            evidence = solver.normalized_action_evidence(puzzle, solution.actions)
            if len(evidence["signatures"]) != 1:
                failures.append("emitted actions do not produce exactly one active path")
            else:
                emitted_hash = evidence["signatures"][0]["normalized_signature_hash"]
                expected_hash = analysis["signatures"][0]["normalized_signature_hash"] if analysis["signatures"] else None
                if emitted_hash != expected_hash:
                    failures.append("emitted Action signature differs from the unique minimum signature")
                if solution.answer_equivalence_key != "unique:" + emitted_hash:
                    failures.append("answer equivalence key mismatch")
            if evidence["has_redundant_turns"] or evidence["has_duplicate_panels"]:
                failures.append("emitted actions contain redundant or non-minimal panel turns")
            if solution.cost != analysis["minimum_quarter_turn_cost"]:
                failures.append("solution does not match normalized minimum-turn oracle")
        else:
            oracle = solver.solve(puzzle)
            if solution.cost != oracle.cost:
                failures.append("solution does not match minimum-quarter-turn oracle")
    return failures


def pipe_difficulty_preset(band: str) -> dict:
    from .preset_loader import difficulty_preset as load_difficulty_preset
    return load_difficulty_preset("pipes", band)


def pipe_difficulty_report(puzzle: PipePuzzleSpec, solution: Solution, rules: PipeRules) -> dict:
    rotations = [action.params["quarter_turns"][0] for action in solution.actions]
    unique_orientations = []
    for mask in puzzle.initial_masks:
        unique_orientations.append(len({rotate_mask(mask, turns) for turns in range(4)}))
    connector_arms = sum(mask.bit_count() for mask in puzzle.initial_masks)
    junction_pieces = sum(mask.bit_count() >= 3 for mask in puzzle.initial_masks)
    elbow_pieces = sum(mask.bit_count() == 2 and mask not in {NORTH | SOUTH, EAST | WEST} for mask in puzzle.initial_masks)
    final = trace_pipes(puzzle, solution.actions, rules).final
    source_goal_ruleset = puzzle.ruleset in {"source-to-goal-v2", "source-to-goal-unique-v3"}
    path = rules.connected_path(puzzle, final) if source_goal_ruleset else ()
    path_edges = {frozenset((first, second)) for first, second in zip(path, path[1:])}
    initial = rules.initial_state(puzzle)
    false_connections = 0
    for y in range(puzzle.height):
        for x in range(puzzle.width):
            cell = (x, y)
            mask = rules.mask(puzzle, initial, cell)
            for dx, dy, bit, opposite in DIRECTIONS[:2]:
                neighbor = (x + dx, y + dy)
                if 0 <= neighbor[0] < puzzle.width and 0 <= neighbor[1] < puzzle.height:
                    if mask & bit and rules.mask(puzzle, initial, neighbor) & opposite:
                        if frozenset((cell, neighbor)) not in path_edges:
                            false_connections += 1
    candidate_routes = 0
    near_optimal_routes = 0
    uniqueness = None
    if puzzle.ruleset == "source-to-goal-unique-v3":
        uniqueness = PipeSolver(rules).analyze_minimum_solutions(puzzle)
        candidate_routes = uniqueness["search"]["feasible_geometric_paths"]
        near_optimal_routes = uniqueness["search"]["near_optimal_path_count"]
    elif puzzle.ruleset == "source-to-goal-v2":
        candidates, _ = PipeSolver(rules).route_candidates(puzzle)
        candidate_routes = len(candidates)
        near_optimal_routes = sum(item["quarter_turns"] <= solution.cost + 2 for item in candidates)
    path_set = set(path)
    adjacent_distractors = sum(
        cell not in path_set and any(abs(cell[0] - px) + abs(cell[1] - py) == 1 for px, py in path_set)
        for cell in ((_x, _y) for _y in range(puzzle.height) for _x in range(puzzle.width))
    )
    mechanical = {
        "required_quarter_turns": sum(abs(value) for value in rotations),
        "required_rotation_pieces": len(solution.actions),
        "required_path_length": len(path),
        "candidate_routes": candidate_routes,
        "near_optimal_routes": near_optimal_routes,
        "false_connection_edges": false_connections,
        "path_adjacent_distractors": adjacent_distractors,
        "goal_irrelevant_actions": 0 if source_goal_ruleset else None,
        "solver_expanded_nodes": solution.expanded_nodes,
        "ambiguous_pieces": sum(count > 1 for count in unique_orientations),
        "orientation_domain_total": sum(unique_orientations),
        "constraint_ambiguity": round(sum(unique_orientations) / len(unique_orientations), 4),
        "visual_clutter": round((connector_arms + 1.5 * junction_pieces + 0.5 * elbow_pieces) / len(puzzle.initial_masks), 4),
        "junction_pieces": junction_pieces,
        "elbow_pieces": elbow_pieces,
        "straight_pieces": sum(mask in {NORTH | SOUTH, EAST | WEST} for mask in puzzle.initial_masks),
        "board_cells": puzzle.width * puzzle.height,
    }
    mechanical["difficulty_score"] = (
        mechanical["required_path_length"]
        + 2 * mechanical["required_rotation_pieces"]
        + mechanical["required_quarter_turns"]
        + min(10, mechanical["candidate_routes"])
        + mechanical["false_connection_edges"]
    )
    human_status = (
        "uncalibrated-pipes-unique-v3" if puzzle.ruleset == "source-to-goal-unique-v3"
        else "uncalibrated-pipes-source-goal-v2"
    )
    report = {
        "mechanical": mechanical,
        "human": {
            "status": human_status, "model_version": None,
            "calibration_scope": None, "predicted_correct_time_ms": None,
            "p_solve_before_reveal": None,
            "features": {
                "required_path_length": mechanical["required_path_length"],
                "required_rotation_pieces": mechanical["required_rotation_pieces"],
                "candidate_routes": mechanical["candidate_routes"],
                "false_connection_edges": mechanical["false_connection_edges"],
            },
        },
        "requested_band": None, "accepted_band": None, "quality_preset": None,
    }
    if uniqueness is not None:
        signature = uniqueness["signatures"][0] if uniqueness["normalized_solution_count"] == 1 else None
        report["solution_uniqueness"] = {
            "status": uniqueness["status"],
            "normalized_solution_count": uniqueness["normalized_solution_count"],
            "normalized_solution_count_capped": uniqueness["normalized_solution_count_capped"],
            "unique_path_count": uniqueness["unique_path_count"],
            "equivalence_policy": uniqueness["equivalence_policy"],
            "equivalence_policy_version": uniqueness["equivalence_policy_version"],
            "normalized_signature": signature,
            "normalized_signature_hash": signature["normalized_signature_hash"] if signature else None,
            "path_identity": signature["path_identity"] if signature else None,
            "path_identity_hash": signature["path_identity_hash"] if signature else None,
            "search": uniqueness["search"],
        }
    return report


def pipe_quality_rejection(difficulty: dict, requested_band: str = "medium") -> str | None:
    preset = pipe_difficulty_preset(requested_band)
    metrics = difficulty["mechanical"]
    difficulty["requested_band"] = requested_band
    difficulty["quality_preset"] = preset["name"]
    uniqueness = difficulty.get("solution_uniqueness")
    if uniqueness is not None:
        if uniqueness["unique_path_count"] != 1:
            return "MULTIPLE_MINIMAL_PATHS"
        if uniqueness["normalized_solution_count"] != 1:
            return "MULTIPLE_MINIMAL_SIGNATURES"
    if any(
        metrics[metric] < threshold for metric, threshold in (
            ("required_path_length", preset["min_required_path_length"]),
            ("required_rotation_pieces", preset["min_required_rotation_pieces"]),
            ("required_quarter_turns", preset["min_required_quarter_turns"]),
            ("candidate_routes", preset["min_candidate_routes"]),
            ("near_optimal_routes", preset.get("min_near_optimal_routes", 0)),
            ("false_connection_edges", preset.get("min_false_connection_edges", 0)),
        )
    ):
        return "TOO_TRIVIAL"
    if metrics["required_quarter_turns"] > preset["max_required_quarter_turns"]:
        return "ANIMATION_TOO_LONG"
    if metrics["difficulty_score"] < preset["min_difficulty_score"]:
        return "TOO_TRIVIAL"
    if metrics["difficulty_score"] > preset["max_difficulty_score"]:
        return "OUTSIDE_BAND"
    difficulty["accepted_band"] = requested_band
    difficulty["human"]["status"] = "uncalibrated-pipes-unique-v3"
    return None
